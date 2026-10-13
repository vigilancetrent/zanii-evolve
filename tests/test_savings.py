"""Savings statement: exact money from token usage, certified only if quality held."""

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import savings as SV  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402
from zanii_evolve.evaluate import TaskResult, aggregate  # noqa: E402

PRICES = {"currency": "AED", "models": {
    "big": {"input_per_mtok": "11.02", "output_per_mtok": "55.08"},
    "small": {"input_per_mtok": "3.67", "output_per_mtok": "18.36"}}}


def _job(run_dir, job, score, inp, out, n=6, k=2):
    for s in range(k):
        for t in range(n):
            d = run_dir / "jobs" / job / f"s{s}" / f"t{t}"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps(
                {"output": "x", "usage": {"input_tokens": inp, "output_tokens": out}}),
                encoding="utf-8")
    aggregate(job, k, {f"t{t}": TaskResult(rewards=[score] * k) for t in range(n)}).save(
        run_dir / "jobs" / job / "eval.json")


def _run(tmp_path, monkeypatch, new_score, **kw):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "test-key")
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    monkeypatch.delenv("ZANII_EVOLVE_POLICY_MODEL", raising=False)
    rd = tmp_path / "runs" / "ops"
    _job(rd, "base", 0.8, 20_000, 2_000)
    _job(rd, "r3_v1", new_score, 8_000, 1_000)
    (rd / "frontier.json").write_text(json.dumps({"incumbent": {"job": "r3_v1"}}),
                                      encoding="utf-8")
    pf = tmp_path / "prices.json"
    pf.write_text(json.dumps(PRICES), encoding="utf-8")
    args = SimpleNamespace(**{"prices": pf, "runs_per_month": 100_000, "base_job": "base",
                              "new_job": None, "base_model": "big", "new_model": "big",
                              "margin": 0.02, "out": None, **kw})
    ctx = SimpleNamespace(args=SimpleNamespace(domain="ops"), run_dir=rd,
                          domain=SimpleNamespace(cfg={"policy_model": "big"}))
    return SV.handler(args, ctx), rd


def test_certified_saving_exact_money(tmp_path, monkeypatch):
    rc, rd = _run(tmp_path, monkeypatch, 0.8)
    assert rc == 0
    stmt = ST.load(rd / "savings.json")
    assert ST.verify(stmt, kind="savings")[0]
    b = stmt["body"]
    # base: 20k x 11.02 + 2k x 55.08 per 1M = 0.2204 + 0.11016 = 0.33056 AED a run
    # new:   8k x 11.02 + 1k x 55.08 per 1M = 0.08816 + 0.05508 = 0.14324 AED a run
    assert b["base"]["cost_per_run"] == "0.330560" and b["new"]["cost_per_run"] == "0.143240"
    assert b["saved_per_month"] == "18732.00"            # 0.18732 x 100,000, exact
    assert b["saved_percent"] == "56.7" and b["certified"] and b["new"]["job"] == "r3_v1"
    assert b["base"]["trials"] == 12


def test_cheaper_but_worse_is_not_certified(tmp_path, monkeypatch):
    rc, rd = _run(tmp_path, monkeypatch, 0.5)
    b = ST.load(rd / "savings.json")["body"]
    assert rc == 1 and not b["certified"] and b["quality"]["verdict"] == "worse"


def test_cheaper_model_on_the_same_harness(tmp_path, monkeypatch):
    rc, rd = _run(tmp_path, monkeypatch, 0.8, new_model="anthropic/small")
    assert rc == 0 and ST.load(rd / "savings.json")["body"]["new"]["model"] == "anthropic/small"


@pytest.mark.parametrize("bad", [
    {"currency": "dirhams", "models": PRICES["models"]},
    {"currency": "AED", "models": {"m": {"input_per_mtok": 1.5, "output_per_mtok": "2"}}},
    {"currency": "AED", "models": {"m": {"input_per_mtok": "-1", "output_per_mtok": "2"}}},
    {"currency": "AED", "models": {"m": {"input_per_mtok": "NaN", "output_per_mtok": "2"}}},
    {"currency": "AED", "models": {}},
])
def test_bad_price_files(tmp_path, bad):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        SV.load_prices(p)


def test_money_minor_units():
    assert SV.money(Decimal("1.005"), "AED") == "1.00"       # banker's rounding
    assert SV.money(Decimal("1.2345"), "KWD") == "1.234"
    assert SV.money(Decimal("99.5"), "JPY") == "100"


def test_only_measured_tasks_with_token_counts_are_priced(tmp_path):
    rd = tmp_path / "runs"
    _job(rd, "j", 0.8, 1_000_000, 0)
    price = {"input_per_mtok": Decimal(2), "output_per_mtok": Decimal(0)}
    # a stray result outside eval.json (an older, cheaper run) is not counted
    stray = rd / "jobs" / "j" / "s0" / "zz"
    stray.mkdir()
    (stray / "result.json").write_text(json.dumps(
        {"usage": {"input_tokens": 1, "output_tokens": 1}}), encoding="utf-8")
    assert SV.job_cost(rd, "j", price) == (Decimal(2), 12)
    # a trial with no usage would cost 0: refused, never counted as cheap
    (rd / "jobs" / "j" / "s1" / "t0" / "result.json").write_text(
        json.dumps({"output": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="token counts"):
        SV.job_cost(rd, "j", price)


def test_jobs_on_different_tasks_are_refused(tmp_path, monkeypatch):
    rd = tmp_path / "runs" / "ops"
    _job(rd, "base", 0.8, 20_000, 2_000, n=8)
    _job(rd, "r3_v1", 0.8, 8_000, 1_000, n=6)       # skipped the two hardest tasks
    with pytest.raises(ValueError, match="different tasks"):
        SV.statement_body("ops", rd, {"currency": "AED", "models": {
            "big": {"input_per_mtok": Decimal(1), "output_per_mtok": Decimal(1)}}},
            10, "base", "r3_v1", "big", "big", 0.02)
