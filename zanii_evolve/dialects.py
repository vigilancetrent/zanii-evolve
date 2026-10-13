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
"""Sub-scores per tag: by dialect, industry, channel or any field in task notes.

    dialects --job heldout_ar --key dialect [--sign --out scores.json]

One S hides where an agent is weak: 91% overall can be 97% in Modern Standard
Arabic and 70% in Egyptian. Each group gets its task count, mean task score
and a 95% range from resampling its tasks (seeded, so the same evaluation
always gives the same range). Tasks without the tag are grouped as "untagged".
With --sign the table is a statement of kind "dialect_scores" (statement.py).
"""

from __future__ import annotations

import json
import random
import statistics as st
from pathlib import Path

from . import statement as ST
from .compare import task_means
from .evaluate import EvalResult


def breakdown(ev: EvalResult, tasks: dict[str, dict], key: str = "dialect",
              reps: int = 2000, seed: int = 7) -> dict[str, dict]:
    means = task_means(ev)
    groups: dict[str, list[float]] = {}
    for t, m in means.items():
        tag = str(((tasks.get(t) or {}).get("notes") or {}).get(key) or "untagged")
        groups.setdefault(tag, []).append(m)
    rng = random.Random(seed)
    out = {}
    for tag, xs in sorted(groups.items()):
        n = len(xs)
        boots = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
        out[tag] = {"n": n, "mean": round(st.fmean(xs), 4),
                    "range95": [round(boots[int(0.025 * reps)], 4),
                                round(boots[int(0.975 * reps) - 1], 4)]}
    return out


# -------------------------------------------------------------------- cli ---
def cli(sub) -> None:
    p = sub.add_parser("dialects", help="sub-scores of a measured job per task tag "
                       "(notes.<key>: dialect, industry, ...) with 95%% ranges")
    p.add_argument("--job", required=True, help="a job under runs/<d>/jobs with eval.json")
    p.add_argument("--key", default="dialect")
    p.add_argument("--sign", action="store_true", help="issue a dialect_scores statement")
    p.add_argument("--out", default=None)
    p.set_defaults(product=_cmd)


def _cmd(args, ctx) -> int:
    path = ctx.run_dir / "jobs" / args.job / "eval.json"
    if not path.exists():
        print(f"no evaluation at {path}")
        return 1
    ev = EvalResult.load(path)
    body = {"domain": args.domain, "job": args.job, "key": args.key, "S": round(ev.S, 4),
            "missing": ev.missing, "groups": breakdown(ev, ctx.domain.tasks, args.key)}
    if not args.sign:
        text = json.dumps(body, ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    out = Path(args.out or ctx.run_dir / f"dialect_scores_{args.job}_{args.key}.json")
    ST.save(ST.issue("dialect_scores", body, ctx.run_dir), out)
    for tag, g in body["groups"].items():
        print(f"{tag:>16}  n={g['n']:<3} {g['mean']:.1%}  [{g['range95'][0]:.1%}, "
              f"{g['range95'][1]:.1%}]")
    print(f"statement {out}")
    return 0
