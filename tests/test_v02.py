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
"""0.2: the confirmation run (the winner's curse), the judge gate, invariants of
the selection rules, and tasks from a Zanii Blackbox gateway. Offline: no LLM, no
network.

    python3 -m pytest -q tests/test_v02.py
"""

import itertools
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zanii_evolve import blackbox as BB                                    # noqa: E402
from zanii_evolve import judgecal as JC                                    # noqa: E402
from zanii_evolve import loop as LOOP                                      # noqa: E402
from zanii_evolve import report as R                                       # noqa: E402
from zanii_evolve import tasksets as TS                                    # noqa: E402
from zanii_evolve.config import ZaniiEvolveConfig                          # noqa: E402
from zanii_evolve.evaluate import EvalResult, TaskResult, aggregate        # noqa: E402
from zanii_evolve.schedule import edit_budget                              # noqa: E402
from zanii_evolve.selection import Candidate, confirm, judge, select_round  # noqa: E402

from test_server import _seed_run                                          # noqa: E402


def _ev(job, wins, n=10, tokens=1000.0):
    """n tasks, one trial each, the first `wins` passing: S = wins / n."""
    per = {f"t{i}": TaskResult(rewards=[1.0 if i < wins else 0.0], tokens=[tokens])
           for i in range(n)}
    return aggregate(job, 1, per)


CFG = ZaniiEvolveConfig(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)


# ---------------------------------------------------------------- confirmation --
def test_confirmation_trials_default_to_k_and_can_be_off():
    assert ZaniiEvolveConfig(k=3).confirm_trials == 3
    assert ZaniiEvolveConfig(k=3, confirm_k=5).confirm_trials == 5
    assert ZaniiEvolveConfig(k=3, confirm_k=0).confirm_trials == 0
    with pytest.raises(ValueError):
        ZaniiEvolveConfig(confirm_k=-1)
    with pytest.raises(ValueError):
        ZaniiEvolveConfig(min_judge_kappa=1.5)


def test_a_winner_that_holds_up_is_confirmed_on_its_fresh_measurement():
    inc = _ev("inc", 5)                                   # S = 0.5
    win = Candidate("A", [{"component": "prompt"}], ev=_ev("A", 8))
    d = confirm(win, _ev("A_confirm", 7), inc, 0.5, 0.05, CFG, {})
    assert d.admissible and d.S == pytest.approx(0.7)     # the fresh S, not the selected 0.8
    assert d.delta_S == pytest.approx(0.2)
    assert d.reason.startswith("confirmed on fresh trials: S 0.8000 -> 0.7000")


def test_a_lucky_winner_is_not_confirmed():
    inc = _ev("inc", 5)
    win = Candidate("A", [{"component": "prompt"}], ev=_ev("A", 8))
    d = confirm(win, _ev("A_confirm", 3), inc, 0.5, 0.05, CFG, {})
    assert not d.admissible and d.reason.startswith("not confirmed")
    assert "floor" in d.reason
    # the cost rule and the guards apply to the fresh measurement too
    dear = confirm(win, _ev("A_confirm", 7, tokens=1000.0 * 20), inc, 0.5, 0.05, CFG, {})
    assert not dear.admissible and "cost rule" in dear.reason
    guarded = confirm(win, _ev("A_confirm", 7), inc, 0.5, 0.05, CFG, {},
                      guard_fn=lambda i, c: ["valid rate fell"])
    assert not guarded.admissible and "guard" in guarded.reason


class _Domain:
    name = "toy"

    @staticmethod
    def guards(incumbent, ev):
        return []


def _run(tmp_path, cfg):
    run = LOOP.Run.__new__(LOOP.Run)                     # the confirmation glue only
    run.domain, run.cfg, run.runs = _Domain(), cfg, tmp_path
    run.jobs, run.wt_root = tmp_path / "jobs", tmp_path / "wt"
    return run


def test_the_round_measures_the_winner_again_and_keeps_the_record(tmp_path, monkeypatch):
    calls = []

    def fresh(domain, root, runs, job, ids, k, log_prefix=""):
        calls.append((job, k))
        return _ev(job, 6)

    monkeypatch.setattr(LOOP, "evaluate", fresh)
    run = _run(tmp_path, ZaniiEvolveConfig(k=2, confirm_k=4))
    rdir = tmp_path / "r3"
    (rdir / "A").mkdir(parents=True)
    inc = _ev("inc", 5)
    a, b = (Candidate("A", [{"component": "prompt"}], ev=_ev("A", 9)),
            Candidate("B", [{"component": "skill"}], ev=_ev("B", 7)))
    win, decs = select_round([a, b], inc, 0.5, 0.05, run.cfg, {})
    assert win is a
    win, decs = run._confirm(3, win, [a, b], decs, rdir, ["t0"], inc, 0.5, 0.05, {})
    assert win is a and decs[0].S == pytest.approx(0.6)
    assert calls == [("r3A_confirm", 4)]
    assert EvalResult.load(rdir / "A" / "confirm.json").S == pytest.approx(0.6)
    rec = [json.loads(x) for x in (tmp_path / "confirmations.jsonl").read_text().splitlines()]
    assert rec == [{"t": 3, "variant": "A", "S_selected": 0.9, "C_selected": 1000.0,
                    "S_confirmed": 0.6, "C_confirmed": 1000.0, "k": 4, "confirmed": True}]
    # a resumed round reuses the stored confirmation and keeps one record per winner
    run._confirm(3, a, [a, b], decs, rdir, ["t0"], inc, 0.5, 0.05, {})
    assert len(calls) == 1
    assert len((tmp_path / "confirmations.jsonl").read_text().splitlines()) == 1


def test_a_round_whose_winner_falls_back_accepts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(LOOP, "evaluate", lambda *a, **k: _ev("x", 4))
    run = _run(tmp_path, ZaniiEvolveConfig(k=2))
    rdir = tmp_path / "r0"
    (rdir / "A").mkdir(parents=True)
    inc = _ev("inc", 5)
    a = Candidate("A", [{"component": "prompt"}], ev=_ev("A", 9))
    win, decs = select_round([a], inc, 0.5, 0.05, run.cfg, {})
    win, decs = run._confirm(0, win, [a], decs, rdir, ["t0"], inc, 0.5, 0.05, {})
    assert win is None and decs[0].reason.startswith("not confirmed")
    # a confirmation that cannot be measured is not a confirmation
    monkeypatch.setattr(LOOP, "evaluate", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    (rdir / "A" / "confirm.json").unlink()
    win, decs = select_round([a], inc, 0.5, 0.05, run.cfg, {})
    win, decs = run._confirm(0, win, [a], decs, rdir, ["t0"], inc, 0.5, 0.05, {})
    assert win is None and "could not be measured" in decs[0].reason


def test_the_certificate_reports_confirmations_shrinkage_and_the_judge(tmp_path):
    _seed_run(tmp_path)
    d = tmp_path / "marketing"
    (d / "confirmations.jsonl").write_text(
        json.dumps({"t": 0, "variant": "A", "S_selected": 0.66, "S_confirmed": 0.62,
                    "confirmed": True}) + "\n"
        + json.dumps({"t": 1, "variant": "B", "S_selected": 0.70, "S_confirmed": 0.58,
                      "confirmed": False}) + "\n")
    JC.record(d, {"model": "claude-sonnet-5-5", "kappa": 0.81, "agreement": 0.92, "n": 60})
    cert = R.build(d, key="k1")
    assert cert["schema"] == "zanii-evolve-certificate/3"
    assert cert["selection_shrinkage"] == pytest.approx(0.08)
    assert [c["confirmed"] for c in cert["confirmations"]] == [True, False]
    assert cert["judge"]["kappa"] == 0.81 and cert["judge"]["model"] == "claude-sonnet-5-5"
    assert R.verify(cert, key="k1")[0]
    md = R.markdown(cert)
    assert "## Confirmation runs" in md and "did not hold" in md and "kappa 0.8100" in md
    bare = tmp_path / "bare"
    _seed_run(bare)
    plain = R.build(bare / "marketing")
    assert plain["confirmations"] == [] and plain["selection_shrinkage"] is None
    assert plain["judge"] is None


# ---------------------------------------------------------------- judge gate --
def test_the_judge_gate(tmp_path):
    assert JC.gate(tmp_path, "m1", None) is None                         # off
    assert "no recorded judge agreement" in JC.gate(tmp_path, "m1", 0.6)
    JC.record(tmp_path, {"model": "m1", "kappa": 0.55, "agreement": 0.8, "n": 40})
    assert "below min_judge_kappa" in JC.gate(tmp_path, "m1", 0.6)
    assert "is for 'm1'" in JC.gate(tmp_path, "m2", 0.5)                # another judge
    JC.record(tmp_path, {"model": "m1", "kappa": 0.7, "agreement": 0.9, "n": 40})
    assert JC.gate(tmp_path, "m1", 0.6) is None
    JC.record(tmp_path, {"model": "m1", "kappa": None, "agreement": 1.0, "n": 4})
    assert "kappa None" in JC.gate(tmp_path, "m1", 0.6)                 # constant raters


# ---------------------------------------------------------------- invariants --
def test_selection_invariants_hold_on_random_rounds():
    rng = random.Random(20261013)
    for _ in range(3000):
        n = rng.choice([4, 10, 25])
        inc = _ev("inc", rng.randint(0, n), n, tokens=rng.choice([500.0, 1000.0, 2000.0]))
        S_star = max(inc.S, rng.random())
        delta = rng.choice([0.0, 0.02, 0.05, 0.1])
        cfg = ZaniiEvolveConfig(objective=rng.choice(["score", "cost"]),
                                beta0=rng.choice([0.0, 0.1]), beta1=rng.choice([0.0, 40.0]))
        cands = []
        for v in "ABCD"[:rng.randint(1, 4)]:
            if rng.random() < 0.15:
                cands.append(Candidate(v, [], gate_failure="critic_reject"))
                continue
            cands.append(Candidate(v, [{"component": rng.choice(["prompt", "skill", "memory"])}],
                                   ev=_ev(v, rng.randint(0, n), n,
                                          tokens=rng.choice([400.0, 1000.0, 3000.0]))))
        win, decs = select_round(cands, inc, S_star, delta, cfg, {})
        adm = [(c, d) for c, d in zip(cands, decs) if d.admissible]
        for c, d in zip(cands, decs):
            if d.admissible:
                assert c.ev is not None and c.ev.S >= S_star - delta - 1e-12
        if not adm:
            assert win is None
            continue
        assert any(c is win for c, _ in adm)                     # the winner is admissible
        wd = next(d for c, d in adm if c is win)
        if cfg.objective == "score":
            assert wd.S == max(d.S for _, d in adm)
        else:
            assert wd.C == min(d.C for _, d in adm)
        # confirming on the very same measurement changes nothing
        assert confirm(win, win.ev, inc, S_star, delta, cfg, {}).admissible
        # and judge() is a pure function of its inputs
        again = judge(win, inc, S_star, delta, cfg, {})
        assert (again.admissible, again.S, again.reason) == (wd.admissible, wd.S, wd.reason)


def test_the_edit_budget_stays_in_bounds_and_never_grows():
    for T in (1, 5, 20, 60):
        for lo, hi in ((1, 1), (1, 4), (2, 6)):
            b = [edit_budget(t, T, lo, hi) for t in range(T + 1)]
            assert all(lo <= x <= hi for x in b)
            assert all(x >= y for x, y in itertools.pairwise(b))
            assert b[0] == hi


# ---------------------------------------------------------------- Blackbox --
@pytest.mark.parametrize("body,ask", [
    ({"messages": [{"role": "user", "content": "Refund order 7"}]}, "Refund order 7"),
    ({"messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"},
                                                {"type": "image", "source": {}}]}]}, "Hi"),
    ({"messages": [{"role": "system", "content": "Be brief."},
                   {"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A"},
                   {"role": "user", "content": "Q2"}]}, "Q2"),
    ({"input": "Plain Responses input"}, "Plain Responses input"),
    ({"input": [{"role": "user", "content": [{"type": "input_text", "text": "R"}]}]}, "R"),
    ({"contents": [{"role": "user", "parts": [{"text": "Gemini ask"}]}]}, "Gemini ask"),
    ({"messages": [{"role": "assistant", "content": "only the agent"}]}, None),
    (["not", "an", "object"], None),
])
def test_the_users_ask_from_any_provider(body, ask):
    assert BB.user_ask(body) == ask


def _gateway(sessions: dict, page_size: int = 2):
    """A fake Blackbox: sessions {sid: (summary, events, {hash: body})}, paged."""
    ids = list(sessions)
    seen = []

    def get(path: str) -> bytes:
        seen.append(path)
        if path.startswith("/v1/sessions?"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(path).query)
            assert q["status"] == ["closed"] and q["label"] == ["refunds"]
            start = ids.index(q["before"][0]) + 1 if "before" in q else 0
            page = ids[start:start + page_size]
            nxt = page[-1] if len(page) == page_size and start + page_size < len(ids) else None
            return json.dumps({"sessions": [{"session_id": s} for s in page],
                               "next": nxt}).encode()
        if path == "/ui/faults.json":
            return json.dumps({"faults": {
                "FALSE_SUCCESS": {"fault": "BBX-4203", "en": "Claimed success without it"},
                "SECRET_SENT": {"fault": "BBX-9106", "en": "A secret was sent out."},
                "SDK_SILENT": {"fault": "BBX-1201", "en": "The SDK stopped reporting"},
                "NEAR_MISS": {"fault": "BBX-6101", "en": "Near miss"}}}).encode()
        sid = path.split("/")[3] if path.startswith("/v1/sessions/") else None
        if sid == "ses_9":
            raise TimeoutError("The read operation timed out")
        if sid and path.endswith("/summary"):
            return json.dumps(sessions[sid][0]).encode()
        if sid and "/events?" in path:
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(path).query)
            after, limit = int(q["after_seq"][0]), int(q["limit"][0])
            evs = [e for e in sessions[sid][1] if e["seq"] > after][:limit]
            return "".join(json.dumps(e) + "\n" for e in evs).encode()
        if path.startswith("/v1/bodies/"):
            h = path.rsplit("/", 1)[1]
            for _, _, bodies in sessions.values():
                if h in bodies:
                    return json.dumps(bodies[h]).encode()
        raise RuntimeError(f"Blackbox answered 404 for {path}")
    get.seen = seen
    return get


def _session(sid, outcome, codes, ask, claims_failed=0):
    h = "sha256:" + sid.encode().hex().ljust(64, "0")[:64]
    events = [{"seq": 0, "kind": "session.open", "body_hash": "sha256:e"},
              {"seq": 1, "kind": "llm.request", "body_hash": h},
              {"seq": 2, "kind": "llm.request", "body_hash": "sha256:later"}]
    body = {"messages": [{"role": "user", "content": ask}]} if ask else {"messages": []}
    return sid, ({"outcome": outcome, "finding_codes": {c: 1 for c in codes},
                  "claims": {"failed": claims_failed}}, events, {h: body})


def _long_session(sid, ask, at):
    s, (summary, events, bodies) = _session(sid, "success", [], ask)
    h = events[1]["body_hash"]
    events = ([{"seq": i, "kind": "sdk.event", "body_hash": "sha256:e"} for i in range(at)]
              + [{"seq": at, "kind": "llm.request", "body_hash": h}])
    return s, (summary, events, bodies)


FLEET = dict([
    _session("ses_1", "success", [], "Refund order 1001 to the card it came from."),
    _session("ses_2", "failure", ["FALSE_SUCCESS", "NEAR_MISS"],
             "Refund order 1002, email the customer at jo@example.com."),
    _session("ses_3", "success", ["SECRET_SENT", "SDK_SILENT"],
             "Refund order 1003 and log the API call."),
    _session("ses_4", "success", [], "Refund order 1001 to the card it came from."),   # repeat
    _session("ses_5", "success", [], None),                                            # no ask
    _session("ses_6", "success", [], "Close ticket 77.", claims_failed=1),
    _long_session("ses_7", "Archive the March invoices.", 120),
    _long_session("ses_8", "Never asked within the first pages.", 400),
    ("ses_9", ({}, [], {})),                       # the gateway times out on this one
])


def test_runs_become_graded_tasks_failures_first():
    get = _gateway(FLEET)
    runs, unreadable = BB.fetch_runs(get, "refunds", 50)
    assert [r["session_id"] for r in runs] == ["ses_1", "ses_2", "ses_3", "ses_4", "ses_6",
                                               "ses_7"]
    assert unreadable == 1                                              # skipped, not fatal
    # `limit` bounds the runs read, not the runs found: 3 read, 2 with an ask
    few, _ = BB.fetch_runs(_gateway(FLEET), "refunds", 3)
    assert [r["session_id"] for r in few] == ["ses_1", "ses_2", "ses_3"]
    none_asked = dict([_session(f"ses_n{i}", "success", [], None) for i in range(9)])
    g = _gateway(none_asked)
    assert BB.fetch_runs(g, "refunds", 4) == ([], 0)
    assert sum(p.startswith("/v1/sessions?") for p in g.seen) == 2     # 2 + 2, then stop
    assert not any("/v1/bodies/sha256:later" in p for p in get.seen)   # only the first request
    # a long record is read a page at a time, and never past the first 200 events
    pages = [p for p in get.seen if p.startswith("/v1/sessions/ses_8/events")]
    assert len(pages) == 4 and all("limit=50" in p for p in pages)
    tasks = BB.to_tasks(runs, BB.catalogue(get), ["Does the job"], 50)
    # went wrong first: a failure, a contradicted claim, a secret sent out
    assert [t["notes"]["blackbox"]["session_id"] for t in tasks] == ["ses_2", "ses_3", "ses_6",
                                                                     "ses_1", "ses_7"]
    by = {t["notes"]["blackbox"]["session_id"]: t for t in tasks}
    assert "[email]" in by["ses_2"]["brief"] and "jo@example.com" not in json.dumps(tasks)
    assert [c["criterion"] for c in by["ses_2"]["rubric"]] == [
        "Does the job",
        "Avoids what went wrong when this was done in production: claimed success without it."]
    assert by["ses_3"]["rubric"][-1]["criterion"].endswith(": a secret was sent out.")
    assert by["ses_3"]["notes"]["blackbox"]["codes"] == ["SECRET_SENT"]  # not the silent SDK
    assert by["ses_6"]["rubric"][-1]["criterion"] == "Says the job is done only when it is."
    assert by["ses_2"]["notes"]["feedback"] == "bad" and by["ses_1"]["notes"]["feedback"] == "good"
    assert by["ses_2"]["notes"]["blackbox"]["codes"] == ["FALSE_SUCCESS"]   # NEAR_MISS isn't output
    assert all(t["id"].startswith("bb-") and t["source"] == "blackbox" for t in tasks)
    assert len(BB.to_tasks(runs, {}, ["x"], 2)) == 2
    assert BB.to_tasks(runs[:1], {}, [], 50) == []                       # nothing to grade it by
    # without the catalogue, only your rubric grades: no code is guessed at
    plain = BB.to_tasks(runs, {}, ["x"], 50)
    assert all([c["criterion"] for c in t["rubric"]] in (["x"], ["x", "Says the job is done "
               "only when it is."]) for t in plain)


def test_cli_ingest_blackbox_needs_a_key_and_is_idempotent(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("ze_cli_bb", ROOT / "zanii_evolve.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    TS.create_domain("ops", domains_dir=tmp_path / "domains")
    monkeypatch.delenv("BLACKBOX_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="BLACKBOX_API_KEY"):
        cli.ingest_blackbox("ops", "https://bb.example", "refunds", "Does the job", 50, 0.2)
    monkeypatch.setenv("BLACKBOX_API_KEY", "test-key")
    asked = []
    monkeypatch.setattr(BB, "http_get", lambda url, key: asked.append((url, key)) or _gateway(FLEET))
    tasks = tmp_path / "domains" / "ops" / "tasks.jsonl"
    n0 = len(tasks.read_text().splitlines())
    cli.ingest_blackbox("ops", "https://bb.example", "refunds", "Does the job", 50, 0.2)
    assert len(tasks.read_text().splitlines()) == n0 + 5
    cli.ingest_blackbox("ops", "https://bb.example", "refunds", "Does the job", 50, 0.2)
    assert len(tasks.read_text().splitlines()) == n0 + 5                  # the same runs add nothing
    assert asked[0] == ("https://bb.example", "test-key")
    with pytest.raises(SystemExit, match="limit"):
        cli.ingest_blackbox("ops", "https://bb.example", "refunds", "x", 5000, 0.2)
