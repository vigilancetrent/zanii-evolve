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
"""Long-term memory harness: a multi-session chat history in, an answer about
the user out. Evolvable.

H_0 loads every session into a fresh, per-task ZaniiDB store (one capture per
turn, end_session to flush extraction), then builds the answer context from
ZaniiDB recall (extracted, supersede-aware memories when ZANII_LLM_* is
configured) plus BM25 search over the raw conversation, ordered oldest first.
Without zaniidb-agent-memory installed it falls back to the full transcript.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

from prompts import SYSTEM_PROMPT, USER_TEMPLATE

MAX_TOKENS = 600
SEARCH_HITS = 12
USER_KEY = "user"


def _transcript(task: dict) -> str:
    return "\n".join(f"[session {s['session']}] {t['role']}: {t['content']}"
                     for s in task["sessions"] for t in s["turns"])


def _store_dir(task: dict) -> Path:
    base = os.environ.get("ZANII_EVOLVE_STATE_DIR") or tempfile.gettempdir()
    return Path(base) / "tasks" / task["id"] / uuid.uuid4().hex[:8]


async def _zanii_context(task: dict, data_dir: Path) -> str:
    from zanii_memory import Settings, ZaniiMemory
    mem = ZaniiMemory(Settings(data_dir=str(data_dir), database_url=""))
    await mem.initialize()
    try:
        for s in task["sessions"]:
            turns = s["turns"]
            for i in range(0, len(turns), 2):            # one capture per completed turn
                await mem.capture(USER_KEY, turns[i:i + 2], session_id=f"s{s['session']}")
        await mem.end_session(USER_KEY)
        recall = await mem.recall(task["question"], USER_KEY)
        hits = await mem.search_conversations(task["question"], limit=SEARCH_HITS,
                                              session_key=USER_KEY)
    finally:
        await mem.close()
    parts = [p for p in (recall.append_system_context, recall.prepend_context) if p]
    hits = sorted(hits, key=lambda h: (h.get("timestamp", 0), h.get("id", 0)))
    if hits:
        parts.append("Relevant conversation excerpts (oldest first):\n" + "\n".join(
            f"[{h.get('session_id', '?')}] {h.get('role')}: {h.get('content')}" for h in hits))
    return "\n\n".join(parts) or "(no relevant memories found)"


def build_context(task: dict) -> tuple[str, str]:
    """-> (context, source); source is 'zaniidb' or 'transcript'."""
    try:
        import zanii_memory  # noqa: F401
    except ImportError:
        return _transcript(task), "transcript"
    return asyncio.run(_zanii_context(task, _store_dir(task))), "zaniidb"


def solve(task: dict, complete) -> dict:
    context, source = build_context(task)
    prompt = USER_TEMPLATE.format(context=context, question=task["question"])
    reply = complete(prompt, system=SYSTEM_PROMPT, max_tokens=MAX_TOKENS)
    return {"output": reply.text.strip(), "memory_source": source,
            "messages": [{"role": "user", "content": prompt},
                         {"role": "assistant", "content": reply.text}]}
