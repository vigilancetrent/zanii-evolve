"""Per-tag sub-scores (dialects.py) and the Arabic service domain."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import dialects as DI  # noqa: E402
from zanii_evolve import providers  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402
from zanii_evolve.brief import BriefDomain  # noqa: E402
from zanii_evolve.evaluate import TaskResult, aggregate  # noqa: E402
from zanii_evolve.tasksets import validate  # noqa: E402

AR = ROOT / "domains" / "arabic_service"


@pytest.fixture
def fake_llm(monkeypatch):
    tests_dir = str(Path(__file__).resolve().parent)
    monkeypatch.syspath_prepend(tests_dir)
    monkeypatch.setenv("ZANII_EVOLVE_PROVIDERS", "fake=fake_provider:Fake")
    monkeypatch.setenv("PYTHONPATH", tests_dir)
    monkeypatch.setenv("ZANII_EVOLVE_POLICY_MODEL", "fake/policy")
    monkeypatch.setenv("ZANII_EVOLVE_JUDGE_MODEL", "fake/judge")
    monkeypatch.setattr(providers, "PROVIDERS", {})
    yield


def _ev(scores):
    return aggregate("j", 2, {t: TaskResult(rewards=[s, s]) for t, s in scores.items()})


def test_breakdown_groups_by_any_tag():
    tasks = {"a": {"notes": {"dialect": "gulf", "industry": "bank"}},
             "b": {"notes": {"dialect": "gulf", "industry": "telco"}},
             "c": {"notes": {"dialect": "egyptian", "industry": "bank"}},
             "d": {}}
    ev = _ev({"a": 1.0, "b": 0.5, "c": 0.25, "d": 0.0})
    g = DI.breakdown(ev, tasks)
    assert g["gulf"]["n"] == 2 and g["gulf"]["mean"] == 0.75
    assert g["gulf"]["range95"][0] <= 0.75 <= g["gulf"]["range95"][1]
    assert g["egyptian"] == {"n": 1, "mean": 0.25, "range95": [0.25, 0.25]}
    assert g["untagged"]["n"] == 1
    assert DI.breakdown(ev, tasks, key="industry")["bank"]["mean"] == 0.625
    assert DI.breakdown(ev, tasks) == g                       # seeded: same ranges every time


def test_cli_signs_dialect_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "k")
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    sub = argparse.ArgumentParser().add_subparsers(dest="cmd")
    DI.cli(sub)
    run_dir = tmp_path / "runs" / "d"
    (run_dir / "jobs" / "h").mkdir(parents=True)
    _ev({"a": 1.0, "c": 0.5}).save(run_dir / "jobs" / "h" / "eval.json")
    ctx = SimpleNamespace(run_dir=run_dir, domain=SimpleNamespace(tasks={
        "a": {"notes": {"dialect": "gulf"}}, "c": {"notes": {"dialect": "msa"}}}))
    args = sub._name_parser_map["dialects"].parse_args(["--job", "h", "--sign"])
    args.domain = "d"
    assert args.product(args, ctx) == 0
    stmt = ST.load(run_dir / "dialect_scores_h_dialect.json")
    assert ST.verify(stmt, kind="dialect_scores")[0]
    assert stmt["body"]["groups"]["msa"]["mean"] == 0.5
    args = sub._name_parser_map["dialects"].parse_args(["--job", "missing"])
    args.domain = "d"
    assert args.product(args, ctx) == 1


def test_arabic_domain_tasks_are_valid_and_spread():
    dom = BriefDomain(AR)
    tasks = list(dom.tasks.values())
    validate(tasks)
    assert Counter(t["split"] for t in tasks) == {"evolve": 24, "heldout": 8, "smoke": 4}
    dialects = Counter(t["notes"]["dialect"] for t in tasks)
    assert set(dialects) == {"gulf", "egyptian", "levantine", "msa", "code_switch", "arabizi"}
    assert len({t["notes"]["industry"] for t in tasks}) >= 10
    for t in tasks:
        kinds = [c["type"] for c in t["checks"]]
        if t["notes"]["dialect"] in ("gulf", "egyptian", "levantine", "msa"):
            assert {"type": "script", "script": "arabic", "min": 0.6} in t["checks"]
        if t["notes"]["dialect"] == "arabizi":
            assert t["checks"][0]["script"] == "latin"
        assert "max_words" in kinds
    # every heldout dialect also appears in evolve, so held-out scores are comparable
    held = {t["notes"]["dialect"] for t in tasks if t["split"] == "heldout"}
    assert held <= {t["notes"]["dialect"] for t in tasks if t["split"] == "evolve"}


def test_arabic_domain_runs_end_to_end(fake_llm, tmp_path):
    dom = BriefDomain(AR)
    ids = dom.smoke_ids()
    dom.run(ROOT, tmp_path, "job", ids, k=1)
    per, _ = dom.score(tmp_path, "job", ids, k=1)
    assert all(per[t].missing == 0 for t in ids)
    sc = json.loads((tmp_path / "jobs/job/s0" / ids[0] / "scores.json").read_text(
        encoding="utf-8"))
    script = [c for c in sc["criteria"] if "script" in c["criterion"]]
    assert script and script[0]["verdict"] == "fail"           # the fake replies in English
