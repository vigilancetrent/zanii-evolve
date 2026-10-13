"""Expert judge packs and the provenance judgecal.record now keeps."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import judgecal as JC  # noqa: E402
from zanii_evolve import judgepack as JP  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402

TASKS = {"a": {"id": "a", "rubric": [{"criterion": "Tone"}, {"criterion": "Facts"}]},
         "b": {"id": "b", "rubric": [{"criterion": "Cites the policy"}]}}


def fake_judge(task, output, model):                  # passes everything
    return [{"verdict": "pass", "reason": ""} for _ in task["rubric"]]


def _labels(tmp_path):
    rows = [{"task_id": "a", "output": f"o{i}", "labels": ["pass", "pass" if i % 3 else "fail"],
             "rater": "dr-amal" if i % 2 else "dr-omar"} for i in range(12)]
    rows += [{"task_id": "b", "output": "x", "labels": ["pass"], "rater": "dr-amal"}]
    p = tmp_path / "expert.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    r = tmp_path / "raters.json"
    r.write_text(json.dumps([{"id": "dr-amal", "credential": "Consultant physician, DHA"}]),
                 encoding="utf-8")
    return p, r


def test_record_keeps_range_raters_and_labels_hash(tmp_path):
    p, r = _labels(tmp_path)
    labels = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    res = JC.agreement(TASKS, labels, "m", judge=fake_judge)
    assert res["raters"] == ["dr-amal", "dr-omar"]
    kept = JC.record(tmp_path, res, p, r)
    assert kept["raters"][0]["credential"].startswith("Consultant")
    assert kept["raters"][0]["credential_source"].startswith("self-declared")
    assert kept["raters"][1] == {"id": "dr-omar"} and len(kept["labels_sha256"]) == 64
    assert JC.recorded(tmp_path)["labels_sha256"] == kept["labels_sha256"]
    assert JC.gate(tmp_path, "m", None) is None


def test_raters_json_never_adds_people_who_did_not_grade(tmp_path):
    p, r = _labels(tmp_path)
    r.write_text(json.dumps([{"id": "dr-amal", "credential": "Consultant physician, DHA"},
                             {"id": "prof-x", "credential": "Professor of Law"}]),
                 encoding="utf-8")
    labels = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    kept = JC.record(tmp_path, JC.agreement(TASKS, labels, "m", judge=fake_judge), p, r)
    assert [x["id"] for x in kept["raters"]] == ["dr-amal", "dr-omar"]
    anonymous = [{k: v for k, v in lab.items() if k != "rater"} for lab in labels]
    kept = JC.record(tmp_path, JC.agreement(TASKS, anonymous, "m", judge=fake_judge), p, r)
    assert "raters" not in kept                     # no label names a rater: nobody is listed


def test_kappa_range():
    pairs = [(True, True), (False, False)] * 20 + [(True, False)] * 4
    lo, hi = JC.kappa_range(pairs)
    assert lo <= JC.kappa(pairs) <= hi <= 1
    assert JC.kappa_range([(True, True)]) is None
    assert JC.kappa_range([(True, True)] * 5) is None               # constant raters


def test_export_and_verify(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "k1")
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    monkeypatch.delenv("ZANII_EVOLVE_JUDGE_MODEL", raising=False)
    p, r = _labels(tmp_path)
    dom = SimpleNamespace(name="clinic", tasks=TASKS, cfg={"judge_model": "fake/judge"})
    pack = JP.export(dom, tmp_path, p, r, judge=fake_judge)
    b = pack["body"]
    assert b["judge_model"] == "fake/judge" and b["n"] == 25 and len(b["criteria"]) == 3
    assert b["per_criterion"][0]["criterion"] == "Facts"              # weakest first
    assert JP.verify(pack, p)[1].endswith("labels match")
    other = tmp_path / "other.jsonl"
    other.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert JP.verify(pack, other) == (False, ("these labels are not the ones the pack was "
                                              "certified on"))
    out = tmp_path / "pack.json"
    ST.save(pack, out)
    args = SimpleNamespace(action="verify", pack=str(out), labels=str(p), owner=None)
    assert JP.handle(args, None) == 0 and "OK" in capsys.readouterr().out
    pack["body"]["kappa"] = 0.99
    ST.save(pack, out)
    assert JP.handle(args, None) == 1
