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
  python3 zanii_evolve.py --domain clients watch --traces prod.jsonl --rubric "Answers" [--every 3600] [--once]
                                                              # ingest -> evolve -> certify -> publish, on a schedule
  python3 zanii_evolve.py serve [--host 127.0.0.1 --port 8765]  # service API + dashboard

Hyperparameters come from domains/<domain>/zanii_evolve.json; any of them can be
overridden on the command line (--T, --k, --m, --b-min, --b-max, --w,
--m-draft, --delta, --beta0, --beta1, --w-s, --w-c, --w-n, --n-prune, --objective score|cost,
--confirm-k (0 = no confirmation run), --min-judge-kappa).
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
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


def _emit(obj, out: str | None) -> None:
    text = json.dumps(obj, indent=1, ensure_ascii=False)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)


def _load_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"{path}: {e}")


def business(args) -> None:
    """cv, risk-profile, sla create/sign, escrow: signed with ZANII_EVOLVE_IDENTITY."""
    from zanii_evolve import trust as T
    from zanii_evolve.registry import default_root
    from zanii_evolve.report import load_identity
    try:
        ident = load_identity()
    except (RuntimeError, ValueError, OSError) as e:
        sys.exit(str(e))
    if not ident:
        sys.exit("set ZANII_EVOLVE_IDENTITY: this is signed")
    ledger = os.environ.get("ZANII_LEDGER_URL")
    reg = default_root(ROOT)
    need = {"sla create": ("client", "start", "end"), "escrow create": ("payer", "amount", "deadline"),
            "escrow check": ("file", "cert"), "escrow release": ("file", "cert"),
            "sla sign": ("file",), "escrow sign": ("file",)}
    missing = [f"--{k}" for k in need.get(f"{args.cmd} {getattr(args, 'action', '')}", ())
               if not getattr(args, k)]
    if missing:
        sys.exit(f"{args.cmd} {args.action} needs {' '.join(missing)}")
    try:
        if args.cmd == "cv":
            out = T.harness_cv(ident, reg, ledger)
        elif args.cmd == "risk-profile":
            out = T.risk_profile(ident, reg, ledger)
        elif args.cmd == "sla":
            out = (T.sla_create(ident, args.client, args.start, args.end, args.min_certificates,
                                args.price, args.currency, not args.any_certificate)
                   if args.action == "create" else T.sla_sign(_load_json(args.file), ident))
        elif args.action == "create":
            out = T.escrow_create(ident, args.payer, args.amount, args.currency, args.deadline,
                                  args.arbiter, args.threshold)
        elif args.action == "sign":
            out = T.escrow_sign(_load_json(args.file), ident)
        elif args.action == "check":
            if not ledger:
                sys.exit("escrow check needs ZANII_LEDGER_URL: release requires ledger proof")
            out = T.escrow_check(_load_json(args.file), _load_json(args.cert), ledger)
            _emit(out, args.out)
            sys.exit(0 if out["ok"] else 1)
        else:
            out = T.escrow_release(_load_json(args.file), _load_json(args.cert), ident)
    except (ValueError, KeyError, RuntimeError) as e:
        sys.exit(str(e))
    _emit(out, args.out)


def data_proof(args) -> None:
    from zanii_evolve import report as R
    from zanii_evolve.tasksets import trace_task_id
    cert = json.loads(Path(args.cert).read_text(encoding="utf-8")) if args.cert else None
    try:
        if args.action == "prove":
            if not cert or not (args.task_id or args.text):
                sys.exit("data prove needs --cert and --task-id or --text")
            _emit(R.prove_task(cert, args.task_id or trace_task_id(args.text)), args.out)
            return
        if not args.proof:
            sys.exit("data verify needs --proof (and --cert to tie it to a certificate)")
        ok, why = R.verify_task_proof(json.loads(Path(args.proof).read_text(encoding="utf-8")),
                                      cert)
    except (ValueError, KeyError, RuntimeError) as e:
        sys.exit(str(e))
    print(why)
    sys.exit(0 if ok else 1)


def make_identity(out: Path, exp: str, pq: bool = False, plaintext: bool = False) -> None:
    from zanii_evolve.report import new_identity
    owner_p, ident_p = out / "owner.json", out / "identity.json"
    if owner_p.exists() or ident_p.exists():
        sys.exit(f"{out} already holds an identity; refusing to overwrite keys")
    passphrase = os.environ.get("ZANII_EVOLVE_IDENTITY_PASSPHRASE")
    if not passphrase and not plaintext:
        sys.exit("set ZANII_EVOLVE_IDENTITY_PASSPHRASE to seal the keys at rest "
                 "(or pass --plaintext to write them unencrypted)")
    try:
        owner, ident = new_identity(exp, passphrase=passphrase, pq=pq)
    except RuntimeError as e:
        sys.exit(str(e))
    out.mkdir(parents=True, exist_ok=True)
    for p, obj in ((owner_p, owner), (ident_p, ident)):
        p.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        os.chmod(p, 0o600)                 # owner-only on POSIX; on Windows rely on the folder ACL
    print(f"owner DID (publish this; verifiers pin it): {owner['did']}\n"
          f"  {owner_p}: owner key, move it OFFLINE (only needed to re-delegate or revoke)\n"
          f"  {ident_p}: signing identity, scope evolve.* until {exp}"
          + (" (+ ML-DSA-65 post-quantum key)" if pq else "") + "\n"
          + ("  both keys sealed with ZANII_EVOLVE_IDENTITY_PASSPHRASE\n" if passphrase
             else "  WARNING: keys written unencrypted\n")
          + f"  next: export ZANII_EVOLVE_IDENTITY={ident_p}")


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


def ingest_traces(name: str, path: str, rubric: str, limit: int, heldout: float,
                  runs_dir: Path | None = None) -> int:
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
    from zanii_evolve.custody import on_store
    on_store(d, Path(runs_dir or ROOT / "runs") / name, [r["id"] for r in rows])
    bad = sum(1 for r in rows if TS._failed(r["notes"]["feedback"]))
    print(f"{name}: minted {len(rows)} tasks from {len(recs)} traces "
          f"({bad} with negative feedback), {n} in tasks.jsonl")
    return bad


def ingest_blackbox(name: str, url: str, label: str, rubric: str, limit: int,
                    heldout: float, runs_dir: Path | None = None) -> int:
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
    from zanii_evolve.custody import on_store
    on_store(d, Path(runs_dir or ROOT / "runs") / name, [r["id"] for r in rows])
    bad = sum(1 for r in rows if r["notes"]["feedback"] == "bad")
    print(f"{name}: minted {len(rows)} tasks from {len(runs)} runs of {label!r} "
          f"({bad} that went wrong), {n} in tasks.jsonl"
          + (f"; {unreadable} runs could not be read and were skipped" if unreadable else ""))
    return bad


# one cycle's evidence; receipts, the judge record, memory and logs carry across cycles
CYCLE_FILES = ("frontier.json", "calibration.json", "history.jsonl", "attribution.jsonl",
               "confirmations.jsonl")


def archive_cycle(run_dir: Path, dest: Path) -> None:
    """Move a finished cycle's evidence to dest so the next cycle starts from a
    fresh baseline: new tasks change D_evolve, and scores on different task sets
    never compare."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in ([run_dir / f for f in CYCLE_FILES] + sorted(run_dir.glob("r[0-9]*"))
              + [run_dir / "jobs"]):
        if p.exists():
            shutil.move(str(p), str(dest / p.name))


def _cli(args: list[str], log: Path) -> int:
    """This command line in a subprocess, so the judge gate and overrides apply."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as lf:
        return subprocess.run([sys.executable, str(Path(__file__).resolve())] + args,
                              stdout=lf, stderr=subprocess.STDOUT, check=False).returncode


def _constitution(domain: str) -> dict | None:
    """The domain's signed rulebook, or None (none written, or zanii not installed)."""
    try:
        from zanii_evolve.trust import load_constitution
        return load_constitution(domain)
    except ImportError:
        return None


class _Awaiting(Exception):
    """A gated step is waiting for the owner's signed approval."""

    def __init__(self, status: str, cid: str, tool: str):
        super().__init__(cid)
        self.status, self.cid, self.tool = status, cid, tool


def _gate(args, run_dir: Path, tool: str, action) -> None:
    """Raise _Awaiting unless the owner approved exactly this action (zanii_evolve.gate).
    `action` is a callable, built only when the step is gated."""
    if tool not in (getattr(args, "require_approval", None) or []):
        return
    action = action()
    from zanii_evolve import gate
    from zanii_evolve.report import load_identity
    owner = load_identity()["delegation"][0]["issuer"]
    status, cid = gate.check(run_dir, tool, action, owner, args.approval_ttl_hours)
    if status != "approved":
        raise _Awaiting(status, cid, tool)


def _backoff(every: float, failures: int) -> float:
    """Seconds before retry number `failures`: every, 2x, 4x ... capped at a day."""
    return min(86400.0, every * 2 ** max(0, failures - 1))


def _hook(cmd: str | None, env: dict, timeout: float) -> dict:
    """Run a user hook without a shell; Windows splits a command string itself."""
    if not cmd:
        return {}
    try:
        r = subprocess.run(cmd if os.name == "nt" else shlex.split(cmd),
                           env={**os.environ, **env}, timeout=timeout, check=False)
        return {"rc": r.returncode}
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}


def watch_tick(args, cfg_T: int, extra: list[str]) -> dict:
    """One pass of continuous evolution:
      1. move a live rollout on (advance, promote, or roll back on harm),
      2. retry a publish that failed, with backoff,
      3. ingest; once enough new failures have built up, run a cycle (fresh
         baseline + T rounds), retried with backoff and abandoned after
         --max-cycle-failures,
      4. certify, publish and start a staged rollout of a gain above the noise band.
    """
    from zanii_evolve import rollout as RO
    runs_root = Path(args.runs)
    run_dir = runs_root / args.domain
    run_dir.mkdir(parents=True, exist_ok=True)
    sp = run_dir / "watch.json"
    st = {"pending": 0, "cycles": 0, "active": None, "failures": 0, "retry_at": 0.0,
          "publish": None, "log": []}
    if sp.exists():
        st.update(json.loads(sp.read_text(encoding="utf-8")))

    def save(event: dict) -> dict:
        event["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if event["event"] != "waiting":            # everything else is worth a page
            event.update({f"on_event_{k}": v for k, v in _hook(
                args.on_event, {"ZANII_EVOLVE_EVENT": json.dumps(event)},
                args.hook_timeout).items()})
        st["log"] = (st["log"] + [event])[-200:]
        sp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        print(f"[watch:{args.domain}] {json.dumps(event)}", flush=True)
        return st

    def ship(n: int, cert: dict) -> dict:
        """Publish (raises on failure), then the publish hook and the rollout."""
        from zanii_evolve import registry as REG
        _gate(args, run_dir, "publish", lambda: {"domain": args.domain,
                                                 "commit": str(cert["incumbent"]["commit"]),
                                                 "gain": f"{cert['gain']:+.4f}"})
        root = REG.default_root(ROOT)
        manifest = REG.publish(ROOT, run_dir, os.path.normpath(
            f"domains/{args.domain}/{load_domain(args.domain).harness_path}"), root)
        out = {"published": True, "commit": manifest["commit"]}
        path = root / args.domain / str(manifest["commit"])[:8] / "manifest.json"
        out.update({f"on_publish_{k}": v for k, v in _hook(
            args.on_publish, {"ZANII_EVOLVE_MANIFEST": str(path)}, args.hook_timeout).items()})
        stable = cert["baseline"]["commit"]
        if args.rollout and stable and stable != manifest["commit"]:
            RO.start(run_dir, stable, manifest["commit"], args.rollout, args.rollout_alpha,
                     args.rollout_min_requests, cycle=n)
            out["rollout"] = {"stable": stable, "canary": manifest["commit"],
                              "shares": args.rollout}
        return out

    # 0) the ledger our receipts are anchored on only grew since the last tick (zanii.monitor)
    ledger = os.environ.get("ZANII_LEDGER_URL")
    if ledger:
        from zanii_evolve.trust import ledger_check
        try:
            m = ledger_check(run_dir, ledger)
            st["ledger_error"] = m.get("skipped")
            if m["ok"] is False:               # a rewritten or forked log
                save({"event": "ledger_violation", "ledger": ledger,
                      "violations": m["violations"]})
        except Exception as e:                 # unreachable: unknown, not clean; no page per tick
            st["ledger_error"] = f"{type(e).__name__}: {e}"

    # 1) a live rollout decides before anything else can change the branch
    rs = RO.load(run_dir)
    if rs and rs["status"] == "running":
        verdict, ev = RO.decide(run_dir, rs, args.stall_hours, _constitution(args.domain))
        if verdict == "promote":                   # rollback is never gated: it is the safety
            try:
                _gate(args, run_dir, "promote", lambda: {"domain": args.domain, "rollout": rs["id"],
                                                         "canary": rs["canary"],
                                                         "stable": rs["stable"]})
            except _Awaiting as w:
                verdict = "wait"
                if w.status != "pending":
                    save({"event": "approval_required", "action": "promote", "id": w.cid,
                          "canary_commit": rs["canary"], "approve": f"approve {w.cid}"})
        if verdict != "wait":
            arms = {"canary_commit": rs["canary"], "stable_commit": rs["stable"]}
            rs = RO.apply(run_dir, rs, verdict, ev)
            if verdict == "rollback":              # the next cycle starts from the stable harness
                G.git(ROOT, "update-ref", f"refs/heads/evolve/{args.domain}", rs["stable"],
                      check=True)
            save({"event": f"rollout_{verdict}", **arms, **ev})
    rolling = bool(rs and rs["status"] == "running")

    # 2) a certified gain that failed to publish is retried before any new cycle
    pub = st["publish"]
    if pub:
        if time.time() < pub["next_at"]:
            return st
        n = pub["cycle"]
        try:
            from zanii_evolve import report as R
            out = ship(n, R.build(run_dir))
        except _Awaiting as w:
            pub["approval"] = w.cid
            if w.status == "pending":
                return st                          # still waiting; nothing new to say
            return save({"event": "approval_required", "action": "publish", "id": w.cid,
                         "cycle": n, "approve": f"approve {w.cid}"})
        except (LookupError, RuntimeError, ValueError, OSError) as e:
            pub["attempts"] += 1
            if pub["attempts"] >= args.max_retries:
                st["publish"] = None
                return save({"event": "publish_gave_up", "cycle": n, "error": str(e),
                             "note": "run `publish` by hand once the cause is fixed"})
            pub["next_at"] = time.time() + _backoff(args.every, pub["attempts"])
            return save({"event": "publish_failed", "cycle": n, "error": str(e),
                         "attempts": pub["attempts"]})
        st["publish"] = None
        return save({"event": "publish_retried", "cycle": n, **out})

    # 3) ingest; an open cycle finishes on the task set its baseline measured
    if st["active"] is None:
        try:                   # the ingest helpers sys.exit on a bad source; a tick survives it
            if args.traces:
                new = ingest_traces(args.domain, args.traces, args.rubric, args.limit,
                                    args.heldout, runs_root)
            else:
                new = ingest_blackbox(args.domain, args.blackbox_url, args.label, args.rubric,
                                      args.limit, args.heldout, runs_root)
        except SystemExit as e:
            return save({"event": "ingest_failed", "error": str(e)})
        st["pending"] += new
        if st["pending"] < args.min_failures or rolling:
            return save({"event": "waiting", "new_failures": new, "pending": st["pending"],
                         "rollout_running": rolling})
    elif time.time() < st["retry_at"]:
        return st                                  # backing off after a failed attempt

    n = st["active"] or st["cycles"] + 1
    if st["active"] is None:                       # a resumed cycle keeps its settled rounds
        if (run_dir / "frontier.json").exists():
            archive_cycle(run_dir, run_dir / "cycles" / f"c{n - 1}")
        st.update(active=n, pending=0, failures=0, retry_at=0.0)
        save({"event": "cycle_started", "cycle": n})
    from zanii_evolve.driver import settled_rounds
    err = None
    try:
        drive(Path(__file__).resolve(), args.domain, runs_root, cfg_T, 0, extra_args=extra)
    except SystemExit as e:                        # baseline, or 3 rounds in a row, failed
        err = str(e)
    if (run_dir / "STOP").exists():
        return save({"event": "stopped", "cycle": n})
    if err is not None or settled_rounds(run_dir / "frontier.json") < cfg_T:
        st["failures"] += 1
        why = err or "rounds did not all settle, see logs/r*.log"
        if st["failures"] >= args.max_cycle_failures:
            fr = run_dir / "frontier.json"
            base = (json.loads(fr.read_text(encoding="utf-8"))["trajectory"][0]["commit"]
                    if fr.exists() else None)
            if base:                               # undo rounds accepted in an uncertified cycle
                G.git(ROOT, "update-ref", f"refs/heads/evolve/{args.domain}", base, check=True)
            archive_cycle(run_dir, run_dir / "cycles" / f"c{n}-abandoned")
            st.update(active=None, failures=0, retry_at=0.0)
            return save({"event": "cycle_abandoned", "cycle": n, "error": why,
                         "branch_reset_to": base})
        st["retry_at"] = time.time() + _backoff(args.every, st["failures"])
        return save({"event": "cycle_failed", "cycle": n, "error": why,
                     "failures": st["failures"], "retry_in_s": st["retry_at"] - time.time()})

    from zanii_evolve import report as R
    st.update(active=None, cycles=n, failures=0, retry_at=0.0)
    cert = R.build(run_dir)
    result = {"event": "cycle_done", "cycle": n, "gain": cert["gain"],
              "noise_band": cert["noise_band"], "published": False}
    if not cert["gain_exceeds_noise"]:
        return save(result)                        # inside the noise band: nothing ships
    result["heldout_rc"] = _cli(                   # rc 1 also covers "no heldout split"
        ["--domain", args.domain, "--runs", str(runs_root)] + extra
        + ["heldout", "--label", f"c{n}"], run_dir / "logs" / f"watch_c{n}.log")
    try:
        result.update(ship(n, cert))
    except _Awaiting as w:                         # parked: the publish branch retries it
        st["publish"] = {"cycle": n, "attempts": 0, "next_at": 0.0, "approval": w.cid}
        result.update(approval_required=w.cid, approve=f"approve {w.cid}")
    except (LookupError, RuntimeError, ValueError, OSError) as e:
        st["publish"] = {"cycle": n, "attempts": 1,
                         "next_at": time.time() + _backoff(args.every, 1)}
        result["error"] = f"publish failed, retrying with backoff: {e}"
    return save(result)


def watch(args, cfg_T: int, extra: list[str]) -> None:
    stop = Path(args.runs) / args.domain / "STOP"
    while not stop.exists():
        watch_tick(args, cfg_T, extra)
        if args.once:
            return
        until = time.time() + args.every
        while time.time() < until and not stop.exists():
            time.sleep(min(30.0, max(0.0, until - time.time())))
    print(f"[watch:{args.domain}] STOP present; exiting", flush=True)


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
    p = sub.add_parser("report", help="generalization certificate (JSON + Markdown)")
    p.add_argument("--out", default=None,
                   help="path stem: writes <out>.json and <out>.md (default: print)")
    p.add_argument("--public", default=None, help="also write <out>.public.json proving only "
                   "these comma-separated fields, e.g. domain,gain,noise_band,gain_exceeds_noise")
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
    p = sub.add_parser("verify-report", help="check a certificate (full or public view): "
                       "digest, HMAC, receipt, post-quantum and ledger inclusion")
    p.add_argument("file")
    p.add_argument("--owner", default=None, help="pin the expected owner did:key")
    p.add_argument("--ledger", default=None, help="Zanii ledger URL: also prove the receipt "
                   "is included there")
    p = sub.add_parser("verify-receipts", help="check the run's chain of issued certificate receipts")
    p.add_argument("--owner", default=None)
    p.add_argument("--revocations", default=None, help="revocations.json from rotate-identity")
    p = sub.add_parser("identity", help="new owner key + delegated signing identity (zanii); "
                       "sealed with ZANII_EVOLVE_IDENTITY_PASSPHRASE")
    p.add_argument("--out", required=True, help="directory for owner.json and identity.json")
    p.add_argument("--exp", default="2031-01-01T00:00:00Z", help="delegation expiry (UTC)")
    p.add_argument("--pq", action="store_true", help="add an ML-DSA-65 key: certificates are "
                   "dual-signed Ed25519 + post-quantum (pip install zanii[pq])")
    p.add_argument("--plaintext", action="store_true", help="write keys unencrypted")
    p = sub.add_parser("rotate-identity", help="new signing key under the same owner; history "
                       "continues through a signed succession")
    p.add_argument("--owner-key", required=True, help="owner.json")
    p.add_argument("--identity", required=True, help="the current identity.json")
    p.add_argument("--out", required=True, help="directory for the new identity")
    p.add_argument("--reason", default="rotation", choices=["rotation", "compromise", "retirement"])
    p.add_argument("--compromised-at", default=None, help="UTC time the old key was exposed")
    p.add_argument("--exp", default="2031-01-01T00:00:00Z")
    sub.add_parser("verify-memory", help="check a deployment's provable memory chain "
                   "(ZANII_EVOLVE_PROVABLE_MEMORY=1) against its stored lessons"
                   ).add_argument("state_dir")
    p = sub.add_parser("data", help="client data: prove a record was or was not in a "
                       "certificate's training set, or delete it with a signed attestation")
    p.add_argument("action", choices=["prove", "verify", "forget", "custody"])
    p.add_argument("--owner", default=None, help="custody: pin the signer's owner did:key")
    p.add_argument("--cert", help="prove/verify: the full certificate JSON")
    p.add_argument("--proof", help="verify: a proof written by `data prove`")
    p.add_argument("--task-id", help="the record's task id (tr-..., bb-...)")
    p.add_argument("--text", help="or the record's original input text")
    p.add_argument("--out", help="write the proof / attestation here (default: print)")
    p = sub.add_parser("cv", help="signed AgentCV of the certifying identity: every published "
                       "certificate as a verifiable benchmark entry")
    p.add_argument("--out")
    p = sub.add_parser("risk-profile", help="underwriting facts about the certifying identity "
                       "(never a score)")
    p.add_argument("--out")
    p = sub.add_parser("sla", help="evolve-as-a-service terms: create and sign them, or assess "
                       "a domain against them from its receipt chain")
    p.add_argument("action", choices=["create", "sign", "assess"])
    p.add_argument("--file", help="sign/assess: the agreement JSON")
    p.add_argument("--client", help="create: the client's did:key")
    p.add_argument("--start", help="create: window start (UTC ISO)")
    p.add_argument("--end", help="create: window end (UTC ISO)")
    p.add_argument("--min-certificates", type=int, default=1)
    p.add_argument("--any-certificate", action="store_true",
                   help="count every certificate, not only certified gains")
    p.add_argument("--price", help="create: informational price, e.g. 5000.00")
    p.add_argument("--currency", default="USD")
    p.add_argument("--out")
    p = sub.add_parser("escrow", help="pay per certified gain: terms, signatures, the "
                       "ledger-proven release verdict, and the release instruction")
    p.add_argument("action", choices=["create", "sign", "check", "release"])
    p.add_argument("--file", help="sign/check/release: the escrow JSON")
    p.add_argument("--payer", help="create: the payer's did:key")
    p.add_argument("--amount", help="create: e.g. 5000.00 (exact, never a float)")
    p.add_argument("--currency", default="USD")
    p.add_argument("--deadline", help="create: UTC ISO")
    p.add_argument("--arbiter", action="append", default=None, help="create: arbiter did:key")
    p.add_argument("--threshold", type=int, default=1)
    p.add_argument("--cert", help="check/release: the certificate JSON")
    p.add_argument("--out")
    p = sub.add_parser("audit", help="auditor's Markdown report over the domain's certificates, "
                       "receipt chains, rollouts, rulebook, client data and memory")
    p.add_argument("--owner", default=None)
    p.add_argument("--bundle", default=None, help="a ledger audit bundle (`zanii bundle`)")
    p.add_argument("--memory-dir", default=None, help="a deployment state dir to include")
    p.add_argument("--out", default=None)
    p = sub.add_parser("anchor-identity", help="anchor the identity's post-quantum key binding "
                       "on the ledger (once), or --check a saved anchor offline")
    p.add_argument("--check", default=None, help="a pq_transition.json to verify offline")
    sub.add_parser("approvals", help="watch steps waiting for the owner's approval")
    p = sub.add_parser("approve", help="the owner signs approval of one pending watch step")
    p.add_argument("id")
    p.add_argument("--owner-key", required=True, help="owner.json")
    sub.add_parser("ledger-check", help="audit the Zanii ledger (ZANII_LEDGER_URL) once and "
                   "save its state; watch then proves on every tick that it only grew")
    p = sub.add_parser("audit-rollout", help="re-check live evidence: the signed decision "
                       "chain, that every arm follows from its plan, plans never interleaved, "
                       "and session receipts under the rulebook")
    p.add_argument("--owner", default=None, help="pin the owner did:key of the decision signer")
    p = sub.add_parser("constitution", help="the domain's rules as a signed rulebook: sign "
                       "domains/<d>/constitution.json, or check one action target")
    p.add_argument("action", choices=["sign", "check"])
    p.add_argument("target", nargs="?", help="check: an action target, e.g. crm.export")
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
    p = sub.add_parser("watch", help="continuous evolution: ingest live failures on a schedule; "
                       "when enough build up, run a cycle and publish a certified gain")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--traces", help="production trace JSONL (re-read each tick; idempotent)")
    src.add_argument("--blackbox-url", help="a Zanii Blackbox gateway (BLACKBOX_API_KEY)")
    p.add_argument("--label", help="the agent's Blackbox label (with --blackbox-url)")
    p.add_argument("--rubric", default="Does what the request asks, completely and correctly",
                   help='"|"-separated pass/fail criteria for every task')
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--heldout", type=float, default=0.2)
    p.add_argument("--min-failures", type=int, default=20,
                   help="new failed runs that trigger a cycle")
    p.add_argument("--every", type=float, default=3600, help="seconds between ticks (>= 60)")
    p.add_argument("--once", action="store_true", help="one tick, then exit (for cron)")
    p.add_argument("--on-publish", default=None, help="command run after a publish with "
                   "ZANII_EVOLVE_MANIFEST set, e.g. to stage the harness (no shell)")
    p.add_argument("--on-event", default=None, help="command run on every rollout, failure "
                   "and publish event with ZANII_EVOLVE_EVENT (JSON) set: alerts (no shell)")
    p.add_argument("--hook-timeout", type=float, default=600)
    p.add_argument("--rollout", type=lambda v: [float(x) for x in v.split(",") if x.strip()],
                   default=None, help="live canary shares per stage, e.g. 0.05,0.25,0.5; "
                   "agents route via /api/domains/<d>/rollout/assign and report outcomes")
    p.add_argument("--rollout-alpha", type=float, default=0.05,
                   help="false-rollback budget of the always-valid harm test")
    p.add_argument("--rollout-min-requests", type=int, default=200,
                   help="canary outcomes a stage needs before it can pass")
    p.add_argument("--stall-hours", type=float, default=24,
                   help="alert once when a stage has gathered too few outcomes for this long")
    p.add_argument("--max-retries", type=int, default=6, help="publish attempts before giving up")
    p.add_argument("--require-approval", type=lambda v: [x.strip() for x in v.split(",") if x.strip()],
                   default=None, help="steps that wait for the owner's signed approval: "
                   "publish, promote (rollback never waits)")
    p.add_argument("--approval-ttl-hours", type=float, default=72,
                   help="an unanswered approval expires and is reopened after this")
    p.add_argument("--max-cycle-failures", type=int, default=4,
                   help="failed attempts before a cycle is abandoned and its rounds undone")
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
                             owner=args.owner, ledger=args.ledger)
        except RuntimeError as e:          # receipt present, zanii not installed
            sys.exit(str(e))
        print(why)
        sys.exit(0 if ok else 1)
    if args.cmd == "identity":
        make_identity(Path(args.out), args.exp, pq=args.pq, plaintext=args.plaintext)
        return
    if args.cmd == "rotate-identity":
        from zanii_evolve.trust import rotate_identity
        try:
            print(json.dumps(rotate_identity(
                Path(args.owner_key), Path(args.identity), Path(args.out), args.reason,
                args.compromised_at, args.exp,
                os.environ.get("ZANII_EVOLVE_IDENTITY_PASSPHRASE")), indent=1))
        except (ValueError, RuntimeError, FileExistsError, OSError) as e:
            sys.exit(str(e))
        return
    if args.cmd == "verify-memory":
        from zanii_evolve.trust import verify_memory
        try:
            rep = verify_memory(Path(args.state_dir))
        except (LookupError, RuntimeError) as e:
            sys.exit(str(e))
        print(json.dumps(rep, indent=1))
        sys.exit(0 if rep["ok"] else 1)
    if args.cmd == "data" and args.action in ("prove", "verify"):
        data_proof(args)
        return
    if args.cmd == "anchor-identity":
        from zanii_evolve import trust as T
        from zanii_evolve.report import ledger_api_key, load_identity
        try:
            if args.check:
                v = T.verify_anchored_identity(Path(args.check))
                print("anchored binding verifies" if v["ok"] else f"FAILED: {v['reasons']}")
                sys.exit(0 if v["ok"] else 1)
            ident, ledger = load_identity(), os.environ.get("ZANII_LEDGER_URL")
            if not ident or not ledger:
                sys.exit("set ZANII_EVOLVE_IDENTITY and ZANII_LEDGER_URL")
            out = Path(os.environ["ZANII_EVOLVE_IDENTITY"]).parent / "pq_transition.json"
            print(json.dumps(T.anchor_identity(ident, ledger, ledger_api_key(), out), indent=1))
        except (ValueError, RuntimeError, FileExistsError, OSError) as e:
            sys.exit(str(e))
        return
    if args.cmd in ("cv", "risk-profile", "escrow") or (args.cmd == "sla"
                                                         and args.action != "assess"):
        business(args)
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
    if args.cmd == "data" and args.action == "custody":
        from zanii_evolve.custody import history
        from zanii_evolve.tasksets import trace_task_id
        if not (args.task_id or args.text):
            sys.exit("data custody needs --task-id or --text")
        cfg = json.loads((ROOT / "domains" / args.domain / "zanii_evolve.json").read_text(
            encoding="utf-8")) if (ROOT / "domains" / args.domain / "zanii_evolve.json").exists() else {}
        try:
            out = history(Path(args.runs) / args.domain, args.task_id or trace_task_id(args.text),
                          args.owner, sorted((cfg.get("subprocessors") or {})))
        except (LookupError, RuntimeError, OSError) as e:
            sys.exit(str(e))
        _emit(out, args.out)
        sys.exit(0 if out["log_intact"] and out["touches_verified"] and out["recipients_declared"]
                 else 1)
    if args.cmd == "data":                     # forget
        from zanii_evolve.report import load_identity
        from zanii_evolve.trust import forget
        ident = load_identity()
        if not ident:
            sys.exit("set ZANII_EVOLVE_IDENTITY: the deletion attestation is signed")
        try:
            out = forget(args.domain, Path(args.runs) / args.domain, ident,
                         text=args.text, task_id=args.task_id)
        except (ValueError, LookupError, RuntimeError, OSError) as e:
            sys.exit(str(e))
        _emit(out, args.out)
        print(f"deleted {out['task_id']}: {out['removed_tasks']} task, {out['removed_files']} "
              f"run files; give the client the attestation and its salt", file=sys.stderr)
        return
    if args.cmd == "sla":                      # assess
        from zanii_evolve.trust import sla_assess
        if not args.file:
            sys.exit("sla assess needs --file")
        out = sla_assess(_load_json(args.file), Path(args.runs) / args.domain)
        _emit(out, args.out)
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "audit":
        from zanii_evolve.registry import default_root
        from zanii_evolve.trust import audit
        try:
            md = audit(args.domain, Path(args.runs) / args.domain, default_root(ROOT), args.owner,
                       _load_json(args.bundle) if args.bundle else None,
                       Path(args.memory_dir) if args.memory_dir else None)
        except (RuntimeError, ValueError, OSError) as e:
            sys.exit(str(e))
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(md)
        return
    if args.cmd in ("approvals", "approve"):
        from zanii_evolve import gate
        run_dir = Path(args.runs) / args.domain
        try:
            out = (gate.pending(run_dir) if args.cmd == "approvals"
                   else gate.approve(run_dir, args.id, Path(args.owner_key)))
        except (LookupError, ValueError, RuntimeError, OSError) as e:
            sys.exit(str(e))
        print(json.dumps(out, indent=1))
        return
    if args.cmd == "ledger-check":
        from zanii_evolve.trust import ledger_check
        if not os.environ.get("ZANII_LEDGER_URL"):
            sys.exit("set ZANII_LEDGER_URL")
        print("auditing the whole ledger history once; this can take minutes", flush=True)
        try:
            m = ledger_check(Path(args.runs) / args.domain, os.environ["ZANII_LEDGER_URL"],
                             bootstrap=True)
        except Exception as e:                 # transport errors from the SDK
            sys.exit(f"ledger check failed: {e}")
        print(json.dumps(m, indent=1))
        sys.exit(0 if m["ok"] else 1)
    if args.cmd == "audit-rollout":
        from zanii_evolve import live
        try:
            rep = live.audit(Path(args.runs) / args.domain, args.owner, _constitution(args.domain))
        except (RuntimeError, ValueError) as e:
            sys.exit(str(e))
        print(json.dumps(rep, indent=1))
        sys.exit(0 if rep["ok"] else 1)
    if args.cmd == "constitution":
        from zanii_evolve import trust as T
        from zanii_evolve.report import load_identity
        try:
            if args.action == "sign":
                ident = load_identity()
                if not ident:
                    sys.exit("set ZANII_EVOLVE_IDENTITY to the identity that signs the rulebook")
                print(f"signed {T.sign_constitution(args.domain, ident)}")
            else:
                if not args.target:
                    sys.exit("constitution check needs an action target")
                print(json.dumps(T.check_action(args.domain, args.target), indent=1))
        except (ValueError, LookupError, RuntimeError, OSError) as e:
            sys.exit(str(e))
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
        ingest_traces(args.domain, args.file, args.rubric, args.limit, args.heldout, Path(args.runs))
        return
    if args.cmd == "ingest-blackbox":
        ingest_blackbox(args.domain, args.url, args.label, args.rubric, args.limit, args.heldout,
                        Path(args.runs))
        return

    extra = [a for k in OVERRIDES if getattr(args, k) is not None
             for a in ("--" + k.replace("_", "-"), str(getattr(args, k)))]
    if args.cmd == "watch":
        if args.blackbox_url and not args.label:
            ap.error("--blackbox-url needs --label")
        if args.min_failures < 1 or args.every < 60:
            ap.error("--min-failures must be >= 1 and --every >= 60 seconds")
        if args.max_retries < 1 or args.max_cycle_failures < 1:
            ap.error("--max-retries and --max-cycle-failures must be >= 1")
        if args.require_approval:
            bad = [x for x in args.require_approval if x not in ("publish", "promote")]
            if bad:
                ap.error(f"--require-approval takes publish and/or promote, not {bad}")
            if not os.environ.get("ZANII_EVOLVE_IDENTITY"):
                ap.error("--require-approval needs ZANII_EVOLVE_IDENTITY: its owner approves")
        if args.rollout is not None:
            from zanii_evolve.rollout import validate
            try:                               # a bad plan fails now, not after a cycle
                validate(args.rollout, args.rollout_alpha, args.rollout_min_requests)
            except ValueError as e:
                ap.error(str(e))
        domain = load_domain(args.domain)
        cfg = ZaniiEvolveConfig.load(domain.root / "zanii_evolve.json",
                                     **{k: getattr(args, k) for k in OVERRIDES})
        watch(args, cfg.T, extra)
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
        drive(Path(__file__), args.domain, Path(args.runs), cfg.T, args.start, extra_args=extra)
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
            if args.public:
                try:
                    view = R.public_view(cert, [f.strip() for f in args.public.split(",")
                                                if f.strip()])
                except ValueError as e:
                    sys.exit(str(e))
                Path(args.out + ".public.json").write_text(json.dumps(view, indent=1),
                                                           encoding="utf-8")
                print(f"wrote {args.out}.public.json proving {sorted(R.disclosed(view))}; "
                      f"keep {args.out}.json private (it holds the disclosure salts)")
    elif args.cmd == "publish":
        from zanii_evolve.registry import default_root, publish
        try:
            print(json.dumps(publish(ROOT, run.runs, run.harness_rel, default_root(ROOT)), indent=1))
        except (LookupError, RuntimeError, ValueError) as e:
            sys.exit(str(e))
    elif args.cmd == "verify-receipts":
        from zanii_evolve.report import verify_chain
        from zanii_evolve.trust import revoked_set
        try:
            ok, why = verify_chain(run.runs, owner=args.owner,
                                   revoked=revoked_set(args.revocations))
        except (RuntimeError, ValueError) as e:
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
