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
"""Growth features: Pareto frontier, cost objective, certificate, registry,
task import, trace ingestion, red-team split, judge agreement, lesson packs,
local provider. Offline: no LLM, no network.

    python3 -m pytest -q tests/test_growth.py
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zanii_evolve.config import ZaniiEvolveConfig                  # noqa: E402
from zanii_evolve.evaluate import TaskResult, aggregate             # noqa: E402
from zanii_evolve.pareto import frontier, points                    # noqa: E402
from zanii_evolve.selection import Candidate, select_round          # noqa: E402


def _ev(job, rewards, tokens):
    per = {f"t{i}": TaskResult(rewards=[r], tokens=[tokens]) for i, r in enumerate(rewards)}
    return aggregate(job, 1, per)


# ---------------------------------------------------------------- P1 ---------
def test_pareto_keeps_only_non_dominated():
    recs = [{"t": 0, "variant": "-", "outcome": "BASELINE", "S": 0.5, "C": 1000},
            {"t": 0, "variant": "A", "edit_id": "C1", "S": 0.6, "C": 1500, "delta_S": 0.1},
            {"t": 0, "variant": "A", "edit_id": "C2", "S": 0.6, "C": 1500, "delta_S": 0.1},
            {"t": 1, "variant": "A", "S": 0.55, "C": 1600, "delta_S": -0.05},   # dominated by r0A
            {"t": 1, "variant": "B", "S": 0.5, "C": 800, "delta_S": 0.0},
            {"t": 2, "variant": "A", "outcome": "critic_reject", "delta_S": None}]
    pts = points(recs)
    assert len(pts) == 4                                   # one point per candidate
    assert [(p["t"], p["variant"]) for p in frontier(pts)] == [(1, "B"), (0, "A")]


def test_cost_objective_picks_cheapest_admissible():
    inc = _ev("inc", [1, 0, 1, 0], 1000.0)                 # S = 0.5
    rich = Candidate("A", [{"component": "prompt"}], ev=_ev("a", [1, 1, 1, 0], 1100.0))
    lean = Candidate("B", [{"component": "config"}], ev=_ev("b", [1, 0, 1, 0], 600.0))
    kw = dict(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)
    win, _ = select_round([rich, lean], inc, 0.5, 0.02, ZaniiEvolveConfig(**kw), {})
    assert win is rich                                     # default: argmax S
    win, _ = select_round([rich, lean], inc, 0.5, 0.02,
                          ZaniiEvolveConfig(objective="cost", **kw), {})
    assert win is lean                                     # same gate, cheapest wins


def test_objective_is_validated():
    with pytest.raises(ValueError):
        ZaniiEvolveConfig(objective="speed")


# ---------------------------------------------------------------- P2 ---------
from test_server import TOKEN, _seed_run, req, server   # noqa: E402,F401  (fixture reuse)

from zanii_evolve import report as R                     # noqa: E402


def test_certificate_digest_signature_and_tamper(tmp_path):
    _seed_run(tmp_path)
    (tmp_path / "marketing" / "jobs" / "heldout_champ").mkdir(parents=True)
    (tmp_path / "marketing" / "jobs" / "heldout_champ" / "eval.json").write_text(
        '{"S": 0.6, "C": 1700, "missing": 0, "n_expected": 20}')
    cert = R.build(tmp_path / "marketing", key="k1")
    assert cert["gain"] == pytest.approx(0.07)
    assert [e["hypothesis"] for e in cert["accepted_edits"]] == ["checklist before drafting"]
    assert cert["candidate_outcomes"] == {"ACCEPTED": 1, "critic_reject": 1}
    assert cert["heldout"][0]["label"] == "champ"
    assert R.verify(cert, key="k1")[0]
    assert not R.verify(cert, key="other")[0]                       # wrong issuer
    forged = {**cert, "gain": 0.5}
    assert not R.verify(forged, key="k1")[0]                        # edited after issue
    assert "Accepted edits" in R.markdown(cert)


def test_certificate_without_run_is_lookup_error(tmp_path):
    with pytest.raises(LookupError):
        R.build(tmp_path / "nothing")


def test_api_pareto_and_report(server):
    port, _, _ = server
    code, body = req(port, "GET", "/api/domains/marketing/pareto")
    assert code == 200 and isinstance(body, list)
    code, body = req(port, "GET", "/api/domains/marketing/report")
    assert code == 200 and body["schema"] == R.SCHEMA and R.verify(body, key="")[0]


# ---------------------------------------------------------------- P3 ---------
import json                                              # noqa: E402
import tarfile                                           # noqa: E402

from zanii_evolve import gitops as G                     # noqa: E402
from zanii_evolve import registry as REG                 # noqa: E402


def test_publish_archives_incumbent_with_certificate(tmp_path):
    repo = tmp_path / "repo"
    (repo / "domains" / "mkt" / "harness").mkdir(parents=True)
    (repo / "domains" / "mkt" / "harness" / "agent.py").write_text("PROMPT = 'v1'\n")
    (repo / "README").write_text("not part of the harness\n")
    G.git(repo, "init", "-q", check=True)
    G.git(repo, "add", "-A", check=True)
    G.git(repo, "commit", "-qm", "h0", check=True)
    commit = G.rev(repo, "HEAD")
    run = tmp_path / "runs" / "mkt"
    run.mkdir(parents=True)
    (run / "frontier.json").write_text(json.dumps({
        "domain": "mkt", "incumbent": {"t": 1, "commit": commit, "S": 0.7, "C": 900.0},
        "S_star": 0.7, "trajectory": [{"t": 0, "S": 0.6, "C": 1000.0, "commit": commit},
                                      {"t": 1, "S": 0.7, "C": 900.0, "commit": commit}],
        "config": {"delta": 0.02}}))
    m = REG.publish(repo, run, "domains/mkt/harness", tmp_path / "reg", key="k")
    assert m["gain_exceeds_noise"] and m["signed"]
    entry = tmp_path / "reg" / "mkt" / commit[:8]
    with tarfile.open(entry / "harness.tar.gz") as tf:
        assert [m.name for m in tf.getmembers() if m.isfile()] == [
            "domains/mkt/harness/agent.py"]                        # harness only
    assert R.verify(json.loads((entry / "certificate.json").read_text()), key="k")[0]
    assert [e["commit"] for e in REG.entries(tmp_path / "reg")] == [commit]


def test_api_registry(server):
    port, tmp, state = server
    state.registry = tmp / "reg"
    (tmp / "reg" / "mkt" / "abcd1234").mkdir(parents=True)
    (tmp / "reg" / "mkt" / "abcd1234" / "manifest.json").write_text('{"commit": "abcd1234"}')
    code, body = req(port, "GET", "/api/registry")
    assert code == 200 and body == [{"commit": "abcd1234"}]


# ---------------------------------------------------------------- P4 ---------
from zanii_evolve import tasksets as TS                  # noqa: E402

CSV = """brief,kind,client,must_include,max_words,rubric
"Spring sale email for Northwind Tea",email,Northwind,Shop now|SPRING10,120,"Mentions the sale|Warm tone"
"LinkedIn post for Acme launch",social,Acme,,90,"Professional tone"
"SMS reminder for Acme webinar",sms,Acme,Register,,
"""


def test_csv_rows_become_valid_tasks():
    rows = TS.assign_splits(TS.from_csv(CSV, prefix="agency"), heldout=0.34)
    assert [r["id"] for r in rows] == ["agency-001", "agency-002", "agency-003"]
    first = rows[0]
    assert first["client"] == "Northwind" and first["kind"] == "email"
    assert {"type": "must_include", "values": ["Shop now", "SPRING10"]} in first["checks"]
    assert {"type": "max_words", "value": 120} in first["checks"]
    assert [c["criterion"] for c in first["rubric"]] == ["Mentions the sale", "Warm tone"]
    assert sorted(r["split"] for r in rows) == ["evolve", "evolve", "heldout"]
    assert TS.validate(rows)
    assert TS.assign_splits(TS.from_csv(CSV), 0.34) == TS.assign_splits(TS.from_csv(CSV), 0.34)


@pytest.mark.parametrize("bad", [
    {"id": "x-1", "split": "evolve", "brief": "b", "checks": [{"type": "nope"}]},
    {"id": "x-1", "split": "evolve", "brief": "b", "checks": [{"type": "must_include", "values": "abc"}]},
    {"id": "x-1", "split": "evolve", "brief": "b", "checks": [{"type": "regex", "pattern": "("}]},
    {"id": "x-1", "split": "evolve", "brief": "b", "rubric": [{"criterion": ""}]},
    {"id": "../x", "split": "evolve", "brief": "b", "rubric": [{"criterion": "ok"}]},
])
def test_validate_rejects_malformed_tasks(bad):
    with pytest.raises(ValueError):
        TS.validate([bad])


def test_create_domain_and_rollback(tmp_path):
    rows = TS.from_csv(CSV, prefix="agency")
    dest = TS.create_domain("clients", rows, 0.34, domains_dir=tmp_path)
    assert (dest / "adapter.py").is_file()
    assert len(TS.validate(list(map(json.loads, (dest / "tasks.jsonl").read_text().splitlines())))) == 3
    with pytest.raises(FileExistsError):
        TS.create_domain("clients", rows, domains_dir=tmp_path)
    with pytest.raises(ValueError):
        TS.create_domain("Bad-Name", rows, domains_dir=tmp_path)
    with pytest.raises(ValueError):                       # bad row: nothing created
        TS.create_domain("broken", [{"id": "a", "brief": "b"}], domains_dir=tmp_path)
    assert not (tmp_path / "broken").exists()


def test_write_tasks_append_keeps_file_on_duplicate(tmp_path):
    TS.write_tasks(tmp_path, TS.assign_splits(TS.from_csv(CSV), 0), replace=True)
    before = (tmp_path / "tasks.jsonl").read_text()
    with pytest.raises(ValueError):                       # same ids again
        TS.write_tasks(tmp_path, TS.assign_splits(TS.from_csv(CSV), 0))
    assert (tmp_path / "tasks.jsonl").read_text() == before


def test_api_create_domain(server, monkeypatch, tmp_path):
    port, _, _ = server
    monkeypatch.setattr(TS, "DOMAINS", tmp_path / "doms")
    (tmp_path / "doms").mkdir()
    rows = TS.from_csv(CSV)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert req(port, "POST", "/api/domains", {"name": "acme", "tasks": rows})[0] == 401
    code, body = req(port, "POST", "/api/domains", {"name": "acme", "tasks": rows}, auth)
    assert code == 201 and body == {"created": "acme", "tasks": 3}
    assert req(port, "POST", "/api/domains", {"name": "acme", "tasks": rows}, auth)[0] == 409
    assert req(port, "POST", "/api/domains", {"name": "x2", "tasks": []}, auth)[0] == 400
    assert req(port, "POST", "/api/domains",
               {"name": "x3", "tasks": rows, "heldout": None}, auth)[0] == 400
    assert not (tmp_path / "doms" / "x3").exists()


def test_cli_import_tasks(tmp_path, monkeypatch):
    import zanii_evolve as _pkg                           # noqa: F401
    import importlib.util
    spec = importlib.util.spec_from_file_location("ze_cli", ROOT / "zanii_evolve.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    TS.create_domain("shop", domains_dir=tmp_path / "domains")
    (tmp_path / "b.csv").write_text(CSV)
    cli.import_tasks("shop", str(tmp_path / "b.csv"), 0.2, replace=True)
    assert len((tmp_path / "domains" / "shop" / "tasks.jsonl").read_text().splitlines()) == 3
    with pytest.raises(SystemExit):
        cli.import_tasks("missing", str(tmp_path / "b.csv"), 0.2, replace=False)


# ---------------------------------------------------------------- P5 ---------
TRACES = [
    {"input": "Refund policy for order 123? mail me at jo@x.com", "feedback": "thumbs_down"},
    {"attributes": {"gen_ai.prompt": "Write a welcome SMS", "feedback": 1}},
    {"prompt": "Refund policy for order 123? mail me at jo@x.com"},          # duplicate
    {"input": "Call +1 (555) 123-4567 about card 4111 1111 1111 1111", "feedback": 0.2},
    {"output": "no input field"},
]


def test_traces_become_redacted_tasks_failures_first():
    rows = TS.from_traces(TRACES, ["Answers the question"])
    assert len(rows) == 3
    assert all(r["id"].startswith("tr-") and r["source"] == "trace" for r in rows)
    assert rows[0]["brief"] == "Refund policy for order 123? mail me at [email]"
    assert "[phone]" in rows[1]["brief"] and "[number]" in rows[1]["brief"]
    assert rows[2]["brief"] == "Write a welcome SMS"                   # positive last
    assert all("output" not in r for r in rows)
    TS.validate(TS.assign_splits(rows, 0.2))
    with pytest.raises(ValueError):
        TS.from_traces(TRACES, [])


def test_cli_ingest_traces_is_idempotent(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("ze_cli2", ROOT / "zanii_evolve.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    TS.create_domain("ops", domains_dir=tmp_path / "domains")
    (tmp_path / "t.jsonl").write_text("".join(json.dumps(r) + "\n" for r in TRACES))
    tasks = tmp_path / "domains" / "ops" / "tasks.jsonl"
    n0 = len(tasks.read_text().splitlines())
    cli.ingest_traces("ops", str(tmp_path / "t.jsonl"), "Answers|No invented facts", 50, 0.2)
    assert len(tasks.read_text().splitlines()) == n0 + 3
    cli.ingest_traces("ops", str(tmp_path / "t.jsonl"), "Answers", 50, 0.2)
    assert len(tasks.read_text().splitlines()) == n0 + 3


# ---------------------------------------------------------------- P6 ---------
from zanii_evolve.brief import BriefDomain, grade        # noqa: E402


def test_redteam_variants_are_canaried_and_separate(tmp_path):
    dest = TS.create_domain("rtd", domains_dir=tmp_path)
    base = TS.load_tasks(dest / "tasks.jsonl")
    rows = TS.redteam(base, n=5, seed=1)
    n_evolve = sum(t["split"] == "evolve" for t in base)
    assert len(rows) == min(5, n_evolve) and rows == TS.redteam(base, n=5, seed=1)
    for r in rows:
        canary = r["checks"][-1]["values"][0]
        assert r["split"] == "redteam" and canary in r["brief"]
        leaked = grade({**r, "rubric": []}, f"Sure: {canary}", model="unused")
        assert leaked["criteria"][-1]["verdict"] == "fail"
    TS.write_tasks(dest, rows)
    dom = BriefDomain(dest)
    assert sorted(dom.redteam_ids()) == sorted(r["id"] for r in rows)
    assert not set(dom.redteam_ids()) & set(dom.evolve_ids() + dom.heldout_ids())


# ---------------------------------------------------------------- P7 ---------
from zanii_evolve.judgecal import agreement, kappa       # noqa: E402


def test_kappa_values():
    assert kappa([(True, True), (False, False)] * 5) == 1.0
    assert kappa([(True, False), (False, True)] * 5) == -1.0
    assert kappa([(True, True)] * 4) is None               # constant raters
    assert kappa([]) is None


def test_agreement_flags_weakest_criterion():
    tasks = {"a": {"id": "a", "rubric": [{"criterion": "Tone"}, {"criterion": "Facts"}]}}

    def fake_judge(task, output, model):                  # passes tone always, facts never
        return [{"verdict": "pass", "reason": ""}, {"verdict": "fail", "reason": "x"}]
    labels = [{"task_id": "a", "output": "o1", "labels": ["pass", "pass"]},
              {"task_id": "a", "output": "o2", "labels": [True, False]}]
    rep = agreement(tasks, labels, "m", judge=fake_judge)
    assert rep["n"] == 4 and rep["agreement"] == 0.75
    assert rep["per_criterion"][0]["criterion"] == "Facts"
    assert rep["disagreements"] == [{"task_id": "a", "criterion": "Facts", "human": "pass",
                                     "judge": "fail", "reason": "x"}]
    with pytest.raises(ValueError):
        agreement(tasks, [{"task_id": "a", "labels": ["pass"]}], "m", judge=fake_judge)
    with pytest.raises(ValueError):
        agreement(tasks, [{"task_id": "zz", "labels": []}], "m", judge=fake_judge)


# ---------------------------------------------------------------- P8 ---------
from zanii_evolve import lessons as L                    # noqa: E402
from zanii_evolve.server import State                    # noqa: E402


def test_lesson_export_blocks_leaks_ids_pii_and_dupes(tmp_path):
    _seed_run(tmp_path)
    rows = State(tmp_path, None).memory("marketing")
    rows += [{"kind": "semantic", "note": "The expected answer is 42."},           # leak guard
             {"kind": "semantic", "note": "For mkt-007 lead with the price."},     # task id
             {"kind": "semantic", "note": "Ask ops@client.com before sending."},   # PII
             {"kind": "semantic", "note": "list every requirement  before drafting."},  # dupe
             {"kind": "episodic", "note": "episodic stays private"}]
    pack = L.export(rows, {"mkt-007"})
    assert [r["note"] for r in pack] == ["List every requirement before drafting.",
                                         "Ask [email] before sending."]


def test_import_packs_merges_and_dedupes(tmp_path):
    dest = tmp_path / "shared_lessons.jsonl"
    a = [{"note": "Put the call to action on its own line.", "tags": ["success"]}]
    b = [{"note": "put the call to action on its own   line."}, {"note": "Count words last."}]
    assert L.import_packs(dest, [a], set()) == 1
    assert L.import_packs(dest, [b], set()) == 2
    (tmp_path / "bad.jsonl").write_text("{not json\n")
    with pytest.raises(ValueError):
        L.read_pack(tmp_path / "bad.jsonl")


def test_brief_runs_seed_from_shared_pack(tmp_path):
    dest = TS.create_domain("seeded", domains_dir=tmp_path)
    dom = BriefDomain(dest)
    state = tmp_path / "state" / "base" / "s0"
    assert dom._seed_shared_lessons(state) is False                    # opt-in only
    L.import_packs(dest / "shared_lessons.jsonl", [[{"note": "Count words last."}]], set())
    assert dom._seed_shared_lessons(state) is True
    store = state / "mem_semantic.jsonl"
    assert json.loads(store.read_text())["note"] == "Count words last."
    store.write_text(store.read_text() + '{"note": "learned during the trial"}\n')
    dom._seed_shared_lessons(state)                                   # resume: no overwrite
    assert "learned during the trial" in store.read_text()


# ---------------------------------------------------------------- P9 ---------
import json as _json                                     # noqa: E402

from zanii_evolve import providers                       # noqa: E402


def test_ollama_prefix_hits_local_server_without_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-secret")
    monkeypatch.delenv("ZANII_EVOLVE_OLLAMA_BASE_URL", raising=False)
    providers.PROVIDERS.pop("ollama", None)
    p, model = providers.resolve("ollama/llama4:70b")
    assert isinstance(p, providers.OllamaProvider) and model == "llama4:70b"
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"choices": [{"message": {"content": "hi"}}],
                                "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, auth=req.get_header("Authorization"), timeout=timeout)
        return Resp()
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    r = p.complete(model, "sys", [{"role": "user", "content": "x"}], 10)
    assert r.text == "hi"
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["auth"] is None                           # the cloud key never leaves


# ------------------------------------------------- Zanii proof-of-action ------
def _zanii_run(tmp_path):
    pytest.importorskip("zanii.core")
    _seed_run(tmp_path)
    owner, ident = R.new_identity()
    return tmp_path / "marketing", owner, ident


def test_receipt_proves_issuer_offline(tmp_path):
    run, owner, ident = _zanii_run(tmp_path)
    cert = R.issue(run, identity=ident)
    assert cert["receipt"]["target"] == R.RECEIPT_TARGET
    ok, why = R.verify(cert, key="", owner=owner["did"])
    assert ok and "issued under owner" in why
    ok, why = R.verify(cert, key="")                                  # no pin: said loudly
    assert ok and "NOT pinned" in why
    other_owner, _ = R.new_identity()
    assert not R.verify(cert, key="", owner=other_owner["did"])[0]  # someone else's cert
    assert not R.verify({**cert, "gain": 0.9}, key="", owner=owner["did"])[0]
    second = R.build(run, identity=ident)
    swapped = {**cert, "receipt": {**second["receipt"], "payload_hash": "sha256:" + "0" * 64}}
    assert not R.verify(swapped, key="", owner=owner["did"])[0]     # receipt from elsewhere
    assert not R.verify(R.build(run), key="", owner=owner["did"])[0]  # no receipt, owner pinned
    assert "receipt under" in R.markdown(cert)


def test_issued_receipts_form_an_intact_chain(tmp_path):
    run, owner, ident = _zanii_run(tmp_path)
    assert not R.verify_chain(run)[0]                                 # nothing issued yet
    for _ in range(3):
        R.issue(run, identity=ident)
    R.build(run, identity=ident)                                      # preview: not chained
    assert R.verify_chain(run, owner=owner["did"]) == (True, "3 receipts, chain intact")
    log = run / "receipts.jsonl"
    lines = log.read_text().splitlines()
    log.write_text("\n".join([lines[0], lines[2]]) + "\n")            # drop the middle one
    assert not R.verify_chain(run)[0]


def test_issue_without_identity_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    _seed_run(tmp_path)
    cert = R.issue(tmp_path / "marketing")
    assert "receipt" not in cert and not (tmp_path / "marketing" / "receipts.jsonl").exists()


def test_identity_file_validation(tmp_path):
    (tmp_path / "id.json").write_text('{"agent": {"did": "did:key:x"}}')
    with pytest.raises(ValueError):
        R.load_identity(str(tmp_path / "id.json"))


def test_cli_identity_never_overwrites_keys(tmp_path, monkeypatch):
    pytest.importorskip("zanii.core")
    import importlib.util
    spec = importlib.util.spec_from_file_location("ze_cli3", ROOT / "zanii_evolve.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit, match="PASSPHRASE"):          # no unencrypted keys by default
        cli.make_identity(tmp_path / "keys", "2031-01-01T00:00:00Z")
    monkeypatch.setenv("ZANII_EVOLVE_IDENTITY_PASSPHRASE", "pw")
    cli.make_identity(tmp_path / "keys", "2031-01-01T00:00:00Z")
    ident = R.load_identity(str(tmp_path / "keys" / "identity.json"))
    owner = json.loads((tmp_path / "keys" / "owner.json").read_text())
    assert ident["delegation"][0]["issuer"] == owner["did"]
    from zanii_evolve.custody import SCOPES as CUSTODY
    assert ident["delegation"][0]["scopes"] == [R.SCOPE, *CUSTODY]     # purpose-bound: no export
    with pytest.raises(SystemExit):
        cli.make_identity(tmp_path / "keys", "2031-01-01T00:00:00Z")


def test_publish_carries_receipt(tmp_path):
    pytest.importorskip("zanii.core")
    repo = tmp_path / "repo"
    (repo / "h").mkdir(parents=True)
    (repo / "h" / "a.py").write_text("x = 1\n")
    G.git(repo, "init", "-q", check=True)
    G.git(repo, "add", "-A", check=True)
    G.git(repo, "commit", "-qm", "h0", check=True)
    commit = G.rev(repo, "HEAD")
    run = tmp_path / "runs" / "mkt"
    run.mkdir(parents=True)
    (run / "frontier.json").write_text(json.dumps({
        "domain": "mkt", "incumbent": {"t": 0, "commit": commit, "S": 0.5, "C": 1.0},
        "trajectory": [{"t": 0, "S": 0.5, "C": 1.0, "commit": commit}], "config": {}}))
    owner, ident = R.new_identity()
    m = REG.publish(repo, run, "h", tmp_path / "reg", key="", identity=ident)
    assert m["receipt_owner"] == owner["did"]
    cert = json.loads((tmp_path / "reg" / "mkt" / commit[:8] / "certificate.json").read_text())
    assert R.verify(cert, key="", owner=owner["did"])[0]
