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
"""How far the rubric judge agrees with humans, before its scores steer a run.

labels.jsonl, one human-graded deliverable per line:
    {"task_id": "mkt-001", "output": "<deliverable>", "labels": ["pass", "fail", ...]}
`labels` follows the task's rubric order (true/false also accepted).

Reported: raw agreement and Cohen's kappa over every (deliverable, criterion)
pair, agreement per criterion (the weakest are the ones to rewrite), and the
disagreements themselves.
"""

from __future__ import annotations


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("pass", "true", "yes", "1"):
        return True
    if s in ("fail", "false", "no", "0"):
        return False
    raise ValueError(f"label {v!r}: use pass/fail")


def kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two binary raters; None when chance agreement is 1
    (both raters gave one constant answer)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(h == j for h, j in pairs) / n
    ph, pj = sum(h for h, _ in pairs) / n, sum(j for _, j in pairs) / n
    pe = ph * pj + (1 - ph) * (1 - pj)
    return None if pe >= 1 else (po - pe) / (1 - pe)


def agreement(tasks: dict, labels: list[dict], model: str, judge=None) -> dict:
    """Run the judge on every labelled deliverable and compare with the humans."""
    if judge is None:
        from .brief import judge_rubric as judge
    pairs, per, disagree = [], {}, []
    for n, lab in enumerate(labels, 1):
        task = tasks.get(lab.get("task_id"))
        if task is None:
            raise ValueError(f"labels line {n}: unknown task {lab.get('task_id')!r}")
        human = [_bool(v) for v in lab.get("labels") or []]
        rubric = task.get("rubric") or []
        if len(human) != len(rubric):
            raise ValueError(f"labels line {n}: {len(human)} labels for {len(rubric)} criteria")
        for i, v in enumerate(judge(task, str(lab.get("output") or ""), model)):
            j = v["verdict"] == "pass"
            pairs.append((human[i], j))
            c = per.setdefault((task["id"], i), {"task_id": task["id"],
                                                 "criterion": rubric[i]["criterion"],
                                                 "n": 0, "agree": 0})
            c["n"] += 1
            c["agree"] += int(human[i] == j)
            if human[i] != j:
                disagree.append({"task_id": task["id"], "criterion": rubric[i]["criterion"],
                                 "human": "pass" if human[i] else "fail",
                                 "judge": v["verdict"], "reason": v.get("reason", "")})
    rows = sorted(({**c, "agreement": c["agree"] / c["n"]} for c in per.values()),
                  key=lambda c: c["agreement"])
    return {"model": model, "n": len(pairs),
            "agreement": (sum(h == j for h, j in pairs) / len(pairs)) if pairs else None,
            "kappa": kappa(pairs), "per_criterion": rows, "disagreements": disagree[:50]}
