# Copyright 2026 The rrsi Authors.
# Modifications Copyright 2026 zaniiai agency.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copyright 2026 Google LLC
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
"""Evolvable mechanism substrate for the four structural levers.

These are inert primitives: importing this module changes nothing. The
proposer WIRES a primitive into terminus_2.py to activate a lever, then the
critic/smoke/eval gates judge whether it helps. Kept dependency-free and
defensive — the harness runs unattended on 178 trials.

Levers:
  skill    — author harness/skills/<name>/SKILL.md; call upload_skills() in
             run()/setup() so the model can discover+cat them in-container
             (progressive disclosure). See Terminus2._build_skills_section.
  memory   — Memory(): ZaniiDB Agent Memory when installed (hybrid recall,
             see ZaniiDBMemory), else fcntl-locked host-side JSONL, semantic + episodic.
             ENTITY-FREE ONLY (critic + SKILL.md enforce): store general
             procedures/lessons, never task-specific I/O — the benchmark is
             evolved and evaluated on the same tasks, so persisting concrete
             task data across trials is memorization, not improvement.
  task memory — TaskMemory: before(situation) recall / after(lesson, outcome)
             record loop over Memory(); same entity-free rule.
  client_tool — ToolRegistry: host-side callables the loop can run and inject.
  subagent — subcall(): one bounded, tightly-briefed extra policy call.
"""

from __future__ import annotations

import asyncio
import atexit
import fcntl
import json
import os
import re
import threading
import shlex
from pathlib import Path

STATE_DIR = Path(os.environ.get("ZANII_EVOLVE_STATE_DIR", "/tmp/tbmh_state"))

# crude entity-ish guard for memory writes (defence-in-depth; critic is primary)
_LEAKY = re.compile(r"/app/|/tests?/|\.py::|expected|reference|answer\s*[:=]",
                    re.I)


# ---------------------------------------------------------------- memory ----
class JsonlMemory:
    """Append-only JSONL memory with fcntl locking. Two granularities:
      semantic  — general, timeless procedures/lessons (task-agnostic).
      episodic  — pattern tied to a task TYPE/situation (still entity-free).
    Reads are ALWAYS filtered (by kind + optional predicate/limit); never
    dump the whole store into context."""

    def __init__(self, state_dir: Path | str | None = None):
        self.dir = Path(state_dir or STATE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str) -> Path:
        kind = kind if kind in ("semantic", "episodic") else "semantic"
        return self.dir / f"{kind}.jsonl"

    def write(self, kind: str, note: str, tags: list[str] | None = None,
              dedupe: bool = True) -> bool:
        """Persist one entity-free lesson. Returns False if rejected/duplicate.
        Rejects notes that look like they carry task-specific data."""
        note = (note or "").strip()
        if not note or _LEAKY.search(note):
            return False
        rec = {"note": note[:600], "tags": (tags or [])[:6]}
        p = self._path(kind)
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
        return True

    def read(self, kind: str, match: str | None = None, limit: int = 8) -> list:
        p = self._path(kind)
        if not p.exists():
            return []
        out = []
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
        return out[-limit:]

    def digest(self, kind: str = "semantic", match: str | None = None,
               limit: int = 8) -> str:
        """Compact, injectable block of filtered lessons (or '')."""
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

# ---------------------------------------------------------- client tools ----
class ToolRegistry:
    """Host-side meta-tools the agent loop can run and feed back into context.
    Register callables that compute/parse/transform (NOT touch the verifier)."""

    def __init__(self):
        self._tools: dict = {}

    def register(self, name: str, fn, desc: str):
        self._tools[name] = (fn, desc)

    def spec(self) -> str:
        if not self._tools:
            return ""
        rows = "\n".join(f"- {n}: {d}" for n, (_, d) in self._tools.items())
        return f"\n\n[CLIENT TOOLS you may request]\n{rows}"

    def call(self, name: str, args: dict) -> str:
        if name not in self._tools:
            return f"ERROR: unknown tool {name}"
        try:
            return str(self._tools[name][0](**(args or {})))[:4000]
        except Exception as e:  # noqa: BLE001 - never crash the rollout
            return f"ERROR: tool {name} raised {type(e).__name__}: {e}"


# -------------------------------------------------------------- subagent ----
def subcall(llm, brief: str, max_tokens: int = 800) -> str:
    """One bounded extra call to the SAME frozen policy. Keep the brief tight
    ('return only X'); never delegate open-ended reasoning or a whole task.
    PRIOR: sub-calls tend to hurt on small policies (early exit); measure,
    don't assume. Returns '' on any error."""
    try:
        from harbor.llms.chat import Chat
        c = Chat(llm)
        return (c.chat(brief, max_tokens=max_tokens) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------- skills -----
async def upload_skills(environment, repo_skills_dir: Path | str,
                        container_dir: str = "/root/skills") -> str | None:
    """Copy authored skills (harness/skills/<name>/SKILL.md) into the task
    container and return the container path to assign to self.skills_dir so
    Terminus2._build_skills_section can advertise them. Returns None if there
    are no skills. Wire this in run() before building the skills section."""
    repo = Path(repo_skills_dir)
    mds = sorted(repo.glob("*/SKILL.md"))
    if not mds:
        return None
    try:
        await environment.exec(f"mkdir -p {shlex.quote(container_dir)}",
                               timeout_sec=10)
        for md in mds:
            name = md.parent.name
            dest = f"{container_dir}/{name}"
            await environment.exec(f"mkdir -p {shlex.quote(dest)}", timeout_sec=10)
            body = md.read_text()
            # write via heredoc-safe base64 to avoid quoting issues
            import base64
            b64 = base64.b64encode(body.encode()).decode()
            await environment.exec(
                f"echo {shlex.quote(b64)} | base64 -d > "
                f"{shlex.quote(dest + '/SKILL.md')}", timeout_sec=15)
        return container_dir
    except Exception:  # noqa: BLE001
        return None
