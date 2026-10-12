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
"""Score/cost Pareto frontier over every harness a run has measured.

Selection keeps one incumbent; this view keeps the trade-off: a harness is on
the frontier when no other measured harness scores at least as high for at
most the same cost (and strictly better on one of the two). Harnesses without
a token count cannot be compared on cost and are left out.
"""

from __future__ import annotations


def points(records: list[dict]) -> list[dict]:
    """One point per measured harness (H_0 baseline + each evaluated candidate)."""
    seen, out = set(), []
    for r in records:
        if r.get("S") is None or r.get("C") is None:
            continue
        if r.get("outcome") != "BASELINE" and r.get("delta_S") is None:
            continue
        key = (r.get("t"), r.get("variant"))
        if key in seen:
            continue                     # one record per edit; one point per candidate
        seen.add(key)
        out.append({"t": r.get("t"), "variant": r.get("variant"), "S": float(r["S"]),
                    "C": float(r["C"]), "outcome": r.get("outcome")})
    return out


def frontier(pts: list[dict]) -> list[dict]:
    """Non-dominated points, sorted by cost ascending."""
    def dominated(p):
        return any(q["S"] >= p["S"] and q["C"] <= p["C"] and (q["S"] > p["S"] or q["C"] < p["C"])
                   for q in pts)
    # ponytail: O(n^2) scan; a run measures tens of harnesses, not thousands
    return sorted((p for p in pts if not dominated(p)), key=lambda p: (p["C"], -p["S"]))
