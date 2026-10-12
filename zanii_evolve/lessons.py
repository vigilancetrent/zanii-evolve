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
"""Lesson packs: opt-in sharing of entity-free lessons between runs.

Memory is scoped to one run on purpose. A pack is the explicit exception:

    export   cross-task lessons of a run -> pack.jsonl, after the same leakage
             guard the harness applies on write, a task-id screen, PII redaction
             and de-duplication
    import   packs -> domains/<d>/shared_lessons.jsonl (main checkout, outside
             the editable harness)

When that file exists, brief domains seed every trial's JSONL memory with it
(H_0 and every candidate alike, so selection stays fair).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .tasksets import redact

# same guard as mechanisms._LEAKY (the harness module imports fcntl, so it is not imported here)
_LEAKY = re.compile(r"expected|gold\s*answer|answer\s*[:=]|rubric|reference\s+answer", re.I)
MAX_NOTE = 600
MAX_PACK = 1000


def _key(note: str) -> str:
    return " ".join(note.lower().split())


def clean(rows: list[dict], task_ids: set[str] | None = None) -> list[dict]:
    """Leak-guarded, redacted, de-duplicated lesson rows."""
    ids = sorted(task_ids or (), key=len, reverse=True)
    id_re = re.compile(r"\b(?:" + "|".join(map(re.escape, ids)) + r")\b") if ids else None
    seen, out = set(), []
    for r in rows:
        note = r.get("note")
        if not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE:
            continue
        if _LEAKY.search(note) or (id_re and id_re.search(note)):
            continue
        note = redact(note.strip())
        k = _key(note)
        if k in seen:
            continue
        seen.add(k)
        tags = [str(t)[:40] for t in (r.get("tags") or [])[:6]]
        out.append({"note": note, "tags": tags})
    return out[:MAX_PACK]


def export(memory_rows: list[dict], task_ids: set[str]) -> list[dict]:
    """Semantic lessons from a run (server.State.memory rows) -> pack rows."""
    return clean([r for r in memory_rows if r.get("kind") == "semantic"], task_ids)


def read_pack(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"{path}:{n}: not JSON") from e
    return rows


def import_packs(dest: Path, packs: list[list[dict]], task_ids: set[str]) -> int:
    """Merge packs into dest (shared_lessons.jsonl); returns the new line count."""
    have = read_pack(dest) if Path(dest).exists() else []
    merged = clean(have + [r for p in packs for r in p], task_ids)
    tmp = Path(dest).with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged),
                   encoding="utf-8")
    tmp.replace(dest)
    return len(merged)
