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
"""Expert judge packs: a rubric judge, certified against named experts.

    judge-pack export --labels expert.jsonl [--raters raters.json] [--out pack.json]
    judge-pack verify pack.json [--labels expert.jsonl] [--owner did:key:...]

`export` runs the judge on every expert-graded deliverable (judgecal.agreement),
records the result for the run's judge gate, and signs a `judge_pack`
statement: the judge model, the rubric criteria it was checked on, kappa with
its 95% range, agreement per criterion, who graded (only ids that appear in a
label's `rater` field; credentials from raters.json [{"id", "credential"}] are
marked self-declared: the pack does not verify them) and the sha256 of the
labels file. Anyone holding the labels can `verify` the pack was computed on
exactly them; anyone without can still check who signed it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import judgecal as JC
from . import statement as ST


def export(domain, run_dir: Path, labels_path: Path, raters_path: Path | None = None,
           judge=None, identity: dict | None = None) -> dict:
    labels = [json.loads(x) for x in Path(labels_path).read_text(encoding="utf-8").splitlines()
              if x.strip()]
    model = os.environ.get("ZANII_EVOLVE_JUDGE_MODEL",
                           domain.cfg.get("judge_model", "claude-sonnet-5-5"))
    result = JC.agreement(domain.tasks, labels, model, judge=judge)
    kept = JC.record(run_dir, result, labels_path, raters_path)
    graded = sorted({lab["task_id"] for lab in labels})
    body = {"domain": domain.name, "judge_model": model,
            "criteria": [{"task_id": t, "criterion": c["criterion"]}
                         for t in graded for c in domain.tasks[t].get("rubric") or []],
            "kappa": kept["kappa"], "kappa_range95": kept.get("kappa_range95"),
            "agreement": kept["agreement"], "n": kept["n"],
            "per_criterion": [{k: c[k] for k in ("task_id", "criterion", "n", "agreement")}
                              for c in result["per_criterion"]],
            "labels_sha256": kept.get("labels_sha256"), "raters": kept.get("raters") or []}
    return ST.issue("judge_pack", body, run_dir, identity)


def verify(pack: dict, labels_path: Path | None = None,
           owner: str | None = None) -> tuple[bool, str]:
    ok, why = ST.verify(pack, owner=owner, kind="judge_pack")
    if not ok or not labels_path:
        return ok, why
    import hashlib
    if hashlib.sha256(Path(labels_path).read_bytes()).hexdigest() != pack["body"].get(
            "labels_sha256"):
        return False, "these labels are not the ones the pack was certified on"
    return True, why + "; labels match"


# --------------------------------------------------------------------- cli ---
def cli(sub) -> None:
    p = sub.add_parser("judge-pack", help="expert judge pack: sign the judge's agreement with "
                                          "named expert graders, or verify a pack")
    p.add_argument("action", choices=["export", "verify"])
    p.add_argument("pack", nargs="?", help="verify: the pack file")
    p.add_argument("--labels", help="export (required) / verify: the expert labels.jsonl")
    p.add_argument("--raters", help="export: raters.json [{id, credential}]")
    p.add_argument("--out", help="export: default runs/<d>/statements/judge_pack.json")
    p.add_argument("--owner", help="verify: pin the issuer's owner DID")
    p.set_defaults(product=handle)


def handle(args, ctx) -> int:
    if args.action == "verify":
        if not args.pack:
            print("judge-pack verify needs the pack file")
            return 2
        ok, why = verify(ST.load(Path(args.pack)), Path(args.labels) if args.labels else None,
                         args.owner)
        print(("OK " if ok else "FAIL ") + why)
        return 0 if ok else 1
    if not args.labels:
        print("judge-pack export needs --labels")
        return 2
    try:
        pack = export(ctx.domain, ctx.run_dir, Path(args.labels),
                      Path(args.raters) if args.raters else None)
    except (ValueError, KeyError, OSError) as e:
        print(f"judge pack not issued: {e}")
        return 1
    out = Path(args.out) if args.out else ctx.run_dir / "statements" / "judge_pack.json"
    ST.save(pack, out)
    b = pack["body"]
    print(f"judge pack: {b['judge_model']} kappa {b['kappa']} (95% {b['kappa_range95']}) over "
          f"{b['n']} judgements by {len(b['raters'])} rater(s) -> {out}")
    return 0
