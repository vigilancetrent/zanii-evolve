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
"""Measure any harness with any model, and compare two measurements fairly.

`measure` runs one harness checkout (`root`) on a list of task ids with the
policy and judge models you name, outside the evolve loop: a model migration,
a pull request, a vendor in a bake-off are all just a root and a model.

`paired` compares B against A on the tasks both measured. Each task's mean
reward is one pair; the 95% range of the mean difference comes from resampling
tasks (seeded, so the same evaluations always give the same verdict):

    better   the whole range is above 0          worse   the whole range is below 0
    unclear  the range straddles 0               non_inferior  low end >= -margin
    too_few  fewer than `min_tasks` tasks: no verdict, never non-inferior

`margin` is how much loss you accept; by default the run's noise band (delta),
so "non-inferior" reads "no drop you could tell apart from re-running A".
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import statistics as st
from contextlib import contextmanager
from pathlib import Path

from .evaluate import EvalResult, evaluate


@contextmanager
def models(policy: str | None = None, judge: str | None = None):
    """Run with these policy/judge models (the harness and grader read the env)."""
    names = {"ZANII_EVOLVE_POLICY_MODEL": policy, "ZANII_EVOLVE_JUDGE_MODEL": judge}
    old = {k: os.environ.get(k) for k in names}
    try:
        for k, v in names.items():
            if v:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def measure(domain, root: Path, runs_dir: Path, job: str, ids: list[str], k: int,
            policy: str | None = None, judge: str | None = None) -> EvalResult:
    """Evaluate the harness under `root` on `ids` (k trials each), saved to
    runs_dir/jobs/<job>/eval.json. Resume-safe like every evaluation."""
    if not ids:
        raise ValueError(f"{job}: no tasks to measure")
    with models(policy, judge):
        ev = evaluate(domain, Path(root), Path(runs_dir), job, ids, k, log_prefix=job)
    path = Path(runs_dir) / "jobs" / job / "eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ev.save(path)
    return ev


def task_means(ev: EvalResult) -> dict[str, float]:
    out = {}
    for t, tr in ev.per_task.items():
        den = sum(tr.weights)
        out[t] = sum(r * w for r, w in zip(tr.rewards, tr.weights)) / den if den else 0.0
    return out


def slug(name: str) -> str:
    """A job/folder-safe name that never collides: "a/b" and "a_b" differ."""
    raw = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)[:40]}-{raw}"


def paired(a: EvalResult, b: EvalResult, margin: float = 0.0, reps: int = 4000,
           seed: int = 7, min_tasks: int = 5) -> dict:
    """B minus A on the same tasks: mean difference, 95% range, verdict. Both
    sides must have measured exactly the same tasks (a side that skips the hard
    ones is refused); under `min_tasks` the verdict is "too_few", never safe."""
    ma, mb = task_means(a), task_means(b)
    if set(ma) != set(mb):
        only_a, only_b = sorted(set(ma) - set(mb)), sorted(set(mb) - set(ma))
        raise ValueError(f"the two measurements cover different tasks: only A {only_a[:5]}, "
                         f"only B {only_b[:5]}")
    shared = sorted(ma)
    if not shared:
        raise ValueError("the two measurements have no tasks")
    diffs = [mb[t] - ma[t] for t in shared]
    rng = random.Random(seed)
    n = len(diffs)
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    lo, hi = boots[int(0.025 * reps)], boots[int(0.975 * reps) - 1]
    mean = st.fmean(diffs)
    few = n < min_tasks
    verdict = "too_few" if few else "better" if lo > 0 else "worse" if hi < 0 else "unclear"
    return {"n_tasks": n, "S_a": round(st.fmean(ma[t] for t in shared), 6),
            "S_b": round(st.fmean(mb[t] for t in shared), 6), "diff": round(mean, 6),
            "range95": [round(lo, 6), round(hi, 6)], "verdict": verdict,
            "margin": round(margin, 6), "non_inferior": not few and lo >= -margin,
            "C_a": a.C, "C_b": b.C, "missing_a": a.missing, "missing_b": b.missing,
            "worst_tasks": [t for _, t in sorted(zip(diffs, shared))[:5] if mb[t] < ma[t]]}


def margin_from(run_dir: Path, default: float = 0.0) -> float:
    """The run's calibrated noise band, or `default` before calibration."""
    from .calibrate import read_delta
    d = read_delta(Path(run_dir) / "calibration.json")
    return d if d is not None else default
