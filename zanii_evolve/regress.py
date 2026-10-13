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
"""The no-regression gate: a harness change merges only if it holds up.

    python zanii_evolve.py --domain acme regress --base origin/main --head HEAD

Both refs are measured on every non-empty split named in --sets (heldout,
incident, redteam by default; none of them is ever selected on), and each
split is compared head against base (compare.paired). The gate fails, exit 1,
when any split is not non-inferior: the low end of the 95% range of
head - base is below -margin. The margin is the run's calibrated noise band,
or 0.05 before calibration, or --margin.

It prints a Markdown summary (for a PR comment or $GITHUB_STEP_SUMMARY) and
writes a signed `regression` statement. Jobs are named by commit, so a base
commit measured once is reused by every pull request on top of it.
"""

from __future__ import annotations

from pathlib import Path

from . import compare as CMP
from . import gitops as G
from . import statement as ST

SETS = {"heldout": "heldout_ids", "incident": "incident_ids", "redteam": "redteam_ids"}
DEFAULT_MARGIN = 0.05


def _rev(ctx, ref: str) -> str:
    return G.git(ctx.run.repo, "rev-parse", "--verify", ref, check=True).stdout.strip()


def compare_refs(ctx, base: str, head: str, sets: list[str], k: int,
                 margin: float) -> dict:
    """{split: compare.paired(base, head)} for every requested split; a requested
    split with no tasks is listed in "empty" (and fails the gate)."""
    commits = {"base": _rev(ctx, base), "head": _rev(ctx, head)}
    # ponytail: jobs keyed by commit only; a model change between runs reuses old trials,
    # so pass a fresh --runs (or delete jobs/regress_*) after changing models
    roots = {side: ctx.checkout(f"regress_{c[:12]}", c) for side, c in commits.items()}
    out, empty = {}, []
    for s in sets:
        ids = getattr(ctx.domain, SETS[s], list)()
        if not ids:
            empty.append(s)
            continue
        ev = {side: CMP.measure(ctx.domain, roots[side], ctx.run_dir,
                                f"regress_{commits[side][:12]}_{s}", ids, k)
              for side in ("base", "head")}
        out[s] = CMP.paired(ev["base"], ev["head"], margin)
    return {"commits": commits, "sets": out, "empty": empty}


def markdown(body: dict) -> str:
    head = "PASS" if body["passed"] else "FAIL"
    lines = [f"### Zanii Evolve regression gate: {head}",
             (f"`{body['domain']}` {body['base']['commit'][:8]} → "
              f"{body['head']['commit'][:8]}, k={body['k']}, margin {body['margin']:.3f}"), "",
             "| split | tasks | base | head | head − base | 95% range | verdict | gate |",
             "|---|---|---|---|---|---|---|---|"]
    for s, p in body["sets"].items():
        lo, hi = p["range95"]
        lines.append(f"| {s} | {p['n_tasks']} | {p['S_a']:.3f} | {p['S_b']:.3f} | "
                     f"{p['diff']:+.3f} | {lo:+.3f} … {hi:+.3f} | {p['verdict']} | "
                     f"{'ok' if p['non_inferior'] else '**regressed**'} |")
        if not p["non_inferior"] and p["worst_tasks"]:
            lines.append(f"| | worst: {', '.join(p['worst_tasks'])} | | | | | | |")
    for s in body.get("empty") or []:
        lines.append(f"| {s} | 0 | | | | | no tasks | **fail** |")
    if not body["sets"]:
        lines.append("| (nothing measured: the gate fails) | | | | | | | |")
    return "\n".join(lines) + "\n"


def _cmd(args, ctx) -> int:
    # named splits must all have tasks; by default, every split the domain has
    explicit = args.sets is not None
    sets = [s.strip() for s in (args.sets if explicit else ",".join(SETS)).split(",") if s.strip()]
    bad = [s for s in sets if s not in SETS]
    if bad or not sets:
        raise SystemExit(f"--sets takes one or more of {', '.join(SETS)}, not {bad or sets}")
    margin = args.margin if args.margin is not None else CMP.margin_from(ctx.run_dir,
                                                                         DEFAULT_MARGIN)
    k = args.trials or ctx.cfg.k
    r = compare_refs(ctx, args.base, args.head, sets, k, margin)
    # nothing measured is no evidence: an empty or partly empty gate fails
    if not explicit:
        r["empty"] = []
    passed = (bool(r["sets"]) and not r["empty"]
              and all(p["non_inferior"] for p in r["sets"].values()))
    body = {"domain": args.domain, "base": {"ref": args.base, "commit": r["commits"]["base"]},
            "head": {"ref": args.head, "commit": r["commits"]["head"]}, "k": k,
            "margin": margin, "sets": r["sets"], "empty": r["empty"], "passed": passed}
    stmt = ST.issue("regression", body, ctx.run_dir)
    out = Path(args.out) if args.out else (ctx.run_dir / "regress"
                                           / f"{r['commits']['head'][:12]}.json")
    ST.save(stmt, out)
    md = markdown(body)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    print(md + f"\nstatement: {out}")
    return 0 if passed else 1


def cli(sub) -> None:
    p = sub.add_parser("regress", help="no-regression gate: measure --base and --head on the "
                       "heldout, incident and redteam splits; exit 1 if head regressed")
    p.add_argument("--base", required=True, help="git ref to compare against (e.g. origin/main)")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--sets", default=None,
                   help=f"comma list of {', '.join(SETS)}; each named split must have tasks "
                        "(default: every one the domain has)")
    p.add_argument("--trials", type=int, default=None, help="trials per task (default: config k)")
    p.add_argument("--margin", type=float, default=None,
                   help="loss accepted (default: the noise band, or 0.05 before calibration)")
    p.add_argument("--out", default=None, help="signed statement path")
    p.add_argument("--markdown", default=None, help="also write the summary here")
    p.set_defaults(product=_cmd)

