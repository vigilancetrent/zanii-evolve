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
"""What happened to each client record, and who received it (zanii.data_custody,
zanii.subprocessor).

Client records are the tasks minted from production traces or a Blackbox
gateway. Every time Zanii Evolve touches one, it signs a custody receipt:

    store   ingested into the task set
    share   sent to a hosted model (evaluation, judging, analysis); the touch names
            the provider, and a subprocessor transfer records it under the
            agreement the domain declares for that provider
    read    used by a local model (ollama): the record never left the machine
    delete  removed by `data forget`

The receipts form one hash chain per domain (runs/<domain>/custody.jsonl), so a
touch cannot be hidden without breaking it, and each names the record only by
an HMAC ref (runs/<domain>/custody.key), never by content. Purpose is enforced by
scope: the signing identity is delegated exactly SCOPES, so an export or a
transform cannot even produce a valid receipt.

It runs when ZANII_EVOLVE_IDENTITY is set and that identity's delegation covers
these scopes (identities made by 0.3.1+ do; older ones skip it silently). It
never stops an evaluation: a custody failure is logged and the run goes on.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
from pathlib import Path

CLASS = "trace"
SCOPES = [f"data.{CLASS}.{op}" for op in ("store", "read", "share", "delete")] + \
    ["subprocessor.transfer.*"]
LOG = "custody.jsonl"
LOCAL = {"ollama", "fake"}                  # providers that keep data on this machine
CLIENT_SOURCES = {"trace", "blackbox"}
_lock = threading.Lock()


def provider_of(model: str) -> str:
    prefix, _, rest = str(model).partition("/")
    if not rest:                            # a bare id is Claude (providers.resolve)
        return "vertex" if os.environ.get("ZANII_EVOLVE_VERTEX_PROJECTS") else "anthropic"
    return prefix


def client_ids(domain, ids) -> list[str]:
    """The ids among `ids` that are client records (traces, Blackbox runs)."""
    tasks = getattr(domain, "tasks", None) or {}
    return [i for i in ids if (tasks.get(i) or {}).get("source") in CLIENT_SOURCES]


class Custody:
    def __init__(self, run_dir: Path, identity: dict, agreements: dict | None = None):
        import zanii.core as zc
        from zanii.delegation import scopes_allow
        self.zc = zc
        self.run_dir = Path(run_dir)
        self.identity = identity
        self.did = identity["agent"]["did"]
        self.agreements = agreements or {}
        scopes = identity["delegation"][-1].get("scopes", [])
        self.enabled = all(scopes_allow(scopes, t) for t in
                           (f"data.{CLASS}.share", "subprocessor.transfer.dpa"))

    @classmethod
    def load(cls, run_dir: Path, agreements: dict | None = None) -> Custody | None:
        """A Custody for this run, or None when custody is not configured."""
        try:
            from .report import load_identity
            ident = load_identity()
            if not ident:
                return None
            c = cls(run_dir, ident, agreements)
        except ImportError:
            return None
        return c if c.enabled else None

    def _key(self) -> bytes:
        p = self.run_dir / "custody.key"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(secrets.token_hex(32), encoding="utf-8")
            os.chmod(p, 0o600)
        return bytes.fromhex(p.read_text(encoding="utf-8").strip())

    def ref(self, task_id: str) -> str:
        from zanii.data_custody import derive_ref
        return derive_ref(task_id, self._key())

    def _sign(self, target: str, payload: dict, subject_tag: str | None = None) -> dict:
        from zanii.client import payload_bytes
        log = self.run_dir / LOG
        with _lock:
            prev = None
            if log.exists():
                lines = log.read_text(encoding="utf-8").splitlines()
                prev = self.zc.receipt_hash(json.loads(lines[-1])["receipt"]) if lines else None
            r = self.zc.create_receipt(
                agent_id=self.did, delegation=self.identity["delegation"], action="custody",
                target=target, payload_hash=self.zc.hash_payload(payload_bytes(payload)),
                ts=_now(), prev=prev, subject_tag=subject_tag,
                agent_private_key=bytes.fromhex(self.identity["agent"]["privateKey"]))
            row = {"receipt": r, "payload": payload}
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        return row

    def touch(self, task_ids, op: str, to: str | None = None) -> int:
        from zanii.data_custody import build_touch
        n = 0
        for tid in task_ids:
            t = build_touch(ref=self.ref(tid), data_class=CLASS, op=op, ts=_now(),
                            junction={"to": to} if to else None)
            self._sign(t["target"], t["payload"], t["subjectTag"])
            n += 1
        return n

    def share(self, task_ids, model: str) -> int:
        """Records sent to `model`: a share touch each and one transfer, or a read
        when the model runs locally."""
        task_ids = list(task_ids)
        if not task_ids:
            return 0
        prov = provider_of(model)
        if prov in LOCAL:
            return self.touch(task_ids, "read")
        from zanii.subprocessor import build_transfer
        agreement = self.agreements.get(prov) or {"type": "other"}
        t = build_transfer(from_=self.did, to=prov, agreement=agreement, ts=_now())
        t["payload"]["records"] = len(task_ids)
        self._sign(t["target"], t["payload"])
        return self.touch(task_ids, "share", to=prov)


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _guard(fn, *args):
    """Custody never stops a run: a failure is reported and the run goes on."""
    try:
        return fn(*args)
    except Exception as e:                  # noqa: BLE001
        print(f"[custody] not recorded: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return 0


def on_evaluate(domain, run_dir: Path, ids) -> None:
    """Called after every evaluation: the policy and judge models saw each record."""
    mine = client_ids(domain, ids)
    if not mine:
        return
    c = Custody.load(run_dir, (getattr(domain, "cfg", None) or {}).get("subprocessors"))
    if c is None:
        return
    cfg = domain.cfg
    models = {os.environ.get("ZANII_EVOLVE_POLICY_MODEL", cfg.get("policy_model", "claude-opus-5-5")),
              os.environ.get("ZANII_EVOLVE_JUDGE_MODEL", cfg.get("judge_model", "claude-sonnet-5-5"))}
    for m in sorted(models):
        _guard(c.share, mine, m)


def on_analyze(domain, run_dir: Path, ids, models) -> None:
    """Records whose traces reached the analyst / proposer models."""
    mine = client_ids(domain, ids)
    c = Custody.load(run_dir, (getattr(domain, "cfg", None) or {}).get("subprocessors")) \
        if mine else None
    if c is None:
        return
    for m in sorted(set(models)):
        _guard(c.share, mine, m)


def on_store(domain_dir: Path, run_dir: Path, task_ids) -> None:
    cfg = _read(Path(domain_dir) / "zanii_evolve.json") or {}
    c = Custody.load(run_dir, cfg.get("subprocessors"))
    if c is not None and task_ids:
        _guard(c.touch, list(task_ids), "store")


def _read(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ the view ---
def history(run_dir: Path, task_id: str, owner: str | None = None,
            declared: list[str] | None = None) -> dict:
    """Everything recorded about one client record: its verified touches, where it
    went, whether it was deleted, and whether every recipient was declared."""
    import zanii.core as zc
    from zanii.data_custody import custody_view, derive_ref, verify_object_chain
    from zanii.subprocessor import verify_transfers
    run_dir = Path(run_dir)
    kp = run_dir / "custody.key"
    if not kp.exists():
        raise LookupError(f"no custody record in {run_dir}")
    rows = [json.loads(x) for x in (run_dir / LOG).read_text(encoding="utf-8").splitlines()
            if x.strip()]
    chain = zc.verify_receipt_chain([r["receipt"] for r in rows], expected_owner=owner)
    ref = derive_ref(task_id, bytes.fromhex(kp.read_text(encoding="utf-8").strip()))
    from zanii.client import payload_bytes
    doctored = sum(r["receipt"]["payload_hash"] != zc.hash_payload(payload_bytes(r["payload"]))
                   for r in rows)                  # verify_object_chain does not check this
    touches = [r for r in rows if r["receipt"].get("subject_tag") == ref]
    obj = verify_object_chain(touches, ref=ref)
    view = custody_view(ref, obj["touches"]) if obj["ok"] else None
    transfers = [r["payload"] for r in rows if r["payload"].get("type") == "subprocessor.transfer"]
    mine = set((view or {}).get("destinations") or [])
    recipients = verify_transfers([t for t in transfers if t["to"] in mine], declared or [])
    return {"task_id": task_id, "ref": ref,
            "log_intact": chain.ok and not doctored,
            "log_error": chain.error or (f"{doctored} payloads do not match their receipts"
                                         if doctored else None),
            "touches_verified": obj["ok"], "touch_error": obj.get("error"),
            "view": view, "recipients_declared": recipients["ok"],
            "recipient_issues": recipients["reasons"]}
