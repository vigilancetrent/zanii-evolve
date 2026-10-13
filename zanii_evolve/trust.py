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
"""Trust artifacts around evolved harnesses, built on the Zanii packages.

    constitution  the domain's rules as a signed, content-addressed rulebook
    rotation      a new signing key whose history continues the old one (succession),
                  with a revocation when the old key was compromised
    data          which production records a cycle trained on (train), and signed
                  deletion of a client's records (retention)
    cv            a signed, portable record of a harness's certificates (AgentCV)
    audit         an auditor's report over certificates, receipts, rollouts and memory
    sla / escrow  certified improvements as an agreement, and payment released only
                  against a ledger-proven certificate receipt
    risk          underwriting facts about the signing identity (insurance)

Every function is offline unless it says otherwise. Protocol objects take no
floats, so scores travel as strings.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import report as R



def _read(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jsonl(p: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return []


def _write(p: Path, obj) -> Path:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def _sk(identity: dict) -> bytes:
    return bytes.fromhex(identity["agent"]["privateKey"])


# --------------------------------------------------------------- constitution ---
EFFECTS = ("allow", "forbid", "require_approval")


def sign_constitution(domain: str, identity: dict) -> Path:
    """domains/<d>/constitution.json -> constitution.signed.json, signed by the identity.

    Source: {"name": str, "default": "allow" | "forbid", "rules": [{"id", "effect",
    "targets": [scope patterns], "note"?}]}, first match wins, top to bottom."""
    R._zanii()
    from zanii import constitution as K
    d = R.DOMAINS / domain
    src = _read(d / "constitution.json")
    if not isinstance(src, dict) or not isinstance(src.get("rules"), list) or not src["rules"]:
        raise ValueError(f"{d / 'constitution.json'}: need a non-empty 'rules' list")
    for r in src["rules"]:
        if r.get("effect") not in EFFECTS or not r.get("id") or not r.get("targets"):
            raise ValueError(f"rule {r.get('id')!r}: need id, targets and an effect in {EFFECTS}")
        for t in r["targets"]:              # Zanii scopes: "*", an exact target, or "prefix.*"
            if "*" in t.rstrip("*") or (t.endswith("*") and t not in ("*",) and not t.endswith(".*")):
                raise ValueError(f"rule {r['id']!r}: target {t!r} is not a scope pattern; use "
                                 f"an exact target or 'prefix.*'")
    c = K.create_constitution(issuer=identity["agent"]["did"], name=src.get("name") or domain,
                              rules=src["rules"], default_effect=src.get("default", "allow"),
                              created_at=R._now(), issuer_private_key=_sk(identity))
    return _write(d / "constitution.signed.json", c)


def load_constitution(domain: str) -> dict | None:
    c = _read(R.DOMAINS / domain / "constitution.signed.json")
    if c is None:
        return None
    from zanii import constitution as K
    if not K.verify_constitution(c):
        raise ValueError(f"{domain}: constitution.signed.json signature invalid")
    return c


def check_action(domain: str, target: str) -> dict:
    """What the domain's rulebook says about one action target."""
    from zanii import constitution as K
    c = load_constitution(domain)
    if c is None:
        raise LookupError(f"{domain} has no signed constitution")
    return K.evaluate(c, target)


# ------------------------------------------------------------------- rotation ---
def rotate_identity(owner_file: Path, identity_file: Path, out: Path, reason: str = "rotation",
                    compromised_at: str | None = None, exp: str = "2031-01-01T00:00:00Z",
                    passphrase: str | None = None) -> dict:
    """A new signing key under the same owner. The owner signs "new succeeds old";
    on a rotation or retirement the old key countersigns, after a compromise it cannot be
    trusted to, the old delegation is revoked, and compromised_at brackets the
    window whose receipts stay trusted."""
    zc = R._zanii()
    from zanii import succession as SU
    if reason not in ("rotation", "compromise", "retirement"):
        raise ValueError("reason must be rotation, compromise or retirement")
    if reason == "compromise" and not compromised_at:
        raise ValueError("a compromise rotation needs --compromised-at (UTC)")
    out = Path(out)
    if (out / "identity.json").exists():
        raise FileExistsError(f"{out} already holds an identity; refusing to overwrite keys")
    owner_obj = _read(owner_file)
    old = R.load_identity(str(identity_file))
    owner_sk = R.open_key(owner_obj, str(owner_file))
    owner_did = owner_obj["did"]
    if old["delegation"][0]["issuer"] != owner_did:
        raise ValueError("that owner did not delegate the current identity")
    new = zc.generate_keypair()
    from .custody import SCOPES as CUSTODY
    cert = zc.create_cert(issuer=owner_did, subject=new.did, scopes=[R.SCOPE, *CUSTODY], exp=exp,
                          issuer_private_key=owner_sk)
    body = SU.build_succession_body(predecessor=old["agent"]["did"], successor=new.did,
                                    owner=owner_did, reason=reason, ts=R._now(),
                                    compromised_at=compromised_at)
    sigs = [SU.sign_succession(body, owner_did, owner_sk)]
    if reason != "compromise":
        sigs.append(SU.sign_succession(body, old["agent"]["did"], _sk(old)))
    lineage = _read(Path(identity_file).parent / "successions.json", []) + [
        SU.assemble_succession(body, sigs)]
    ident = {"agent": (R.seal_key(new.did, new.private_key, passphrase) if passphrase
                       else {"did": new.did, "privateKey": new.private_key.hex()}),
             "delegation": [cert]}
    _write(out / "identity.json", ident)
    _write(out / "successions.json", lineage)
    revoked = []
    if reason == "compromise":
        revoked = _read(Path(identity_file).parent / "revocations.json", []) + [
            zc.create_revocation(old["delegation"][0], R._now(), owner_sk)]
        _write(out / "revocations.json", revoked)
    check = SU.verify_lineage(lineage, new.did)
    return {"did": new.did, "predecessor": old["agent"]["did"], "lineage_ok": check["ok"],
            "lineage": len(lineage), "revoked": len(revoked)}


def revoked_set(path: Path | None) -> set[str] | None:
    """Revoked delegation hashes from a revocations.json, for receipt checks."""
    if not path:
        return None
    from zanii.delegation import cert_hash, verify_revocation
    recs = _read(path, [])
    bad = [r for r in recs if not verify_revocation(r)]
    if bad:
        raise ValueError(f"{path}: {len(bad)} revocation records have bad signatures")
    return {cert_hash(r["cert"]) for r in recs}


# --------------------------------------------------------------------- memory ---
def verify_memory(state_dir: Path) -> dict:
    """Re-check a deployment's provable memory (ZANII_EVOLVE_PROVABLE_MEMORY=1): the
    zanii.memory chain is intact, and it matches the lessons on disk one to one,
    so a lesson edited, deleted or slipped in outside the chain shows."""
    R._zanii()
    from zanii import memory as M
    state_dir = Path(state_dir)
    chain = _jsonl(state_dir / "memory_chain.jsonl")
    if not chain:
        raise LookupError(f"{state_dir}: no memory_chain.jsonl (provable memory off?)")
    out = M.verify_memory_chain([e["payload"] for e in chain])
    report = {"ok": out["ok"], "entries": out["length"], "problems": list(out["reasons"]),
              "head": chain[-1]["payload"]["entry_hash"]}
    stored = [(kind, r.get("note", "")) for kind in ("semantic", "episodic")
              for r in _jsonl(state_dir / f"mem_{kind}.jsonl")]
    if not stored:                          # ZaniiDB backend: the chain is all we can check here
        report["content"] = "not checked (no JSONL lessons in this state dir)"
        return report
    unmatched = list(stored)
    for e in chain:                         # ponytail: O(entries x lessons); index if memory grows large
        p = e["payload"]
        hit = next((x for x in unmatched if x[0] == p["kind"]
                    and M.verify_content(x[1], e["salt"], p["content_commitment"])), None)
        if hit:
            unmatched.remove(hit)
        else:
            report["problems"].append(f"entry {p['seq']}: its lesson was edited or deleted")
    report["problems"] += [f"{k} lesson not in the chain: {n[:60]!r}" for k, n in unmatched]
    report["ok"] = not report["problems"]
    return report


# ----------------------------------------------------------------------- data ---
def _data_receipt(run_dir: Path, identity: dict, payload: dict, target: str) -> dict:
    """Sign a data-governance payload into runs/<d>/data_receipts.jsonl (hash-linked)."""
    zc = R._zanii()
    from zanii.client import payload_bytes
    log = Path(run_dir) / "data_receipts.jsonl"
    prior = _jsonl(log)
    prev = zc.receipt_hash(prior[-1]["receipt"]) if prior else None
    r = zc.create_receipt(agent_id=identity["agent"]["did"], delegation=identity["delegation"],
                          action="attest", target=target,
                          payload_hash=zc.hash_payload(payload_bytes(payload)), ts=R._now(),
                          prev=prev, agent_private_key=_sk(identity))
    row = {"receipt": r, "payload": payload}
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def forget(domain: str, run_dir: Path, identity: dict, *, text: str | None = None,
           task_id: str | None = None, policy: str = "data-subject request") -> dict:
    """Delete one client record: its task from domains/<d>/tasks.jsonl and every
    per-task file under runs/<domain>/ that names it, then sign a deletion
    attestation (zanii.retention) naming the subject only by a salted commitment.
    Returns the attestation and the salt, which the client keeps to check it."""
    from zanii import retention as RT

    from . import tasksets as TS
    if not (text or task_id):
        raise ValueError("forget needs the record's text or its task id")
    tid = task_id or TS.trace_task_id(text)
    from .custody import Custody
    cust = Custody.load(run_dir)
    if cust is not None:                    # the record's custody ends with its deletion
        cust.touch([tid], "delete")
    tasks_file = R.DOMAINS / domain / "tasks.jsonl"
    rows = TS.load_tasks(tasks_file) if tasks_file.is_file() else []
    keep = [t for t in rows if t.get("id") != tid]
    removed = len(rows) - len(keep)
    if removed:
        TS.write_tasks(R.DOMAINS / domain, keep, replace=True)
    files = [p for p in Path(run_dir).rglob(f"*{tid}*")] if Path(run_dir).exists() else []
    for p in sorted(files, key=lambda p: len(p.parts), reverse=True):
        if p.is_dir():
            import shutil
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    if not removed and not files:
        raise LookupError(f"no record {tid} in {domain}")
    att = RT.build_retention_attestation(
        subject=tid, action="deleted", policy=policy, executed_at=R._now(),
        scope=[f"domains/{domain}/tasks.jsonl", f"runs/{domain}/**/*{tid}*"],
        method="file deletion", evidence=f"{removed} task, {len(files)} run files")
    row = _data_receipt(run_dir, identity, att["payload"], "evolve.data.retention")
    return {"task_id": tid, "removed_tasks": removed, "removed_files": len(files),
            "attestation": row, "salt": att["salt"],
            "note": "certificates already issued keep the task in their signed manifest: "
                    "that is a record that it was used, not a copy of it"}


def verify_forget(row: dict, task_id: str, salt: str, owner: str | None = None) -> tuple[bool, str]:
    """A client checks the deletion attestation for their own record."""
    zc = R._zanii()
    from zanii import retention as RT
    from zanii.client import payload_bytes
    p, r = row["payload"], row["receipt"]
    if r["payload_hash"] != zc.hash_payload(payload_bytes(p)):
        return False, "attestation does not match its receipt"
    if not zc.verify_receipt(r, expected_owner=owner).ok:
        return False, "receipt signature or delegation invalid"
    if not RT.verify_retention(p).get("ok") or not RT.verify_subject(task_id, salt,
                                                                      p["subject_commitment"]):
        return False, "attestation is not about this record"
    return True, f"deletion of {task_id} attested at {p['executed_at']}"


# --------------------------------------------------------------------- ledger ---
def ledger_check(run_dir: Path, ledger: str, bootstrap: bool = False) -> dict:
    """zanii.monitor against the ledger certificates are anchored on: the log only
    grew since the last check (append-only), and its anchors hold. The first check
    audits the whole history, which takes minutes on a large ledger, so it runs
    only with bootstrap=True (`ledger-check`); after that each check is one proof."""
    R._zanii()
    from zanii import client as ZC
    from zanii.monitor import check_once
    mp = Path(run_dir) / "ledger_monitor.json"
    prev = _read(mp)
    if prev is None and not bootstrap:
        return {"ok": None, "skipped": "no saved ledger state yet: run `ledger-check` once"}
    m = check_once(ledger, prev, get=lambda url: ZC._http_json(url))   # the SDK's HTTP
    if m["ok"]:                             # a violation keeps the last good state
        _write(mp, m["state"])
    return {"ok": m["ok"], "size": m["size"], "violations": m["violations"]}


# ------------------------------------------------------------- reputation ---
def _published(registry: Path, did: str) -> list[dict]:
    """Certificates in the registry receipted by `did`, oldest first."""
    out = []
    for p in sorted(Path(registry).glob("*/*/certificate.json")):
        c = _read(p)
        if c and (c.get("receipt") or {}).get("agent_id") == did:
            out.append(c)
    return sorted(out, key=lambda c: c["receipt"]["ts"])


def _status_get(ZC):
    """The SDK's HTTP as the (status, body) getter zanii.a2a_directory expects."""
    def get(url):
        try:
            return 200, ZC._http_json(url)
        except RuntimeError as e:           # the SDK names the HTTP status in its error
            return (404 if str(e).startswith("HTTP 404 ") else 502), None
    return get


def trust_summary(identity: dict, registry: Path, ledger: str | None = None) -> dict:
    """The aggregate a CV or an underwriter reads: the ledger's own count when the
    certificates are anchored there (the agent cannot inflate it), else the local
    registry, labelled as such."""
    did = identity["agent"]["did"]
    if ledger:
        from zanii import client as ZC
        from zanii.cv import resolve_agent
        t = resolve_agent(did, server=ledger, get=_status_get(ZC))
        if t is not None:                   # the delegation names the owner when the ledger doesn't
            return {"owner": identity["delegation"][0]["issuer"], **t,
                    "source": f"ledger {ledger}"}
    certs = _published(registry, did)
    ts = [c["receipt"]["ts"] for c in certs]
    return {"agent_id": did, "owner": identity["delegation"][0]["issuer"], "receipts": len(certs),
            "distinct_counterparties": 0, "first_ts": ts[0] if ts else None,
            "last_ts": ts[-1] if ts else None, "revoked": False, "fully_anchored": False,
            "source": "local registry (not ledger-attested)"}


def harness_cv(identity: dict, registry: Path, ledger: str | None = None) -> dict:
    """A signed AgentCV for the identity that certifies harnesses: each published
    certificate is a benchmark entry pointing at its receipt."""
    zc = R._zanii()
    from zanii import cv as CV
    from zanii.delegation import cert_hash
    t = trust_summary(identity, registry, ledger)
    summary = {"receipts": t["receipts"], "distinct_counterparties": t["distinct_counterparties"],
               "first_seen": t["first_ts"], "last_seen": t["last_ts"], "revoked": t["revoked"],
               "fully_anchored": t["fully_anchored"], "source": t["source"]}
    benchmarks = [{"name": f"{c['domain']}@{str(c['incumbent']['commit'])[:8]}",
                   "score": f"{c['incumbent']['S']:.4f}",          # protocol objects: no floats
                   "gain": None if c["gain"] is None else f"{c['gain']:+.4f}",
                   "above_noise": bool(c["gain_exceeds_noise"]),
                   "certificate": c["digest"], "receipt": zc.receipt_hash(c["receipt"])}
                  for c in _published(registry, identity["agent"]["did"])]
    d = identity["delegation"][0]
    cv = CV.build_cv(did=identity["agent"]["did"], owner=d["issuer"], summary=summary,
                     generated_at=R._now(), benchmarks=benchmarks,
                     authority=[{"cert": cert_hash(d), "scope": s} for s in d["scopes"]],
                     skills=[R.RECEIPT_TARGET, R.GAIN_TARGET])
    return CV.sign_cv(cv, _sk(identity))


def risk_profile(identity: dict, registry: Path, ledger: str | None = None) -> dict:
    """Underwriting facts about the signing identity (zanii.insurance): never a
    score. Thin, stale, unanchored or wildcard authority is flagged for the insurer."""
    R._zanii()
    from zanii import insurance as I
    t = trust_summary(identity, registry, ledger)
    p = I.build_risk_profile(t, as_of=R._now(), delegation=identity["delegation"])
    return {**p, "source": t["source"], "profile_hash": I.risk_profile_hash(p)}


# ---------------------------------------------------------------------- sla ---
def sla_create(identity: dict, client: str, start: str, end: str, min_certificates: int,
               price: str | None = None, currency: str = "USD", gains_only: bool = True) -> dict:
    """Terms for evolve-as-a-service, signed by the provider (this identity): at
    least `min_certificates` certified gains (or any certificates) in the window.
    The client countersigns with `sla_sign`."""
    R._zanii()
    from zanii import sla as SL
    body = SL.build_sla_body(
        provider=identity["agent"]["did"], client=client,
        scope=R.GAIN_TARGET if gains_only else "evolve.report.*",
        window={"start": start, "end": end}, commitments={"min_actions": int(min_certificates)},
        price={"amount": str(price), "currency": currency} if price else None,  # exact minor units
        created_at=R._now())
    return SL.assemble_sla(body, [SL.sign_sla(body, identity["agent"]["did"], _sk(identity))])


def sla_sign(sla: dict, identity: dict) -> dict:
    from zanii import sla as SL
    did = identity["agent"]["did"]
    if did not in (sla["body"]["provider"], sla["body"]["client"]):
        raise ValueError("only the provider or the client signs this agreement")
    sigs = [s for s in sla["signatures"] if s.get("did") != did]
    return SL.assemble_sla(sla["body"], sigs + [SL.sign_sla(sla["body"], did, _sk(identity))])


def sla_assess(sla: dict, run_dir: Path, as_of: str | None = None) -> dict:
    """Compliance computed from the provider's own certificate receipt chain: hiding
    a receipt breaks the chain, so a missed commitment cannot be trimmed away."""
    from zanii import sla as SL
    v = SL.verify_sla(sla)
    if not v.get("ok"):
        return {"ok": False, "breaches": [{"kind": "INVALID_AGREEMENT",
                                           "reasons": v.get("reasons")}]}
    chain = [r for r in _jsonl(Path(run_dir) / "receipts.jsonl")
             if r.get("agent_id") == sla["body"]["provider"]]
    return SL.assess_compliance(sla, chain, as_of=as_of)


# ------------------------------------------------------------------- escrow ---
def escrow_create(identity: dict, payer: str, amount: str, currency: str, deadline: str,
                  arbiters: list[str] | None = None, threshold: int = 1) -> dict:
    """Pay-per-certified-gain terms: funds are released only against a ledger-proven
    `evolve.report.gain` receipt from this identity before `deadline`. Zanii never
    holds funds; this is the verifiable agreement. Signed by the payee here; the
    payer adds a signature with `escrow_sign`."""
    R._zanii()
    from zanii import escrow as E
    body = E.build_escrow_body(
        payer=payer, payee=identity["agent"]["did"], amount=amount, currency=currency,
        criteria={"target": R.GAIN_TARGET, "deadline": deadline,
                  "agent": identity["agent"]["did"]},
        arbiters={"dids": arbiters, "threshold": threshold} if arbiters else None,
        created_at=R._now())
    return E.assemble_escrow(body, [E.sign_escrow(body, identity["agent"]["did"], _sk(identity))])


def escrow_sign(escrow: dict, identity: dict) -> dict:
    from zanii import escrow as E
    did = identity["agent"]["did"]
    sigs = [s for s in escrow["signatures"] if s.get("did") != did]
    return E.assemble_escrow(escrow["body"], sigs + [E.sign_escrow(escrow["body"], did,
                                                                    _sk(identity))])


def escrow_check(escrow: dict, cert: dict, ledger: str) -> dict:
    """Release verdict for a certificate: its receipt must meet every criterion and
    be Merkle-proven in the ledger. A certificate kept only locally never releases."""
    zc = R._zanii()
    from zanii import client as ZC
    from zanii import escrow as E
    h = zc.receipt_hash(cert["receipt"])
    try:
        body = ZC._http_json(f"{ledger}/v1/proof/{h}")
        evidence = {"sth": body["sth"], "index": body["index"], "proof": body["proof"]}
    except Exception:                       # not in the log: no evidence, so no release
        evidence = None
    verdict = E.check_release(escrow, cert["receipt"], evidence)
    ok, why = R.verify(cert)
    if not ok:                              # the receipt must cover this exact certificate
        verdict = {"ok": False, "reasons": verdict.get("reasons", []) + [f"certificate: {why}"]}
    return {**verdict, "receipt": h}


def escrow_release(escrow: dict, cert: dict, identity: dict) -> dict:
    """A signed release instruction from the payer (or an arbiter) for this
    certificate's receipt; settlement then happens on the payment rail."""
    zc = R._zanii()
    from zanii import escrow as E
    body = E.build_release(escrow, receipt_hash=zc.receipt_hash(cert["receipt"]), ts=R._now())
    instr = E.assemble_instruction(body, [E.sign_instruction(body, identity["agent"]["did"],
                                                             _sk(identity))])
    v = E.verify_instruction(instr, escrow)
    if not v.get("ok"):
        raise ValueError(f"this identity cannot authorize the release: {v.get('reasons')}")
    return instr


# -------------------------------------------------------------------- audit ---
def audit(domain: str, run_dir: Path, registry: Path, owner: str | None = None,
          bundle: dict | None = None, memory_dir: Path | None = None) -> str:
    """An auditor's Markdown report over everything a domain has evidence for:
    certificates, the receipt chains, live rollouts, data governance and memory.
    With a ledger audit bundle (`zanii bundle`), the ledger's own compliance
    report (zanii.compliance) is appended."""
    zc = R._zanii()
    from zanii.client import payload_bytes

    from . import live
    run_dir = Path(run_dir)
    lines = [f"# Audit report: {domain}", "", f"Generated {R._now()}"
             + (f" against owner `{owner}`" if owner else " (owner NOT pinned: pass --owner)"), ""]

    def section(title, rows):
        lines.extend([f"## {title}", ""] + (rows or ["- nothing recorded"]) + [""])

    rows = []
    for p in sorted((Path(registry) / domain).glob("*/certificate.json")):
        ok, why = R.verify(_read(p), owner=owner)
        c = _read(p)
        rows.append(f"- `{p.parent.name}` gain {c.get('gain')}, above noise "
                    f"{c.get('gain_exceeds_noise')}: {'PASS' if ok else 'FAIL'} ({why})")
    section("Published certificates", rows)
    if (run_dir / "receipts.jsonl").exists():
        ok, why = R.verify_chain(run_dir, owner=owner)
        section("Certificate receipt chain", [f"- {'PASS' if ok else 'FAIL'}: {why}"])
    else:
        section("Certificate receipt chain", [])
    try:
        con = load_constitution(domain)
    except ValueError as e:
        con, lines_c = None, [f"- FAIL: {e}"]
    else:
        lines_c = ([f"- `{con['name']}`, {len(con['rules'])} rules, default {con['default']}"]
                   if con else [])
    section("Rulebook", lines_c)
    rep = live.audit(run_dir, owner, con)
    rows = [f"- {rep['decisions']} signed rollout decisions, {rep['session_receipts']} session "
            f"receipts: {'PASS' if rep['ok'] else 'FAIL'}"]
    rows += [f"  - {p}" for p in rep["problems"]]
    if rep.get("governance"):
        g = rep["governance"]
        rows.append(f"- governance: {g['forbidden']} forbidden, {g['ungoverned']} ungoverned, "
                    f"{g['approvals_required']} needing approval")
    section("Live rollouts", rows)
    data = _jsonl(run_dir / "data_receipts.jsonl")
    rows = []
    if data:
        chain = zc.verify_receipt_chain([d["receipt"] for d in data], expected_owner=owner)
        bad = sum(d["receipt"]["payload_hash"] != zc.hash_payload(payload_bytes(d["payload"]))
                  for d in data)
        rows.append(f"- {len(data)} signed data attestations, chain "
                    f"{'intact' if chain.ok else 'BROKEN: ' + str(chain.error)}"
                    + (f", {bad} payloads not matching their receipts" if bad else ""))
        rows += [f"  - {d['payload'].get('action')} at {d['payload'].get('executed_at')} "
                 f"({d['payload'].get('policy')})" for d in data]
    section("Client data", rows)
    if memory_dir:
        try:
            m = verify_memory(Path(memory_dir))
            section("Provable memory", [f"- {m['entries']} entries, head `{m['head'][:20]}`: "
                                        f"{'PASS' if m['ok'] else 'FAIL'}"]
                    + [f"  - {p}" for p in m["problems"]])
        except LookupError as e:
            section("Provable memory", [f"- {e}"])
    if bundle is not None:
        from zanii.compliance import build_compliance_report, render_compliance_markdown
        lines.append(render_compliance_markdown(build_compliance_report(bundle)))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------- pq transition ---
PQ_TARGET = "evolve.pq.transition"


def anchor_identity(identity: dict, ledger: str, api_key: str | None, out: Path) -> dict:
    """Anchor the identity's post-quantum key binding on the ledger (zanii.pq): the
    log's timestamp then proves the ML-DSA-65 key was bound to this did:key before
    any future Ed25519 break. The receipt commits to the binding unsalted, so anyone
    can recompute it; the transition, its Merkle evidence and the verdict go to `out`.

    The receipt is built directly rather than through ZaniiAgent.record(): record()
    JCS-encodes every payload, while zanii.pq hashes the binding's compact JSON
    string, so a record()ed transition could never verify (zanii 0.25.1)."""
    import urllib.parse

    zc = R._zanii()
    from zanii import client as ZC
    from zanii import pq as PQ
    if not identity.get("pq"):
        raise ValueError("this identity has no post-quantum key (create it with identity --pq)")
    if not api_key:
        raise ValueError("writing to the ledger needs ZANII_API_KEY or ZANII_API_KEY_FILE")
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} exists: this identity's binding is already anchored")
    did, binding = identity["agent"]["did"], identity["pq"]["binding"]
    head = ZC._http_json(f"{ledger}/v1/head/{urllib.parse.quote(did, safe='')}").get("head")
    r = zc.create_receipt(agent_id=did, delegation=identity["delegation"], action="attest",
                          target=PQ_TARGET, payload_hash=zc.hash_payload(PQ.transition_payload(binding)),
                          ts=R._now(), prev=head, agent_private_key=_sk(identity))
    res = ZC._http_json(f"{ledger}/v1/receipts", {"receipts": [r]}, api_key=api_key)
    status = ((res or {}).get("results") or [{}])[0]
    if status.get("status") not in ("accepted", "duplicate"):
        raise RuntimeError(f"ledger rejected the transition receipt: {status}")
    h = zc.receipt_hash(r)
    body = ZC._http_json(f"{ledger}/v1/proof/{urllib.parse.quote(h, safe='')}")
    evidence = {"sth": body["sth"], "index": body["index"], "proof": body["proof"]}
    verdict = PQ.verify_transition(binding, r, evidence)
    record = {"ledger": ledger, "receipt": r, "receipt_hash": h, "binding": binding,
              "evidence": evidence, "verdict": verdict}
    _write(out, record)
    return {"receipt_hash": h, "index": evidence["index"], "ledger_size": evidence["sth"]["size"],
            "ok": verdict["ok"], "reasons": verdict["reasons"], "saved": str(out)}


def verify_anchored_identity(path: Path) -> dict:
    """Re-check a saved transition offline: binding, receipt and Merkle evidence."""
    from zanii import pq as PQ
    rec = _read(path)
    return PQ.verify_transition(rec["binding"], rec["receipt"], rec["evidence"])
