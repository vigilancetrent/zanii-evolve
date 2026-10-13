"""Procurement bake-off: vendors on the same hidden tasks, noise-tied ranks, signed."""

import argparse
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import bakeoff as BK  # noqa: E402
from zanii_evolve import providers  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402
from zanii_evolve import tasksets as TS  # noqa: E402
from zanii_evolve.brief import BriefDomain  # noqa: E402
from zanii_evolve.evaluate import TaskResult, aggregate  # noqa: E402

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


BAD_RUN = '''import argparse, json, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("--tasks"); ap.add_argument("--out"); ap.add_argument("--concurrency")
a = ap.parse_args()
for t in json.loads(pathlib.Path(a.tasks).read_text(encoding="utf-8")):
    assert "checks" not in t and "split" not in t          # vendors see the public view only
    d = pathlib.Path(a.out) / t["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps({"output": "no idea", "usage": {}}),
                                   encoding="utf-8")
'''


SEEN: list = []


class Vendor(BaseHTTPRequestHandler):

    def do_POST(self):  # noqa: N802
        task = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((task, self.headers.get("Authorization")))
        body = json.dumps({"output": "Porto trip, planned.",
                           "usage": {"input_tokens": 10, "output_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _domain(tmp_path):
    tasks = [{"id": f"t{i}", "split": "heldout", "brief": f"Plan trip {i} to Porto.",
              "checks": [{"type": "must_include", "values": ["Porto"]}]} for i in range(6)]
    tasks.append({"id": "e0", "split": "evolve", "brief": "x",
                  "checks": [{"type": "min_words", "value": 1}]})
    return BriefDomain(TS.create_domain("tender", tasks, domains_dir=tmp_path / "domains"))


def test_bakeoff_end_to_end(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", "k")
    monkeypatch.setenv("VENDOR_C_TOKEN", "sekret-token")
    dom = _domain(tmp_path)
    good = tmp_path / "good"
    shutil.copytree(ROOT / "zanii_evolve" / "templates" / "brief" / "harness", good,
                    ignore=shutil.ignore_patterns("__pycache__"))
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "run.py").write_text(BAD_RUN, encoding="utf-8")
    srv = HTTPServer(("127.0.0.1", 0), Vendor)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        vendors = [{"name": "Bad Co", "harness": str(bad)},
                   {"name": "Good Co", "harness": str(good)},
                   {"name": "Cloud C", "url": f"http://127.0.0.1:{srv.server_port}/agent",
                    "headers_env": "VENDOR_C_TOKEN"}]
        (tmp_path / "vendors.json").write_text(json.dumps(vendors), encoding="utf-8")
        ap = argparse.ArgumentParser()
        ap.add_argument("--domain", default="tender")
        BK.cli(ap.add_subparsers(dest="cmd"))
        args = ap.parse_args(["bakeoff", "--vendors", str(tmp_path / "vendors.json"),
                              "--trials", "1", "--out", str(tmp_path / "card.json")])
        ctx = SimpleNamespace(domain=dom, run_dir=tmp_path / "runs", cfg=SimpleNamespace(k=2))
        assert args.product(args, ctx) == 0
    finally:
        srv.shutdown()
    card = ST.load(tmp_path / "card.json")
    assert ST.verify(card, kind="scorecard")[0]
    rows = {r["name"]: r for r in card["body"]["vendors"]}
    assert rows["Good Co"]["S"] == 1 and rows["Cloud C"]["S"] == 1 and rows["Bad Co"]["S"] == 0
    assert rows["Good Co"]["rank_group"] == rows["Cloud C"]["rank_group"] == 1     # a tie
    assert rows["Bad Co"]["rank_group"] == 2 and rows["Bad Co"]["vs_leader"]["diff"] == -1
    assert card["body"]["n_tasks"] == 6 and card["body"]["judge_model"] == "fake/judge"
    assert "t0" not in json.dumps(card)                     # ids hashed, tasks never listed
    # the http vendor got the public view, with its token, and nothing else
    task, auth = SEEN[0]
    assert auth == "Bearer sekret-token" and set(task) == {"id", "brief"}
    assert "sekret-token" not in json.dumps(card)
    cfgs = list((tmp_path / "runs").rglob("vendor.json"))
    assert cfgs and all("sekret" not in c.read_text(encoding="utf-8") for c in cfgs)


def test_rank_ties_noise_and_separates_real_gaps():
    def ev(s):
        return aggregate("j", 2, {f"t{i}": TaskResult(rewards=[s(i)] * 2) for i in range(20)})
    rows = BK.rank({"A": ev(lambda i: 0.9), "B": ev(lambda i: 0.9 if i % 10 else 0.8),
                    "C": ev(lambda i: 0.3)})
    assert [r["name"] for r in rows] == ["A", "B", "C"]
    assert [r["rank_group"] for r in rows] == [1, 1, 2]


@pytest.mark.parametrize("vendors,msg", [
    ([{"name": "A", "url": "https://a"}], "at least two"),
    ([{"name": "A", "url": "https://a"}, {"name": "A", "url": "https://b"}], "unique"),
    # same slug (name case/space): the two would share a root and a job
    ([{"name": "Vendor A", "url": "https://a"}, {"name": "vendor a ", "url": "https://b"}],
     "unique"),
    ([{"name": "A", "url": "ftp://a"}, {"name": "B", "url": "https://b"}], "http"),
    ([{"name": "A"}, {"name": "B", "url": "https://b"}], "harness or url"),
])
def test_vendor_file_is_validated(tmp_path, vendors, msg):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(vendors), encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        BK.load_vendors(p)


def test_distinct_names_get_distinct_slugs():
    assert BK._slug("Vendor A") != BK._slug("vendor-a") != BK._slug("vendor_a")
    assert BK._slug("Vendor A") == BK._slug(" vendor a")
