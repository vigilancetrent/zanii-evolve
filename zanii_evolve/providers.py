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
"""LLM providers behind one call shape.

A model string names its provider: `anthropic/claude-opus-5-5`,
`vertex/claude-opus-4-8`, `openai/gpt-5` (any OpenAI-compatible endpoint:
OpenAI, a LiteLLM proxy, vLLM, Ollama), `ollama/llama4` (a local Ollama
server, no key). A bare `claude-*` id keeps the
original behaviour: Vertex AI when ZANII_EVOLVE_VERTEX_PROJECTS is set,
otherwise the Anthropic API.

    provider, model_id = resolve("anthropic/claude-opus-5-5")
    reply = provider.complete(model_id, system, messages, max_tokens)

Custom backends: ZANII_EVOLVE_PROVIDERS="name=module:attr,..." registers
`attr` (a class or factory returning an object with `complete`) under
`name/`, in every process, including harness subprocesses.

Every provider returns a Reply (text + token usage) and raises an exception
whose `retryable` attribute tells llm.py whether another attempt can help.
"""

from __future__ import annotations

import importlib
import itertools
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

# Models that accept the server-side refusal fallback (Anthropic API only).
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ProviderError(RuntimeError):
    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


def _text_of(content) -> str:
    """Flatten Anthropic-style content (str or list of text blocks) to a string."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


class _AnthropicBase:
    """Shared Messages API call for the Anthropic API and Vertex AI."""

    fallbacks = False

    def _client(self):
        raise NotImplementedError

    def complete(self, model: str, system: str, messages: list, max_tokens: int,
                 effort: str | None = None) -> Reply:
        import anthropic
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        if effort:
            kwargs["output_config"] = {"effort": effort}
        client = self._client()
        try:
            if self.fallbacks and model in _FALLBACK_MODELS:
                resp = client.beta.messages.create(
                    betas=[_FALLBACK_BETA], extra_body={"fallbacks": "default"}, **kwargs)
            else:
                resp = client.messages.create(**kwargs)
        except (anthropic.BadRequestError, anthropic.AuthenticationError,
                anthropic.PermissionDeniedError, anthropic.NotFoundError) as e:
            raise ProviderError(f"{type(e).__name__}: {e}", retryable=False) from e
        except anthropic.APIError as e:            # 429, 5xx, connection, timeout
            raise ProviderError(f"{type(e).__name__}: {e}") from e
        except TypeError as e:                     # SDK found no credentials
            raise ProviderError(f"no Anthropic credentials: {e}", retryable=False) from e
        if resp.stop_reason == "refusal":
            raise ProviderError(f"{model} refused the request", retryable=False)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        u = resp.usage
        return Reply(text, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)


class AnthropicProvider(_AnthropicBase):
    """Anthropic API. Credentials resolve the SDK's usual way (ANTHROPIC_API_KEY,
    ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile)."""

    fallbacks = True

    def __init__(self):
        self._c = None
        self._lock = threading.Lock()

    def _client(self):
        with self._lock:
            if self._c is None:
                import anthropic
                self._c = anthropic.Anthropic()
            return self._c


class VertexProvider(_AnthropicBase):
    """Claude on Vertex AI, round-robin over ZANII_EVOLVE_VERTEX_PROJECTS so a
    retry lands on the next project."""

    def __init__(self):
        self.projects = [p.strip() for p in
                         os.environ.get("ZANII_EVOLVE_VERTEX_PROJECTS", "").split(",") if p.strip()]
        self.region = os.environ.get("ZANII_EVOLVE_VERTEX_REGION", "global")
        self._clients: dict = {}
        self._lock = threading.Lock()
        self._rr = itertools.count()

    def _client(self):
        if not self.projects:
            raise ProviderError("set ZANII_EVOLVE_VERTEX_PROJECTS to a comma-separated list of "
                                "GCP projects with Claude on Vertex AI enabled", retryable=False)
        idx = next(self._rr) % len(self.projects)
        with self._lock:
            c = self._clients.get(idx)
            if c is None:
                from anthropic import AnthropicVertex
                c = AnthropicVertex(project_id=self.projects[idx], region=self.region)
                self._clients[idx] = c
            return c


class OpenAICompatProvider:
    """Any OpenAI-compatible /chat/completions endpoint (OpenAI, LiteLLM proxy,
    vLLM, Ollama). ZANII_EVOLVE_OPENAI_BASE_URL (default api.openai.com/v1) and
    ZANII_EVOLVE_OPENAI_API_KEY (falls back to OPENAI_API_KEY). Stdlib HTTP, so
    no extra dependency."""

    BASE_ENV, DEFAULT_BASE = "ZANII_EVOLVE_OPENAI_BASE_URL", "https://api.openai.com/v1"

    def __init__(self, timeout: float = 600):
        self.timeout = timeout

    def _key(self) -> str:
        return os.environ.get("ZANII_EVOLVE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")

    def complete(self, model: str, system: str, messages: list, max_tokens: int,
                 effort: str | None = None) -> Reply:
        base = os.environ.get(self.BASE_ENV, self.DEFAULT_BASE).rstrip("/")
        key = self._key()
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m["role"], "content": _text_of(m["content"])} for m in messages]
        body = {"model": model, "messages": msgs, "max_tokens": max_tokens}
        if effort:
            body["reasoning_effort"] = effort
        req = urllib.request.Request(
            f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {key}"} if key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:500].decode(errors="replace")
            raise ProviderError(f"HTTP {e.code}: {detail}",
                                retryable=e.code == 429 or e.code >= 500) from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise ProviderError(f"{type(e).__name__}: {e}") from e
        choices = data.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        u = data.get("usage") or {}
        return Reply(text, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))


class OllamaProvider(OpenAICompatProvider):
    """A local Ollama server (on-prem, air-gapped, sovereign deployments):
    `ollama/<model>`, ZANII_EVOLVE_OLLAMA_BASE_URL (default localhost:11434/v1),
    no API key. Local models are slower, so the timeout is longer."""

    BASE_ENV, DEFAULT_BASE = "ZANII_EVOLVE_OLLAMA_BASE_URL", "http://localhost:11434/v1"

    def __init__(self, timeout: float = 1800):
        super().__init__(timeout)

    def _key(self) -> str:
        return ""                        # never send a cloud key to a local server


PROVIDERS: dict = {}
_lock = threading.Lock()
_FACTORIES = {"anthropic": AnthropicProvider, "vertex": VertexProvider,
              "openai": OpenAICompatProvider, "ollama": OllamaProvider}


def register(name: str, provider) -> None:
    """Install a provider instance under a prefix (tests, custom backends)."""
    with _lock:
        PROVIDERS[name] = provider


def _load_plugins() -> None:
    for spec in filter(None, (x.strip() for x in
                              os.environ.get("ZANII_EVOLVE_PROVIDERS", "").split(","))):
        name, _, target = spec.partition("=")
        mod, _, attr = target.partition(":")
        if not (name and mod and attr):
            raise ProviderError(f"ZANII_EVOLVE_PROVIDERS entry {spec!r}: use name=module:attr",
                                retryable=False)
        _FACTORIES.setdefault(name.strip(), getattr(importlib.import_module(mod.strip()), attr.strip()))


def resolve(model: str):
    """`prefix/model` -> (provider, model id). Bare ids are Claude: Vertex when
    projects are configured, else the Anthropic API."""
    prefix, _, rest = model.partition("/")
    if not rest:
        prefix, rest = ("vertex" if os.environ.get("ZANII_EVOLVE_VERTEX_PROJECTS") else
                        "anthropic"), model
    with _lock:
        if prefix not in PROVIDERS:
            if prefix not in _FACTORIES:
                _load_plugins()
            if prefix not in _FACTORIES:
                raise ProviderError(f"unknown provider {prefix!r} in model {model!r}; "
                                    f"use one of {sorted(_FACTORIES)}", retryable=False)
            PROVIDERS[prefix] = _FACTORIES[prefix]()
        return PROVIDERS[prefix], rest
