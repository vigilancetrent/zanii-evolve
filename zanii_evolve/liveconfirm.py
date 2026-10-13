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
"""Close the loop: did the certified gain hold up live?

    python zanii_evolve.py --domain acme confirm-live [--cert certificate.json] \\
        [--blackbox-url https://blackbox.example.com --label refunds] [--min-runs 30]

The certificate showed the gain offline. After the harness ships, two live
sources say whether it held:

  rollout   runs/<d>/rollout.json + rollout_outcomes.jsonl (rollout.py): every
            outcome of this rollout by arm, canary vs stable, with 95% Wilson
            ranges, and whether the harm test rolled it back.
  Blackbox  GET /v1/logbook/growth?label=L (BLACKBOX_API_KEY as Bearer): the
            version whose runs named this certificate's digest
            (`self_change(..., certificate=...)`), its success range and
            `vs_previous` (better / worse / unclear, from Blackbox's own band).

Verdict, in this order:
  regressed  the rollout was rolled back, or Blackbox says the version is worse
  confirmed  Blackbox says `better` on >= --min-runs runs, or the rollout
             promoted the canary (its harm test passed every stage) on >= --min-runs
  too_early  anything else: too few runs, or Blackbox `unclear` / no verdict and no
             finished rollout. Silence is never read as "no worse".
It is signed as a `live_confirmation` statement; exit 1 on `regressed`.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
from pathlib import Path

from . import net as NET
from . import rollout as RO


def wilson(s: int, n: int, z: float = 1.96) -> list[float]:
    """95% Wilson range of s successes in n runs ([0, 1] with no runs)."""
    if n <= 0:
        return [0.0, 1.0]
    p = s / n
    mid, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    d = 1 + z * z / n
    return [round(max(0.0, (mid - half) / d), 4), round(min(1.0, (mid + half) / d), 4)]


def rollout_summary(run_dir: Path, commit: str | None = None) -> dict | None:
    """All outcomes of the current rollout by arm (every stage), or None."""
    st = RO.load(run_dir)
    if not st:
        return None
    arms = {"canary": [0, 0], "stable": [0, 0]}              # [runs, successes]
    p = Path(run_dir) / RO.OUTCOMES
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue                                    # a torn last line
            a = arms.get(r.get("arm")) if r.get("rollout") == st["id"] else None
            if a is not None:
                a[0] += 1
                a[1] += bool(r.get("ok"))
    last = (st.get("history") or [{}])[-1]
    return {"id": st["id"], "status": st["status"], "canary": st["canary"],
            "stable": st["stable"], "stage": st["stage"],
            "matches_certificate": (None if not commit else
                                    str(st["canary"]).startswith(str(commit)[:8])),
            "e_value": last.get("e_value"), "threshold": last.get("threshold"),
            **{arm: {"runs": n, "successes": s, "success_range": wilson(s, n)}
               for arm, (n, s) in arms.items()}}


def growth(url: str, label: str, timeout: float = 20) -> dict:
    key = os.environ.get("BLACKBOX_API_KEY")
    try:                           # https, no redirects, bounded, local when offline (net.py)
        return json.loads(NET.request(
            "GET", f"{url.rstrip('/')}/v1/logbook/growth?" + urllib.parse.urlencode(
                {"label": label, "limit": "2000"}),
            {"Accept": "application/json", **({"Authorization": f"Bearer {key}"}
                                              if key else {})}, timeout=timeout))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Blackbox answered {e.code} for the growth of {label!r}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"Blackbox at {url} could not be read: {e}") from e


def blackbox_summary(g: dict, digest: str | None) -> dict:
    """The version that named this certificate (or, without one, the newest)."""
    versions = g.get("versions") or []
    want = f"sha256:{digest}" if digest else None
    hit = next((v for v in reversed(versions) if want and v.get("certificate") == want), None)
    v = hit or (versions[-1] if versions and not want else None)
    if v is None:
        return {"matched": False, "versions_total": g.get("versions_total", len(versions)),
                "pending": g.get("pending")}
    ppm = 1_000_000

    def frac(key: str, missing: int) -> float:
        x = v.get(key)
        return (x if isinstance(x, (int, float)) else missing) / ppm
    return {"matched": bool(hit) if want else None, "version": v.get("id"), "model": v.get("model"),
            "changed": v.get("changed"), "runs": v.get("sessions") or 0,
            "success": frac("success_ppm", 0),
            "success_range": [frac("success_low_ppm", 0), frac("success_high_ppm", ppm)],
            "vs_previous": v.get("vs_previous"), "certificate": v.get("certificate"),
            "pending": g.get("pending")}


def verdict(ro: dict | None, bb: dict | None, min_runs: int) -> tuple[str, list[str]]:
    why = []
    if ro and ro.get("matches_certificate") is not False and ro["status"] == "rolled_back":
        why.append(f"the rollout of {str(ro['canary'])[:8]} was rolled back")
    if bb and bb.get("vs_previous") == "worse":
        why.append(f"Blackbox: version {bb.get('version')} succeeds less than the one before")
    if why:
        return "regressed", why
    ro_ok = bool(ro) and ro.get("matches_certificate") is not False
    bb_ok = bool(bb) and bb.get("matched") is not False
    ro_runs = (ro or {}).get("canary", {}).get("runs", 0) if ro_ok else 0
    bb_runs = (bb or {}).get("runs", 0) if bb_ok else 0
    # positive evidence only: Blackbox says better, or the rollout promoted the canary
    # (its harm test passed every stage) on enough runs; "unclear" or silence is too early
    if bb_ok and bb_runs >= min_runs and bb.get("vs_previous") == "better":
        return "confirmed", [f"Blackbox: {bb_runs} live runs, better than the version before"]
    if ro_ok and ro_runs >= min_runs and ro.get("status") == "promoted":
        return "confirmed", [f"rollout promoted after {ro_runs} canary runs, no harm found"]
    runs = max(ro_runs, bb_runs)
    if runs < min_runs:
        return "too_early", [f"{runs} live runs of the new version, {min_runs} needed"]
    return "too_early", [(f"{runs} live runs but no source shows it no worse yet (Blackbox "
                          f"{(bb or {}).get('vs_previous')!r}, rollout "
                          f"{(ro or {}).get('status')!r})")]


def confirm(run_dir: Path, cert: dict | None = None, blackbox: dict | None = None,
            min_runs: int = 30) -> dict:
    digest = (cert or {}).get("digest")
    commit = ((cert or {}).get("incumbent") or {}).get("commit")
    ro = rollout_summary(run_dir, commit)
    bb = blackbox_summary(blackbox, digest) if blackbox is not None else None
    v, why = verdict(ro, bb, min_runs)
    return {"domain": (cert or {}).get("domain") or Path(run_dir).name,
            "certificate_digest": digest, "commit": commit or (ro or {}).get("canary"),
            "rollout": ro, "blackbox": bb, "min_runs": min_runs, "verdict": v, "why": why}


def _handler(args, ctx) -> int:
    from . import report as R
    from . import statement as ST
    cert = None
    if args.cert:
        cert = json.loads(Path(args.cert).read_text(encoding="utf-8"))
        ok, note = R.verify(cert)
        if not ok:
            print(f"confirm-live: certificate rejected: {note}")
            return 1
    if args.blackbox_url and not args.label:
        print("confirm-live: --blackbox-url needs --label")
        return 1
    try:
        g = growth(args.blackbox_url, args.label) if args.blackbox_url else None
    except RuntimeError as e:
        print(f"confirm-live: {e}")
        return 1
    body = confirm(ctx.run_dir, cert, g, args.min_runs)
    if body["rollout"] is None and body["blackbox"] is None:
        print("confirm-live: no live evidence (no rollout here and no --blackbox-url)")
        return 1
    stmt = ST.issue("live_confirmation", body, ctx.run_dir)
    out = ST.save(stmt, Path(args.out) if args.out else ctx.run_dir / "live_confirmation.json")
    print(f"{body['verdict']}: {'; '.join(body['why'])}\n  signed -> {out}")
    return 1 if body["verdict"] == "regressed" else 0


def cli(sub) -> None:
    p = sub.add_parser("confirm-live", help="did the certified gain hold up live? rollout + "
                       "Blackbox growth, signed (exit 1 if it regressed)")
    p.add_argument("--cert", help="the certificate of the promoted harness")
    p.add_argument("--blackbox-url", help="Zanii Blackbox gateway (BLACKBOX_API_KEY)")
    p.add_argument("--label", help="the agent's Blackbox label")
    p.add_argument("--min-runs", type=int, default=30, help="live runs before a verdict")
    p.add_argument("--out", help="default runs/<domain>/live_confirmation.json")
    p.set_defaults(product=_handler)
