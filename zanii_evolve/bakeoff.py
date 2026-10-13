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
"""Procurement bake-off: every vendor's agent on the same hidden tasks.

    python zanii_evolve.py --domain tender_42 bakeoff --vendors vendors.json

vendors.json lists the candidates:

    [{"name": "Vendor A", "harness": "path/to/their/harness"},      # runs here
     {"name": "Vendor B", "url": "https://b.example/agent",         # their endpoint
      "headers_env": "VENDOR_B_TOKEN", "timeout_s": 120}]

The buyer writes the tasks; vendors only ever receive a task's public view
(brief.public_view), never its checks, rubric or split, and the scorecard
names the task set by the sha256 of its sorted ids, not by the tasks. Each
vendor is measured with the same judge on the same ids and k, ranked by S, and
neighbours whose difference is `unclear` (compare.paired) share a rank group:
a lucky vendor cannot win on noise. A harness vendor's code runs on this
machine with this process's environment, so give untrusted vendors a url.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from . import compare as CMP
from . import statement as ST

ROOT = Path(__file__).resolve().parent.parent
HTTP_TEMPLATE = Path(__file__).resolve().parent / "templates" / "vendor_http"


def load_vendors(path: Path) -> list[dict]:
    vendors = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(vendors, list) or len(vendors) < 2:
        raise ValueError(f"{path}: a bake-off needs a list of at least two vendors")
    slugs = set()
    for v in vendors:
        name = str(v.get("name") or "").strip()
        # the slug names the vendor's root and job: two names, one slug would share them
        if not name or _slug(name) in slugs:
            raise ValueError(f"{path}: every vendor needs a unique name ({name!r})")
        slugs.add(_slug(name))
        if ("harness" in v) == ("url" in v):
            raise ValueError(f"{path}: vendor {name!r} needs either harness or url")
        if "url" in v and not str(v["url"]).startswith(("https://", "http://")):
            raise ValueError(f"{path}: vendor {name!r} url must be http(s)")
        if "harness" in v and not (Path(v["harness"]) / "run.py").is_file():
            raise ValueError(f"{path}: vendor {name!r}: no run.py in {v['harness']}")
    return vendors


def _slug(name: str) -> str:
    return CMP.slug(name.strip().lower())


def vendor_root(vendor: dict, domain, base: Path) -> Path:
    """A root holding only domains/<d>/<harness_path>/: the vendor's harness, or
    the http adapter pointed at the vendor's url."""
    root = base / _slug(vendor["name"])
    dest = root / "domains" / domain.name / domain.harness_path
    shutil.rmtree(root, ignore_errors=True)
    src = Path(vendor["harness"]) if "harness" in vendor else HTTP_TEMPLATE
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if "url" in vendor:
        cfg = {k: vendor[k] for k in ("url", "headers_env", "timeout_s") if k in vendor}
        (dest / "vendor.json").write_text(json.dumps(cfg), encoding="utf-8")
    return root


@contextmanager
def _importable():
    """The vendor root has no zanii_evolve package: put this repo on PYTHONPATH."""
    old = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT)] + ([old] if old else []))
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old


def rank(evals: dict) -> list[dict]:
    """Vendors by S, best first; a neighbour not clearly worse shares the group."""
    # ponytail: ties chain (A~B, B~C puts A and C together even if A beats C); a full
    # pairwise tie matrix if a tender ever needs strict separation
    order = sorted(evals, key=lambda n: evals[n].S, reverse=True)
    leader, group, rows = order[0], 1, []
    for i, name in enumerate(order):
        if i and CMP.paired(evals[order[i - 1]], evals[name])["verdict"] == "worse":
            group += 1
        vs = CMP.paired(evals[leader], evals[name]) if name != leader else None
        rows.append({"name": name, "S": round(evals[name].S, 6), "rank_group": group,
                     "vs_leader": None if vs is None else {"diff": vs["diff"],
                                                           "range95": vs["range95"]},
                     "C": evals[name].C, "missing": evals[name].missing,
                     "n_expected": evals[name].n_expected})
    return rows


def run_bakeoff(domain, run_dir: Path, vendors: list[dict], ids: list[str], k: int,
                split: str) -> dict:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = Path(run_dir) / "bakeoff" / stamp
    evals = {}
    with _importable():
        for v in vendors:
            root = vendor_root(v, domain, base / "roots")
            evals[v["name"]] = CMP.measure(domain, root, run_dir,
                                           f"bakeoff_{stamp}_{_slug(v['name'])}", ids, k)
    judge = os.environ.get("ZANII_EVOLVE_JUDGE_MODEL",
                           (getattr(domain, "cfg", None) or {}).get("judge_model"))
    return {"domain": domain.name, "set": split, "n_tasks": len(ids), "k": k,
            "judge_model": judge, "run": stamp,
            "tasks_manifest": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
            "vendors": rank(evals)}


def _cmd(args, ctx) -> int:
    vendors = load_vendors(Path(args.vendors))
    getter = {"heldout": "heldout_ids", "evolve": "evolve_ids", "incident": "incident_ids",
              "redteam": "redteam_ids"}[args.set]
    ids = getattr(ctx.domain, getter, list)()
    if not ids:
        raise SystemExit(f"domain {args.domain} has no {args.set} tasks")
    body = run_bakeoff(ctx.domain, ctx.run_dir, vendors, ids, args.trials or ctx.cfg.k, args.set)
    stmt = ST.issue("scorecard", body, ctx.run_dir)
    out = Path(args.out) if args.out else ctx.run_dir / "bakeoff" / body["run"] / "scorecard.json"
    ST.save(stmt, out)
    print(f"bake-off {body['domain']} ({body['n_tasks']} {args.set} tasks x k={body['k']}):")
    for r in body["vendors"]:
        vs = r["vs_leader"]
        print(f"  #{r['rank_group']} {r['name']}: S={r['S']:.3f}" + (
            f" ({vs['diff']:+.3f} vs leader, range {vs['range95'][0]:+.3f} … "
            f"{vs['range95'][1]:+.3f})" if vs else " (leader)"))
    print(f"scorecard: {out}")
    return 0


def cli(sub) -> None:
    p = sub.add_parser("bakeoff", help="procurement bake-off: every vendor's agent on the same "
                       "hidden tasks, ranked with noise-tied groups, signed scorecard")
    p.add_argument("--vendors", required=True, help="vendors.json (name + harness or url)")
    p.add_argument("--set", default="heldout",
                   choices=["heldout", "evolve", "incident", "redteam"])
    p.add_argument("--trials", type=int, default=None, help="trials per task (default: config k)")
    p.add_argument("--out", default=None, help="signed scorecard path")
    p.set_defaults(product=_cmd)
