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
"""Staged live rollout of a certified harness, with automatic rollback.

A published harness (canary) serves a growing share of live traffic next to the
one it replaces (stable). Agents ask which harness a session gets (`assign`,
sticky per key) and report each outcome (`record`). `decide` is checked on every
watch tick, so the harm test must stay valid under continuous monitoring:

  Assignment is randomized with known share r. If the canary fails no more
  often than stable, each failure lands on the canary with probability at most r.
  The arms of the failures are tested against Bernoulli(r) with a mixture
  likelihood ratio over q ~ Uniform[r, 1]. Under the null that ratio is a
  nonnegative supermartingale, so by Ville's inequality it ever crosses 1/alpha
  with probability at most alpha, however often it is looked at.

alpha is split evenly across stages. A stage passes after `min_requests` canary
outcomes without the test firing; passing the last stage promotes the canary.
That is evidence of no detectable harm; the gain itself was already shown
offline by the certificate.

State: runs/<domain>/rollout.json; outcomes: runs/<domain>/rollout_outcomes.jsonl
(append-only, one JSON object per line, written by the server or by you).
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import secrets
import time
from pathlib import Path

STATE = "rollout.json"
OUTCOMES = "rollout_outcomes.jsonl"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load(run_dir: Path) -> dict | None:
    p = Path(run_dir) / STATE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(run_dir: Path, st: dict) -> dict:
    p = Path(run_dir) / STATE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(p)                                # readers never see a half-written state
    return st


def validate(shares: list[float], alpha: float, min_requests: int) -> None:
    if not shares or any(not 0 < s < 1 for s in shares) or shares != sorted(shares):
        raise ValueError("rollout shares must be increasing and strictly between 0 and 1")
    if not 0 < alpha < 1 or min_requests < 1:
        raise ValueError("alpha must be in (0, 1) and min_requests >= 1")


def start(run_dir: Path, stable: str, canary: str, shares: list[float], alpha: float,
          min_requests: int, cycle: int | None = None) -> dict:
    validate(shares, alpha, min_requests)
    return save(run_dir, {"id": secrets.token_hex(8),   # rollout of the same canary never count
                          "status": "running", "stable": stable, "canary": canary,
                          "cycle": cycle, "shares": shares, "stage": 0, "alpha": alpha,
                          "min_requests": min_requests, "started_at": now(),
                          "stage_started_at": now(), "stalled_alerted": False,
                          "history": []})


def assign(st: dict, key: str) -> dict:
    """Which harness a session gets: sticky per key, fresh per canary."""
    if st["status"] != "running":
        return {"arm": "stable", "commit": st["stable"]}
    u = int(hashlib.sha256(f"{st['canary']}:{key}".encode()).hexdigest()[:13], 16) / 16 ** 13
    if u < st["shares"][st["stage"]]:
        return {"arm": "canary", "commit": st["canary"], "stage": st["stage"]}
    return {"arm": "stable", "commit": st["stable"], "stage": st["stage"]}


def check_outcome(st: dict, key: str, commit: str, ok: bool) -> str:
    """The arm an outcome belongs to; raises when it cannot be counted."""
    if st["status"] != "running":
        raise RuntimeError(f"no running rollout (status {st['status']})")
    if not isinstance(ok, bool) or not isinstance(key, str) or not 0 < len(key) <= 200:
        raise ValueError("outcome needs a key (1-200 chars) and a boolean ok")
    arm = {st["canary"]: "canary", st["stable"]: "stable"}.get(commit)
    if arm is None:
        raise ValueError("commit is neither arm of the running rollout")
    if assign(st, key)["arm"] != arm:          # a key reports only for the arm it was served
        raise ValueError("this key is not assigned to that arm at the current stage")
    return arm


def record(run_dir: Path, st: dict, key: str, commit: str, ok: bool) -> dict:
    """Append one live outcome for the running rollout."""
    arm = check_outcome(st, key, commit, ok)
    row = {"rollout": st["id"], "stage": st["stage"], "arm": arm, "key": key, "ok": ok,
           "at": now()}
    with open(Path(run_dir) / OUTCOMES, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def counts(run_dir: Path, st: dict) -> dict:
    """Outcomes of the current stage, by arm. A torn last line is skipped."""
    c = {"canary": [0, 0], "stable": [0, 0]}             # [requests, failures]
    p = Path(run_dir) / OUTCOMES
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("rollout") == st["id"] and r.get("stage") == st["stage"]:
                arm = c.get(r.get("arm"))
                if arm is not None:
                    arm[0] += 1
                    arm[1] += not r.get("ok")
    return {"canary": {"n": c["canary"][0], "failures": c["canary"][1]},
            "stable": {"n": c["stable"][0], "failures": c["stable"][1]}}


def e_value(k: int, m: int, r: float, grid: int = 4000) -> float:
    """Mixture likelihood ratio for k of m failures on the canary, share r:
    E = mean over q ~ U[r, 1] of q^k (1-q)^(m-k), divided by r^k (1-r)^(m-k)."""
    if m == 0:
        return 1.0
    null = k * math.log(r) + (m - k) * math.log1p(-r)
    logs = []
    for i in range(grid + 1):                # trapezoid on a log scale, stable for large m
        q = r + (1 - r) * i / grid
        if q >= 1:
            v = 0.0 if k == m else -math.inf
        else:
            v = k * math.log(q) + (m - k) * math.log1p(-q)
        logs.append(v + (math.log(0.5) if i in (0, grid) else 0.0))
    top = max(logs)
    if top == -math.inf:
        return 0.0
    log_mean = top + math.log(sum(math.exp(v - top) for v in logs)) - math.log(grid)
    return math.exp(min(700.0, log_mean - null))


def decide(run_dir: Path, st: dict, stall_hours: float,
           constitution: dict | None = None) -> tuple[str, dict]:
    """("rollback" | "advance" | "promote" | "stalled" | "wait", evidence).

    Two gates, either rolls back: the outcome test below, and, when agents upload
    signed session receipts, their behaviour (zanii_evolve.live.behaviour): an
    action the rulebook forbids, or high-severity drift from how stable behaves."""
    if (Path(run_dir) / "live_receipts.jsonl").exists():
        from . import live
        b = live.behaviour(run_dir, st, constitution)
        if b["harm"]:
            return "rollback", {"stage": st["stage"], "share": st["shares"][st["stage"]],
                                "reason": "behaviour", "governance": b["governance"],
                                "findings": b["findings"], "receipts": b["receipts"]}
    c = counts(run_dir, st)
    r = st["shares"][st["stage"]]
    k = c["canary"]["failures"]
    m = k + c["stable"]["failures"]
    e = e_value(k, m, r)
    ev = {"stage": st["stage"], "share": r, **c, "e_value": e,
          "threshold": len(st["shares"]) / st["alpha"]}
    if e >= ev["threshold"]:
        return "rollback", ev
    if c["canary"]["n"] >= st["min_requests"]:
        return ("promote" if st["stage"] == len(st["shares"]) - 1 else "advance"), ev
    began = calendar.timegm(time.strptime(st["stage_started_at"], "%Y-%m-%dT%H:%M:%SZ"))
    if not st["stalled_alerted"] and time.time() - began > stall_hours * 3600:
        return "stalled", ev
    return "wait", ev


def apply(run_dir: Path, st: dict, verdict: str, ev: dict) -> dict:
    """Move the state on after a decision; returns the saved state."""
    st["history"] = (st["history"] + [{"verdict": verdict, "at": now(), **ev}])[-100:]
    if verdict == "advance":
        st.update(stage=st["stage"] + 1, stage_started_at=now(), stalled_alerted=False)
    elif verdict == "promote":
        st.update(status="promoted", stable=st["canary"], finished_at=now())
    elif verdict == "rollback":
        st.update(status="rolled_back", finished_at=now())
    elif verdict == "stalled":
        st["stalled_alerted"] = True
    return save(run_dir, st)


if __name__ == "__main__":
    # Ville check: under the null the test fires at most alpha of the time, even
    # when looked at after every single failure.
    import random
    rng, fired, alpha, r = random.Random(7), 0, 0.05, 0.2
    for _ in range(300):
        k = m = 0
        for _ in range(300):
            m += 1
            k += rng.random() < r
            if e_value(k, m, r, grid=400) >= 1 / alpha:
                fired += 1
                break
    assert fired / 300 <= alpha, fired
    assert e_value(30, 40, 0.2) > 1e6 and e_value(8, 40, 0.2) < 1
    print("ok", fired / 300)
