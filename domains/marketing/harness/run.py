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
"""Harness runner: solve every task in a task file, one result.json each.

    python run.py --tasks _tasks.json --out <dir> [--concurrency N]

Resume-safe: a task that already has <out>/<id>/result.json is skipped. A
task that raises writes error.txt and no result, so the next attempt retries
it. The policy model comes from ZANII_EVOLVE_POLICY_MODEL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))        # repo root: zanii_evolve.llm

from zanii_evolve.llm import complete as _complete   # noqa: E402
import agent                                         # noqa: E402

MODEL = os.environ.get("ZANII_EVOLVE_POLICY_MODEL", "claude-opus-5-5")


class Usage:
    """Token tally across every policy call one task makes."""

    def __init__(self):
        self.input_tokens = self.output_tokens = 0

    def call(self, prompt: str, **kw):
        reply = _complete(prompt, model=MODEL, **kw)
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        return reply

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens}


def solve_one(task: dict, out: Path) -> None:
    d = out / task["id"]
    if (d / "result.json").exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    usage = Usage()
    try:
        res = agent.solve(task, usage.call)
    except Exception:  # noqa: BLE001 - leave no result so the next attempt retries
        (d / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return
    res["usage"] = usage.as_dict()
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
