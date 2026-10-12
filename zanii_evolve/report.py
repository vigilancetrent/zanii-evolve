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

Everything is read from runs/<domain>/; nothing is re-measured. `verify` checks
that a certificate was not edited after it was issued. HMAC proves the issuer
only to holders of the shared key; the receipt is an Ed25519 signature under an
owner-delegated did:key, so anyone can check it offline with the owner's DID.
Issued receipts are hash-linked in runs/<domain>/receipts.jsonl.

Identity file (the zanii.mcp_proxy format; `zanii_evolve.py identity` writes one):
    {"agent": {"did": "did:key:...", "privateKey": "<hex>"}, "delegation": [<cert>]}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

SCHEMA = "zanii-evolve-certificate/1"
RECEIPT_ACTION, RECEIPT_TARGET, SCOPE = "certificate_issued", "evolve.report", "evolve.*"


def _zanii():
    try:
        import zanii.core as zc
    except ImportError as e:
        raise RuntimeError('proof-of-action receipts need the zanii package: '
                           'pip install -e ".[provable]"') from e
    return zc


def load_identity(path: str | None = None) -> dict | None:
    """The signing identity from `path` or ZANII_EVOLVE_IDENTITY (None when unset)."""
    path = path or os.environ.get("ZANII_EVOLVE_IDENTITY")
    if not path:
        return None
    ident = json.loads(Path(path).read_text(encoding="utf-8"))
    if not ((ident.get("agent") or {}).get("did") and ident["agent"].get("privateKey")
            and ident.get("delegation")):
        raise ValueError(f"{path}: need agent.did, agent.privateKey and delegation")
    return ident


def new_identity(exp: str = "2031-01-01T00:00:00Z") -> tuple[dict, dict]:
    """(owner, identity): an owner key (keep it offline) and a signing identity
    the owner delegated `evolve.*` to."""
    zc = _zanii()
    owner, agent = zc.generate_keypair(), zc.generate_keypair()
    cert = zc.create_cert(issuer=owner.did, subject=agent.did, scopes=[SCOPE], exp=exp,
                          issuer_private_key=owner.private_key)
    return ({"did": owner.did, "private_key": owner.private_key.hex()},     # zanii keygen format
            {"agent": {"did": agent.did, "privateKey": agent.private_key.hex()},
             "delegation": [cert]})


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
    body = {
        "schema": SCHEMA,
        "domain": fr.get("domain") or run_dir.name,
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
        "config": cfg,
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    cert = {**body, "digest": digest}
    key = key if key is not None else os.environ.get("ZANII_EVOLVE_SIGNING_KEY")
    if key:
        cert["signature"] = hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest()
    if identity:
        zc = _zanii()
        cert["receipt"] = zc.create_receipt(
            agent_id=identity["agent"]["did"], delegation=identity["delegation"],
            action=RECEIPT_ACTION, target=RECEIPT_TARGET,
            payload_hash=zc.hash_payload(digest), ts=body["issued_at"], prev=prev,
            agent_private_key=bytes.fromhex(identity["agent"]["privateKey"]))
    return cert


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


def verify_chain(run_dir: Path, owner: str | None = None) -> tuple[bool, str]:
    """Every issued receipt valid and hash-linked (nothing removed or edited)."""
    chain = _records(Path(run_dir) / "receipts.jsonl")
    if not chain:
        return False, "no receipts issued for this run"
    r = _zanii().verify_receipt_chain(chain, expected_owner=owner)
    if not r.ok:
        return False, f"receipt chain broken at {r.failed_index}: {r.error}"
    return True, f"{len(chain)} receipts, chain intact"


def _verify_receipt(rc: dict, digest: str, owner: str | None) -> tuple[bool, str]:
    zc = _zanii()
    if rc.get("action") != RECEIPT_ACTION or rc.get("target") != RECEIPT_TARGET:
        return False, "receipt is not a certificate receipt"
    if rc.get("payload_hash") != zc.hash_payload(digest):
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


def verify(cert: dict, key: str | None = None, owner: str | None = None) -> tuple[bool, str]:
    body = {k: v for k, v in cert.items() if k not in ("digest", "signature", "receipt")}
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
        ok, why = _verify_receipt(cert["receipt"], digest, owner)
        if not ok:
            return False, why
        notes.append(why)
    elif owner:
        return False, "no receipt: this certificate cannot prove its issuer"
    if "signature" not in cert and "receipt" not in cert:
        notes.append("unsigned")
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
    lines += ["", "## Held-out and out-of-distribution evaluations", ""]
    lines += [f"- {h['label']}: S {_f(h['S'])}, missing {h['missing']}/{h['n_expected']}"
              for h in cert["heldout"]] or ["- none run yet (`heldout --label <name>`)"]
    return "\n".join(lines) + "\n"
