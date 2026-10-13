"""Compliance evidence packs: framework files, gathering, coverage, the signed zip."""

import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import compliance as CO  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402


def test_framework_files_are_well_formed():
    assert {"eu_ai_act", "iso_42001", "uae_ai_charter", "uae_pdpl"} <= set(CO.frameworks())
    keys = set(CO.RUN_FILES.values()) | set(CO.KINDS) | {
        "data_forget", "approvals", "constitution", "certificate", "heldout", "tasks_manifest"}
    for name in CO.frameworks():
        fw = CO.load_framework(name)
        assert fw["id"] == name and "not legal advice" in fw["disclaimer"]
        for c in fw["controls"]:
            assert c["ref"] and c["title"] and set(c["evidence"]) <= keys, (name, c)
    with pytest.raises(ValueError):
        CO.load_framework("nope")


def _signed_cert(key: str, **over) -> dict:
    import hashlib
    import hmac

    from zanii_evolve import report as R
    body = {"gain": 0.1, "gain_exceeds_noise": True,
            "heldout": [{"label": "final"}, {"label": "redteam"}],
            "tasks_manifest": {"hash": "abc", "size": 4}, **over}
    digest = hashlib.sha256(R._canonical(body)).hexdigest()
    return {**body, "digest": digest,
            "signature": hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest()}


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "k1")
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    run, dom = tmp_path / "runs" / "acme", tmp_path / "domains" / "acme"
    (run / "statements").mkdir(parents=True)
    dom.mkdir(parents=True)
    (run / "receipts.jsonl").write_text('{"r": 1}\n{"r": 2}\n', encoding="utf-8")
    (run / "custody.jsonl").write_text('{"op": "read"}\n', encoding="utf-8")
    (run / "judge_agreement.json").write_text('{"kappa": 0.8}', encoding="utf-8")
    (dom / "constitution.json").write_text('{"rules": []}', encoding="utf-8")
    cert = tmp_path / "certificate.json"
    cert.write_text(json.dumps(_signed_cert("k1")), encoding="utf-8")
    ST.save(ST.issue("migration", {"ok": True}, identity={}), run / "statements" / "m.json")
    (run / "statements" / "junk.json").write_text("{}", encoding="utf-8")
    return run, dom, cert


def test_gather_and_coverage(tmp_path, monkeypatch):
    run, dom, cert = _seed(tmp_path, monkeypatch)
    ev = CO.gather(run, dom, cert)
    assert set(ev) == {"receipts_chain", "custody", "judge_kappa", "constitution",
                       "certificate", "heldout", "redteam", "tasks_manifest", "migration"}
    assert ev["receipts_chain"][0]["detail"] == "2 records"
    rows = {r["ref"]: r for r in CO.coverage(CO.load_framework("eu_ai_act"), ev)}
    assert rows["Art. 14"]["found"] == [] and "approvals" in rows["Art. 14"]["missing"]
    assert "migration" in rows["Art. 15"]["found"]


def test_pack_zip_is_signed_and_complete(tmp_path, monkeypatch, capsys):
    run, _dom, cert = _seed(tmp_path, monkeypatch)
    args = SimpleNamespace(framework="eu_ai_act,uae_pdpl", cert=str(cert), statements=None,
                           out=None, domain="acme", runs=str(tmp_path / "runs"))
    ctx = SimpleNamespace(root=tmp_path, run_dir=run)
    assert CO.handle(args, ctx) == 0
    out = capsys.readouterr().out
    assert "eu_ai_act: 7/9 controls evidenced; missing Art. 14, Art. 72" in out
    assert "uae_pdpl: 4/5 controls evidenced; missing Data subject rights" in out
    z = zipfile.ZipFile(run / "evidence_eu_ai_act_uae_pdpl.zip")
    names = set(z.namelist())
    assert {"index.json", "statement.json", "eu_ai_act.md", "uae_pdpl.md",
            "evidence/acme/receipts.jsonl", "evidence/statements/m.json"} <= names
    index = json.loads(z.read("index.json"))
    stmt = json.loads(z.read("statement.json"))
    assert stmt["body"] == index and ST.verify(stmt, kind="evidence_pack")[0]
    import hashlib
    for arc, sha in index["files"].items():
        assert hashlib.sha256(z.read(arc)).hexdigest() == sha
    md = z.read("eu_ai_act.md").decode("utf-8")
    assert "not legal advice" in md and "**MISSING**" in md
    args.framework = "gdpr"
    assert CO.handle(args, ctx) == 2


def test_forged_or_unsigned_evidence_is_listed_but_not_counted(tmp_path, monkeypatch):
    run, dom, cert = _seed(tmp_path, monkeypatch)
    good = ST.issue("savings", {"saved": "1.00"}, identity={})
    forged = {k: v for k, v in good.items() if k != "signature"}  # body edited, digest redone
    forged["body"] = {"saved": "9999.00"}
    forged["digest"] = ST._digest(forged)
    ST.save(forged, run / "statements" / "forged.json")
    edited = dict(ST.issue("regression", {"passed": False}, identity={}))
    edited["body"] = {"passed": True}                               # digest no longer matches
    ST.save(edited, run / "statements" / "edited.json")
    ev = CO.gather(run, dom, cert)
    assert "savings" not in ev and "regression" not in ev and "migration" in ev
    bad = {e["file"].name: e["detail"] for e in ev[CO.UNVERIFIED]}
    assert set(bad) == {"forged.json", "edited.json"}
    assert "signing key is set" in bad["forged.json"] and "digest mismatch" in bad["edited.json"]
    monkeypatch.delenv("ZANII_EVOLVE_SIGNING_KEY")                  # no key: unsigned != signed
    ev = CO.gather(run, dom, None)
    assert "savings" not in ev and "unsigned" in {
        e["file"].name: e["detail"] for e in ev[CO.UNVERIFIED]}["forged.json"]


def test_a_forged_certificate_is_not_evidence(tmp_path, monkeypatch):
    run, dom, cert = _seed(tmp_path, monkeypatch)
    c = json.loads(cert.read_text(encoding="utf-8"))
    c["gain"] = 0.9                                                 # edited after issue
    cert.write_text(json.dumps(c), encoding="utf-8")
    ev = CO.gather(run, dom, cert)
    assert not {"certificate", "heldout", "redteam", "tasks_manifest"} & set(ev)
    assert "digest mismatch" in ev[CO.UNVERIFIED][0]["detail"]
    other = tmp_path / "other.json"
    other.write_text(json.dumps(_signed_cert("not-k1")), encoding="utf-8")
    assert "certificate" not in CO.gather(run, dom, other)          # wrong key


def test_pack_signs_the_tables_and_keeps_same_named_files(tmp_path, monkeypatch):
    run, dom, cert = _seed(tmp_path, monkeypatch)
    extra = tmp_path / "more" / "statements"
    extra.mkdir(parents=True)
    ST.save(ST.issue("scorecard", {"vendors": []}, identity={}), extra / "m.json")
    ST.save(ST.issue("migration", {"ok": False}, identity={}), run / "statements" / "m2.json")
    out = tmp_path / "pack.zip"
    index = CO.pack(["eu_ai_act"], run, dom, out, cert, statements=extra)
    z = zipfile.ZipFile(out)
    import hashlib
    for name, sha in index["documents"].items():
        assert hashlib.sha256(z.read(name)).hexdigest() == sha
    stmt = json.loads(z.read("statement.json"))
    assert ST.verify(stmt)[0] and stmt["body"]["documents"] == index["documents"]
    # two different files named statements/m.json: both kept, neither overwritten
    run2 = tmp_path / "runs2" / "acme"
    (run2 / "statements").mkdir(parents=True)
    ST.save(ST.issue("migration", {"a": 1}, identity={}), run2 / "statements" / "m.json")
    ev = {"migration": [{"file": run2 / "statements" / "m.json", "sha256": "a" * 64,
                         "detail": ""}],
          "scorecard": [{"file": extra / "m.json", "sha256": "b" * 64, "detail": ""}]}
    monkeypatch.setattr(CO, "gather", lambda *a, **k: ev)
    index = CO.pack(["eu_ai_act"], run, dom, tmp_path / "p2.zip")
    assert sorted(index["files"]) == ["evidence/statements/2_m.json", "evidence/statements/m.json"]
