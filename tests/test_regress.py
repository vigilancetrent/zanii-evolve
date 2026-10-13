"""The no-regression gate: head vs base on the splits nobody selects on."""

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import compare as CMP  # noqa: E402
from zanii_evolve import regress as RG  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402
from zanii_evolve.evaluate import TaskResult, aggregate  # noqa: E402


class Dom:
    name = "acme"

    def heldout_ids(self):
        return [f"h{i}" for i in range(12)]

    def incident_ids(self):
        return [f"i{i}" for i in range(6)]

    def redteam_ids(self):
        return []


def _ctx(tmp_path):
    return SimpleNamespace(domain=Dom(), run_dir=tmp_path, cfg=SimpleNamespace(k=2),
                           checkout=lambda label, ref: tmp_path / label)


def _run(tmp_path, monkeypatch, head_scores, *extra):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "k")
    monkeypatch.setattr(RG, "_rev", lambda ctx, ref: ("b" if ref == "main" else "h") * 40)
    seen = []

    def measure(domain, root, runs_dir, job, ids, k):
        seen.append(job)
        score = head_scores if "regress_hhh" in job else (lambda t: 0.8)
        return aggregate(job, k, {t: TaskResult(rewards=[score(t)] * k) for t in ids})

    monkeypatch.setattr(CMP, "measure", measure)
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="acme")
    RG.cli(ap.add_subparsers(dest="cmd"))
    args = ap.parse_args(["regress", "--base", "main", "--out", str(tmp_path / "s.json"),
                          "--markdown", str(tmp_path / "s.md"), *extra])
    return args.product(args, _ctx(tmp_path)), seen


def test_unchanged_head_passes(tmp_path, monkeypatch):
    rc, seen = _run(tmp_path, monkeypatch, lambda t: 0.8)
    assert rc == 0
    assert sorted(seen) == ["regress_bbbbbbbbbbbb_heldout", "regress_bbbbbbbbbbbb_incident",
                            "regress_hhhhhhhhhhhh_heldout", "regress_hhhhhhhhhhhh_incident"]
    stmt = ST.load(tmp_path / "s.json")
    assert ST.verify(stmt, kind="regression")[0] and stmt["body"]["passed"]
    assert set(stmt["body"]["sets"]) == {"heldout", "incident"}      # redteam is empty
    assert "PASS" in (tmp_path / "s.md").read_text(encoding="utf-8")


def test_incident_regression_fails(tmp_path, monkeypatch):
    rc, _ = _run(tmp_path, monkeypatch, lambda t: 0.2 if t.startswith("i") else 0.8)
    assert rc == 1
    md = (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "FAIL" in md and "**regressed**" in md and "worst: i" in md
    body = ST.load(tmp_path / "s.json")["body"]
    assert body["sets"]["heldout"]["non_inferior"] and not body["sets"]["incident"]["non_inferior"]


def test_small_drop_inside_margin_passes(tmp_path, monkeypatch):
    rc, _ = _run(tmp_path, monkeypatch, lambda t: 0.78, "--margin", "0.05")
    assert rc == 0
    rc, _ = _run(tmp_path, monkeypatch, lambda t: 0.78, "--margin", "0.01")
    assert rc == 1


def test_default_margin_is_the_noise_band(tmp_path):
    assert CMP.margin_from(tmp_path, RG.DEFAULT_MARGIN) == 0.05
    (tmp_path / "calibration.json").write_text('{"delta": 0.012}', encoding="utf-8")
    assert CMP.margin_from(tmp_path, RG.DEFAULT_MARGIN) == 0.012


def test_action_runs_the_gate():
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert "actions/setup-python@v5" in text and " regress --base" in text
    assert "GITHUB_STEP_SUMMARY" in text and "exit $rc" in text


def test_an_empty_gate_never_passes(tmp_path, monkeypatch):
    # a named split with no tasks fails, even when the others held
    rc, _ = _run(tmp_path, monkeypatch, lambda t: 0.8, "--sets", "heldout,redteam")
    body = ST.load(tmp_path / "s.json")["body"]
    assert rc == 1 and not body["passed"] and body["empty"] == ["redteam"]
    assert "no tasks" in (tmp_path / "s.md").read_text(encoding="utf-8")
    # nothing measured at all is no evidence
    monkeypatch.setattr(Dom, "heldout_ids", lambda self: [])
    monkeypatch.setattr(Dom, "incident_ids", lambda self: [])
    rc, _ = _run(tmp_path, monkeypatch, lambda t: 0.8)
    assert rc == 1 and not ST.load(tmp_path / "s.json")["body"]["passed"]
    with pytest.raises(SystemExit):
        _run(tmp_path, monkeypatch, lambda t: 0.8, "--sets", "")
