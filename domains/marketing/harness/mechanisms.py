# Copyright 2026 zaniiai agency.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Evolvable mechanism substrate for a brief-domain harness.

Inert primitives: importing this module changes nothing. The proposer WIRES
one into agent.py to activate a lever; the critic, smoke and evaluation gates
then judge whether it helps. Dependency-free and defensive: the harness runs
unattended on every task of the evolve set.

Levers:
  memory      â€” Memory(): ZaniiDB Agent Memory when installed (hybrid recall,
                see ZaniiDBMemory), else a JSONL store under ZANII_EVOLVE_STATE_DIR.
                ENTITY-FREE ONLY (critic + SKILL.md enforce): store general
                procedures/lessons, never task-specific content â€” the harness
                is evolved and evaluated on the same tasks, so persisting task
                data across trials is memorization, not improvement.
  task memory â€” TaskMemory: before(situation) recall / after(lesson, outcome)
                record loop over Memory(); same entity-free rule.
  subagent    â€” any extra `complete(...)` call in agent.py (a reviewer, a
                planner); it costs tokens and must pay for itself.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import tempfile
import threading
from pathlib import Path

try:
    import fcntl
except ImportError:                     # Windows: single-host runs, no flock
    class fcntl:                        # noqa: N801 - module stand-in
        LOCK_EX = LOCK_SH = LOCK_UN = 0

        @staticmethod
        def flock(*_):
            return None


def _state_dir() -> Path:
    d = os.environ.get("ZANII_EVOLVE_STATE_DIR") or os.path.join(tempfile.gettempdir(), "zanii_evolve_state")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


# crude entity-ish guard for memory writes (defence-in-depth; critic is primary)
_LEAKY = re.compile(r"expected|gold\s*answer|answer\s*[:=]|rubric|reference\s+answer", re.I)


# ---------------------------------------------------------------- memory ----
class JsonlMemory:
    """Append-only JSONL memory with fcntl locking under ZANII_EVOLVE_STATE_DIR. Two
    granularities: semantic (general timeless lessons) and episodic (pattern
    tied to a task TYPE/situation, still entity-free). Reads are ALWAYS
    filtered; never dump the whole store into context."""

    def __init__(self, state_dir: Path | str | None = None):
        self.dir = Path(state_dir) if state_dir else _state_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str) -> Path:
        kind = kind if kind in ("semantic", "episodic") else "semantic"
        return self.dir / f"mem_{kind}.jsonl"

    def write(self, kind: str, note: str, tags: list | None = None,
              dedupe: bool = True) -> bool:
        note = (note or "").strip()
        if not note or _LEAKY.search(note):
            return False
        rec = {"note": note[:600], "tags": (tags or [])[:6]}
        p = self._path(kind)
        try:
            with open(p, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                if dedupe:
                    f.seek(0)
                    for line in f:
                        try:
                            if json.loads(line).get("note") == rec["note"]:
                                fcntl.flock(f, fcntl.LOCK_UN)
                                return False
                        except json.JSONDecodeError:
                            pass
                f.seek(0, os.SEEK_END)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001 - never crash the rollout
            return False
        return True

    def read(self, kind: str, match: str | None = None, limit: int = 8) -> list:
        p = self._path(kind)
        if not p.exists():
            return []
        out = []
        try:
            with open(p) as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if match and match.lower() not in (
                            r.get("note", "") + " " + " ".join(r.get("tags", []))
                    ).lower():
                        continue
                    out.append(r)
                fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            return []
        return out[-limit:]

    def digest(self, kind: str = "semantic", match: str | None = None,
               limit: int = 8) -> str:
        recs = self.read(kind, match=match, limit=limit)
        if not recs:
            return ""
        lines = "\n".join(f"- {r['note']}" for r in recs)
        return f"\n\n[MEMORY — {kind} lessons from prior tasks]\n{lines}"


# -------------------------------------------------------- zaniidb memory ----
class ZaniiDBMemory(JsonlMemory):
    """The same write/read/digest surface backed by ZaniiDB Agent Memory
    (`pip install zaniidb-agent-memory`): lessons become L1 memories with
    hybrid BM25+vector recall (vectors only when ZANII_EMBEDDING_* is set).
    semantic -> ZaniiDB `instruction`, episodic -> `episodic`; tags ride in
    scene_name. Exact duplicates are always skipped (dedupe=False is ignored).
    The store lives under <state_dir>/zaniidb so it is scoped to the run like
    the JSONL files; ZANII_DATABASE_URL is deliberately ignored, a shared
    database would leak lessons across runs. Construction raises when ZaniiDB
    is unavailable so Memory() can fall back to JSONL."""

    _TYPES = {"semantic": "instruction", "episodic": "episodic"}
    _loop = None
    _cores: dict = {}
    _lock = threading.Lock()

    def __init__(self, state_dir: Path | str | None = None):
        super().__init__(state_dir)
        from zanii_memory import Settings, ZaniiMemory
        key = str(self.dir / "zaniidb")
        with self._lock:
            core = self._cores.get(key)
        if core is None:
            core = ZaniiMemory(Settings(data_dir=key, database_url=""))
            self._run(core.initialize())
            with self._lock:
                self._cores[key] = core
            atexit.register(lambda: self._run(core.close(), timeout=10))
        self._core = core

    @classmethod
    def _run(cls, coro, timeout: float = 30):
        # ZaniiDB is async and the harness may already be inside an event
        # loop, so it runs on one private daemon loop per process.
        with cls._lock:
            if cls._loop is None:
                cls._loop = asyncio.new_event_loop()
                threading.Thread(target=cls._loop.run_forever, daemon=True).start()
        return asyncio.run_coroutine_threadsafe(coro, cls._loop).result(timeout)

    def write(self, kind: str, note: str, tags: list | None = None,
              dedupe: bool = True) -> bool:
        note = (note or "").strip()
        if not note or _LEAKY.search(note):
            return False
        rec = {"content": note[:600], "type": self._TYPES.get(kind, "instruction"),
               "priority": 80, "scene_name": ",".join(map(str, (tags or [])[:6]))}
        outcome = next((t for t in (tags or []) if t in ("success", "failure")), None)
        try:
            if outcome:
                return self._run(self._insert(rec, {"outcome": outcome})) > 0
            return self._run(self._core.seed([rec])) > 0
        except Exception:  # noqa: BLE001 - never crash the rollout
            return False

    async def _insert(self, rec: dict, metadata: dict) -> int:
        """seed() plus record metadata (seed() takes none): same duplicate
        check, optional embedding and ledger receipt."""
        from zanii_memory.types import MemoryRecord
        core = self._core
        if core.store.l1_content_exists(rec["content"]):
            return 0
        emb = None
        if core.embedder.enabled and core.store.vec_enabled:
            try:
                emb = await core.embedder.embed_one(rec["content"])
            except Exception:  # noqa: BLE001 - store without a vector
                emb = None
        core.store.insert_l1(MemoryRecord(metadata=metadata, **rec), emb)
        try:
            core.ledger.emit("l1.seed", rec["content"])
        except Exception:  # noqa: BLE001 - a ledger failure never breaks memory ops
            pass
        return 1

    def read(self, kind: str, match: str | None = None, limit: int = 8) -> list:
        mtype = self._TYPES.get(kind, "instruction")

        async def _rows():
            if match:
                return await self._core.search_memories(match, limit=limit, type=mtype)
            return self._core.store.get_l1_filtered(type=mtype, limit=limit)

        try:
            rows = self._run(_rows())
        except Exception:  # noqa: BLE001
            return []
        return [{"note": r["content"],
                 "tags": [t for t in (r.get("scene_name") or "").split(",") if t]}
                for r in rows]


def Memory(state_dir: Path | str | None = None) -> JsonlMemory:
    """The harness memory store. ZaniiDB when installed (ZANII_EVOLVE_MEMORY=
    zaniidb, the default), otherwise the fcntl-locked JSONL store; set
    ZANII_EVOLVE_MEMORY=jsonl to force JSONL. Both share write/read/digest."""
    if os.environ.get("ZANII_EVOLVE_MEMORY", "zaniidb").lower() == "zaniidb":
        try:
            return ZaniiDBMemory(state_dir)
        except Exception:  # noqa: BLE001 - ZaniiDB missing or failed to open
            pass
    return JsonlMemory(state_dir)

# ----------------------------------------------------------- task memory ----
_WORD = re.compile(r"[a-z0-9]{3,}")


def _overlap(a: str, b: str) -> int:
    return len(set(_WORD.findall(a.lower())) & set(_WORD.findall(b.lower())))


class TaskMemory:
    """Recall before a task, record after it. Inert until wired:

        tm = TaskMemory()
        prompt += tm.before(situation)            # lessons ranked for this situation
        ... run the task ...
        tm.after(lesson, outcome="success")       # one transferable lesson

    `situation` describes the task TYPE (kind of work, tools, constraints),
    never ids, paths, names or answers. Successes are stored as `semantic`
    procedures, failures as `episodic` pitfalls; both are tagged with the
    outcome, which ZaniiDB keeps as record metadata (its skill generation turns
    successes into procedures and failures into pitfalls). With ZaniiDB, recall
    is its hybrid BM25+vector search; on JSONL it is word-overlap ranking.
    The entity-free write guard applies on both backends."""

    def __init__(self, memory: JsonlMemory | None = None, limit: int = 6):
        self.mem = memory if memory is not None else Memory()
        self.limit = limit

    def _recall(self, kind: str, situation: str) -> list:
        if isinstance(self.mem, ZaniiDBMemory):
            return self.mem.read(kind, match=situation, limit=self.limit)
        recs = self.mem.read(kind, limit=500)
        ranked = sorted(recs, key=lambda r: _overlap(situation, r["note"] + " "
                                                     + " ".join(r.get("tags", []))),
                        reverse=True)
        return [r for r in ranked[:self.limit]
                if _overlap(situation, r["note"] + " " + " ".join(r.get("tags", [])))]

    def before(self, situation: str) -> str:
        """Injectable block of relevant procedures and pitfalls (or '')."""
        if not (situation or "").strip():
            return ""
        wins = self._recall("semantic", situation)
        pits = [r for r in self._recall("episodic", situation)
                if "failure" in r.get("tags", [])]
        parts = []
        if wins:
            parts.append("Procedures that worked on similar tasks:\n"
                         + "\n".join(f"- {r['note']}" for r in wins))
        if pits:
            parts.append("Pitfalls seen on similar tasks:\n"
                         + "\n".join(f"- {r['note']}" for r in pits))
        return ("\n\n[MEMORY â€” lessons from prior tasks]\n" + "\n\n".join(parts)) if parts else ""

    def after(self, lesson: str, outcome: str = "success",
              tags: list | None = None) -> bool:
        """Store one entity-free lesson. outcome: 'success' | 'failure'."""
        outcome = "failure" if outcome == "failure" else "success"
        kind = "semantic" if outcome == "success" else "episodic"
        return self.mem.write(kind, lesson, tags=[outcome, *(tags or [])][:6])

    def consolidate(self) -> dict:
        """Merge near-duplicate lessons (ZaniiDB only; {} on JSONL)."""
        if not isinstance(self.mem, ZaniiDBMemory):
            return {}
        try:
            return self.mem._run(self.mem._core.consolidate(), timeout=120)
        except Exception:  # noqa: BLE001
            return {}
