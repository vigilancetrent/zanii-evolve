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
"""The human gate for watch's irreversible steps (zanii.runtime, Article VI).

`watch --require-approval publish,promote` pauses those steps until the owner
approves the exact action. zanii.runtime keeps its ConfirmationGate in memory;
watch runs as separate ticks (often separate cron processes), so the gate is
kept on disk instead and the approval is an owner-signed zanii confirmation
(`create_confirmation`) bound to the hash of that one action:

    runs/<domain>/approvals/<id>.json   {tool, args, action_hash, opened_at,
                                         expires_at, confirmation?}

An approval for one action never unlocks another (the hash differs), an
unanswered one expires and is reopened (so silence never becomes consent), and
a used one is moved to approvals/used/. Only the owner who delegated the
signing identity can approve.
"""

from __future__ import annotations

import calendar
import json
import time
from pathlib import Path

GATED = ("publish", "promote")


def action_hash(tool: str, args: dict) -> str:
    from zanii.runtime import _action_hash      # the runtime's own binding of an action
    return _action_hash(tool, args)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(ts: str) -> float:
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def check(run_dir: Path, tool: str, args: dict, owner: str, ttl_hours: float = 72) -> tuple[str, str]:
    """("approved" | "pending" | "opened" | "reopened", approval id). Only
    "approved" lets the caller act, and it consumes the approval."""
    from zanii.core import verify_confirmation
    h = action_hash(tool, args)
    cid = h[7:23]
    d = Path(run_dir) / "approvals"
    p = d / f"{cid}.json"
    rec = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if rec and rec.get("confirmation"):
        c = rec["confirmation"]
        if (verify_confirmation(c) and c.get("owner_id") == owner
                and c.get("action_hash") == h == rec["action_hash"]):
            (d / "used").mkdir(parents=True, exist_ok=True)
            p.replace(d / "used" / f"{cid}-{int(time.time())}.json")
            return "approved", cid
    if rec and time.time() < _epoch(rec["expires_at"]):
        return "pending", cid
    d.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tool": tool, "args": args, "action_hash": h, "opened_at": _now(),
                             "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                         time.gmtime(time.time() + ttl_hours * 3600))},
                            indent=1), encoding="utf-8")
    return ("reopened" if rec else "opened"), cid


def pending(run_dir: Path) -> list[dict]:
    d = Path(run_dir) / "approvals"
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        rec = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": p.stem, "tool": rec["tool"], "args": rec["args"],
                    "expires_at": rec["expires_at"], "approved": "confirmation" in rec})
    return out


def approve(run_dir: Path, cid: str, owner_file: Path) -> dict:
    """The owner signs a confirmation of exactly this pending action."""
    import zanii.core as zc

    from .report import open_key
    p = Path(run_dir) / "approvals" / f"{cid}.json"
    if not p.exists():
        raise LookupError(f"no pending approval {cid}")
    rec = json.loads(p.read_text(encoding="utf-8"))
    if time.time() >= _epoch(rec["expires_at"]):
        raise ValueError(f"approval {cid} expired at {rec['expires_at']}; watch reopens it")
    owner = json.loads(Path(owner_file).read_text(encoding="utf-8"))
    rec["confirmation"] = zc.create_confirmation(owner["did"], rec["action_hash"], _now(),
                                                 open_key(owner, str(owner_file)))
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return {"id": cid, "tool": rec["tool"], "args": rec["args"], "approved_by": owner["did"]}
