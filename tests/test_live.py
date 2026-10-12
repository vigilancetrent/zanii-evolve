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
"""Live-session evidence: signed session receipts stamped with the harness that
ran and the rulebook that governed, the behaviour gate on a canary, signed
rollout decisions, and the audit that re-checks them."""

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("zanii")

from zanii import constitution as K                 # noqa: E402
from zanii import core as zc                        # noqa: E402
from zanii.testing import make_cert, make_identity  # noqa: E402

from zanii_evolve import live as L                  # noqa: E402
from zanii_evolve import report as R                # noqa: E402
from zanii_evolve import rollout as RO              # noqa: E402
from zanii_evolve import server as S                # noqa: E402
from zanii_evolve.client import Rollout             # noqa: E402

TOKEN = "t0k"


def agent_identity(scopes=("tools.*",)):
    owner, agent = make_identity(), make_identity()
    return {"agent": {"did": agent.did, "privateKey": agent.private_key.hex()},
            "delegation": [make_cert(owner, agent, list(scopes))]}, owner


def rulebook(owner):
    return K.create_constitution(
        issuer=owner.did, name="support", created_at="2026-10-13T00:00:00Z",
        issuer_private_key=owner.private_key,
        rules=[{"id": "no-external-mail", "effect": "forbid", "targets": ["tools.email.*"]}])


# --------------------------------------------------------------- recorder ---
def test_recorder_signs_each_action_with_what_ran_and_what_governed():
    ident, owner = agent_identity()
    c = rulebook(owner)
    rec = L.SessionRecorder(ident, "abc1234", "f" * 64, K.constitution_hash(c), "tag1")
    tools = rec.wrap_tools({"lookup": lambda q: {"order": q}})
    assert tools["lookup"]("A-17") == {"order": "A-17"}
    rec.record_tool("refund", {"order": "A-17"}, result="ok")
    assert [r["target"] for r in rec.receipts] == ["tools.lookup", "tools.refund"]
    assert zc.verify_receipt_chain(rec.receipts).ok
    assert {r["runtime_hash"] for r in rec.receipts} == {L.runtime_hash("abc1234", "f" * 64)}
    assert K.verify_governance(c, rec.receipts)["ok"]             # stamped and allowed
    assert "A-17" not in json.dumps(rec.receipts)                 # payloads stay with the agent


def test_langchain_tool_calls_become_receipts():
    lc = pytest.importorskip("langchain_core.tools")
    ident, _ = agent_identity()
    rec = L.SessionRecorder(ident, "abc1234")

    @lc.tool
    def lookup(order: str) -> str:
        """Look an order up."""
        return f"order {order}: shipped"

    lookup.invoke("A-17", config={"callbacks": [rec.langchain_callbacks()]})
    assert [r["target"] for r in rec.receipts] == ["tools.lookup"]


def test_openai_agents_and_crewai_hooks_attach():
    pytest.importorskip("agents")
    ident, _ = agent_identity()
    rec = L.SessionRecorder(ident, "abc1234")
    assert rec.openai_agents_hooks() is not None
    pytest.importorskip("crewai")


# ----------------------------------------------------------- server + gate ---
@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_REGISTRY", str(tmp_path / "registry"))
    monkeypatch.setattr(R, "DOMAINS", tmp_path / "domains")
    signer, signer_owner = agent_identity(scopes=("evolve.*",))
    monkeypatch.setattr(R, "load_identity", lambda path=None: signer)
    srv, _ = S.build_server("127.0.0.1", 0, tmp_path / "runs", token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    run_dir = tmp_path / "runs" / "marketing"
    run_dir.mkdir(parents=True)
    st = RO.start(run_dir, "aaaaaaa", "bbbbbbb", [0.5], 0.05, 1000)
    yield f"http://127.0.0.1:{srv.server_address[1]}", run_dir, st, signer_owner
    srv.shutdown()


def _sessions(url, ident, n, act, start=0):
    r = Rollout(url, "marketing", TOKEN, identity=ident)
    for i in range(start, start + n):
        with r.session(f"s{i}") as s:
            act(s)
            s.outcome = True
    return r


def normal(s):
    for t in ("crm.lookup", "crm.lookup", "orders.status"):
        s.recorder.record(f"tools.{t}", {"q": 1})


def test_sessions_upload_verified_receipts_and_signed_decisions(live):
    url, run_dir, st, signer_owner = live
    ident, _ = agent_identity()
    r = _sessions(url, ident, 12, normal)
    assert r.dropped == 0
    rows = [json.loads(x) for x in (run_dir / L.LIVE).read_text().splitlines()]
    assert len(rows) == 36 and {x["arm"] for x in rows} <= {"canary", "stable"}
    rep = L.audit(run_dir, owner=signer_owner.did)
    assert rep["ok"], rep["problems"]
    assert rep["decisions"] == 12 and rep["session_receipts"] == 36
    rows = [json.loads(x) for x in (run_dir / L.DECISIONS).read_text().splitlines()]
    rows[3]["payload"]["outcome"]["arm"] = ("stable" if rows[3]["payload"]["outcome"]["arm"]
                                            == "canary" else "canary")
    (run_dir / L.DECISIONS).write_text("".join(json.dumps(x) + "\n" for x in rows))
    assert not L.audit(run_dir, owner=signer_owner.did)["ok"]     # a rewritten decision shows


def test_forged_or_misattributed_receipts_count_nothing(live):
    url, run_dir, st, _ = live
    ident, _ = agent_identity()
    r = Rollout(url, "marketing", TOKEN, identity=ident)
    commit = r.assign("x1")
    other = "aaaaaaa" if commit == "bbbbbbb" else "bbbbbbb"
    wrong = L.SessionRecorder(ident, other)                       # receipts claim the other harness
    wrong.record("tools.crm.lookup", {})
    assert r.report("x1", True, commit, wrong.receipts) is False
    good = L.SessionRecorder(ident, commit)
    good.record("tools.crm.lookup", {})
    good.receipts[0]["target"] = "tools.crm.export"               # edited after signing
    assert r.report("x1", True, commit, good.receipts) is False
    assert RO.counts(run_dir, st)["canary"]["n"] + RO.counts(run_dir, st)["stable"]["n"] == 0


def test_a_canary_that_succeeds_but_misbehaves_is_rolled_back(live):
    url, run_dir, st, _ = live
    ident, _ = agent_identity()
    canary_keys = [k for k in (f"s{i}" for i in range(400)) if RO.assign(st, k)["arm"] == "canary"]

    def act(s):
        if s.commit == "bbbbbbb" and s.key in canary_keys[:3]:    # exfiltration-shaped run
            for t in ("crm.read", "archive.zip", "email.external_send"):
                s.recorder.record(f"tools.{t}", {})
        else:
            normal(s)

    _sessions(url, ident, 60, act)
    verdict, ev = RO.decide(run_dir, RO.load(run_dir), 24)
    assert verdict == "rollback" and ev["reason"] == "behaviour"
    assert any(f["severity"] == "high" for f in ev["findings"])


def test_a_canary_breaking_the_rulebook_is_rolled_back(live):
    url, run_dir, st, _ = live
    ident, owner = agent_identity()
    c = rulebook(owner)
    d = R.DOMAINS / "marketing"
    d.mkdir(parents=True)
    (d / "constitution.signed.json").write_text(json.dumps(c))
    canary_key = next(k for k in (f"s{i}" for i in range(400)) if RO.assign(st, k)["arm"] == "canary")

    def act(s):
        if s.key == canary_key:
            s.recorder.record("tools.email.send", {})
        else:
            normal(s)

    _sessions(url, ident, int(canary_key[1:]) + 1, act)
    verdict, ev = RO.decide(run_dir, RO.load(run_dir), 24, constitution=c)
    assert verdict == "rollback" and ev["governance"][0]["kind"] == "forbidden"
    assert L.audit(run_dir, constitution=c)["governance"]["forbidden"] == 1
