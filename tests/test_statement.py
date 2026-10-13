"""Signed statements, the paired comparison, the script check and the incident split."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import compare as CMP  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402
from zanii_evolve.brief import load_tasks, run_check  # noqa: E402
from zanii_evolve.evaluate import TaskResult, aggregate  # noqa: E402


def _ev(scores: dict[str, float], k: int = 2, tok: int = 100):
    return aggregate("j", k, {t: TaskResult(rewards=[s] * k, tokens=[tok] * k)
                              for t, s in scores.items()})


def test_statement_hmac_and_tamper(monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "k1")
    s = ST.issue("savings", {"saved": "12.50", "currency": "AED"}, identity={})
    assert ST.verify(s, kind="savings") == (True, "savings: digest ok; HMAC ok")
    assert not ST.verify(s, kind="migration")[0]
    assert not ST.verify(s, key="other")[0]
    s["body"]["saved"] = "99.00"
    assert ST.verify(s)[1].startswith("digest mismatch")


def test_a_stripped_signature_never_verifies(monkeypatch):
    """The digest is unkeyed: edit the body, recompute it, drop the signature."""
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "k1")
    s = ST.issue("judge_pack", {"kappa": "0.41"}, identity={})
    forged = {k: v for k, v in s.items() if k != "signature"}
    forged["body"] = {"kappa": "0.95"}
    forged["digest"] = ST._digest(forged)
    assert not ST.verify(forged)[0]                         # a key is set: must be signed
    assert not ST.verify(forged, allow_unsigned=True)[0]
    monkeypatch.delenv("ZANII_EVOLVE_SIGNING_KEY")
    assert ST.verify(forged)[1].startswith("unsigned")
    assert ST.verify(forged, allow_unsigned=True)[0]        # only when the caller opts in
    assert not ST.verify({"schema": ST.SCHEMA, "digest": "x"})[0]     # malformed, no KeyError
    assert not ST.verify(None)[0]  # type: ignore[arg-type]


def test_statement_receipts_are_chained(tmp_path, monkeypatch):
    pytest.importorskip("zanii")
    from zanii_evolve.report import new_identity
    monkeypatch.delenv("ZANII_EVOLVE_SIGNING_KEY", raising=False)
    owner, ident = new_identity()
    a = ST.issue("redteam", {"n": 1}, tmp_path, ident)
    b = ST.issue("redteam", {"n": 2}, tmp_path, ident)
    ok, why = ST.verify(b, owner=owner["did"])
    assert ok and "issued under owner" in why
    assert b["receipt"]["prev"] and len((tmp_path / "statements.jsonl").read_text(
        encoding="utf-8").splitlines()) == 2
    assert not ST.verify(a, owner="did:key:zSomeoneElse")[0]
    a["receipt"]["target"] = "evolve.savings"
    assert not ST.verify(a)[0]


def test_paired_verdicts():
    base = _ev({f"t{i}": 0.8 for i in range(20)})
    same = CMP.paired(base, _ev({f"t{i}": 0.8 for i in range(20)}), margin=0.02)
    assert same["verdict"] == "unclear" and same["non_inferior"] and same["diff"] == 0
    drop = CMP.paired(base, _ev({f"t{i}": 0.6 for i in range(20)}), margin=0.02)
    assert drop["verdict"] == "worse" and not drop["non_inferior"]
    assert len(drop["worst_tasks"]) == 5
    up = CMP.paired(base, _ev({f"t{i}": 0.9 if i % 2 else 0.8 for i in range(20)}))
    assert up["verdict"] == "better" and up["range95"][0] > 0
    with pytest.raises(ValueError):
        CMP.paired(base, _ev({"other": 1.0}))


def test_models_context_restores_env(monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_POLICY_MODEL", "a/1")
    monkeypatch.delenv("ZANII_EVOLVE_JUDGE_MODEL", raising=False)
    import os
    with CMP.models("b/2", "j/3"):
        assert os.environ["ZANII_EVOLVE_POLICY_MODEL"] == "b/2"
    assert os.environ["ZANII_EVOLVE_POLICY_MODEL"] == "a/1"
    assert "ZANII_EVOLVE_JUDGE_MODEL" not in os.environ


@pytest.mark.parametrize("text,script,ok", [
    ("مرحبا، رقم طلبك 1234", "arabic", True),
    ("Your order is ready", "arabic", False),
    ("Your order جاهز now", "latin", True),
    ("", "arabic", False),
])
def test_script_check(text, script, ok):
    assert run_check({"type": "script", "script": script, "min": 0.6}, text)[0] is ok


def test_incident_split_loads(tmp_path):
    p = tmp_path / "tasks.jsonl"
    p.write_text('{"id": "i1", "split": "incident", "brief": "b", "rubric": '
                 '[{"criterion": "c"}]}\n', encoding="utf-8")
    assert load_tasks(p)[0]["split"] == "incident"
