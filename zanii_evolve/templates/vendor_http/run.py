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
"""Vendor adapter for a bake-off: a vendor's agent behind an HTTPS endpoint.

    python run.py --tasks _tasks.json --out <dir> [--concurrency N]

vendor.json next to this file: {"url": "https://...", "headers_env": "VENDOR_TOKEN",
"timeout_s": 120}. Each task's public view (never its checks or rubric) is POSTed
as JSON; the reply is JSON {"output": "...", "usage": {"input_tokens": n,
"output_tokens": n}} (usage optional) or plain text. The token is read from the
env var named in headers_env and sent as a bearer token; it is never written.
A failed call writes error.txt and no result, so the next attempt retries it.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from zanii_evolve import net as NET  # the bake-off puts this repo on PYTHONPATH

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "vendor.json").read_text(encoding="utf-8"))
MAX_REPLY = 2_000_000                   # bytes read from one reply


def _usage(u) -> dict:
    if not isinstance(u, dict):
        return {}
    i, o = u.get("input_tokens"), u.get("output_tokens")
    if not (isinstance(i, int) and isinstance(o, int)):
        return {}
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


def call(task: dict) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get(CFG.get("headers_env") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # https only (http to loopback), no redirects (they would carry the token),
    # local only under ZANII_EVOLVE_OFFLINE, reply capped: zanii_evolve/net.py
    raw = NET.request("POST", CFG["url"], headers, json.dumps(task).encode("utf-8"),
                      timeout=float(CFG.get("timeout_s", 120)),
                      max_bytes=MAX_REPLY).decode("utf-8", "replace")
    try:
        body = json.loads(raw)
    except ValueError:
        return {"output": raw, "usage": {}}
    if isinstance(body, dict) and "output" in body:
        return {"output": str(body["output"]), "usage": _usage(body.get("usage"))}
    return {"output": raw, "usage": {}}


def solve_one(task: dict, out: Path) -> None:
    d = out / task["id"]
    if (d / "result.json").exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    try:
        res = call(task)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:  # refused URL
        (d / "error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
        return
    tmp = d / "result.json.tmp"
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "result.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    out = Path(args.out)
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        list(ex.map(lambda t: solve_one(t, out), tasks))


if __name__ == "__main__":
    main()
