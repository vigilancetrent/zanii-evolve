"""net.request: https only, no redirects (they would carry the token), bounded reads."""

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import net as NET  # noqa: E402


@pytest.fixture
def server(monkeypatch):
    monkeypatch.delenv("ZANII_EVOLVE_OFFLINE", raising=False)
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "https://elsewhere.example.com/steal")
                self.end_headers()
                return
            body = b"x" * (5000 if self.path == "/big" else 10)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def test_loopback_http_is_allowed_and_bounded(server):
    url, _ = server
    assert NET.request("GET", url + "/ok", {"Authorization": "Bearer t0k"}) == b"x" * 10
    with pytest.raises(RuntimeError, match="more than 100 bytes"):
        NET.request("GET", url + "/big", max_bytes=100)
    assert len(NET.request("GET", url + "/big", max_bytes=100, truncate=True)) == 101


def test_redirect_is_never_followed(server):
    url, seen = server
    with pytest.raises(RuntimeError) as e:
        NET.request("GET", url + "/redirect", {"Authorization": "Bearer s3cret"})
    assert "elsewhere.example.com" in str(e.value) and "s3cret" not in str(e.value)
    assert [p for p, _ in seen] == ["/redirect"]          # the token went nowhere else


@pytest.mark.parametrize("url,why", [
    ("http://blackbox.example.com/x", "use https"),
    ("file:///etc/passwd", "no host"),
    ("ftp://example.com/x", "use https"),
    ("http://10.0.0.5/x", "use https"),                   # private but not loopback
])
def test_refused_urls(url, why, monkeypatch):
    monkeypatch.delenv("ZANII_EVOLVE_OFFLINE", raising=False)
    assert why in NET.check_url(url)
    with pytest.raises(RuntimeError):
        NET.request("GET", url)


def test_offline_needs_a_local_host(monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_OFFLINE", "1")
    monkeypatch.delenv("ZANII_EVOLVE_OFFLINE_ALLOW", raising=False)
    assert "not a local host" in NET.check_url("https://bb.example.com")
    assert NET.check_url("http://127.0.0.1:9/x") == ""


def test_unknown_script_fails_validation_not_grading():
    """brief.run_check: a typo'd script name is a ValueError at import time (tasksets
    validates with run_check(c, "")), not a swallowed error that scores trials 0."""
    from zanii_evolve.brief import run_check
    with pytest.raises(ValueError, match="script must be one of"):
        run_check({"type": "script", "script": "arabik", "min": 0.6}, "")
