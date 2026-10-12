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

#!/usr/bin/env python3
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
"""Zanii Evolve command line.

  python3 zanii_evolve.py --domain eng baseline                # evaluate H_0, seed the frontier
  python3 zanii_evolve.py --domain eng calibrate [--jobs base,base2]   # noise band delta
  python3 zanii_evolve.py --domain eng round --t 3 [--dry-run] # one round (Alg. 1 + Alg. 2)
  python3 zanii_evolve.py --domain eng run [--start 0]         # driver: rounds until T
  python3 zanii_evolve.py --domain eng readjudicate --t 3      # re-apply Alg. 2 to round 3's stored measurements
  python3 zanii_evolve.py --domain eng reevaluate --t 3        # re-measure round 3's candidates (infra failure), then re-adjudicate
  python3 zanii_evolve.py --domain workspace heldout --label champ [--ref <c>] [--set heldout|redteam]
  python3 zanii_evolve.py --domain eng smoke                   # liveness check of the incumbent
  python3 zanii_evolve.py --domain eng status                  # frontier + history summary
  python3 zanii_evolve.py --domain eng pareto                  # score/cost trade-off of measured harnesses
  python3 zanii_evolve.py --domain eng report [--out cert]     # generalization certificate
  python3 zanii_evolve.py --domain clients lessons export --out pack.jsonl
  python3 zanii_evolve.py --domain clients lessons import pack.jsonl other.jsonl
  python3 zanii_evolve.py --domain clients judge-agreement --labels human.jsonl
  python3 zanii_evolve.py verify-report cert.json [--owner <did>]   # digest / HMAC / receipt check
  python3 zanii_evolve.py identity --out keys/                 # Zanii signing identity for receipts
  python3 zanii_evolve.py --domain eng verify-receipts [--owner <did>]  # issued-receipt chain intact
  python3 zanii_evolve.py --domain eng publish                 # incumbent harness -> registry/
  python3 zanii_evolve.py registry                             # list published harnesses
  python3 zanii_evolve.py --domain clients init-domain          # scaffold a new brief domain
  python3 zanii_evolve.py --domain clients import-tasks briefs.csv [--replace] [--heldout 0.2]
  python3 zanii_evolve.py --domain clients redteam [--n 10]     # prompt-injection split
  python3 zanii_evolve.py --domain clients ingest-traces prod.jsonl --rubric "Answers|No made-up facts"
  BLACKBOX_API_KEY=... python3 zanii_evolve.py --domain clients ingest-blackbox --url https://blackbox.example.com --label refunds
  python3 zanii_evolve.py --domain clients judge-agreement --labels human.jsonl --record   # for --min-judge-kappa
  python3 zanii_evolve.py serve [--host 127.0.0.1 --port 8765]  # service API + dashboard

Hyperparameters come from domains/<domain>/zanii_evolve.json; any of them can be
overridden on the command line (--T, --k, --m, --b-min, --b-max, --w,
--m-draft, --delta, --beta0, --beta1, --w-s, --w-c, --w-n, --n-prune, --objective score|cost,
--confirm-k (0 = no confirmation run), --min-judge-kappa).
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from zanii_evolve import gitops as G                 # noqa: E402
from zanii_evolve.config import ZaniiEvolveConfig          # noqa: E402
from zanii_evolve.domain import load_domain          # noqa: E402
from zanii_evolve.driver import drive                # noqa: E402
from zanii_evolve.loop import Run                    # noqa: E402
from zanii_evolve.schedule import budget_table       # noqa: E402

OVERRIDES = ["T", "k", "m", "b_min", "b_max", "w", "m_draft", "delta", "delta_z",
             "beta0", "beta1", "w_s", "w_c", "w_n", "n_prune", "eval_parallel", "objective",
             "confirm_k", "min_judge_kappa"]


def judge_model(domain) -> str:
    """The rubric judge a brief domain grades with."""
    return os.environ.get("ZANII_EVOLVE_JUDGE_MODEL",
                          domain.cfg.get("judge_model", "claude-sonnet-5-5"))


def init_domain(name: str) -> None:
    """Copy the brief-domain template to domains/<name>/."""
    from zanii_evolve.tasksets import create_domain
    try:
        dest = create_domain(name)
    except (ValueError, FileExistsError) as e:
        sys.exit(str(e))
    print(f"created {dest}\n  next: write tasks.jsonl (or `import-tasks file.csv --replace`), "
          f"edit briefs.py / SKILL.md / harness/prompts.py,\n"
          f"  then: python3 zanii_evolve.py --domain {name} smoke")


def import_tasks(name: str, path: str, heldout: float, replace: bool) -> None:
    from zanii_evolve import tasksets as TS
    d = ROOT / "domains" / name
    if not (d / "tasks.jsonl").is_file():
        sys.exit(f"{name} is not a brief domain (no {d / 'tasks.jsonl'})")
    try:
        rows = TS.assign_splits(TS.read_file(Path(path), prefix=name[:8]), heldout)
        n = TS.write_tasks(d, rows, replace=replace)
    except (ValueError, OSError) as e:
        sys.exit(f"import failed, tasks.jsonl unchanged: {e}")
    print(f"{name}: imported {len(rows)} tasks, {n} in tasks.jsonl")


def redteam(name: str, n: int, seed: int) -> None:
    from zanii_evolve import tasksets as TS
    d = ROOT / "domains" / name
    if not (d / "tasks.jsonl").is_file():
        sys.exit(f"{name} is not a brief domain (no {d / 'tasks.jsonl'})")
    tasks = TS.load_tasks(d / "tasks.jsonl")
    known = {t["id"] for t in tasks}
    rows = [r for r in TS.redteam(tasks, n, seed) if r["id"] not in known]
    if rows:
        TS.write_tasks(d, rows)
    print(f"{name}: {len(rows)} red-team tasks added; evaluate with "
          f"`--domain {name} heldout --label rt --set redteam`")


def make_identity(out: Path, exp: str) -> None:
    from zanii_evolve.report import new_identity
    owner_p, ident_p = out / "owner.json", out / "identity.json"
    if owner_p.exists() or ident_p.exists():
        sys.exit(f"{out} already holds an identity; refusing to overwrite keys")
    try:
        owner, ident = new_identity(exp)
    except RuntimeError as e:
        sys.exit(str(e))
    out.mkdir(parents=True, exist_ok=True)
    for p, obj in ((owner_p, owner), (ident_p, ident)):
        p.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        os.chmod(p, 0o600)                 # owner-only on POSIX; on Windows rely on the folder ACL
    print(f"owner DID (publish this; verifiers pin it): {owner['did']}\n"
          f"  {owner_p}: owner key, move it OFFLINE (only needed to re-delegate or revoke)\n"
          f"  {ident_p}: signing identity, scope evolve.* until {exp}\n"
          f"  next: export ZANII_EVOLVE_IDENTITY={ident_p}")


def lessons_cmd(name: str, action: str, files: list[str], out: str | None,
                runs: Path) -> None:
    from zanii_evolve import lessons as L
    from zanii_evolve.brief import load_tasks
    from zanii_evolve.server import State
    d = ROOT / "domains" / name
    ids = {t["id"] for t in load_tasks(d / "tasks.jsonl")} if (d / "tasks.jsonl").is_file() else set()
    if action == "export":
        if not out:
            sys.exit("lessons export needs --out pack.jsonl")
        rows = L.export(State(runs, None).memory(name, limit=L.MAX_PACK * 5), ids)
        Path(out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                             encoding="utf-8")
        print(f"{name}: exported {len(rows)} lessons to {out}")
        return
    if not files:
        sys.exit("lessons import needs one or more pack files")
    if not d.is_dir():
        sys.exit(f"unknown domain {name!r}")
    try:
        n = L.import_packs(d / "shared_lessons.jsonl", [L.read_pack(Path(f)) for f in files], ids)
    except (ValueError, OSError) as e:
        sys.exit(f"import failed, shared_lessons.jsonl unchanged: {e}")
    print(f"{name}: shared_lessons.jsonl now holds {n} lessons; every trial seeds from it "
          f"(delete the file to opt out). Re-run `baseline` so H_0 is measured with it.")


def ingest_traces(name: str, path: str, rubric: str, limit: int, heldout: float) -> None:
    from zanii_evolve import tasksets as TS
    d = ROOT / "domains" / name
    if not (d / "tasks.jsonl").is_file():
        sys.exit(f"{name} is not a brief domain (no {d / 'tasks.jsonl'})")
    try:
        recs = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()
                if x.strip()]
        known = {t["id"] for t in TS.load_tasks(d / "tasks.jsonl")}
        rows = [r for r in TS.from_traces(recs, [c.strip() for c in rubric.split("|")
                                                 if c.strip()], limit)
                if r["id"] not in known]          # re-ingesting a file is a no-op
        n = TS.write_tasks(d, TS.assign_splits(rows, heldout)) if rows else len(known)
    except (ValueError, OSError) as e:
        sys.exit(f"ingest failed, tasks.jsonl unchanged: {e}")
    print(f"{name}: minted {len(rows)} tasks from {len(recs)} traces "
          f"({sum(1 for r in rows if TS._failed(r['notes']['feedback']))} with negative "
          f"feedback), {n} in tasks.jsonl")


def ingest_blackbox(name: str, url: str, label: str, rubric: str, limit: int,
                    heldout: float) -> None:
    """Brief tasks from a Zanii Blackbox gateway's record of one agent's runs."""
    from zanii_evolve import blackbox as BB
    from zanii_evolve import tasksets as TS
    d = ROOT / "domains" / name
    if not (d / "tasks.jsonl").is_file():
        sys.exit(f"{name} is not a brief domain (no {d / 'tasks.jsonl'})")
    key = os.environ.get("BLACKBOX_API_KEY")
    if not key:
        sys.exit("set BLACKBOX_API_KEY to the gateway's admin key (it reads request bodies)")
    if not 1 <= limit <= 1000:
        sys.exit("--limit must be 1-1000")
    get = BB.http_get(url, key)
    try:
        runs, unreadable = BB.fetch_runs(get, label, limit)
        known = {t["id"] for t in TS.load_tasks(d / "tasks.jsonl")}
        rows = [r for r in BB.to_tasks(runs, BB.catalogue(get),
                                       [c.strip() for c in rubric.split("|") if c.strip()],
                                       limit)
                if r["id"] not in known]          # ingesting the same runs again adds nothing
        n = TS.write_tasks(d, TS.assign_splits(rows, heldout)) if rows else len(known)
    except (ValueError, OSError, RuntimeError) as e:
        sys.exit(f"ingest failed, tasks.jsonl unchanged: {e}")
    print(f"{name}: minted {len(rows)} tasks from {len(runs)} runs of {label!r} "
          f"({sum(1 for r in rows if r['notes']['feedback'] == 'bad')} that went wrong), "
          f"{n} in tasks.jsonl"
          + (f"; {unreadable} runs could not be read and were skipped" if unreadable else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain")
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    for name in OVERRIDES:
        typ = (int if name in ("T", "k", "m", "b_min", "b_max", "w", "m_draft",
                               "n_prune", "eval_parallel", "confirm_k") else
               str if name == "objective" else float)
        ap.add_argument("--" + name.replace("_", "-"), dest=name, type=typ, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("baseline").add_argument("--job", default="base")
    sub.add_parser("calibrate").add_argument("--jobs", default="base")
    p = sub.add_parser("round")
    p.add_argument("--t", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("run").add_argument("--start", type=int, default=0)
    sub.add_parser("readjudicate", help="re-run Algorithm 2 on a stored round after a "
                   "delta / weight change (no new evaluation)").add_argument("--t", type=int, required=True)
    p = sub.add_parser("reevaluate", help="re-measure a round's committed candidates after an "
                       "infrastructure failure, then re-adjudicate")
    p.add_argument("--t", type=int, required=True)
    p.add_argument("--variants", default="", help="e.g. A,B (default: all)")
    p = sub.add_parser("heldout")
    p.add_argument("--label", required=True)
    p.add_argument("--ref", default=None, help="commit/branch to evaluate (default incumbent)")
    p.add_argument("--set", default="heldout", choices=["heldout", "evolve", "redteam"])
    sub.add_parser("smoke")
    sub.add_parser("status")
    sub.add_parser("pareto", help="score/cost Pareto frontier of every measured harness")
    sub.add_parser("report", help="generalization certificate (JSON + Markdown)").add_argument(
        "--out", default=None, help="path stem: writes <out>.json and <out>.md (default: print)")
    sub.add_parser("publish", help="publish the incumbent harness + certificate to the registry")
    sub.add_parser("registry", help="list published harnesses")
    p = sub.add_parser("judge-agreement", help="rubric judge vs human labels (agreement, "
                       "Cohen's kappa)")
    p.add_argument("--labels", required=True)
    p.add_argument("--record", action="store_true",
                   help="keep the result for the min_judge_kappa gate and the certificate")
    p = sub.add_parser("lessons", help="opt-in lesson packs: export a run's lessons / "
                       "import packs into domains/<d>/shared_lessons.jsonl")
    p.add_argument("action", choices=["export", "import"])
    p.add_argument("files", nargs="*", help="import: pack files")
    p.add_argument("--out", help="export: pack file to write")
    p = sub.add_parser("verify-report", help="check a certificate's digest / HMAC / receipt")
    p.add_argument("file")
    p.add_argument("--owner", default=None, help="pin the expected owner did:key")
    sub.add_parser("verify-receipts", help="check the run's chain of issued certificate receipts"
                   ).add_argument("--owner", default=None)
    p = sub.add_parser("identity", help="new owner key + delegated signing identity (zanii)")
    p.add_argument("--out", required=True, help="directory for owner.json and identity.json")
    p.add_argument("--exp", default="2031-01-01T00:00:00Z", help="delegation expiry (UTC)")
    sub.add_parser("init-domain", help="scaffold domains/<--domain> from the brief template")
    p = sub.add_parser("import-tasks", help="add tasks to a brief domain from .csv or .jsonl")
    p.add_argument("file")
    p.add_argument("--heldout", type=float, default=0.2, help="fraction of unsplit rows held out")
    p.add_argument("--replace", action="store_true", help="replace tasks.jsonl instead of appending")
    p = sub.add_parser("redteam", help="add prompt-injection variants of evolve tasks "
                       "(split redteam; run with heldout --set redteam)")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("ingest-traces", help="mint brief tasks from production traces (JSONL)")
    p.add_argument("file")
    p.add_argument("--rubric", required=True, help='"|"-separated pass/fail criteria for every task')
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--heldout", type=float, default=0.2)
    p = sub.add_parser("ingest-blackbox", help="mint brief tasks from a Zanii Blackbox "
                       "gateway's record of one agent's runs (BLACKBOX_API_KEY)")
    p.add_argument("--url", required=True, help="the gateway, e.g. https://blackbox.example.com")
    p.add_argument("--label", required=True, help="the agent's Blackbox label")
    p.add_argument("--rubric", default="Does what the request asks, completely and correctly",
                   help='"|"-separated pass/fail criteria for every task')
    p.add_argument("--limit", type=int, default=200, help="newest closed runs to read (1-1000)")
    p.add_argument("--heldout", type=float, default=0.2)
    p = sub.add_parser("serve", help="service API + dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    if args.cmd == "serve":
        from zanii_evolve.server import serve
        serve(args.host, args.port, Path(args.runs))
        return
    if args.cmd == "verify-report":
        from zanii_evolve.report import verify
        try:
            ok, why = verify(json.loads(Path(args.file).read_text(encoding="utf-8")),
                             owner=args.owner)
        except RuntimeError as e:          # receipt present, zanii not installed
            sys.exit(str(e))
        print(why)
        sys.exit(0 if ok else 1)
    if args.cmd == "identity":
        make_identity(Path(args.out), args.exp)
        return
    if args.cmd == "registry":
        from zanii_evolve.registry import default_root, entries
        print(json.dumps(entries(default_root(ROOT)), indent=1))
        return
    if not args.domain:
        ap.error("--domain is required for this command")

    if args.cmd == "init-domain":
        init_domain(args.domain)
        return
    if args.cmd == "import-tasks":
        import_tasks(args.domain, args.file, args.heldout, args.replace)
        return
    if args.cmd == "lessons":
        lessons_cmd(args.domain, args.action, args.files, args.out, Path(args.runs))
        return
    if args.cmd == "redteam":
        redteam(args.domain, args.n, args.seed)
        return
    if args.cmd == "ingest-traces":
        ingest_traces(args.domain, args.file, args.rubric, args.limit, args.heldout)
        return
    if args.cmd == "ingest-blackbox":
        ingest_blackbox(args.domain, args.url, args.label, args.rubric, args.limit, args.heldout)
        return

    domain = load_domain(args.domain)
    cfg = ZaniiEvolveConfig.load(domain.root / "zanii_evolve.json",
                           **{k: getattr(args, k) for k in OVERRIDES})
    run = Run(domain, cfg, ROOT, Path(args.runs))
    # the judge gate: a rubric run measures nothing until its judge agrees with people
    if args.cmd in ("baseline", "round", "run", "heldout") and hasattr(domain, "tasks"):
        from zanii_evolve.judgecal import gate
        why = gate(run.runs, judge_model(domain), cfg.min_judge_kappa)
        if why:
            sys.exit(why)

    if args.cmd == "baseline":
        run.baseline(args.job)
        run.calibrate([args.job])
    elif args.cmd == "calibrate":
        run.calibrate([j.strip() for j in args.jobs.split(",") if j.strip()])
    elif args.cmd == "round":
        run.round(args.t, dry_run=args.dry_run)
    elif args.cmd == "readjudicate":
        run.readjudicate(args.t)
    elif args.cmd == "reevaluate":
        run.reevaluate(args.t, [v for v in args.variants.split(",") if v] or None)
    elif args.cmd == "run":
        drive(Path(__file__), args.domain, Path(args.runs), cfg.T, args.start,
              extra_args=[a for k in OVERRIDES if getattr(args, k) is not None
                          for a in ("--" + k.replace("_", "-"), str(getattr(args, k)))])
    elif args.cmd == "heldout":
        ids = {"heldout": domain.heldout_ids, "evolve": domain.evolve_ids,
               "redteam": getattr(domain, "redteam_ids", list)}[args.set]()
        if not ids:
            sys.exit(f"domain {domain.name} has no {args.set} split")
        run.heldout(args.label, ids, ref=args.ref)
    elif args.cmd == "smoke":
        run.ensure_branch()
        wt = run.checkout("smoke", run.branch)
        ok, detail = domain.smoke(wt, run.runs, "smoke", domain.smoke_ids())
        print(json.dumps({"ok": ok, **detail}, indent=1))
        sys.exit(0 if ok else 1)
    elif args.cmd == "pareto":
        from zanii_evolve.pareto import frontier, points
        print(json.dumps(frontier(points(run.history.records())), indent=1))
    elif args.cmd == "report":
        from zanii_evolve import report as R
        try:
            cert = R.issue(run.runs) if args.out else R.build(run.runs)   # preview never issues
        except (LookupError, RuntimeError, ValueError) as e:
            sys.exit(str(e))
        if not args.out:
            print(R.markdown(cert))
        else:
            Path(args.out + ".json").write_text(json.dumps(cert, indent=1), encoding="utf-8")
            Path(args.out + ".md").write_text(R.markdown(cert), encoding="utf-8")
            print(f"wrote {args.out}.json and {args.out}.md (digest {cert['digest'][:16]})")
    elif args.cmd == "publish":
        from zanii_evolve.registry import default_root, publish
        try:
            print(json.dumps(publish(ROOT, run.runs, run.harness_rel, default_root(ROOT)), indent=1))
        except (LookupError, RuntimeError, ValueError) as e:
            sys.exit(str(e))
    elif args.cmd == "verify-receipts":
        from zanii_evolve.report import verify_chain
        try:
            ok, why = verify_chain(run.runs, owner=args.owner)
        except RuntimeError as e:
            sys.exit(str(e))
        print(why)
        sys.exit(0 if ok else 1)
    elif args.cmd == "judge-agreement":
        from zanii_evolve.judgecal import agreement
        if not hasattr(domain, "tasks"):
            sys.exit(f"{domain.name} is not a brief domain (no rubric judge)")
        labels = [json.loads(x) for x in Path(args.labels).read_text(encoding="utf-8").splitlines()
                  if x.strip()]
        result = agreement(domain.tasks, labels, judge_model(domain))
        if args.record:
            from zanii_evolve.judgecal import record
            record(run.runs, result)
        print(json.dumps(result, indent=1, ensure_ascii=False))
    elif args.cmd == "status":
        fr = run.frontier()
        print(json.dumps({k: v for k, v in fr.items() if k != "config"}, indent=1))
        print("b_t schedule:", budget_table(cfg.T, cfg.b_min, cfg.b_max))
        print(f"delta = {run.delta():.5f}")
        for r in run.history.render(60):
            print(json.dumps(r, ensure_ascii=False))
        print("evolve branch:", G.rev(ROOT, run.branch), "tree", run.harness_tree(run.branch))


if __name__ == "__main__":
    main()
