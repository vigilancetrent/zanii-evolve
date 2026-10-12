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
"""Brief harness: one task in, one text deliverable out. Evolvable.

solve(task, complete) receives the PUBLIC task fields and a `complete(prompt,
system=..., max_tokens=...)` callable bound to the frozen policy model, and
returns {"output": <deliverable text>, "messages": [...]}.
"""

from __future__ import annotations

import json

import mechanisms
from prompts import REFLECT_TEMPLATE, SYSTEM_PROMPT, USER_TEMPLATE

MAX_TOKENS = 4000
MEMORY_LOOP = False     # TaskMemory: recall lessons before the task, record one after


def _situation(task: dict) -> str:
    """Task TYPE for memory recall: never ids, names or task content."""
    return f"{task.get('kind', 'marketing copy')} for a client brand"


def _reflect(tm, situation: str, output: str, complete, messages: list) -> None:
    from zanii_evolve.llm import extract_json
    prompt = REFLECT_TEMPLATE.format(situation=situation, output=output[:6000])
    reply = complete(prompt, max_tokens=400)
    messages += [{"role": "user", "content": prompt},
                 {"role": "assistant", "content": reply.text}]
    try:
        r = json.loads(extract_json(reply.text))
        tm.after(str(r.get("lesson", "")), outcome=str(r.get("outcome", "success")),
                 tags=[situation[:40]])
    except (ValueError, AttributeError):
        pass


def solve(task: dict, complete) -> dict:
    tm = mechanisms.TaskMemory() if MEMORY_LOOP else None
    situation = _situation(task)
    prompt = USER_TEMPLATE.format(task=json.dumps(task, ensure_ascii=False, indent=1))
    if tm:
        prompt += tm.before(situation)
    reply = complete(prompt, system=SYSTEM_PROMPT, max_tokens=MAX_TOKENS)
    output = reply.text.strip()
    messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": reply.text}]
    if tm and output:
        _reflect(tm, situation, output, complete, messages)
    return {"output": output, "messages": messages}
