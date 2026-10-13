"""confirm-live: rollout outcomes + Blackbox growth -> a signed live verdict."""

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import liveconfirm as LC  # noqa: E402
from zanii_evolve import rollout as RO  # noqa: E402
from zanii_evolve import statement as ST  # noqa: E402
from zanii_evolve.products import Ctx  # noqa: E402

CANARY, STABLE, DIGEST = "c" * 40, "s" * 40, "ab" * 32


def _rollout(run_dir: Path, canary_ok: int, canary_fail: int, status: str = "running"):
    st = RO.start(run_dir, STABLE, CANARY, [0.1, 0.5], 0.05, 20)
    rows = ([{"rollout": st["id"], "stage": 0, "arm": "canary", "ok": True}] * canary_ok
            + [{"rollout": st["id"], "stage": 1, "arm": "canary", "ok": False}] * canary_fail
            + [{"rollout": st["id"], "stage": 0, "arm": "stable", "ok": True}] * 50
            + [{"rollout": "older", "stage": 0, "arm": "canary", "ok": False}] * 99)
    (run_dir / RO.OUTCOMES).write_text(
        "".join(json.dumps(r) + "\n" for r in rows) + '{"torn', encoding="utf-8")
    if status != "running":
        RO.save(run_dir, {**st, "status": status})


def _cert():
    return {"domain": "acme", "digest": DIGEST, "incumbent": {"commit": CANARY}}


GROWTH = {"sessions": 90, "pending": 0, "versions_total": 2, "versions": [
    {"id": "ver_old", "sessions": 50, "success_ppm": 800000, "success_low_ppm": 670000,
     "success_high_ppm": 890000, "vs_previous": None, "certificate": None, "model": "m"},
    {"id": "ver_new", "sessions": 40, "success_ppm": 950000, "success_low_ppm": 830000,
     "success_high_ppm": 990000, "vs_previous": "unclear", "certificate": f"sha256:{DIGEST}",
     "model": "m", "changed": ["prompt"]}]}


def test_wilson():
    assert LC.wilson(0, 0) == [0.0, 1.0]
    lo, hi = LC.wilson(95, 100)
    assert 0.88 < lo < 0.95 < hi < 0.99


def test_rollout_only(tmp_path):
    _rollout(tmp_path, 40, 2)
    r = LC.confirm(tmp_path, _cert(), None, min_runs=30)
    assert r["verdict"] == "too_early"           # still running: the harm test has not passed
    ro = r["rollout"]
    assert ro["canary"]["runs"] == 42 and ro["canary"]["successes"] == 40   # every stage
    assert ro["stable"]["runs"] == 50 and ro["matches_certificate"] is True
    _rollout(tmp_path, 40, 2, status="promoted")
    assert LC.confirm(tmp_path, _cert(), None, min_runs=30)["verdict"] == "confirmed"
    assert LC.confirm(tmp_path, _cert(), None, min_runs=100)["verdict"] == "too_early"


def test_rolled_back_or_worse_is_regressed(tmp_path):
    _rollout(tmp_path, 5, 9, status="rolled_back")
    assert LC.confirm(tmp_path, _cert())["verdict"] == "regressed"
    worse = json.loads(json.dumps(GROWTH))
    worse["versions"][1]["vs_previous"] = "worse"
    r = LC.confirm(tmp_path / "none", _cert(), worse)
    assert r["verdict"] == "regressed" and "succeeds less" in r["why"][0]


def test_other_canary_is_not_this_certificate(tmp_path):
    _rollout(tmp_path, 5, 9, status="rolled_back")
    cert = {**_cert(), "incumbent": {"commit": "d" * 40}}
    r = LC.confirm(tmp_path, cert, None)
    assert r["rollout"]["matches_certificate"] is False and r["verdict"] == "too_early"


def test_blackbox_unclear_or_silent_is_never_confirmed(tmp_path):
    g = json.loads(json.dumps(GROWTH))
    g["versions"][1]["sessions"] = 500                   # plenty of runs, verdict unclear
    assert LC.confirm(tmp_path / "none", _cert(), g, min_runs=30)["verdict"] == "too_early"
    g["versions"][1]["vs_previous"] = None
    assert LC.confirm(tmp_path / "none", _cert(), g, min_runs=30)["verdict"] == "too_early"
    g["versions"][1]["vs_previous"] = "better"
    assert LC.confirm(tmp_path / "none", _cert(), g, min_runs=30)["verdict"] == "confirmed"
    g["versions"][1].update(success_high_ppm=0, success_ppm=0, success_low_ppm=0,
                            sessions=None)
    bb = LC.blackbox_summary(g, DIGEST)
    assert bb["success_range"] == [0.0, 0.0] and bb["runs"] == 0   # 0 stays 0, null is safe


def test_growth_refuses_plain_http_and_redirects(monkeypatch):
    with pytest.raises(RuntimeError, match="use https"):
        LC.growth("http://blackbox.example.com", "x")
    monkeypatch.setenv("ZANII_EVOLVE_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="not a local host"):
        LC.growth("https://blackbox.example.com", "x")


def test_blackbox_match_by_digest():
    bb = LC.blackbox_summary(GROWTH, DIGEST)
    assert bb["matched"] and bb["version"] == "ver_new" and bb["runs"] == 40
    assert bb["success_range"] == [0.83, 0.99]
    assert LC.blackbox_summary(GROWTH, "ff" * 32)["matched"] is False
    assert LC.blackbox_summary(GROWTH, None)["version"] == "ver_new"


@pytest.fixture
def blackbox():
    seen, served = [], json.loads(json.dumps(GROWTH))

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append((self.path, self.headers.get("Authorization")))
            ok = self.path.startswith("/v1/logbook/growth?") and "label=refunds" in self.path
            body = json.dumps(served if ok else {"error": "no"}).encode()
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen, served
    srv.shutdown()


def _run(tmp_path, argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="acme")
    ap.add_argument("--runs", default=str(tmp_path))
    LC.cli(ap.add_subparsers(dest="cmd"))
    args = ap.parse_args(argv)
    return args.product(args, Ctx(args, ROOT))


def test_cli_end_to_end(tmp_path, blackbox, monkeypatch):
    url, seen, served = blackbox
    monkeypatch.setenv("BLACKBOX_API_KEY", "bbx_test")
    monkeypatch.delenv("ZANII_EVOLVE_IDENTITY", raising=False)
    (tmp_path / "acme").mkdir()
    _rollout(tmp_path / "acme", 40, 1)
    cert_p = tmp_path / "cert.json"
    import hashlib

    from zanii_evolve import report as R
    body = {"domain": "acme", "incumbent": {"commit": CANARY}}
    cert = {**body, "digest": hashlib.sha256(R._canonical(body)).hexdigest()}
    cert_p.write_text(json.dumps(cert), encoding="utf-8")
    served["versions"][1]["certificate"] = f"sha256:{cert['digest']}"
    served["versions"][1]["vs_previous"] = "better"
    assert _run(tmp_path, ["confirm-live", "--cert", str(cert_p), "--blackbox-url", url,
                           "--label", "refunds"]) == 0
    assert seen[0][1] == "Bearer bbx_test"
    stmt = ST.load(tmp_path / "acme" / "live_confirmation.json")
    assert ST.verify(stmt, kind="live_confirmation", allow_unsigned=True)[0]
    assert stmt["body"]["verdict"] == "confirmed" and stmt["body"]["blackbox"]["matched"]
    assert _run(tmp_path, ["confirm-live", "--blackbox-url", url, "--label", "nope"]) == 1
    assert _run(tmp_path, ["confirm-live", "--blackbox-url", url]) == 1    # needs --label
    cert["incumbent"]["commit"] = "x"                                  # tampered certificate
    cert_p.write_text(json.dumps(cert), encoding="utf-8")
    assert _run(tmp_path, ["confirm-live", "--cert", str(cert_p)]) == 1


def test_no_evidence_is_an_error(tmp_path):
    (tmp_path / "acme").mkdir()
    assert _run(tmp_path, ["confirm-live"]) == 1
