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
"""Offline LLM for tests, loaded through ZANII_EVOLVE_PROVIDERS=fake=fake_provider:Fake.

Judge calls (system prompt from zanii_evolve.brief) get an all-pass verdict
for every rubric line; every other call gets a short canned deliverable.
"""

import json
import re

from zanii_evolve.providers import Reply


class Fake:
    def complete(self, model, system, messages, max_tokens, effort=None):
        content = messages[-1]["content"]
        text = content if isinstance(content, str) else "".join(b["text"] for b in content)
        if "grade one deliverable" in (system or ""):
            rubric = text.split("RUBRIC:\n", 1)[1].split("\n\nDELIVERABLE", 1)[0]
            n = len(re.findall(r"(?m)^\d+\. ", rubric))
            out = json.dumps({"verdicts": [{"i": i, "verdict": "pass", "reason": "ok"}
                                           for i in range(n)]})
        else:
            out = "The user lives in Porto now. Plan your trip with us."
        return Reply(out, input_tokens=len(text) // 4, output_tokens=len(out) // 4)
