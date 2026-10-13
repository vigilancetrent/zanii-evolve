"""Model migration: both models on the same harness, signed safe/not-safe verdict."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import blackbox as BB  # noqa: E402
from zanii_evolve import compare as CMP  # noqa: E402
from zanii_evolve import migrate as MG  # noqa: E402
from zanii_evolve import providers  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402
from zanii_evolve import tasksets as TS  # noqa: E402
from zanii_evolve.brief import BriefDomain  # noqa: E402
from zanii_evolve.evaluate import TaskResult, aggregate  # noqa: E402

FAKE = "fake=fake_provider:Fake"


@pytest.fixture
def fake_llm(monkeypatch):
    tests_dir = str(Path(__file__).resolve().parent)
    monkeypatch.syspath_prepend(tests_dir)
    monkeypatch.setenv("ZANII_EVOLVE_PROVIDERS", FAKE)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([tests_dir, str(ROOT)]))
    monkeypatch.setenv("ZANII_EVOLVE_JUDGE_MODEL", "fake/judge")
    monkeypatch.delenv("ZANII_EVOLVE_POLICY_MODEL", raising=False)
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "test-key")
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    monkeypatch.setattr(providers, "PROVIDERS", {})
    yield


def _domain(tmp_path):
    tasks = [{"id": f"t{i}", "split": "heldout" if i < 6 else "evolve", "brief": f"Task {i}",
              "rubric": [{"criterion": "Answers the request"}]} for i in range(8)]
    return BriefDomain(TS.create_domain("mig", tasks, domains_dir=tmp_path / "domains"))


def _args(**kw):
    base = {"plan": False, "from_model": "fake/old", "to_model": "fake/new", "sets": "heldout",
            "ref": None, "margin": None, "trials": 1, "out": None, "domain": "mig"}
    return SimpleNamespace(**{**base, **kw})


def _ctx(tmp_path, dom):
    return SimpleNamespace(domain=dom, run_dir=tmp_path / "runs" / "mig",
                           cfg=SimpleNamespace(k=1), checkout=lambda label, ref: tmp_path)


def test_migration_end_to_end_is_safe_and_signed(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(MG.G, "rev", lambda cwd, ref: "abc1234")
    dom = _domain(tmp_path)
    assert MG.handler(_args(sets="heldout,redteam"), _ctx(tmp_path, dom)) == 0
    stmt = ST.load(tmp_path / "runs" / "mig" / "migration.json")
    assert ST.verify(stmt, kind="migration")[0]
    b = stmt["body"]
    assert b["safe"] and b["from"] == "fake/old" and b["judge"] == "fake/judge"
    assert list(b["splits"]) == ["heldout"]              # redteam has no tasks: skipped
    assert b["splits"]["heldout"]["n_tasks"] == 6 and b["splits"]["heldout"]["S_a"] > 0
    assert b["splits"]["heldout"]["missing_b"] == 0
    # both arms ran, on the same tasks, as separate jobs keyed by commit, judge and model
    jobs = tmp_path / "runs" / "mig" / "jobs"
    tag = f"abc1234_{CMP.slug('fake/judge')}"
    for m in ("fake/old", "fake/new"):
        assert (jobs / f"migrate_{tag}_{CMP.slug(m)}_heldout" / "eval.json").is_file()


def test_jobs_never_reuse_another_commits_trials(tmp_path, monkeypatch):
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    jobs = []

    def measure(domain, root, runs_dir, job, ids, k, policy=None, judge=None):
        jobs.append(job)
        return aggregate(job, k, {t: TaskResult(rewards=[0.9]) for t in ids})

    monkeypatch.setattr(CMP, "measure", measure)
    dom = SimpleNamespace(name="mig", cfg={}, heldout_ids=lambda: list("abcdef"))
    for commit in ("aaaa1111", "bbbb2222"):
        monkeypatch.setattr(MG.G, "rev", lambda cwd, ref, c=commit: c)
        MG.compare_models(dom, tmp_path, tmp_path, "x/a", "x_a", ["heldout"], 1, 0.0)
    assert len(set(jobs)) == 4                       # 2 commits x 2 models, "x/a" != "x_a"
    assert CMP.slug("x/a") != CMP.slug("x_a")


def test_a_worse_model_is_not_safe(tmp_path, monkeypatch):
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    monkeypatch.setattr(MG.G, "rev", lambda cwd, ref: "abc1234")
    seen = []

    def measure(domain, root, runs_dir, job, ids, k, policy=None, judge=None):
        seen.append((policy, judge))
        s = 0.9 if policy == "fake/old" else 0.5
        return aggregate(job, k, {t: TaskResult(rewards=[s]) for t in ids})

    monkeypatch.setattr(CMP, "measure", measure)
    dom = SimpleNamespace(name="mig", cfg={"judge_model": "j/1"},
                          heldout_ids=lambda: list("abcdef"), incident_ids=lambda: ["x"])
    assert MG.handler(_args(sets="heldout,incident", margin=0.05), _ctx(tmp_path, dom)) == 1
    b = ST.load(tmp_path / "runs" / "mig" / "migration.json")["body"]
    assert not b["safe"] and set(b["splits"]) == {"heldout", "incident"}
    assert {j for _, j in seen} == {"j/1"}               # one judge for both arms
    assert MG.handler(_args(sets="nope"), _ctx(tmp_path, dom)) == 2
    assert MG.handler(_args(to_model=None), _ctx(tmp_path, dom)) == 2


def test_deprecated_models_from_blackbox():
    pages = {
        "/v1/sessions?label=ops&status=closed&limit=100": {"sessions": [
            {"session_id": "s1"}, {"session_id": "s2"}, {"session_id": "s3"}]},
        "/v1/sessions/s1/findings": {"findings": [
            {"code": "MODEL_DEPRECATED", "meta": {"ref": {"seq": 4, "model": "old-1"}}},
            {"code": "D1_REPEAT_CALL", "meta": {"ref": {"tool": "x"}}}]},
        "/v1/sessions/s2/findings": {"findings": [
            {"code": "MODEL_DEPRECATED", "meta": {"ref": {"model": "old-1"}}},
            {"code": "MODEL_DEPRECATED", "meta": {"ref": {"model": "old-2"}}}]},
    }

    def get(path):
        if path not in pages:
            raise RuntimeError(f"Blackbox answered 404 for {path}")
        return json.dumps(pages[path]).encode()

    assert BB.deprecated_models(get, "ops") == {"old-1": 2, "old-2": 1}
