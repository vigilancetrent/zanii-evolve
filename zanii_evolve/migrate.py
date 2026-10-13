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
"""Model migration with a certificate: move an agent from model A to model B.

    migrate --from A --to B [--ref REF] [--sets heldout,redteam,incident]

The same harness commit runs with policy model A and then B on every split
that has tasks, graded by the same judge. Each split is compared pair by pair
(compare.paired); the move is safe only when B is non-inferior on every split,
within `--margin` (default: the run's noise band). The verdict is a signed
statement of kind "migration"; the exit code is 0 when safe, 1 when not.

    migrate --plan --blackbox-url URL --label L

lists the models Blackbox flagged MODEL_DEPRECATED in that agent's recent runs:
the ones to move off first.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import compare as CMP
from . import gitops as G
from . import statement as ST

SETS = {"heldout": "heldout_ids", "redteam": "redteam_ids", "incident": "incident_ids",
        "evolve": "evolve_ids"}


def judge_of(domain) -> str:
    return os.environ.get("ZANII_EVOLVE_JUDGE_MODEL",
                          (getattr(domain, "cfg", None) or {}).get("judge_model",
                                                                   "claude-sonnet-5-5"))


def compare_models(domain, root: Path, runs_dir: Path, a: str, b: str, sets: list[str],
                   k: int, margin: float) -> dict:
    """Split -> paired result of model B against model A on one harness root."""
    judge = judge_of(domain)          # one judge for both arms, or the comparison is unfair
    # trials are resumed by job name: key it by commit, judge and model, so a run on
    # another commit or judge never reuses these trials
    tag = f"{G.rev(root, 'HEAD')[:12]}_{CMP.slug(judge)}"
    out = {}
    for s in sets:
        ids = getattr(domain, SETS[s], list)()
        if not ids:
            continue
        ea = CMP.measure(domain, root, runs_dir, f"migrate_{tag}_{CMP.slug(a)}_{s}", ids, k,
                         a, judge)
        eb = CMP.measure(domain, root, runs_dir, f"migrate_{tag}_{CMP.slug(b)}_{s}", ids, k,
                         b, judge)
        out[s] = CMP.paired(ea, eb, margin)
    if not out:
        raise ValueError(f"none of {sets} has tasks in domain {domain.name}")
    return out


def body(domain: str, commit: str, a: str, b: str, judge: str, splits: dict) -> dict:
    return {"domain": domain, "commit": commit, "from": a, "to": b, "judge": judge,
            "splits": splits,
            "tokens_per_trial": {s: {"from": r["C_a"], "to": r["C_b"]} for s, r in splits.items()},
            "safe": all(r["non_inferior"] for r in splits.values())}


def _plan(args) -> int:
    from . import blackbox as BB
    key = os.environ.get("BLACKBOX_API_KEY")
    if not (args.blackbox_url and args.label and key):
        print("--plan needs --blackbox-url, --label and BLACKBOX_API_KEY")
        return 2
    found = BB.deprecated_models(BB.http_get(args.blackbox_url, key), args.label)
    if not found:
        print(f"no deprecated models in the recent runs of {args.label!r}")
        return 0
    for m, n in found.items():
        print(f"{m}: deprecated, called in {n} run(s); next: zanii_evolve.py --domain "
              f"{args.domain} migrate --from {m} --to <successor>")
    return 0


def handler(args, ctx) -> int:
    if args.plan:
        return _plan(args)
    if not (args.from_model and args.to_model):
        print("migrate needs --from and --to (or --plan)")
        return 2
    sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    bad = [s for s in sets if s not in SETS]
    if bad:
        print(f"--sets takes {', '.join(SETS)}, not {bad}")
        return 2
    root = ctx.checkout("migrate", args.ref)
    margin = args.margin if args.margin is not None else CMP.margin_from(ctx.run_dir)
    splits = compare_models(ctx.domain, root, ctx.run_dir, args.from_model, args.to_model,
                            sets, args.trials or ctx.cfg.k, margin)
    b = body(ctx.domain.name, G.rev(root, "HEAD"), args.from_model, args.to_model,
             judge_of(ctx.domain), splits)
    stmt = ST.issue("migration", b, ctx.run_dir)
    out = ST.save(stmt, Path(args.out) if args.out else ctx.run_dir / "migration.json")
    for s, r in splits.items():
        print(f"{s}: {r['S_a']:.3f} -> {r['S_b']:.3f} (diff {r['diff']:+.3f}, 95% "
              f"{r['range95'][0]:+.3f}..{r['range95'][1]:+.3f}, margin {r['margin']:.3f}) "
              f"{'non-inferior' if r['non_inferior'] else 'WORSE'}")
    print(("SAFE to migrate " if b["safe"] else "NOT safe to migrate ")
          + f"{args.from_model} -> {args.to_model}; statement {out}")
    return 0 if b["safe"] else 1


def cli(sub) -> None:
    p = sub.add_parser("migrate", help="certify a move from one model to another: the same "
                       "harness on every split with both models, non-inferior or not")
    p.add_argument("--from", dest="from_model")
    p.add_argument("--to", dest="to_model")
    p.add_argument("--ref", help="harness commit (default: the evolve branch tip)")
    p.add_argument("--sets", default="heldout,redteam,incident")
    p.add_argument("--trials", type=int, help="trials per task (default: the domain's k)")
    p.add_argument("--margin", type=float, help="accepted loss (default: the noise band)")
    p.add_argument("--out", help="statement path (default runs/<d>/migration.json)")
    p.add_argument("--plan", action="store_true",
                   help="list models Blackbox flagged deprecated for --label")
    p.add_argument("--blackbox-url")
    p.add_argument("--label")
    p.set_defaults(product=handler)

