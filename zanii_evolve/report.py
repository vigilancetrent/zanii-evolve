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
"""Generalization certificate: the audit evidence of one run, in one file.

    certificate = {incumbent vs H_0, noise band, every accepted edit with its
                   hypothesis and measured change, candidate outcomes, held-out
                   evaluations, hyperparameters}
    digest      = sha256 of the canonical JSON body
    signature   = HMAC-SHA256(ZANII_EVOLVE_SIGNING_KEY, digest)   when the key is set
    receipt     = Zanii proof-of-action receipt over the digest   when issued with
                  an identity (ZANII_EVOLVE_IDENTITY, `pip install -e ".[provable]"`)
    disclosure  = per-field salted commitment (zanii.redact); the receipt signs
                  "<digest>|<root>", so `public_view` can prove chosen fields
                  (the gain, say) without revealing the rest
    pq          = Ed25519 + ML-DSA-65 dual signature over the digest (zanii.pq),
                  when the identity carries a post-quantum key
    constitution= hash of the domain's signed rulebook, when it has one

With ZANII_LEDGER_URL set (and ZANII_API_KEY or ZANII_API_KEY_FILE), the receipt is recorded on that
Zanii ledger instead of only locally, so its time and order cannot be rewritten;
`verify(..., ledger=url)` checks its Merkle inclusion.

Everything is read from runs/<domain>/; nothing is re-measured. `verify` checks
that a certificate was not edited after it was issued. HMAC proves the issuer
only to holders of the shared key; the receipt is an Ed25519 signature under an
owner-delegated did:key, so anyone can check it offline with the owner's DID.
Issued receipts are hash-linked in runs/<domain>/receipts.jsonl.

Identity file (the zanii.mcp_proxy format; `zanii_evolve.py identity` writes one):
    {"agent": {"did": "did:key:...", "privateKey": "<hex>"}, "delegation": [<cert>]}
A sealed file carries {"did", "sealed"} in place of privateKey (zanii.kms, scrypt +
AES-256-GCM) and is opened with ZANII_EVOLVE_IDENTITY_PASSPHRASE.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

SCHEMA = "zanii-evolve-certificate/3"
RECEIPT_ACTION, RECEIPT_TARGET, SCOPE = "certificate_issued", "evolve.report", "evolve.*"
DOMAINS = Path(__file__).resolve().parent.parent / "domains"
# a certificate whose gain clears the noise band is receipted on its own target, so an
# escrow or SLA can require "a certified gain" from the ledger alone
GAIN_TARGET = RECEIPT_TARGET + ".gain"


def _zanii():
    try:
        import zanii.core as zc
    except ImportError as e:
        raise RuntimeError('proof-of-action receipts need the zanii package: '
                           'pip install -e ".[provable]"') from e
    return zc


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _passphrase(what: str) -> str:
    pw = os.environ.get("ZANII_EVOLVE_IDENTITY_PASSPHRASE")
    if not pw:
        raise RuntimeError(f"{what} is sealed: set ZANII_EVOLVE_IDENTITY_PASSPHRASE")
    return pw


def seal_key(did: str, private_key: bytes, passphrase: str) -> dict:
    """{"did", "sealed"}: only the private key is encrypted (zanii.kms)."""
    _zanii()
    from zanii import kms
    return kms.seal_identity({"did": did, "private_key": private_key}, passphrase)


def open_key(obj: dict, what: str) -> bytes:
    """The private key of a {"did", "privateKey" | "private_key" | "sealed"} entry."""
    if "sealed" in obj:
        _zanii()
        from zanii import kms
        try:
            return kms.open_identity(obj, _passphrase(what))["private_key"]
        except (ValueError, KeyError) as e:
            raise RuntimeError(f"{what}: wrong passphrase or damaged seal") from e
        except Exception as e:              # cryptography's InvalidTag on a wrong passphrase
            raise RuntimeError(f"{what}: wrong passphrase or damaged seal") from e
    return bytes.fromhex(obj.get("privateKey") or obj["private_key"])


def load_identity(path: str | None = None) -> dict | None:
    """The signing identity from `path` or ZANII_EVOLVE_IDENTITY (None when unset);
    a sealed file is opened here, so callers always see agent.privateKey."""
    path = path or os.environ.get("ZANII_EVOLVE_IDENTITY")
    if not path:
        return None
    ident = json.loads(Path(path).read_text(encoding="utf-8"))
    agent = ident.get("agent") or {}
    if not (agent.get("did") and (agent.get("privateKey") or agent.get("sealed"))
            and ident.get("delegation")):
        raise ValueError(f"{path}: need agent.did, agent.privateKey (or sealed) and delegation")
    ident["agent"] = {"did": agent["did"], "privateKey": open_key(agent, path).hex()}
    pq = ident.get("pq")
    if pq and "sealed_secret" in pq:
        from zanii import kms
        try:
            secret = kms.open(pq["sealed_secret"], _passphrase(path))
        except Exception as e:              # InvalidTag on a wrong passphrase
            raise RuntimeError(f"{path}: wrong passphrase or damaged seal") from e
        ident["pq"] = {k: v for k, v in pq.items() if k != "sealed_secret"}
        ident["pq"]["secret_key"] = secret.hex()
    return ident


def new_identity(exp: str = "2031-01-01T00:00:00Z", passphrase: str | None = None,
                 pq: bool = False) -> tuple[dict, dict]:
    """(owner, identity): an owner key (keep it offline) and a signing identity
    the owner delegated `evolve.*` to. With a passphrase both private keys are
    sealed; with pq the identity also gets an ML-DSA-65 key bound to its did:key."""
    zc = _zanii()
    owner, agent = zc.generate_keypair(), zc.generate_keypair()
    from .custody import SCOPES as CUSTODY
    cert = zc.create_cert(issuer=owner.did, subject=agent.did, scopes=[SCOPE, *CUSTODY], exp=exp,
                          issuer_private_key=owner.private_key)
    ident = {"agent": {"did": agent.did, "privateKey": agent.private_key.hex()},
             "delegation": [cert]}
    if pq:
        from zanii import pq as PQ
        pk, sk = PQ.generate_pq_keypair()
        ident["pq"] = {"public_key": pk.hex(), "secret_key": sk.hex(),
                       "binding": PQ.bind_pq_key(did=agent.did, pq_public_key=pk, ts=_now(),
                                                 ed_private_key=agent.private_key,
                                                 pq_secret_key=sk)}
    owner_out = {"did": owner.did, "private_key": owner.private_key.hex()}  # zanii keygen format
    if passphrase:
        from zanii import kms
        owner_out = seal_key(owner.did, owner.private_key, passphrase)
        ident["agent"] = seal_key(agent.did, agent.private_key, passphrase)
        if pq:
            ident["pq"]["sealed_secret"] = kms.seal(bytes.fromhex(ident["pq"].pop("secret_key")),
                                                    passphrase)
    return owner_out, ident


def task_ref(task_id: str) -> str:
    """A task as a training-manifest ref: a hash, never content."""
    return "sha256:" + hashlib.sha256(f"task:{task_id}".encode()).hexdigest()


def _tasks_manifest(run_dir: Path, domain: str, base: dict, created_at: str) -> dict | None:
    """The evolve tasks H_0 was measured on, committed as a sorted-Merkle manifest."""
    ev = _read(run_dir / "jobs" / str(base.get("job")) / "eval.json") if base.get("job") else None
    ids = sorted((ev or {}).get("per_task") or {})
    if not ids:
        return None
    try:
        from zanii import train
    except ImportError:
        return None
    refs = sorted(task_ref(t) for t in ids)
    m = train.build_manifest(run_id=f"{domain}:{base.get('commit')}", refs=refs,
                             created_at=created_at)
    return {"manifest": m, "refs": refs, "hash": train.manifest_hash(m)}


def prove_task(cert: dict, task_id: str) -> dict:
    """Proof that a task was, or was not, in the set this certificate trained on."""
    t = cert.get("tasks")
    if not t:
        raise ValueError("this certificate carries no task manifest (or is a public view)")
    from zanii import train
    ref = task_ref(task_id)
    present = ref in set(t["refs"])
    proof = (train.prove_membership if present else train.prove_absence)(t["manifest"], t["refs"], ref)
    return {"task_ref": ref, "present": present, "proof": proof, "manifest": t["manifest"],
            "certificate_digest": cert["digest"]}


def verify_task_proof(p: dict, cert: dict | None = None) -> tuple[bool, str]:
    """Check a prove_task result; with the certificate, also that its manifest is the
    one the certificate signed."""
    from zanii import train
    if cert is not None:
        signed = (cert.get("tasks_manifest") or {}).get("hash")
        if signed != train.manifest_hash(p["manifest"]):
            return False, "manifest is not the one this certificate signed"
    ok = (train.verify_membership if p["present"] else train.verify_absence)(p["proof"], p["manifest"])
    if not ok or p["proof"].get("ref", p["task_ref"]) != p["task_ref"]:
        return False, "proof does not verify"
    return True, ("the task WAS in the training set" if p["present"]
                  else "the task was NOT in the training set")


def constitution_ref(domain: str) -> str | None:
    """Hash of domains/<domain>/constitution.signed.json, when the domain has one."""
    p = DOMAINS / str(domain) / "constitution.signed.json"
    if not p.is_file():
        return None
    _zanii()
    from zanii import constitution as K
    c = json.loads(p.read_text(encoding="utf-8"))
    if not K.verify_constitution(c):
        raise ValueError(f"{p}: signature invalid; re-run `constitution sign`")
    return K.constitution_hash(c)


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _records(p: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return []


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def build(run_dir: Path, key: str | None = None, identity: dict | None = None,
          prev: str | None = None) -> dict:
    """Certificate for the run stored in run_dir (runs/<domain>). With an
    identity it carries a receipt over the digest, linked to `prev`."""
    run_dir = Path(run_dir)
    fr = _read(run_dir / "frontier.json")
    if not fr:
        raise LookupError(f"no frontier in {run_dir}; run `baseline` first")
    cfg = fr.get("config") or {}
    delta = cfg.get("delta")
    if delta is None:
        delta = (_read(run_dir / "calibration.json", {}) or {}).get("delta")
    traj = fr.get("trajectory") or []
    base, inc = (traj[0] if traj else {}), fr["incumbent"]
    gain = inc["S"] - base["S"] if base else None
    hist = _records(run_dir / "history.jsonl")
    outcomes: dict[str, int] = {}
    seen = set()
    for r in hist:
        cand = (r.get("t"), r.get("variant"))
        if r.get("variant") in (None, "-") or cand in seen:
            continue                     # one count per candidate, not per edit
        seen.add(cand)
        o = r.get("outcome") or "unknown"
        outcomes[o] = outcomes.get(o, 0) + 1
    heldout = []
    for p in sorted((run_dir / "jobs").glob("heldout_*/eval.json")):
        ev = _read(p, {}) or {}
        heldout.append({"label": p.parent.name[len("heldout_"):], "S": ev.get("S"),
                        "C": ev.get("C"), "missing": ev.get("missing"),
                        "n_expected": ev.get("n_expected")})
    confirmations = _records(run_dir / "confirmations.jsonl")
    both = [c for c in confirmations
            if c.get("S_selected") is not None and c.get("S_confirmed") is not None]
    shrinkage = (sum(c["S_selected"] - c["S_confirmed"] for c in both) / len(both)
                 if both else None)
    judge = _read(run_dir / "judge_agreement.json")
    issued_at = _now()
    tasks = _tasks_manifest(run_dir, fr.get("domain") or run_dir.name, base, issued_at)
    body = {
        "schema": SCHEMA,
        "domain": fr.get("domain") or run_dir.name,
        "issued_at": issued_at,
        "baseline": {k: base.get(k) for k in ("S", "C", "commit")},
        "incumbent": {k: inc.get(k) for k in ("t", "S", "C", "commit", "harness_tree")},
        "gain": gain,
        "cost_change": ((inc["C"] - base["C"]) / base["C"]
                        if base.get("C") and inc.get("C") else None),
        "noise_band": delta,
        "gain_exceeds_noise": (gain is not None and delta is not None and gain > delta),
        "rounds": max(0, len(traj) - 1),
        "accepted_edits": [{k: r.get(k) for k in ("t", "variant", "component", "hypothesis",
                                                   "delta_S", "delta_C")}
                           for r in hist if r.get("accepted") and r.get("edit_id")],
        "candidate_outcomes": outcomes,
        "heldout": heldout,
        # 0.2: every winner measured again on fresh trials, and how much its selected
        # score overstated it (the winner's curse), mean over the confirmed ones
        "confirmations": confirmations,
        "selection_shrinkage": shrinkage,
        # 0.2: the rubric judge as checked against people, when it was
        "judge": judge,
        # 0.3: the signed rulebook the harness was evolved under, when the domain has one
        "constitution": constitution_ref(fr.get("domain") or run_dir.name),
        # 0.3: a Merkle manifest of the exact tasks the cycle trained on (zanii.train): any
        # record can be proven in or out of it against this signed hash
        "tasks_manifest": ({"hash": tasks["hash"], "size": tasks["manifest"]["size"]}
                           if tasks else None),
        "config": cfg,
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    cert = {**body, "digest": digest}
    if tasks:                               # the refs make proofs; they stay with the issuer
        cert["tasks"] = {"manifest": tasks["manifest"], "refs": tasks["refs"]}
    key = key if key is not None else os.environ.get("ZANII_EVOLVE_SIGNING_KEY")
    if key:
        cert["signature"] = hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest()
    if identity:
        zc = _zanii()
        from zanii import redact
        # protocol objects take no floats: each field is committed as its canonical JSON
        com = redact.commit({k: _canonical(v).decode() for k, v in body.items()})
        cert["disclosure"] = com            # holds the salts: the full certificate stays private
        signed = f"{digest}|{com['envelope']['root']}"
        sk = bytes.fromhex(identity["agent"]["privateKey"])
        ledger = os.environ.get("ZANII_LEDGER_URL")
        target = GAIN_TARGET if body["gain_exceeds_noise"] else RECEIPT_TARGET
        if ledger:
            cert["receipt"] = _anchor(identity, signed, ledger, target)
        else:
            cert["receipt"] = zc.create_receipt(
                agent_id=identity["agent"]["did"], delegation=identity["delegation"],
                action=RECEIPT_ACTION, target=target,
                payload_hash=zc.hash_payload(signed), ts=body["issued_at"], prev=prev,
                agent_private_key=sk)
        if identity.get("pq"):
            from zanii import pq as PQ
            cert["pq"] = {"binding": identity["pq"]["binding"], "sigs": PQ.dual_sign(
                {"digest": digest}, sk, bytes.fromhex(identity["pq"]["secret_key"]))}
    return cert


def ledger_api_key() -> str | None:
    """ZANII_API_KEY, or the first zk_live_ key in the file ZANII_API_KEY_FILE names
    (a bare key, or `name = value` lines), so the key never sits in a shell history."""
    if os.environ.get("ZANII_API_KEY"):
        return os.environ["ZANII_API_KEY"]
    path = os.environ.get("ZANII_API_KEY_FILE")
    if not path:
        return None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        value = line.split("=", 1)[-1].split(":", 1)[-1].strip() if not line.lstrip().startswith("#") else ""
        if value.startswith("zk_live_"):
            return value
    raise ValueError(f"{path}: no zk_live_ ingest key in it")


def _anchor(identity: dict, signed: str, ledger: str, target: str = RECEIPT_TARGET) -> dict:
    """Record the certificate receipt on a Zanii ledger, which fixes its time and order."""
    from zanii.client import ZaniiAgent
    agent = ZaniiAgent(server_url=ledger, agent_did=identity["agent"]["did"],
                       agent_private_key=bytes.fromhex(identity["agent"]["privateKey"]),
                       delegation=identity["delegation"], api_key=ledger_api_key())
    try:
        receipt, _ = agent.record(target=target, payload=signed, action=RECEIPT_ACTION,
                                  salt=False)
        agent.flush()
    except Exception as e:                  # the SDK raises its own transport errors
        raise RuntimeError(f"ledger {ledger} did not take the receipt: {e}") from e
    return receipt


def issue(run_dir: Path, identity: dict | None = None, key: str | None = None) -> dict:
    """build() for a certificate that is handed out: with an identity, its
    receipt is chained onto runs/<domain>/receipts.jsonl."""
    run_dir = Path(run_dir)
    identity = identity if identity is not None else load_identity()
    log = run_dir / "receipts.jsonl"
    prev = None
    if identity:
        chain = _records(log)
        prev = _zanii().receipt_hash(chain[-1]) if chain else None
    cert = build(run_dir, key=key, identity=identity, prev=prev)
    if "receipt" in cert:
        # ponytail: single writer assumed (one CLI issuing at a time); add a file lock
        # if certificates are ever issued concurrently for the same run
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(cert["receipt"], ensure_ascii=False) + "\n")
    return cert


def verify_chain(run_dir: Path, owner: str | None = None,
                 revoked: set[str] | None = None) -> tuple[bool, str]:
    """Every issued receipt valid and hash-linked (nothing removed or edited); with
    `revoked`, a receipt under a revoked delegation fails."""
    chain = _records(Path(run_dir) / "receipts.jsonl")
    if not chain:
        return False, "no receipts issued for this run"
    r = _zanii().verify_receipt_chain(chain, expected_owner=owner, revoked=revoked)
    if not r.ok:
        return False, f"receipt chain broken at {r.failed_index}: {r.error}"
    return True, f"{len(chain)} receipts, chain intact"


def _verify_receipt(rc: dict, digest: str, owner: str | None,
                    envelope: dict | None = None) -> tuple[bool, str]:
    zc = _zanii()
    if rc.get("action") != RECEIPT_ACTION or rc.get("target") not in (RECEIPT_TARGET, GAIN_TARGET):
        return False, "receipt is not a certificate receipt"
    signed = f"{digest}|{envelope['root']}" if envelope else digest     # schema 2: digest only
    from zanii.client import payload_bytes                              # a ledger record() encodes
    if rc.get("payload_hash") not in (zc.hash_payload(signed),          # the string as JSON
                                      zc.hash_payload(payload_bytes(signed))):
        return False, "receipt covers a different certificate"
    r = zc.verify_receipt(rc, expected_owner=owner)
    if not r.ok:
        return False, f"receipt invalid: {r.error}"
    issuer = rc["delegation"][0]["issuer"]
    if owner:
        return True, f"receipt ok, issued under owner {issuer}"
    # without a pinned owner anyone could mint an owner key and sign: say so
    return True, (f"receipt self-consistent but owner {issuer} NOT pinned "
                  f"(pass --owner <did> to check who issued it)")


UNSIGNED = ("digest", "signature", "receipt", "disclosure", "pq", "tasks")


def verify(cert: dict, key: str | None = None, owner: str | None = None,
           ledger: str | None = None) -> tuple[bool, str]:
    if cert.get("public"):
        return verify_public(cert, owner, ledger)
    body = {k: v for k, v in cert.items() if k not in UNSIGNED}
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    if not hmac.compare_digest(digest, str(cert.get("digest", ""))):
        return False, "digest mismatch: the certificate was edited after it was issued"
    notes = ["digest ok"]
    key = key if key is not None else os.environ.get("ZANII_EVOLVE_SIGNING_KEY")
    if "signature" in cert:
        want = hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest() if key else None
        if want is None:
            notes.append("HMAC not checked (set ZANII_EVOLVE_SIGNING_KEY)")
        elif not hmac.compare_digest(want, str(cert["signature"])):
            return False, "signature mismatch: wrong key or forged certificate"
        else:
            notes.append("HMAC ok")
    if "receipt" in cert:
        disc = cert.get("disclosure")
        if disc and not _envelope_matches(disc, body):
            return False, "disclosure commitment does not match the certificate"
        ok, why = _verify_receipt(cert["receipt"], digest, owner,
                                  disc["envelope"] if disc else None)
        if not ok:
            return False, why
        if (cert["receipt"]["target"] == GAIN_TARGET) != bool(body.get("gain_exceeds_noise")):
            return False, "receipt target does not match the certificate's own gain verdict"
        notes.append(why)
        for extra in (_verify_pq(cert, digest), _verify_ledger(cert["receipt"], ledger)):
            if extra is not None:
                if not extra[0]:
                    return False, extra[1]
                notes.append(extra[1])
    elif owner:
        return False, "no receipt: this certificate cannot prove its issuer"
    if "signature" not in cert and "receipt" not in cert:
        notes.append("unsigned")
    return True, "; ".join(notes)


def _envelope_matches(disc: dict, body: dict) -> bool:
    """The committed values are exactly this certificate's own fields."""
    fields = (disc.get("secrets") or {}).get("fields") or {}
    return (set(fields) == set(body) and disc["envelope"].get("keys") == sorted(body)
            and all(fields[k]["value"] == _canonical(v).decode() for k, v in body.items()))


def _verify_pq(cert: dict, digest: str) -> tuple[bool, str] | None:
    if "pq" not in cert:
        return None
    from zanii import pq as PQ
    binding = cert["pq"]["binding"]
    if not PQ.verify_pq_binding(binding).get("ok"):
        return False, "post-quantum key binding invalid"
    did = cert["receipt"].get("agent_id") or binding.get("did")
    r = PQ.verify_dual({"digest": digest}, did, binding, cert["pq"]["sigs"])
    if not r.get("ok"):
        return False, f"post-quantum dual signature invalid: {r.get('reasons')}"
    return True, "Ed25519 + ML-DSA-65 dual signature ok"


def _verify_ledger(receipt: dict, ledger: str | None) -> tuple[bool, str] | None:
    if not ledger:
        return None
    from zanii.client import fetch_and_verify_proof
    try:
        r = fetch_and_verify_proof(ledger, _zanii().receipt_hash(receipt))
    except Exception as e:                  # transport errors from the SDK
        return False, f"ledger {ledger} could not prove inclusion: {e}"
    if not r.ok:
        return False, f"ledger inclusion failed: {[c for c in r.checks if not c.get('ok')]}"
    return True, f"included in the ledger at {ledger}"


def public_view(cert: dict, fields: list[str]) -> dict:
    """A shareable certificate that proves only `fields`: each comes with a Merkle
    disclosure against the envelope the receipt signed; the rest stays committed."""
    if not cert.get("disclosure") or not cert.get("receipt"):
        raise ValueError("a public view needs a certificate issued with an identity")
    from zanii import redact
    unknown = [f for f in fields if f not in cert["disclosure"]["secrets"]["fields"]]
    if unknown:
        raise ValueError(f"no such certificate fields: {unknown}")
    view = {"public": True, "schema": cert.get("schema"), "digest": cert["digest"],
            "envelope": cert["disclosure"]["envelope"], "receipt": cert["receipt"],
            "disclosures": [redact.disclose(cert["disclosure"], f) for f in fields]}
    if "pq" in cert:
        view["pq"] = cert["pq"]
    return view


def disclosed(view: dict) -> dict:
    """The field values a public view discloses."""
    return {d["key"]: json.loads(d["value"]) for d in view.get("disclosures", [])}


def verify_public(view: dict, owner: str | None = None,
                  ledger: str | None = None) -> tuple[bool, str]:
    from zanii import redact
    for d in view.get("disclosures", []):
        if d.get("root") != view["envelope"]["root"] or not redact.verify_disclosure(
                d, view["envelope"]):
            return False, f"disclosure of {d.get('key')!r} does not match the signed envelope"
    ok, why = _verify_receipt(view["receipt"], view["digest"], owner, view["envelope"])
    if not ok:
        return False, why
    notes = [f"{len(view.get('disclosures', []))} disclosed fields proven", why]
    for extra in (_verify_pq(view, view["digest"]), _verify_ledger(view["receipt"], ledger)):
        if extra is not None:
            if not extra[0]:
                return False, extra[1]
            notes.append(extra[1])
    return True, "; ".join(notes)


def _f(x, fmt="{:.4f}"):
    return "–" if x is None else fmt.format(x)


def markdown(cert: dict) -> str:
    lines = [f"# Generalization certificate: {cert['domain']}", "",
             f"Issued {cert['issued_at']} · digest `{cert['digest'][:16]}…`"
             + (f" · receipt under `{cert['receipt']['delegation'][0]['issuer']}`"
                if cert.get("receipt") else " · HMAC-signed" if cert.get("signature")
                else " · unsigned"), "",
             "| | Score | Cost (tokens/trial) | Commit |", "|---|---|---|---|",
             f"| H_0 | {_f(cert['baseline'].get('S'))} | {_f(cert['baseline'].get('C'), '{:.0f}')} "
             f"| `{str(cert['baseline'].get('commit') or '')[:8]}` |",
             f"| Incumbent (round {cert['incumbent'].get('t')}) | {_f(cert['incumbent'].get('S'))} "
             f"| {_f(cert['incumbent'].get('C'), '{:.0f}')} "
             f"| `{str(cert['incumbent'].get('commit') or '')[:8]}` |", "",
             f"Gain {_f(cert['gain'], '{:+.4f}')} against a noise band of {_f(cert['noise_band'])}: "
             + ("**above noise**." if cert["gain_exceeds_noise"] else "**not above noise**."),
             f"Cost change {_f(cert['cost_change'], '{:+.1%}')} over {cert['rounds']} rounds.", "",
             "## Accepted edits", ""]
    lines += [f"- r{e['t']}{e['variant']} `{e['component']}`: {e['hypothesis']} "
              f"(dS {_f(e['delta_S'], '{:+.4f}')}, dC {_f(e['delta_C'], '{:+.3f}')})"
              for e in cert["accepted_edits"]] or ["- none"]
    lines += ["", "## Candidate outcomes", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(cert["candidate_outcomes"].items())] or ["- none"]
    conf = cert.get("confirmations") or []
    lines += ["", "## Confirmation runs", ""]
    lines += [f"- r{c['t']}{c['variant']}: selected S {_f(c.get('S_selected'))}, on fresh trials "
              f"{_f(c.get('S_confirmed'))}: " + ("held" if c.get("confirmed") else "did not hold")
              for c in conf] or ["- none (confirmation off, or no round had a winner)"]
    if cert.get("selection_shrinkage") is not None:
        lines += ["", (f"Selected scores overstated the confirmed ones by "
                       f"{_f(cert['selection_shrinkage'], '{:+.4f}')} on average.")]
    j = cert.get("judge")
    lines += ["", "## Rubric judge", "",
              (f"- `{j.get('model')}` checked against people: kappa {_f(j.get('kappa'))}, "
               f"agreement {_f(j.get('agreement'), '{:.1%}')} over {j.get('n')} judgements "
               f"({j.get('recorded_at')})") if j else "- not checked against people"]
    lines += ["", "## Held-out and out-of-distribution evaluations", ""]
    lines += [f"- {h['label']}: S {_f(h['S'])}, missing {h['missing']}/{h['n_expected']}"
              for h in cert["heldout"]] or ["- none run yet (`heldout --label <name>`)"]
    return "\n".join(lines) + "\n"
