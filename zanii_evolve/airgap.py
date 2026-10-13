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
"""The air-gapped edition: prove nothing a run uses would leave the machine.

    ZANII_EVOLVE_OFFLINE=1 python zanii_evolve.py --domain acme airgap-check \\
        [--url https://blackbox.internal] [--sign --out airgap.json]

With ZANII_EVOLVE_OFFLINE=1 providers.resolve refuses every provider outside
{ollama, fake} + ZANII_EVOLVE_OFFLINE_ALLOW and every base URL that is not a
loopback or private address. `airgap-check` lists each role model (policy,
judge, search, proposer, analyst, critic), the provider it resolves to and
every configured endpoint (model servers, the Zanii ledger, a remote registry,
Vertex, the URLs you pass such as a Blackbox gateway), and exits 1 when the
guard is off or anything would leave. `--sign` issues an `airgap` statement.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import providers as P

# endpoints outside the provider layer; plus every ZANII_* / BLACKBOX_* env var whose
# name ends in _URL, _BASE_URL or _REGISTRY (see url_envs)
URL_ENVS = ("ZANII_LEDGER_URL", "ZANII_EVOLVE_REGISTRY", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL")
_HOSTPORT = re.compile(r"^[A-Za-z0-9.-]+:\d{1,5}(/.*)?$")


def url_envs() -> list[str]:
    names = set(URL_ENVS) | {k for k in os.environ if k.upper().startswith(("ZANII_", "BLACKBOX_"))
                             and k.upper().endswith(("_URL", "_REGISTRY"))}
    return sorted(names)


def as_url(value: str) -> str | None:
    """`value` as a URL to check, or None when it is a local path (a registry folder).
    A scheme in any case counts; so does a bare host:port."""
    v = value.strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", v):
        return v
    if _HOSTPORT.match(v):                  # host:port (C:\path never has digits)
        return "http://" + v
    return None


def _provider(model: str) -> str:
    prefix, _, rest = str(model).partition("/")
    if rest:
        return prefix
    return "vertex" if os.environ.get("ZANII_EVOLVE_VERTEX_PROJECTS") else "anthropic"


def role_models(domain_cfg: dict | None = None, cfg=None) -> dict[str, str]:
    """The model each role would call, as the harness, grader and loop pick it."""
    d = domain_cfg or {}
    roles = {
        "policy": os.environ.get("ZANII_EVOLVE_POLICY_MODEL",
                                 d.get("policy_model", "claude-opus-5-5")),
        "judge": os.environ.get("ZANII_EVOLVE_JUDGE_MODEL",
                                d.get("judge_model", "claude-sonnet-5-5")),
        "search": os.environ.get("ZANII_EVOLVE_SEARCH_MODEL", "claude-opus-4-8"),
    }
    if cfg is not None:
        roles.update(proposer=cfg.proposer_model, analyst=cfg.analyst_model,
                     critic=cfg.critic_model)
    return roles


def check(domain_cfg: dict | None = None, cfg=None, urls: list[str] = ()) -> dict:
    """{offline, models: [...], endpoints: [...], leaks: [...], ok}."""
    leaks, models, endpoints = [], [], []
    guard = P.offline()
    if not guard:
        leaks.append("ZANII_EVOLVE_OFFLINE is not set: nothing stops a cloud call")
    seen = set()
    for role, model in role_models(domain_cfg, cfg).items():
        prefix = _provider(model)
        allowed = prefix in P.OFFLINE_LOCAL | P._allow()
        models.append({"role": role, "model": model, "provider": prefix, "local": allowed})
        if not allowed:
            leaks.append(f"{role} model {model} runs on {prefix}, off this machine")
        url = P.base_url(prefix)
        if url and prefix not in seen:
            seen.add(prefix)
            endpoints.append({"name": f"{prefix} base URL", "url": url,
                              "local": P.local_url(url)})
    for name in url_envs():
        v = as_url(os.environ.get(name, ""))
        if v and not any(e["url"] == v for e in endpoints):
            local = v.lower().startswith(("http://", "https://")) and P.local_url(v)
            endpoints.append({"name": name, "url": v, "local": local})
    if os.environ.get("ZANII_EVOLVE_VERTEX_PROJECTS"):
        endpoints.append({"name": "ZANII_EVOLVE_VERTEX_PROJECTS", "url": "Google Vertex AI",
                          "local": False})
    endpoints += [{"name": "--url", "url": u, "local": P.local_url(u)} for u in urls]
    leaks += [f"{e['name']} {e['url']} is not a local host" for e in endpoints if not e["local"]]
    return {"offline": guard, "models": models, "endpoints": endpoints, "leaks": leaks,
            "ok": not leaks}


def _handler(args, ctx) -> int:
    dcfg = cfg = None
    if args.domain:
        dcfg = getattr(ctx.domain, "cfg", None)
        dcfg = dcfg if isinstance(dcfg, dict) else None
        cfg = ctx.cfg
    r = check(dcfg, cfg, args.url or [])
    for m in r["models"]:
        print(f"{'ok  ' if m['local'] else 'LEAK'} {m['role']:9} {m['model']} ({m['provider']})")
    for e in r["endpoints"]:
        print(f"{'ok  ' if e['local'] else 'LEAK'} {e['name']}: {e['url']}")
    for why in r["leaks"]:
        print(f"  - {why}")
    print("air-gapped: nothing leaves this machine" if r["ok"] else
          f"NOT air-gapped: {len(r['leaks'])} problem(s)")
    if args.sign:
        from . import statement as ST
        stmt = ST.issue("airgap", {"domain": args.domain, **r},
                        ctx.run_dir if args.domain else None)
        out = Path(args.out or "airgap.json")
        ST.save(stmt, out)
        print(f"signed airgap statement -> {out}")
    elif args.out:
        Path(args.out).write_text(json.dumps(r, indent=1), encoding="utf-8")
    return 0 if r["ok"] else 1


def cli(sub) -> None:
    p = sub.add_parser("airgap-check", help="prove every model and endpoint a run uses stays "
                       "on this machine (exit 1 if not); needs ZANII_EVOLVE_OFFLINE=1")
    p.add_argument("--url", action="append", help="another endpoint to check (repeatable), "
                   "e.g. a Blackbox gateway")
    p.add_argument("--sign", action="store_true", help="issue a signed airgap statement")
    p.add_argument("--out", help="where to write the result (default airgap.json with --sign)")
    p.set_defaults(product=_handler)
