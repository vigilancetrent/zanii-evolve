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
"""Custody of client records: every touch signed, where each record went,
recipients checked against the declared list, deletion closing the record, and
purpose bound by scope so an export cannot be recorded at all."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("zanii")

from zanii_evolve import custody as C       # noqa: E402
from zanii_evolve import report as R        # noqa: E402
from zanii_evolve import tasksets as TS     # noqa: E402
from zanii_evolve import trust as T         # noqa: E402

TEXT = "Customer 4411 asks where order 9 is"


@pytest.fixture
def env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ze_cli_custody", ROOT / "zanii_evolve.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(R, "DOMAINS", tmp_path / "domains")
    owner, ident = R.new_identity()
    (tmp_path / "identity.json").write_text(json.dumps(ident))
    monkeypatch.setenv("ZANII_EVOLVE_IDENTITY", str(tmp_path / "identity.json"))
    d = TS.create_domain("ops", domains_dir=tmp_path / "domains")
    cfg = json.loads((d / "zanii_evolve.json").read_text())
    cfg.update(policy_model="anthropic/claude-x", judge_model="ollama/qwen2.5",
               subprocessors={"anthropic": {"type": "zero-retention"}})
    (d / "zanii_evolve.json").write_text(json.dumps(cfg))
    trace = tmp_path / "t.jsonl"
    trace.write_text(json.dumps({"input": TEXT, "feedback": "bad"}) + "\n")
    run = tmp_path / "runs" / "ops"
    cli.ingest_traces("ops", str(trace), "Answers", 50, 0.0, tmp_path / "runs")
    tid = TS.trace_task_id(TEXT)
    domain = SimpleNamespace(cfg=cfg, tasks={t["id"]: t for t in TS.load_tasks(d / "tasks.jsonl")})
    return SimpleNamespace(cli=cli, run=run, tid=tid, domain=domain, owner=owner, ident=ident,
                           tmp=tmp_path)


def test_every_touch_is_signed_and_the_view_shows_where_it_went(env):
    C.on_evaluate(env.domain, env.run, [env.tid, "example-001"])   # a benchmark task is not tracked
    h = C.history(env.run, env.tid, env.owner["did"], ["anthropic"])
    assert h["log_intact"] and h["touches_verified"] and h["recipients_declared"]
    v = h["view"]
    assert v["ops"] == ["store", "share", "read"]                  # ollama kept it on this machine
    assert v["destinations"] == ["anthropic"] and v["destroyed_at"] is None
    log = (env.run / C.LOG).read_text()
    assert TEXT not in log and env.tid not in log                  # refs only, never content


def test_an_undeclared_recipient_is_flagged(env):
    env.domain.cfg["policy_model"] = "openai/gpt-x"
    C.on_evaluate(env.domain, env.run, [env.tid])
    h = C.history(env.run, env.tid, None, ["anthropic"])
    assert not h["recipients_declared"]
    assert any("openai" in r for r in h["recipient_issues"])


def test_forget_closes_the_record(env):
    C.on_evaluate(env.domain, env.run, [env.tid])
    T.forget("ops", env.run, env.ident, text=TEXT)
    v = C.history(env.run, env.tid, env.owner["did"], ["anthropic"])["view"]
    assert v["ops"][-1] == "delete" and v["destroyed_at"] and not v["touched_after_destroy"]


def test_hidden_or_doctored_touches_show(env):
    C.on_evaluate(env.domain, env.run, [env.tid])
    log = env.run / C.LOG
    rows = log.read_text().splitlines()
    log.write_text("\n".join(rows[:1] + rows[2:]) + "\n")         # hide one touch
    assert not C.history(env.run, env.tid)["log_intact"]
    log.write_text("\n".join(rows) + "\n")
    row = json.loads(rows[-1])
    row["payload"]["junction"] = {"to": "nobody"}                  # rewrite where it went
    log.write_text("\n".join(rows[:-1] + [json.dumps(row)]) + "\n")
    assert not C.history(env.run, env.tid)["log_intact"]


def test_purpose_is_bound_by_scope(env):
    from zanii.client import payload_bytes
    from zanii.data_custody import build_touch
    import zanii.core as zc
    c = C.Custody(env.run, env.ident)
    t = build_touch(ref=c.ref(env.tid), data_class="trace", op="export", ts=R._now())
    r = zc.create_receipt(agent_id=env.ident["agent"]["did"], delegation=env.ident["delegation"],
                          action="custody", target=t["target"],
                          payload_hash=zc.hash_payload(payload_bytes(t["payload"])), ts=R._now(),
                          prev=None, agent_private_key=bytes.fromhex(env.ident["agent"]["privateKey"]))
    assert not zc.verify_receipt(r).ok                            # an export cannot be valid


def test_older_identities_skip_custody(env, monkeypatch):
    import zanii.core as zc
    o, a = zc.generate_keypair(), zc.generate_keypair()
    old = {"agent": {"did": a.did, "privateKey": a.private_key.hex()},
           "delegation": [zc.create_cert(o.did, a.did, ["evolve.*"], "2031-01-01T00:00:00Z",
                                         o.private_key)]}
    (env.tmp / "old.json").write_text(json.dumps(old))
    monkeypatch.setenv("ZANII_EVOLVE_IDENTITY", str(env.tmp / "old.json"))
    assert C.Custody.load(env.tmp / "elsewhere") is None
