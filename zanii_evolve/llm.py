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
"""LLM client for the three search roles (proposer, analyst, critic) and the
brief-domain judge.

The model string picks the provider (see providers.py): `anthropic/...`,
`vertex/...`, `openai/...`, `ollama/...`, or a bare Claude id (Vertex AI when
ZANII_EVOLVE_VERTEX_PROJECTS is set, else the Anthropic API). Transient
failures are retried with backoff; Vertex rotates projects on each attempt.
`cache_prefix` sends a large stable leading block (constitution + harness
source) as its own ephemeral-cached content block so repeated turns pay to
read it once.
"""

from __future__ import annotations

import os
import random
import re
import time

from zanii_evolve.providers import ProviderError, Reply, resolve

MODEL = os.environ.get("ZANII_EVOLVE_SEARCH_MODEL", "claude-opus-4-8")
MAX_TOKENS = 20_000

_JSON_SUFFIX = ("\n\nOutput ONLY a single valid JSON object. No prose before or "
                "after, no markdown fences.")


def extract_json(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith("{") and not t.startswith("["):
        start = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
        if start != -1:
            t = t[start:]
    if t and t[0] == "{" and not t.endswith("}"):
        end = t.rfind("}")
        if end != -1:
            t = t[:end + 1]
    if t and t[0] == "[" and not t.endswith("]"):
        end = t.rfind("]")
        if end != -1:
            t = t[:end + 1]
    return t


def complete(prompt: str, system: str | None = None, model: str | None = None,
             max_tokens: int = MAX_TOKENS, cache_prefix: str | None = None,
             effort: str | None = None, max_retries: int = 6) -> Reply:
    """One user turn -> Reply(text, token usage). Retries transient errors with
    exponential backoff and jitter; a non-retryable error (bad request, auth,
    unknown model, refusal) is raised at once."""
    provider, model_id = resolve(model or MODEL)
    content = ([{"type": "text", "text": cache_prefix,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": prompt}] if cache_prefix else prompt)
    messages = [{"role": "user", "content": content}]
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            reply = provider.complete(model_id, system or "", messages, max_tokens, effort)
            if reply.text:
                return reply
            last_err = ProviderError("empty response")
        except ProviderError as e:
            if not e.retryable:
                raise
            last_err = e
        time.sleep(min(2 ** attempt, 30) + random.uniform(0, 1))
    raise RuntimeError(f"LLM call failed after {max_retries} tries: {last_err}")


def generate(prompt: str, system: str | None = None, max_retries: int = 6,
             json_only: bool = False, model: str | None = None,
             max_tokens: int = MAX_TOKENS, cache_prefix: str | None = None) -> str:
    sys_prompt = (system or "") + (_JSON_SUFFIX if json_only else "")
    text = complete(prompt, system=sys_prompt, model=model, max_tokens=max_tokens,
                    cache_prefix=cache_prefix, max_retries=max_retries).text
    return extract_json(text) if json_only else text


if __name__ == "__main__":
    print(generate("Reply with exactly: OK", max_tokens=16))
