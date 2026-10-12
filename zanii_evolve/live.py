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
"""Evidence from live sessions, built on the Zanii packages.

Agent side, `SessionRecorder` signs a receipt for every action a session takes,
offline and hash-linked, each stamped with which harness ran (zanii.attest, the
receipt's runtime_hash) and which rulebook governed it (zanii.constitution, the
manifest_hash). Raw payloads never leave the agent: receipts carry a salted
hash. It has the `record()` surface the Zanii framework hooks call, so
zanii.connectors, zanii.langchain, zanii.openai_agents and zanii.crewai work on
it unchanged.

Server side, `accept_receipts` checks an uploaded session (signatures,
delegation, chain, and that it ran the harness its arm was assigned),
`record_decision` signs which arm served each session under which rollout plan
(zanii.decisions), and `behaviour` is the second rollout gate: a canary that
breaks the domain's rulebook, or drifts from how stable behaves (zanii.sentinel:
novel targets, rate spikes, exfiltration-shaped sequences), is rolled back even
when its sessions report success. `audit` re-checks all of it afterwards.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

LIVE = "live_receipts.jsonl"
DECISIONS = "rollout_decisions.jsonl"
DECISION_TARGET = "evolve.rollout.decision"
MAX_RECEIPTS = 500                         # per session upload


def _zc():
    try:
        import zanii.core as zc
    except ImportError as e:
        raise RuntimeError('live receipts need the zanii package: pip install "zanii-evolve[provable]"') from e
    return zc


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def attestation(commit: str, harness_sha256: str | None = None) -> dict:
    """What ran: the harness commit and, when known, its published archive hash."""
    return {"platform": "zanii-evolve-harness", "measurement": harness_sha256 or commit,
            "commit": commit}


def runtime_hash(commit: str, harness_sha256: str | None = None) -> str:
    from zanii import attest
    return attest.attestation_hash(attestation(commit, harness_sha256))


# ------------------------------------------------------------------ agent side ---
class SessionRecorder:
    """Signs one receipt per action of one session. `identity` is the agent's own
    Zanii identity ({"agent": {"did", "privateKey"}, "delegation": [...]})."""

    def __init__(self, identity: dict, commit: str, harness_sha256: str | None = None,
                 constitution_hash: str | None = None, session_tag: str | None = None):
        self.zc = _zc()
        self.did = identity["agent"]["did"]
        self.delegation = identity["delegation"]
        self._sk = bytes.fromhex(identity["agent"]["privateKey"])
        self.runtime_hash = runtime_hash(commit, harness_sha256)
        self.manifest_hash = constitution_hash
        self.session_tag = session_tag
        self.receipts: list[dict] = []
        self.nonces: list[str] = []         # keep: they prove what a receipt's payload was
        self._lock = threading.Lock()

    def record(self, target: str, payload=None, action: str = "tool_call", ts: str | None = None,
               salt: bool = True, provenance: dict | None = None,
               subject_tag: str | None = None) -> tuple[dict, str]:
        from zanii.client import payload_bytes
        raw = payload_bytes(payload)
        if salt:
            h = self.zc.salted_payload_hash(raw)
            payload_hash, nonce = h["payload_hash"], h["nonce"]
        else:
            payload_hash, nonce = self.zc.hash_payload(raw), ""
        with self._lock:
            prev = self.zc.receipt_hash(self.receipts[-1]) if self.receipts else None
            r = self.zc.create_receipt(
                agent_id=self.did, delegation=self.delegation, action=action, target=target,
                payload_hash=payload_hash, ts=ts or _now(), prev=prev,
                agent_private_key=self._sk, runtime_hash=self.runtime_hash,
                manifest_hash=self.manifest_hash, subject_tag=subject_tag or self.session_tag)
            self.receipts.append(r)
            self.nonces.append(nonce)
        return r, self.zc.receipt_hash(r)

    def flush(self) -> list[dict]:          # the ZaniiAgent surface; nothing is queued here
        return []

    def _observe(self, target: str, payload: dict) -> None:
        """Recording never changes a tool's outcome: a payload that cannot be
        encoded is recorded by its text, and a signing failure is counted."""
        try:
            try:
                self.record(target, payload)
            except (TypeError, ValueError):
                self.record(target, {"repr": repr(payload)[:2000]})
        except Exception:                   # the tool's result matters more than its receipt
            self.failed = getattr(self, "failed", 0) + 1

    def wrap_tool(self, target: str, fn):
        """The ZaniiAgent.wrap_tool surface: sync or async, result or error recorded."""
        import functools
        import inspect
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapped(*args, **kwargs):
                try:
                    result = await fn(*args, **kwargs)
                except Exception as e:
                    self._observe(target, {"args": list(args), "kwargs": kwargs, "error": str(e)})
                    raise
                self._observe(target, {"args": list(args), "kwargs": kwargs, "result": result})
                return result
            return async_wrapped

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                self._observe(target, {"args": list(args), "kwargs": kwargs, "error": str(e)})
                raise
            self._observe(target, {"args": list(args), "kwargs": kwargs, "result": result})
            return result
        return wrapped

    def attest_memory(self, state_dir, target: str = "memory.head") -> str | None:
        """Sign the head of a provable memory chain into this session, so a later
        rewrite of the whole chain cannot match what the session saw. Needs the
        agent's delegation to cover `target`."""
        p = Path(state_dir) / "memory_chain.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        if not lines:
            return None
        head = json.loads(lines[-1])["payload"]
        _, h = self.record(target, {"entry_hash": head["entry_hash"], "seq": head["seq"]},
                           action="attest", salt=False)
        return h

    # one-line hooks for agent frameworks: every tool call becomes a receipt
    def wrap_tools(self, tools: dict, prefix: str = "tools") -> dict:
        from zanii.connectors import wrap_toolbox
        return wrap_toolbox(self, tools, prefix)

    def record_tool(self, name: str, arguments=None, result=None, error=None,
                    prefix: str = "tools") -> str:
        from zanii.connectors import record_tool_call
        return record_tool_call(self, name, arguments, result=result, error=error, prefix=prefix)

    def langchain_callbacks(self, prefix: str = "tools"):
        from zanii.langchain import zanii_callbacks
        return zanii_callbacks(self, prefix)

    def openai_agents_hooks(self, prefix: str = "tools"):
        from zanii.openai_agents import ZaniiRunHooks
        return ZaniiRunHooks(self, prefix)

    def instrument_crew(self, crew, prefix: str = "tools"):
        from zanii.crewai import instrument_crew
        return instrument_crew(crew, self, prefix)


# ----------------------------------------------------------------- server side ---
def _jsonl(p: Path) -> list[dict]:
    try:
        lines = Path(p).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for x in lines:
        try:
            out.append(json.loads(x))
        except ValueError:
            continue                        # a torn last line
    return out


def manifest_hash_for(run_dir: Path, commit: str) -> str | None:
    """The published harness archive hash of a commit, from the registry manifest."""
    from .registry import default_root
    m = default_root(Path(__file__).resolve().parent.parent) / Path(run_dir).name / commit[:8]
    try:
        return json.loads((m / "manifest.json").read_text(encoding="utf-8"))["harness_sha256"]
    except (OSError, ValueError, KeyError):
        return None


def accept_receipts(run_dir: Path, st: dict, key: str, arm: str, commit: str,
                    receipts: list) -> int:
    """Check and store one session's receipts; raises ValueError on anything that
    does not hold up. Returns how many were stored."""
    zc = _zc()
    if not isinstance(receipts, list) or len(receipts) > MAX_RECEIPTS:
        raise ValueError(f"receipts must be a list of at most {MAX_RECEIPTS}")
    if not receipts:
        return 0
    chain = zc.verify_receipt_chain(receipts)
    if not chain.ok:
        raise ValueError(f"session receipts do not verify at {chain.failed_index}: {chain.error}")
    want = {runtime_hash(commit, manifest_hash_for(run_dir, commit)), runtime_hash(commit)}
    if any(r.get("runtime_hash") not in want for r in receipts):
        raise ValueError(f"receipts were not produced by the harness the {arm} arm serves")
    rows = [json.dumps({"rollout": st["id"], "stage": st["stage"], "arm": arm, "key": key,
                        "receipt": r}) + "\n" for r in receipts]
    with open(Path(run_dir) / LIVE, "a", encoding="utf-8") as f:
        f.writelines(rows)
    return len(rows)


def plan_hash(st: dict) -> str:
    """The rollout plan a decision was made under: the 'rulebook' of zanii.decisions."""
    from zanii.encoding import jcs_hash
    return jcs_hash({"rollout": st["id"], "stable": st["stable"], "canary": st["canary"],
                     "shares_milli": [round(s * 1000) for s in st["shares"]],
                     "alpha_milli": round(st["alpha"] * 1000)})


def bucket(canary: str, key: str) -> str:
    """The assignment hash prefix; reveals nothing about the key, lets anyone recompute u."""
    return hashlib.sha256(f"{canary}:{key}".encode()).hexdigest()[:13]


def record_decision(run_dir: Path, st: dict, key: str, arm: str, commit: str, ok: bool,
                    identity: dict) -> dict:
    """A signed, hash-linked receipt: session `key` was served `arm` at this stage of
    this plan. Appended to runs/<domain>/rollout_decisions.jsonl."""
    zc = _zc()
    from zanii import decisions as D
    from zanii.client import payload_bytes
    d = D.build_decision_receipt(
        kind="rollout.assignment", manifest_hash=plan_hash(st), ts=_now(),
        tag=hashlib.sha256(key.encode()).hexdigest(),
        outcome={"arm": arm, "commit": commit, "stage": st["stage"],
                 "share_milli": round(st["shares"][st["stage"]] * 1000),
                 "bucket": bucket(st["canary"], key), "ok": bool(ok)})
    log = Path(run_dir) / DECISIONS
    prior = _jsonl(log)
    prev = zc.receipt_hash(prior[-1]["receipt"]) if prior else None
    r = zc.create_receipt(agent_id=identity["agent"]["did"], delegation=identity["delegation"],
                          action="decision", target=DECISION_TARGET,
                          payload_hash=zc.hash_payload(payload_bytes(d["payload"])), ts=_now(),
                          prev=prev, agent_private_key=bytes.fromhex(identity["agent"]["privateKey"]),
                          manifest_hash=plan_hash(st), subject_tag=d["subject_tag"])
    row = {"receipt": r, "payload": d["payload"]}
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


# ----------------------------------------------------------------- the gate ---
def stage_receipts(run_dir: Path, st: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"canary": [], "stable": []}
    for row in _jsonl(Path(run_dir) / LIVE):
        if row.get("rollout") == st["id"] and row.get("stage") == st["stage"] \
                and row.get("arm") in out:
            out[row["arm"]].append(row["receipt"])
    return out


def behaviour(run_dir: Path, st: dict, constitution: dict | None,
              min_sample: int = 20) -> dict:
    """{"harm": bool, "governance": [...], "findings": [...]} for the canary at
    this stage. Harm = an action its rulebook forbids, or a high-severity drift
    from how stable behaves."""
    rs = stage_receipts(run_dir, st)
    out = {"harm": False, "governance": [], "findings": [],
           "receipts": {k: len(v) for k, v in rs.items()}}
    if not rs["canary"]:
        return out
    if constitution is not None:
        from zanii import constitution as K
        v = K.verify_governance(constitution, rs["canary"])
        out["governance"] = [x for x in v["violations"] if x["kind"] == "forbidden"]
    from zanii import sentinel as S
    by_agent: dict[str, list] = {}
    for r in rs["stable"]:
        by_agent.setdefault(r["agent_id"], []).append(r)
    for agent in sorted({r["agent_id"] for r in rs["canary"]}):
        base = by_agent.get(agent, [])
        if len(base) < min_sample:
            continue                        # too little stable behaviour to compare against
        canary = sorted((r for r in rs["canary"] if r["agent_id"] == agent),
                        key=lambda r: r["ts"])
        b = S.build_baseline(agent, receipts=sorted(base, key=lambda r: r["ts"]),
                             delegation=base[0]["delegation"])
        out["findings"] += [f for f in S.scan(canary, b, min_sample=min_sample)
                            if f.get("severity") == "high"]
    out["harm"] = bool(out["governance"] or out["findings"])
    return out


# --------------------------------------------------------------------- audit ---
def audit(run_dir: Path, owner: str | None = None, constitution: dict | None = None) -> dict:
    """Re-check a domain's live evidence: the decision chain is intact, every
    decision's arm follows from its plan (u < share), one plan governed at a time,
    and the session receipts verify under the rulebook."""
    zc = _zc()
    from zanii import decisions as D
    from zanii.client import payload_bytes
    run_dir = Path(run_dir)
    rows = _jsonl(run_dir / DECISIONS)
    report: dict = {"decisions": len(rows), "problems": []}
    if rows:
        chain = zc.verify_receipt_chain([r["receipt"] for r in rows], expected_owner=owner)
        if not chain.ok:
            report["problems"].append(f"decision chain broken at {chain.failed_index}: {chain.error}")
        for i, row in enumerate(rows):
            p = row["payload"]
            if row["receipt"]["payload_hash"] != zc.hash_payload(payload_bytes(p)):
                report["problems"].append(f"decision {i}: payload does not match its receipt")
            if not D.verify_decision(p).get("ok"):
                report["problems"].append(f"decision {i}: malformed")
            o = p.get("outcome") or {}
            u_milli = int(o.get("bucket", "0"), 16) * 1000 // 16 ** 13
            if (o.get("arm") == "canary") != (u_milli < o.get("share_milli", 0)):
                report["problems"].append(f"decision {i}: arm {o.get('arm')} does not follow "
                                          f"from the plan's share")
        rc = D.rule_consistency([r["payload"] for r in rows])
        report["plans"] = rc["rulebooks"]
        if not rc["consistent"]:
            report["problems"].append(f"rollout plans interleaved: {rc['reasons']}")
    live = [row["receipt"] for row in _jsonl(run_dir / LIVE)]
    report["session_receipts"] = len(live)
    bad = sum(not zc.verify_receipt(r).ok for r in live)
    if bad:
        report["problems"].append(f"{bad} session receipts fail verification")
    if constitution is not None and live:
        from zanii import constitution as K
        v = K.verify_governance(constitution, live)
        report["governance"] = {"forbidden": sum(x["kind"] == "forbidden" for x in v["violations"]),
                                "ungoverned": sum(x["kind"] == "ungoverned" for x in v["violations"]),
                                "approvals_required": len(v["approvals_required"])}
        report["rulebooks"] = K.governance_trail(sorted(live, key=lambda r: r["ts"]))["counts"]
    report["ok"] = not report["problems"]
    return report
