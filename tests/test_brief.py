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
"""Providers, the LLM retry loop and the brief-domain engine, offline.

    python3 -m pytest -q tests/test_brief.py
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zanii_evolve import llm, providers                           # noqa: E402
from zanii_evolve.brief import (BriefDomain, load_tasks, public_view,  # noqa: E402
                                run_check)
from zanii_evolve.domain import load_domain                       # noqa: E402
from zanii_evolve.evaluate import aggregate                       # noqa: E402

FAKE = "fake=fake_provider:Fake"


@pytest.fixture
def fake_llm(monkeypatch):
    tests_dir = str(Path(__file__).resolve().parent)
    monkeypatch.syspath_prepend(tests_dir)
    monkeypatch.setenv("ZANII_EVOLVE_PROVIDERS", FAKE)
    monkeypatch.setenv("PYTHONPATH", tests_dir)
    monkeypatch.setenv("ZANII_EVOLVE_POLICY_MODEL", "fake/policy")
    monkeypatch.setenv("ZANII_EVOLVE_JUDGE_MODEL", "fake/judge")
    monkeypatch.setattr(providers, "PROVIDERS", {})
    yield


# ---------------------------------------------------------------- providers --
def test_resolve_prefixes(monkeypatch):
    monkeypatch.setattr(providers, "PROVIDERS", {})
    monkeypatch.delenv("ZANII_EVOLVE_VERTEX_PROJECTS", raising=False)
    p, m = providers.resolve("anthropic/claude-opus-5-5")
    assert isinstance(p, providers.AnthropicProvider) and m == "claude-opus-5-5"
    p, m = providers.resolve("openai/meta-llama/llama-4")
    assert isinstance(p, providers.OpenAICompatProvider) and m == "meta-llama/llama-4"
    assert isinstance(providers.resolve("claude-opus-5-5")[0], providers.AnthropicProvider)
    monkeypatch.setenv("ZANII_EVOLVE_VERTEX_PROJECTS", "proj-a")
    assert isinstance(providers.resolve("claude-opus-4-8")[0], providers.VertexProvider)
    with pytest.raises(providers.ProviderError) as e:
        providers.resolve("nope/model")
    assert not e.value.retryable


def test_vertex_without_projects_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ZANII_EVOLVE_VERTEX_PROJECTS", raising=False)
    with pytest.raises(providers.ProviderError, match="ZANII_EVOLVE_VERTEX_PROJECTS"):
        providers.VertexProvider()._client()


def test_complete_retries_transient_then_succeeds(monkeypatch):
    calls = []

    class Flaky:
        def complete(self, model, system, messages, max_tokens, effort=None):
            calls.append(1)
            if len(calls) < 3:
                raise providers.ProviderError("503")
            return providers.Reply("OK", 3, 1)

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    providers.register("flaky", Flaky())
    r = llm.complete("hi", model="flaky/m")
    assert (r.text, r.total_tokens, len(calls)) == ("OK", 4, 3)


def test_complete_raises_non_retryable_at_once(monkeypatch):
    calls = []

    class Bad:
        def complete(self, *a, **k):
            calls.append(1)
            raise providers.ProviderError("401", retryable=False)

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    providers.register("bad", Bad())
    with pytest.raises(providers.ProviderError):
        llm.complete("hi", model="bad/m")
    assert len(calls) == 1


def test_provider_plugin_env(fake_llm):
    assert "Porto" in llm.generate("hello", model="fake/x")


# ------------------------------------------------------------------- checks --
@pytest.mark.parametrize("check,text,ok", [
    ({"type": "max_words", "value": 3}, "one two three", True),
    ({"type": "max_words", "value": 2}, "one two three", False),
    ({"type": "min_words", "value": 2}, "one", False),
    ({"type": "max_chars", "value": 5}, "123456", False),
    ({"type": "must_include", "values": ["Book a visit"]}, "please book A VISIT", True),
    ({"type": "must_include_any", "values": ["x", "porto"]}, "Porto", True),
    ({"type": "must_not_include", "values": ["painless"]}, "a Painless visit", False),
    ({"type": "regex", "pattern": r"(?im)^button:"}, "Headline: a\nButton: go", True),
    ({"type": "regex_count", "pattern": r"#\w+", "min": 3, "max": 5}, "#a #b", False),
    ({"type": "field_max_chars", "field": "Headline 1", "value": 5}, "Headline 1: abc", True),
    ({"type": "field_max_chars", "field": "Headline 2", "value": 5}, "Headline 1: abc", False),
])
def test_checks(check, text, ok):
    assert run_check(check, text)[0] is ok


def test_load_tasks_validates(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"id": "../evil", "split": "evolve", "checks": [{"type": "max_chars", "value": 1}]}\n')
    with pytest.raises(ValueError, match="task id"):
        load_tasks(p)
    p.write_text('{"id": "a", "split": "evolve", "rubric": [{"criterion": "x"}]}\n' * 2)
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(p)
    p.write_text("")
    with pytest.raises(ValueError, match="no tasks"):
        load_tasks(p)


def test_public_view_hides_grader_fields():
    t = {"id": "a", "split": "evolve", "brief": "b", "checks": [1], "rubric": [2], "expected": 3}
    assert public_view(t) == {"id": "a", "brief": "b"}


# --------------------------------------------------------------- end to end --
@pytest.mark.parametrize("name", ["memory", "marketing"])
def test_domain_end_to_end(name, fake_llm, tmp_path):
    dom = load_domain(name)
    ids = dom.smoke_ids()
    dom.run(ROOT, tmp_path, "job", ids, k=1)
    per, extra = dom.score(tmp_path, "job", ids, k=1)
    for t in ids:
        out = tmp_path / "jobs" / "job" / "s0" / t
        res = json.loads((out / "result.json").read_text())
        assert res["output"] and res["usage"]["total_tokens"] > 0
        assert "checks" not in json.dumps(json.loads((tmp_path / "jobs" / "job" / "s0"
                                                      / "_tasks.json").read_text()))
        sc = json.loads((out / "scores.json").read_text())
        assert sc["total"] > 0 and 0 <= sc["passed"] <= sc["total"]
        assert per[t].missing == 0 and per[t].tokens[0] > 0
        rec = dom.load_trial(tmp_path, "job", t, 0)
        assert "## grade" in dom.render_trace(rec) and t in dom.task_row(t, rec, per[t])
    ev = aggregate("job", 1, per, extra)
    assert 0 < ev.S <= 1 and ev.missing == 0
    if name == "memory":
        assert json.loads((tmp_path / "jobs/job/s0" / ids[0] / "result.json").read_text())[
            "memory_source"] in ("zaniidb", "transcript")


def test_missing_trial_counts_as_failed(fake_llm, tmp_path):
    dom = load_domain("marketing")
    per, _ = dom.score(tmp_path, "never-ran", dom.smoke_ids(), k=2)
    assert all(tr.missing == 2 and tr.mean == 0 for tr in per.values())


def test_smoke_gate(fake_llm, tmp_path):
    dom = load_domain("memory")
    ok, detail = dom.smoke(ROOT, tmp_path, "smoke", dom.smoke_ids())
    assert ok, detail


def test_template_is_a_valid_domain(tmp_path):
    d = tmp_path / "domains" / "clients"
    shutil.copytree(ROOT / "zanii_evolve" / "templates" / "brief", d)
    dom = BriefDomain(d)
    assert dom.name == "clients" and dom.evolve_ids() and dom.smoke_ids()
    assert set(dom.briefs) == {"analyst", "digester", "proposer", "critic"}


def test_critic_patterns_catch_task_ids_and_grader():
    import re
    dom = load_domain("marketing")
    pats = [re.compile(p) for p, _ in dom.critic_patterns]
    assert any(p.search('+ if task["id"] == "mkt-007":') for p in pats)
    assert any(p.search("+ open('scores.json')") for p in pats)
    assert any(p.search("+ # tuned for Fernleaf") for p in pats)
    assert not any(p.search("+ SYSTEM_PROMPT = 'Check every requirement.'") for p in pats)
