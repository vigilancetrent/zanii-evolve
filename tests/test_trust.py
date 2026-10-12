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
"""Keys and certificates on the Zanii packages: sealed keys, post-quantum dual
signatures, public views, signed rulebooks, key rotation and ledger anchoring."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("zanii")

from zanii_evolve import report as R        # noqa: E402
from zanii_evolve import trust as T         # noqa: E402

from test_server import _seed_run           # noqa: E402

PW = "correct horse battery staple"


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DOMAINS", tmp_path / "domains")
    (tmp_path / "domains" / "marketing").mkdir(parents=True)
    monkeypatch.delenv("ZANII_LEDGER_URL", raising=False)
    _seed_run(tmp_path / "runs")
    return tmp_path / "runs" / "marketing"


def _identity(tmp_path, monkeypatch, pq=False, passphrase=PW):
    owner, ident = R.new_identity(passphrase=passphrase, pq=pq)
    (tmp_path / "keys").mkdir(exist_ok=True)
    (tmp_path / "keys" / "owner.json").write_text(json.dumps(owner))
    (tmp_path / "keys" / "identity.json").write_text(json.dumps(ident))
    monkeypatch.setenv("ZANII_EVOLVE_IDENTITY_PASSPHRASE", PW)
    return owner, R.load_identity(str(tmp_path / "keys" / "identity.json"))


# ------------------------------------------------------------------- kms ---
def test_sealed_keys_open_only_with_the_passphrase(tmp_path, monkeypatch):
    owner, ident = _identity(tmp_path, monkeypatch)
    raw = (tmp_path / "keys" / "identity.json").read_text()
    assert "privateKey" not in raw and "sealed" in raw            # nothing usable at rest
    assert "private_key" not in (tmp_path / "keys" / "owner.json").read_text()
    assert len(bytes.fromhex(ident["agent"]["privateKey"])) == 32
    monkeypatch.setenv("ZANII_EVOLVE_IDENTITY_PASSPHRASE", "wrong")
    with pytest.raises(RuntimeError, match="wrong passphrase"):
        R.load_identity(str(tmp_path / "keys" / "identity.json"))
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY_PASSPHRASE")
    with pytest.raises(RuntimeError, match="sealed"):
        R.load_identity(str(tmp_path / "keys" / "identity.json"))


# ---------------------------------------------------------- certificates ---
def test_a_public_view_proves_chosen_fields_and_nothing_else(run, tmp_path, monkeypatch):
    owner, ident = _identity(tmp_path, monkeypatch)
    cert = R.issue(run, identity=ident)
    assert R.verify(cert, owner=owner["did"])[0]
    view = R.public_view(cert, ["domain", "gain", "gain_exceeds_noise"])
    assert set(R.disclosed(view)) == {"domain", "gain", "gain_exceeds_noise"}
    assert "accepted_edits" not in json.dumps(view["disclosures"])  # the rest stays committed
    ok, why = R.verify(view, owner=owner["did"])
    assert ok and "3 disclosed fields proven" in why
    forged = json.loads(json.dumps(view))
    forged["disclosures"][1]["value"] = json.dumps(0.99)            # inflate the gain
    assert not R.verify(forged, owner=owner["did"])[0]
    edited = json.loads(json.dumps(cert))
    edited["disclosure"]["secrets"]["fields"]["gain"]["value"] = "0.99"
    assert not R.verify(edited)[0]                                  # commitment must match body


def test_post_quantum_dual_signature(run, tmp_path, monkeypatch):
    pytest.importorskip("dilithium_py", reason="zanii[pq] not installed")
    owner, ident = _identity(tmp_path, monkeypatch, pq=True)
    assert "secret_key" in ident["pq"]                              # unsealed in memory only
    assert "secret_key" not in (tmp_path / "keys" / "identity.json").read_text()
    cert = R.issue(run, identity=ident)
    ok, why = R.verify(cert, owner=owner["did"])
    assert ok and "ML-DSA-65" in why
    sig = cert["pq"]["sigs"]["sig_pq"]
    cert["pq"]["sigs"] = {**cert["pq"]["sigs"], "sig_pq": sig[:-8] + ("0" if sig[-8] != "0" else "1") * 8}
    assert not R.verify(cert)[0]


def test_older_certificates_still_verify(run):
    cert = R.build(run, key="k")
    assert cert["schema"] == "zanii-evolve-certificate/3" and cert["constitution"] is None
    assert R.verify(cert, key="k")[0]


# ---------------------------------------------------------- constitution ---
def test_a_signed_rulebook_is_named_in_the_certificate(run, tmp_path, monkeypatch):
    owner, ident = _identity(tmp_path, monkeypatch)
    d = tmp_path / "domains" / "marketing"
    (d / "constitution.json").write_text(json.dumps({"name": "marketing rules", "rules": [
        {"id": "no-export", "effect": "forbid", "targets": ["crm.export.*"]},
        {"id": "refunds", "effect": "require_approval", "targets": ["payments.*"]}]}))
    T.sign_constitution("marketing", ident)
    assert T.check_action("marketing", "crm.export.csv")["effect"] == "forbid"
    assert T.check_action("marketing", "payments.refund")["effect"] == "require_approval"
    assert T.check_action("marketing", "crm.lookup")["effect"] == "allow"
    cert = R.issue(run, identity=ident)
    from zanii import constitution as K
    signed = json.loads((d / "constitution.signed.json").read_text())
    assert cert["constitution"] == K.constitution_hash(signed)
    signed["rules"][0]["effect"] = "allow"                          # weaken a rule after signing
    (d / "constitution.signed.json").write_text(json.dumps(signed))
    with pytest.raises(ValueError, match="signature invalid"):
        R.build(run)
    (d / "constitution.json").write_text(json.dumps({"rules": [{"id": "x", "effect": "maybe",
                                                               "targets": ["a"]}]}))
    with pytest.raises(ValueError, match="effect"):
        T.sign_constitution("marketing", ident)
    (d / "constitution.json").write_text(json.dumps({"rules": [{"id": "x", "effect": "forbid",
                                                               "targets": ["crm.export*"]}]}))
    with pytest.raises(ValueError, match="scope pattern"):
        T.sign_constitution("marketing", ident)


# -------------------------------------------------------------- rotation ---
def test_rotation_keeps_one_history_and_compromise_revokes(run, tmp_path, monkeypatch):
    owner, ident = _identity(tmp_path, monkeypatch)
    R.issue(run, identity=ident)
    out = T.rotate_identity(tmp_path / "keys" / "owner.json", tmp_path / "keys" / "identity.json",
                            tmp_path / "keys2", passphrase=PW)
    assert out["lineage_ok"] and out["predecessor"] == ident["agent"]["did"]
    new = R.load_identity(str(tmp_path / "keys2" / "identity.json"))
    R.issue(run, identity=new)
    assert R.verify_chain(run, owner=owner["did"])[0]               # one chain across two keys
    with pytest.raises(FileExistsError):
        T.rotate_identity(tmp_path / "keys" / "owner.json", tmp_path / "keys" / "identity.json",
                          tmp_path / "keys2", passphrase=PW)
    with pytest.raises(ValueError, match="compromised-at"):
        T.rotate_identity(tmp_path / "keys" / "owner.json", tmp_path / "keys2" / "identity.json",
                          tmp_path / "keys3", reason="compromise")
    out = T.rotate_identity(tmp_path / "keys" / "owner.json", tmp_path / "keys2" / "identity.json",
                            tmp_path / "keys3", reason="compromise",
                            compromised_at="2026-10-13T00:00:00Z", passphrase=PW)
    assert out["revoked"] == 1 and out["lineage"] == 2
    revoked = T.revoked_set(tmp_path / "keys3" / "revocations.json")
    ok, why = R.verify_chain(run, owner=owner["did"], revoked=revoked)
    assert not ok and "revoked" in why                              # the exposed key's receipt


# ------------------------------------------------------- ledger anchoring ---
def test_certificate_receipt_anchored_on_a_ledger(run, tmp_path, monkeypatch):
    from zanii.testing import fake_ledger
    owner, ident = _identity(tmp_path, monkeypatch)
    with fake_ledger() as ledger:
        monkeypatch.setenv("ZANII_LEDGER_URL", "http://ledger.test")
        cert = R.issue(run, identity=ident)
        assert ledger.has_recorded(R.RECEIPT_TARGET) and ledger.verify_all()
        ok, why = R.verify(cert, owner=owner["did"], ledger="http://ledger.test")
        assert ok and "included in the ledger" in why, why
    monkeypatch.setenv("ZANII_LEDGER_URL", "http://127.0.0.1:9")    # nothing listening
    with pytest.raises(RuntimeError, match="did not take the receipt"):
        R.issue(run, identity=ident)


# ---------------------------------------------------------------- memory ---
def _mechanisms():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mech_t", ROOT / "zanii_evolve" / "templates" / "brief" / "harness" / "mechanisms.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_provable_memory_shows_any_lesson_changed_outside_the_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_PROVABLE_MEMORY", "1")
    mem = _mechanisms().JsonlMemory(tmp_path / "state")
    for note in ("List every requirement before drafting.", "Quote the order number back.",
                 "Escalate refunds over the limit."):
        assert mem.write("semantic", note)
    rep = T.verify_memory(tmp_path / "state")
    assert rep["ok"] and rep["entries"] == 3
    f = tmp_path / "state" / "mem_semantic.jsonl"
    rows = f.read_text().splitlines()
    f.write_text("\n".join(rows[:1] + [rows[1].replace("order number", "account number")]
                           + rows[2:]) + "\n")
    rep = T.verify_memory(tmp_path / "state")
    assert not rep["ok"] and any("edited or deleted" in p for p in rep["problems"])
    assert any("not in the chain" in p for p in rep["problems"])     # the edit is also an insert
    ident, _ = _agent()
    from zanii_evolve.live import SessionRecorder
    rec = SessionRecorder(ident, "abc1234")
    assert rec.attest_memory(tmp_path / "state").startswith("sha256:")
    assert rec.receipts[-1]["target"] == "memory.head"


def test_memory_without_the_flag_writes_no_chain(tmp_path, monkeypatch):
    monkeypatch.delenv("ZANII_EVOLVE_PROVABLE_MEMORY", raising=False)
    _mechanisms().JsonlMemory(tmp_path / "s").write("semantic", "A lesson.")
    assert not (tmp_path / "s" / "memory_chain.jsonl").exists()


def _agent():
    from zanii.testing import make_cert, make_identity
    owner, agent = make_identity(), make_identity()
    return ({"agent": {"did": agent.did, "privateKey": agent.private_key.hex()},
             "delegation": [make_cert(owner, agent, ["tools.*", "memory.*"])]}, owner)


# ------------------------------------------------------------------ data ---
def test_a_record_is_provably_in_or_out_of_the_training_set(run, tmp_path, monkeypatch):
    fr = json.loads((run / "frontier.json").read_text())
    fr["trajectory"][0].update(job="base", commit="0a1b2c3")
    (run / "frontier.json").write_text(json.dumps(fr))
    (run / "jobs" / "base").mkdir(parents=True)
    (run / "jobs" / "base" / "eval.json").write_text(json.dumps(
        {"per_task": {"tr-1111111111": {}, "tr-2222222222": {}, "mem-001": {}}}))
    owner, ident = _identity(tmp_path, monkeypatch)
    cert = R.issue(run, identity=ident)
    assert cert["tasks_manifest"]["size"] == 3
    inside = R.prove_task(cert, "tr-2222222222")
    assert inside["present"] and R.verify_task_proof(inside, cert)[0]
    outside = R.prove_task(cert, "tr-9999999999")
    ok, why = R.verify_task_proof(outside, cert)
    assert ok and "NOT" in why
    lie = {**outside, "present": True}                               # claim absence as presence
    assert not R.verify_task_proof(lie, cert)[0]
    other = json.loads(json.dumps(cert))
    other["tasks_manifest"]["hash"] = "sha256:" + "0" * 64
    assert not R.verify_task_proof(inside, other)[0]                  # a different certificate
    view = R.public_view(cert, ["tasks_manifest"])                    # provable without the rest
    assert R.verify(view, owner=owner["did"])[0]


def test_forget_deletes_a_record_and_signs_it(run, tmp_path, monkeypatch):
    from zanii_evolve import tasksets as TS
    owner, ident = _identity(tmp_path, monkeypatch)
    TS.create_domain("ops", domains_dir=tmp_path / "domains")
    text = "Customer 4411 asks where order 9 is"
    rows = TS.from_traces([{"input": text, "feedback": "bad"},
                           {"input": "Another request", "feedback": "good"}], ["Answers"])
    TS.write_tasks(tmp_path / "domains" / "ops", TS.assign_splits(rows, 0.0))
    tid = TS.trace_task_id(text)
    trial = run / "jobs" / "base" / tid
    trial.mkdir(parents=True)
    (trial / "output.txt").write_text("deliverable")
    out = T.forget("ops", run, ident, text=text)
    assert out["removed_tasks"] == 1 and out["removed_files"] >= 1 and not trial.exists()
    left = [t["id"] for t in TS.load_tasks(tmp_path / "domains" / "ops" / "tasks.jsonl")]
    assert tid not in left and TS.trace_task_id("Another request") in left
    ok, why = T.verify_forget(out["attestation"], tid, out["salt"], owner=owner["did"])
    assert ok, why
    assert not T.verify_forget(out["attestation"], "tr-0000000000", out["salt"])[0]
    assert text not in json.dumps(out["attestation"])                 # the subject stays private
    with pytest.raises(LookupError):
        T.forget("ops", run, ident, text=text)


def test_post_quantum_binding_anchored_and_verified(tmp_path, monkeypatch):
    pytest.importorskip("dilithium_py", reason="zanii[pq] not installed")
    from zanii.testing import fake_ledger
    owner, ident = _identity(tmp_path, monkeypatch, pq=True)
    out = tmp_path / "keys" / "pq_transition.json"
    with fake_ledger() as ledger:
        res = T.anchor_identity(ident, "http://ledger.test", "zk_live_test", out)
        assert res["ok"], res["reasons"]
        assert ledger.has_recorded(T.PQ_TARGET)
        with pytest.raises(FileExistsError):
            T.anchor_identity(ident, "http://ledger.test", "zk_live_test", out)
    assert T.verify_anchored_identity(out)["ok"]                     # offline, from the file
    rec = json.loads(out.read_text())
    rec["binding"]["ts"] = "2020-01-01T00:00:00Z"                    # backdate the binding
    out.write_text(json.dumps(rec))
    assert not T.verify_anchored_identity(out)["ok"]
    (tmp_path / "x").mkdir()
    with pytest.raises(ValueError, match="post-quantum"):
        T.anchor_identity(_identity(tmp_path / "x", monkeypatch)[1], "http://l", "k", tmp_path / "y")
