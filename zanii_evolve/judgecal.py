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
            "kappa": kappa(pairs), "kappa_range95": kappa_range(pairs),
            "raters": sorted({str(lab["rater"]) for lab in labels if lab.get("rater")}),
            "per_criterion": rows, "disagreements": disagree[:50]}


def kappa_range(pairs: list[tuple[bool, bool]], reps: int = 2000,
                seed: int = 7) -> list[float] | None:
    """95% range of kappa from resampling the judged pairs (seeded); None when
    there are too few pairs or every resample has constant raters."""
    import random
    if len(pairs) < 2:
        return None
    rng = random.Random(seed)
    ks = sorted(k for k in (kappa([pairs[rng.randrange(len(pairs))] for _ in pairs])
                            for _ in range(reps)) if k is not None)
    if not ks:
        return None
    return [round(ks[int(0.025 * len(ks))], 4), round(ks[int(0.975 * len(ks)) - 1], 4)]


RECORD = "judge_agreement.json"


def record(run_dir, result: dict, labels_path=None, raters_path=None) -> dict:
    """Keep an agreement result as runs/<domain>/judge_agreement.json, for the gate
    and the certificate: the judge model, kappa, agreement and how many pairs.
    Also kept when known: kappa's 95% range, who graded (the labels' `rater`
    field, with credentials from raters.json [{id, credential}]) and the sha256 of
    the labels file, so a judge pack proves which human grades it was checked on."""
    import hashlib
    import json
    import time
    from pathlib import Path

    kept = {"model": result["model"], "kappa": result["kappa"],
            "agreement": result["agreement"], "n": result["n"],
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if result.get("kappa_range95") is not None:
        kept["kappa_range95"] = result["kappa_range95"]
    # only people who graded a label; raters.json adds their credentials, never names
    raters = [{"id": r} for r in result.get("raters") or []]
    if raters_path:
        creds = {str(r["id"]): r for r in json.loads(Path(raters_path).read_text(
            encoding="utf-8"))}
        raters = [{**r, **({"credential": str(creds[r["id"]]["credential"]),
                            "credential_source": "self-declared (raters.json, not verified)"}
                           if creds.get(r["id"], {}).get("credential") else {})}
                  for r in raters]
    if raters:
        kept["raters"] = raters
    if labels_path:
        kept["labels_sha256"] = hashlib.sha256(Path(labels_path).read_bytes()).hexdigest()
    p = Path(run_dir)
    p.mkdir(parents=True, exist_ok=True)
    (p / RECORD).write_text(json.dumps(kept, indent=1), encoding="utf-8")
    return kept


def recorded(run_dir) -> dict | None:
    """The recorded agreement, or None."""
    import json
    from pathlib import Path

    p = Path(run_dir) / RECORD
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def gate(run_dir, model: str, min_kappa: float | None) -> str | None:
    """Why a rubric run may not start yet, or None. Off when min_kappa is None. The
    judge must have been checked against people, with this exact model, and agree
    with them at least as well as min_kappa."""
    if min_kappa is None:
        return None
    rec = recorded(run_dir)
    if rec is None:
        return (f"min_judge_kappa is {min_kappa}: no recorded judge agreement yet. Grade a "
                f"few deliverables by hand, then `judge-agreement --labels human.jsonl --record`.")
    if rec.get("model") != model:
        return (f"the recorded judge agreement is for {rec.get('model')!r}, the judge now is "
                f"{model!r}: record it again with `judge-agreement --record`.")
    k = rec.get("kappa")
    if k is None or k < min_kappa:
        return (f"the judge agrees with people at kappa {k}, below min_judge_kappa "
                f"{min_kappa}: rewrite the weakest criteria (see `judge-agreement`) and record "
                f"again.")
    return None

