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
"""Signed statements: the one envelope every Evolve product hands out.

A migration certificate, a savings statement, a regression verdict, a vendor
scorecard, a red-team score, a judge pack, a live confirmation: each is a
statement of one `kind` with a free-form `body`. It is signed the same way a
generalization certificate is (report.py):

    {"schema": "zanii-evolve-statement/1", "kind": ..., "issued_at": ...,
     "body": {...}, "digest": sha256(canonical JSON of schema+kind+issued_at+body),
     "signature": HMAC(ZANII_EVOLVE_SIGNING_KEY, digest)        # when the key is set
     "receipt": zanii receipt over the digest, target evolve.<kind>}   # with an identity

Issued for a run, a copy is kept in runs/<domain>/statements/ (evidence-pack
reads them). With an identity each receipt is chained onto runs/<domain>/statements.jsonl,
so a statement that was issued and then hidden (a bad red-team month, a vendor
that lost) breaks the chain. `verify` checks digest, HMAC and receipt offline.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

from . import report as R

SCHEMA = "zanii-evolve-statement/1"
ACTION = "statement_issued"
SIGNED = ("schema", "kind", "issued_at", "body")


def _digest(stmt: dict) -> str:
    return hashlib.sha256(R._canonical({k: stmt[k] for k in SIGNED})).hexdigest()


def issue(kind: str, body: dict, run_dir: Path | None = None, identity: dict | None = None,
          key: str | None = None) -> dict:
    """Sign `body` as a statement of `kind`. `identity` defaults to the run's
    configured one (report.load_identity); pass {} to issue without one."""
    stmt = {"schema": SCHEMA, "kind": kind, "issued_at": R._now(), "body": body}
    stmt["digest"] = _digest(stmt)
    key = key if key is not None else os.environ.get("ZANII_EVOLVE_SIGNING_KEY")
    if key:
        stmt["signature"] = hmac.new(key.encode(), stmt["digest"].encode(),
                                     hashlib.sha256).hexdigest()
    identity = R.load_identity() if identity is None else identity
    if identity:
        zc = R._zanii()
        log = Path(run_dir) / "statements.jsonl" if run_dir else None
        chain = R._records(log) if log else []
        stmt["receipt"] = zc.create_receipt(
            agent_id=identity["agent"]["did"], delegation=identity["delegation"],
            action=ACTION, target=f"evolve.{kind}", payload_hash=zc.hash_payload(stmt["digest"]),
            ts=stmt["issued_at"], prev=zc.receipt_hash(chain[-1]) if chain else None,
            agent_private_key=bytes.fromhex(identity["agent"]["privateKey"]))
        if log:
            # ponytail: single writer per run, like report.issue
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(stmt["receipt"], ensure_ascii=False) + "\n")
    if run_dir:                         # kept with the run: evidence-pack finds it here
        stamp = stmt["issued_at"].replace(":", "").replace("-", "")
        save(stmt, Path(run_dir) / "statements" / f"{kind}-{stamp}-{stmt['digest'][:8]}.json")
    return stmt


def verify(stmt: dict, key: str | None = None, owner: str | None = None,
           kind: str | None = None, allow_unsigned: bool = False) -> tuple[bool, str]:
    """Digest, HMAC and receipt. The digest alone is unkeyed (anyone can recompute
    it), so a statement with neither signature nor receipt fails unless
    `allow_unsigned`, and always fails when a signing key is at hand."""
    if not isinstance(stmt, dict) or stmt.get("schema") != SCHEMA:
        return False, f"not a statement (schema {(stmt or {}).get('schema')!r})"
    if any(k not in stmt for k in SIGNED) or not isinstance(stmt["kind"], str):
        return False, f"malformed statement: needs {', '.join(SIGNED)}"
    if kind and stmt.get("kind") != kind:
        return False, f"statement is a {stmt.get('kind')!r}, not a {kind!r}"
    digest = _digest(stmt)
    if not hmac.compare_digest(digest, str(stmt.get("digest", ""))):
        return False, "digest mismatch: the statement was edited after it was issued"
    notes = [f"{stmt['kind']}: digest ok"]
    key = key if key is not None else os.environ.get("ZANII_EVOLVE_SIGNING_KEY")
    if "signature" not in stmt and not stmt.get("receipt"):
        if key:
            return False, "unsigned, but a signing key is set: signature stripped or forged"
        if not allow_unsigned:
            return False, "unsigned: anyone could have written it (allow_unsigned to accept)"
    if "signature" in stmt:
        if not key:
            notes.append("HMAC not checked (set ZANII_EVOLVE_SIGNING_KEY)")
        elif not hmac.compare_digest(
                hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest(),
                str(stmt["signature"])):
            return False, "signature mismatch: wrong key or forged statement"
        else:
            notes.append("HMAC ok")
    rc = stmt.get("receipt")
    if rc:
        zc = R._zanii()
        if rc.get("action") != ACTION or rc.get("target") != f"evolve.{stmt['kind']}":
            return False, "receipt is not this statement's receipt"
        if rc.get("payload_hash") != zc.hash_payload(digest):
            return False, "receipt covers a different statement"
        r = zc.verify_receipt(rc, expected_owner=owner)
        if not r.ok:
            return False, f"receipt invalid: {r.error}"
        issuer = rc["delegation"][0]["issuer"]
        notes.append(f"receipt ok, issued under owner {issuer}" if owner else
                     f"receipt self-consistent but owner {issuer} NOT pinned (pass --owner)")
    elif owner:
        return False, "no receipt: this statement cannot prove its issuer"
    if "signature" not in stmt and not rc:
        notes.append("unsigned")
    return True, "; ".join(notes)


def save(stmt: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stmt, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
