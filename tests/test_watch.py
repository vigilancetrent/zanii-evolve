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
"""watch and rollout: ingest on a schedule, run a cycle once enough failures build
up, ship only a gain above the noise band, retry what failed with backoff, give
up cleanly, and roll a live canary forward or back on evidence."""

import importlib.util
import json
import random
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zanii_evolve import registry as REG    # noqa: E402
from zanii_evolve import report as R        # noqa: E402
from zanii_evolve import rollout as RO      # noqa: E402
from zanii_evolve import tasksets as TS     # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ze_cli_watch", ROOT / "zanii_evolve.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_domain", lambda name: SimpleNamespace(harness_path="harness"))
    TS.create_domain("ops", domains_dir=tmp_path / "domains")
    traces = tmp_path / "t.jsonl"
    args = Namespace(domain="ops", runs=str(tmp_path / "runs"), traces=str(traces),
                     blackbox_url=None, label=None, rubric="Answers", limit=200, heldout=0.0,
                     min_failures=3, every=60, once=True, on_publish=None, on_event=None,
                     hook_timeout=30, rollout=None, rollout_alpha=0.05,
                     rollout_min_requests=20, stall_hours=24, max_retries=3,
                     max_cycle_failures=3, require_approval=None, approval_ttl_hours=72)
    calls = SimpleNamespace(drive=0, published=[], gain=0.2, publish_error=None, refs=[])

    def drive(entry, domain, runs_root, T, start, extra_args=None):
        calls.drive += 1
        (Path(runs_root) / domain / "frontier.json").write_text(
            json.dumps({"trajectory": [{"commit": "base0001"}] * (T + 1)}))

    def publish(*a):
        if calls.publish_error:
            raise RuntimeError(calls.publish_error)
        calls.published.append(a)
        return {"commit": "canary01"}

    monkeypatch.setattr(cli, "drive", drive)
    monkeypatch.setattr(cli, "_cli", lambda a, log: 0)
    monkeypatch.setattr(cli.G, "git", lambda cwd, *a, check=False: calls.refs.append(a))
    monkeypatch.setattr(R, "build", lambda d: {"gain": calls.gain, "noise_band": 0.05,
                                               "gain_exceeds_noise": calls.gain > 0.05,
                                               "baseline": {"commit": "stable01"},
                                               "incumbent": {"commit": "canary01"}})
    monkeypatch.setattr(REG, "publish", publish)

    def add(n, start=0):
        with open(traces, "a", encoding="utf-8") as f:
            f.writelines(json.dumps({"input": f"refund order {i} please", "feedback": "bad"}) + "\n"
                         for i in range(start, start + n))
    return SimpleNamespace(cli=cli, args=args, calls=calls, add=add,
                           run_dir=tmp_path / "runs" / "ops", tmp=tmp_path)


def tick(e):
    return e.cli.watch_tick(e.args, 3, [])


def no_backoff(e):
    p = e.run_dir / "watch.json"
    st = json.loads(p.read_text())
    st["retry_at"] = 0.0
    if st["publish"]:
        st["publish"]["next_at"] = 0.0
    p.write_text(json.dumps(st))


# ---------------------------------------------------------------- watch ---
def test_waits_for_enough_new_failures_and_counts_each_once(env):
    env.add(2)
    assert tick(env)["pending"] == 2
    assert tick(env)["pending"] == 2                       # the same traces count once
    assert env.calls.drive == 0
    env.add(1, start=2)
    st = tick(env)
    assert env.calls.drive == 1 and st["cycles"] == 1 and st["pending"] == 0
    assert st["log"][-1]["published"] is True and st["log"][-1]["commit"] == "canary01"


def test_gain_inside_noise_band_never_ships(env):
    env.calls.gain = 0.01
    env.add(3)
    st = tick(env)
    assert st["log"][-1]["event"] == "cycle_done" and st["log"][-1]["published"] is False
    assert env.calls.published == []


def test_next_cycle_archives_the_last_one(env):
    env.add(3)
    tick(env)
    (env.run_dir / "r0").mkdir()
    (env.run_dir / "receipts.jsonl").write_text("keep\n")
    env.add(3, start=3)
    assert tick(env)["cycles"] == 2
    assert (env.run_dir / "cycles" / "c1" / "frontier.json").exists()
    assert (env.run_dir / "cycles" / "c1" / "r0").is_dir()
    assert (env.run_dir / "receipts.jsonl").read_text() == "keep\n"   # the receipt chain carries on


def test_failed_cycle_backs_off_then_resumes_on_the_same_tasks(env, monkeypatch):
    real = env.cli.drive

    def broken(*a, **k):
        (env.run_dir / "frontier.json").write_text('{"trajectory": [{"commit": "base0001"}]}')
        raise SystemExit(1)

    monkeypatch.setattr(env.cli, "drive", broken)
    env.add(3)
    st = tick(env)
    assert st["log"][-1]["event"] == "cycle_failed" and st["active"] == 1
    assert st["log"][-1]["retry_in_s"] > 0
    assert tick(env)["log"][-1]["event"] == "cycle_failed"   # still backing off: nothing ran
    monkeypatch.setattr(env.cli, "drive", real)
    tasks = env.tmp / "domains" / "ops" / "tasks.jsonl"
    before = tasks.read_text()
    env.add(5, start=3)
    no_backoff(env)
    st = tick(env)
    assert st["cycles"] == 1 and st["active"] is None
    assert tasks.read_text() == before                     # new tasks waited for the next cycle
    assert not (env.run_dir / "cycles").exists()


def test_unsettled_rounds_are_not_a_finished_cycle(env, monkeypatch):
    monkeypatch.setattr(env.cli, "drive", lambda *a, **k: (env.run_dir / "frontier.json")
                        .write_text('{"trajectory": [{"commit": "base0001"}]}'))
    env.add(3)
    st = tick(env)
    assert st["log"][-1]["event"] == "cycle_failed" and st["active"] == 1
    assert env.calls.published == []


def test_a_cycle_that_keeps_failing_is_abandoned_and_its_rounds_undone(env, monkeypatch):
    monkeypatch.setattr(env.cli, "drive", lambda *a, **k: (env.run_dir / "frontier.json")
                        .write_text('{"trajectory": [{"commit": "base0001"}, {"commit": "x"}]}'))
    env.add(3)
    for _ in range(env.args.max_cycle_failures):
        no_backoff(env) if (env.run_dir / "watch.json").exists() else None
        st = tick(env)
    ev = st["log"][-1]
    assert ev["event"] == "cycle_abandoned" and ev["branch_reset_to"] == "base0001"
    assert ("update-ref", "refs/heads/evolve/ops", "base0001") in env.calls.refs
    assert (env.run_dir / "cycles" / "c1-abandoned" / "frontier.json").exists()
    assert st["active"] is None and st["failures"] == 0


def test_failed_publish_is_retried_then_given_up(env):
    env.calls.publish_error = "registry disk full"
    env.add(3)
    st = tick(env)
    assert "retrying" in st["log"][-1]["error"] and st["publish"]["attempts"] == 1
    env.add(3, start=3)
    no_backoff(env)
    st = tick(env)
    assert st["log"][-1]["event"] == "publish_failed" and env.calls.drive == 1   # no new cycle
    env.calls.publish_error = None
    no_backoff(env)
    st = tick(env)
    assert st["log"][-1]["event"] == "publish_retried" and st["publish"] is None
    env.calls.publish_error = "still broken"
    env.add(3, start=6)
    tick(env)                                              # cycle 2, publish fails
    for _ in range(env.args.max_retries):
        no_backoff(env)
        st = tick(env)
        if st["log"][-1]["event"] == "publish_gave_up":
            break
    assert st["log"][-1]["event"] == "publish_gave_up" and st["publish"] is None


def test_bad_source_is_logged_not_fatal(env):
    env.args.traces = str(env.tmp / "missing.jsonl")
    assert tick(env)["log"][-1]["event"] == "ingest_failed"


def test_hooks_get_the_manifest_and_the_events(env):
    out, ev_out = env.tmp / "hook.txt", env.tmp / "events.txt"
    script = env.tmp / "hook.py"
    script.write_text(f"import os\nopen({str(out)!r}, 'a').write(os.environ.get("
                      f"'ZANII_EVOLVE_MANIFEST', ''))\nopen({str(ev_out)!r}, 'a').write("
                      f"os.environ.get('ZANII_EVOLVE_EVENT', '') + chr(10))")
    env.args.on_publish = env.args.on_event = f'"{sys.executable}" "{script}"'
    env.add(3)
    st = tick(env)
    assert st["log"][-1]["on_publish_rc"] == 0
    assert out.read_text().endswith(str(Path("ops") / "canary01" / "manifest.json"))
    events = [json.loads(x)["event"] for x in ev_out.read_text().splitlines() if x]
    assert events == ["cycle_started", "cycle_done"]       # "waiting" never pages


# -------------------------------------------------------------- rollout ---
def test_rollout_harm_test_is_valid_under_continuous_monitoring():
    rng, fired, alpha, r = random.Random(11), 0, 0.05, 0.25
    for _ in range(200):
        k = m = 0
        for _ in range(200):
            m += 1
            k += rng.random() < r                          # canary exactly as good as stable
            if RO.e_value(k, m, r, grid=400) >= 1 / alpha:
                fired += 1
                break
    assert fired / 200 <= alpha
    assert RO.e_value(60, 100, 0.25) > 1 / alpha           # 60% of failures on a 25% arm
    assert RO.e_value(20, 100, 0.25) < 1                   # fewer than its share: no harm


def test_assignment_is_sticky_and_matches_the_share(tmp_path):
    st = RO.start(tmp_path, "stable01", "canary01", [0.1, 0.5], 0.05, 10)
    arms = [RO.assign(st, f"s{i}")["arm"] for i in range(4000)]
    assert 0.08 < arms.count("canary") / 4000 < 0.12
    assert all(RO.assign(st, f"s{i}")["arm"] == arms[i] for i in range(200))
    with pytest.raises(ValueError):
        RO.start(tmp_path, "a", "b", [0.5, 1.0], 0.05, 10)  # a stage must keep a control arm


def _feed(run_dir, st, canary_fail, stable_fail, n=4000):
    rng = random.Random(3)
    for i in range(n):
        a = RO.assign(st, f"k{i}")
        bad = rng.random() < (canary_fail if a["arm"] == "canary" else stable_fail)
        RO.record(run_dir, st, f"k{i}", a["commit"], not bad)


def test_a_harmful_canary_is_rolled_back_and_a_good_one_promoted(tmp_path):
    st = RO.start(tmp_path, "stable01", "canary01", [0.25], 0.05, 100)
    _feed(tmp_path, st, canary_fail=0.30, stable_fail=0.10)
    verdict, ev = RO.decide(tmp_path, st, 24)
    assert verdict == "rollback" and ev["e_value"] >= ev["threshold"]
    good = tmp_path / "good"
    good.mkdir()
    st = RO.start(good, "stable01", "canary02", [0.1, 0.5], 0.05, 100)
    _feed(good, st, canary_fail=0.08, stable_fail=0.10)
    verdict, _ = RO.decide(good, st, 24)
    assert verdict == "advance"
    st = RO.apply(good, st, verdict, {})
    assert RO.decide(good, st, 24)[0] == "wait"            # a new stage starts from zero
    _feed(good, st, canary_fail=0.08, stable_fail=0.10)
    verdict, _ = RO.decide(good, st, 24)
    assert verdict == "promote"
    st = RO.apply(good, st, verdict, {})
    assert st["status"] == "promoted" and st["stable"] == "canary02"
    assert RO.assign(st, "anyone") == {"arm": "stable", "commit": "canary02"}


def test_outcomes_are_checked_against_the_assignment(tmp_path):
    st = RO.start(tmp_path, "stable01", "canary01", [0.5], 0.05, 10)
    a = RO.assign(st, "sess-1")
    other = "stable01" if a["arm"] == "canary" else "canary01"
    with pytest.raises(ValueError, match="not assigned"):
        RO.record(tmp_path, st, "sess-1", other, True)
    with pytest.raises(ValueError, match="neither arm"):
        RO.record(tmp_path, st, "sess-1", "deadbeef", True)
    with pytest.raises(ValueError, match="boolean"):
        RO.record(tmp_path, st, "sess-1", a["commit"], "yes")


def test_watch_starts_a_rollout_and_rolls_back_the_branch_on_harm(env):
    env.args.rollout = [0.25]
    env.add(3)
    st = tick(env)
    assert st["log"][-1]["rollout"]["canary"] == "canary01"
    rs = RO.load(env.run_dir)
    assert rs["status"] == "running" and rs["stable"] == "stable01"
    env.add(3, start=3)
    assert tick(env)["log"][-1]["rollout_running"] is True and env.calls.drive == 1
    _feed(env.run_dir, rs, canary_fail=0.4, stable_fail=0.1, n=2000)
    st = tick(env)
    rb = next(e for e in st["log"] if e["event"] == "rollout_rollback")
    assert rb["canary_commit"] == "canary01" and rb["e_value"] >= rb["threshold"]
    assert ("update-ref", "refs/heads/evolve/ops", "stable01") in env.calls.refs
    assert env.calls.drive == 2                            # the pending failures now run a cycle
    assert RO.load(env.run_dir)["cycle"] == 2              # whose gain starts a fresh rollout


def test_server_routes_sessions_and_takes_outcomes(tmp_path, monkeypatch):
    from test_server import req
    from zanii_evolve import server as S
    monkeypatch.setattr(S, "ROOT", ROOT)
    srv, state = S.build_server("127.0.0.1", 0, tmp_path, token="t0k")
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        run_dir = tmp_path / "marketing"
        run_dir.mkdir()
        assert req(port, "GET", "/api/domains/marketing/rollout/assign?key=s1")[0] == 404
        RO.start(run_dir, "stable01", "canary01", [0.5], 0.05, 10)
        st, a = req(port, "GET", "/api/domains/marketing/rollout/assign?key=s1")
        assert st == 200 and a["commit"] in ("stable01", "canary01")
        body = {"key": "s1", "commit": a["commit"], "ok": True}
        assert req(port, "POST", "/api/domains/marketing/rollout/outcome", body)[0] == 401
        auth = {"Authorization": "Bearer t0k"}
        assert req(port, "POST", "/api/domains/marketing/rollout/outcome", body, auth)[0] == 200
        bad = {**body, "commit": "deadbeef"}
        assert req(port, "POST", "/api/domains/marketing/rollout/outcome", bad, auth)[0] == 400
        st, view = req(port, "GET", "/api/domains/marketing/rollout")
        assert st == 200 and view["counts"][a["arm"]]["n"] == 1
    finally:
        srv.shutdown()


def test_a_restarted_rollout_ignores_the_earlier_outcomes(tmp_path):
    st = RO.start(tmp_path, "stable01", "canary01", [0.25], 0.05, 100)
    _feed(tmp_path, st, canary_fail=0.5, stable_fail=0.1)
    assert RO.decide(tmp_path, st, 24)[0] == "rollback"
    st = RO.start(tmp_path, "stable01", "canary01", [0.25], 0.05, 100)   # same canary again
    assert RO.counts(tmp_path, st)["canary"]["n"] == 0



# ------------------------------------------------------------ human gate ---
def _owner_identity(env, monkeypatch):
    pytest.importorskip("zanii")
    from zanii_evolve import report as Rp
    owner, ident = Rp.new_identity()
    (env.tmp / "identity.json").write_text(json.dumps(ident))
    (env.tmp / "owner.json").write_text(json.dumps(owner))
    monkeypatch.setenv("ZANII_EVOLVE_IDENTITY", str(env.tmp / "identity.json"))
    return owner


def test_publish_waits_for_the_owners_signed_approval(env, monkeypatch):
    from zanii_evolve import gate
    _owner_identity(env, monkeypatch)
    env.args.require_approval = ["publish"]
    env.add(3)
    st = tick(env)
    ev = st["log"][-1]
    assert ev["event"] == "cycle_done" and "approval_required" in ev and env.calls.published == []
    cid = ev["approval_required"]
    assert tick(env)["log"][-1]["event"] == "cycle_done"        # still parked: no new event
    import zanii.core as zc
    other = zc.generate_keypair()
    (env.tmp / "stranger.json").write_text(json.dumps({"did": other.did,
                                                      "private_key": other.private_key.hex()}))
    gate.approve(env.run_dir, cid, env.tmp / "stranger.json")  # signed, but not by the owner
    tick(env)
    assert env.calls.published == []
    gate.approve(env.run_dir, cid, env.tmp / "owner.json")
    st = tick(env)
    assert st["log"][-1]["event"] == "publish_retried" and len(env.calls.published) == 1
    assert gate.pending(env.run_dir) == []                      # the approval was consumed


def test_an_approval_is_bound_to_one_action_and_expires(env, monkeypatch):
    from zanii_evolve import gate
    owner = _owner_identity(env, monkeypatch)
    status, cid = gate.check(env.run_dir, "publish", {"commit": "aaa"}, owner["did"], 1)
    assert status == "opened"
    gate.approve(env.run_dir, cid, env.tmp / "owner.json")
    assert gate.check(env.run_dir, "publish", {"commit": "bbb"}, owner["did"])[0] == "opened"
    assert gate.check(env.run_dir, "publish", {"commit": "aaa"}, owner["did"])[0] == "approved"
    status, cid = gate.check(env.run_dir, "promote", {"canary": "c"}, owner["did"], -1)
    with pytest.raises(ValueError, match="expired"):               # silence is not consent
        gate.approve(env.run_dir, cid, env.tmp / "owner.json")
    assert gate.check(env.run_dir, "promote", {"canary": "c"}, owner["did"])[0] == "reopened"


def test_promote_is_gated_but_rollback_never_waits(env, monkeypatch):
    from zanii_evolve import gate
    _owner_identity(env, monkeypatch)
    env.args.require_approval = ["promote"]
    st0 = RO.start(env.run_dir, "stable01", "canary01", [0.5], 0.05, 20)
    _feed(env.run_dir, st0, canary_fail=0.10, stable_fail=0.10, n=200)
    st = tick(env)
    ev = next(e for e in st["log"] if e["event"] == "approval_required")
    assert ev["action"] == "promote" and RO.load(env.run_dir)["status"] == "running"
    gate.approve(env.run_dir, ev["id"], env.tmp / "owner.json")
    tick(env)
    assert RO.load(env.run_dir)["status"] == "promoted"
    st1 = RO.start(env.run_dir, "stable01", "canary02", [0.5], 0.05, 20)
    _feed(env.run_dir, st1, canary_fail=0.6, stable_fail=0.1, n=400)
    tick(env)
    assert RO.load(env.run_dir)["status"] == "rolled_back"      # no approval asked for
