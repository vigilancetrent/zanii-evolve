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
"""Certified gains as business artifacts: a signed CV, underwriting facts, an SLA
assessed from the receipt chain, an escrow released only by a ledger-proven gain,
and the auditor's report."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("zanii")

from zanii.testing import fake_ledger, make_identity  # noqa: E402

from zanii_evolve import report as R                  # noqa: E402
from zanii_evolve import trust as T                   # noqa: E402

from test_server import _seed_run                     # noqa: E402

LEDGER = "http://ledger.test"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DOMAINS", tmp_path / "domains")
    monkeypatch.delenv("ZANII_LEDGER_URL", raising=False)
    _seed_run(tmp_path / "runs")
    run = tmp_path / "runs" / "marketing"
    fr = json.loads((run / "frontier.json").read_text())
    fr["config"] = {"delta": 0.05}                   # 0.07 gain: a certified gain
    (run / "frontier.json").write_text(json.dumps(fr))
    owner, ident = R.new_identity()
    return run, tmp_path / "registry", ident, owner


def _publish(registry, cert, name="abc12345"):
    d = registry / "marketing" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "certificate.json").write_text(json.dumps(cert))


def _person():
    kp = make_identity()
    return {"agent": {"did": kp.did, "privateKey": kp.private_key.hex()},
            "delegation": [{"issuer": kp.did, "scopes": []}]}


def test_gain_receipts_carry_their_own_target(env):
    run, _, ident, owner = env
    cert = R.issue(run, identity=ident)
    assert cert["gain_exceeds_noise"] and cert["receipt"]["target"] == R.GAIN_TARGET
    assert R.verify(cert, owner=owner["did"])[0]
    forged = json.loads(json.dumps(cert))
    forged["receipt"]["target"] = R.RECEIPT_TARGET      # re-labelling breaks the signature anyway
    assert not R.verify(forged)[0]


def test_cv_lists_published_certificates_and_detects_edits(env):
    from zanii import cv as CV
    run, registry, ident, owner = env
    _publish(registry, R.issue(run, identity=ident))
    cv = T.harness_cv(ident, registry)
    v = CV.verify_cv(cv)
    assert v["ok"] and v["signed"] and not v["warnings"]
    assert cv["benchmarks"][0]["gain"] == "+0.0700" and cv["benchmarks"][0]["above_noise"]
    assert cv["summary"]["source"].startswith("local registry")
    cv["benchmarks"][0]["gain"] = "+0.9000"
    assert not CV.verify_cv(cv)["ok"]


def test_ledger_backs_the_cv_and_risk_profile(env, monkeypatch):
    run, registry, ident, owner = env
    with fake_ledger():
        monkeypatch.setenv("ZANII_LEDGER_URL", LEDGER)
        _publish(registry, R.issue(run, identity=ident))
        cv = T.harness_cv(ident, registry, LEDGER)
        assert cv["summary"]["source"] == f"ledger {LEDGER}" and cv["summary"]["receipts"] == 1
        risk = T.risk_profile(ident, registry, LEDGER)
    assert "THIN_HISTORY" in risk["flags"] and "WILDCARD_AUTHORITY" not in risk["flags"]
    local = T.risk_profile(ident, registry)
    assert "NOT_FULLY_ANCHORED" in local["flags"]          # local history is not ledger-attested


def test_sla_is_assessed_from_the_receipt_chain(env):
    run, _, ident, owner = env
    client = _person()
    sla = T.sla_create(ident, client["agent"]["did"], "2026-01-01T00:00:00Z",
                       "2099-01-01T00:00:00Z", 1, price="5000", currency="USD")
    assert sla["body"]["price"]["minor"] == "500000"         # integer minor units
    sla = T.sla_sign(sla, client)
    R.issue(run, identity=ident)
    assert T.sla_assess(sla, run, as_of=R._now())["ok"]
    two = T.sla_sign(T.sla_create(ident, client["agent"]["did"], "2026-01-01T00:00:00Z",
                                  "2099-01-01T00:00:00Z", 2), client)
    verdict = T.sla_assess(two, run)
    assert not verdict["ok"] and verdict["breaches"][0]["kind"] == "TOO_FEW_ACTIONS"
    with pytest.raises(ValueError, match="only the provider or the client"):
        T.sla_sign(sla, _person())


def test_escrow_releases_only_on_a_ledger_proven_gain(env, monkeypatch):
    run, _, ident, owner = env
    payer = _person()
    esc = T.escrow_sign(T.escrow_create(ident, payer["agent"]["did"], "5000.00", "USD",
                                        "2099-01-01T00:00:00Z"), payer)
    local = R.issue(run, identity=ident)                  # signed, but only on this disk
    with fake_ledger():
        monkeypatch.setenv("ZANII_LEDGER_URL", LEDGER)
        assert not T.escrow_check(esc, local, LEDGER)["ok"]
        anchored = R.issue(run, identity=ident)
        verdict = T.escrow_check(esc, anchored, LEDGER)
        assert verdict["ok"], verdict
        fr = json.loads((run / "frontier.json").read_text())
        fr["config"] = {"delta": 0.5}                     # same run, gain now inside the noise
        (run / "frontier.json").write_text(json.dumps(fr))
        no_gain = R.issue(run, identity=ident)
        assert no_gain["receipt"]["target"] == R.RECEIPT_TARGET
        assert not T.escrow_check(esc, no_gain, LEDGER)["ok"]
    instr = T.escrow_release(esc, anchored, payer)
    assert instr["body"]["receipt"] == verdict["receipt"]
    with pytest.raises(ValueError, match="cannot authorize"):
        T.escrow_release(esc, anchored, _person())


def test_audit_report_covers_every_evidence_stream(env):
    run, registry, ident, owner = env
    _publish(registry, R.issue(run, identity=ident))
    md = T.audit("marketing", run, registry, owner=owner["did"])
    assert "## Published certificates" in md and "PASS" in md
    assert "## Certificate receipt chain" in md and "## Live rollouts" in md
    assert "FAIL" not in md
