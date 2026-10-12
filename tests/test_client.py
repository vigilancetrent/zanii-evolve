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
"""The rollout client against a real server: routing, any notion of success,
never breaking a session, and a verified harness download."""

import hashlib
import io
import json
import sys
import tarfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zanii_evolve import rollout as RO      # noqa: E402
from zanii_evolve import server as S        # noqa: E402
from zanii_evolve.client import Rollout     # noqa: E402

TOKEN = "t0k"


def _tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _publish(registry: Path, commit: str, blob: bytes, sha: str | None = None):
    d = registry / "marketing" / commit[:8]
    d.mkdir(parents=True)
    (d / "harness.tar.gz").write_bytes(blob)
    (d / "manifest.json").write_text(json.dumps(
        {"commit": commit, "harness_sha256": sha or hashlib.sha256(blob).hexdigest()}))


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_REGISTRY", str(tmp_path / "registry"))
    srv, _ = S.build_server("127.0.0.1", 0, tmp_path / "runs", token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    run_dir = tmp_path / "runs" / "marketing"
    run_dir.mkdir(parents=True)
    st = RO.start(run_dir, "aaaaaaa", "bbbbbbb", [0.5], 0.05, 10)
    yield f"http://127.0.0.1:{srv.server_address[1]}", run_dir, st, tmp_path
    srv.shutdown()


def test_routes_and_reports_any_kind_of_outcome(live):
    url, run_dir, st, _ = live
    r = Rollout(url, "marketing", TOKEN)
    commits = {k: r.assign(k) for k in ("s1", "s2", "s3", "s4", "s5", "s6")}
    assert set(commits.values()) <= {"aaaaaaa", "bbbbbbb"}
    assert all(c == RO.assign(st, k)["commit"] for k, c in commits.items())   # same as server
    assert r.report("s1", True) and r.report("s2", 0.9) and r.report("s3", 0.1)
    custom = Rollout(url, "marketing", TOKEN,
                     success=lambda o: o["resolved"] and not o["escalated"])
    custom.assign("s4")
    assert custom.report("s4", {"resolved": True, "escalated": True})
    rows = [json.loads(x) for x in (run_dir / RO.OUTCOMES).read_text().splitlines()]
    assert [x["ok"] for x in rows] == [True, True, False, False]
    with pytest.raises(TypeError, match="success=fn"):
        r.report("s5", {"resolved": True})      # no rule for a dict: say so, don't guess


def test_session_reports_on_exit_and_a_crash_as_failure(live):
    url, run_dir, _, _ = live
    r = Rollout(url, "marketing", TOKEN)
    with r.session("ok-1") as s:
        assert s.commit in ("aaaaaaa", "bbbbbbb")
        s.outcome = True
    with pytest.raises(RuntimeError), r.session("boom-1"):
        raise RuntimeError("agent crashed")
    rows = {json.loads(x)["key"]: json.loads(x)["ok"]
            for x in (run_dir / RO.OUTCOMES).read_text().splitlines()}
    assert rows == {"ok-1": True, "boom-1": False}


def test_never_breaks_a_session_when_the_server_is_gone(live):
    url, run_dir, st, _ = live
    r = Rollout(url, "marketing", TOKEN, timeout=0.5, retries=0)
    stable_key = next(k for k in (f"k{i}" for i in range(100))
                      if RO.assign(st, k)["arm"] == "stable")
    assert r.assign(stable_key) == "aaaaaaa"            # learns the stable commit
    down = Rollout("http://127.0.0.1:9", "marketing", TOKEN, timeout=0.5, retries=0,
                   default="fallback")
    assert down.assign("x") == "fallback"
    r.base = "http://127.0.0.1:9"
    assert r.assign("new-session") == "aaaaaaa"         # last stable it saw
    assert r.report("new-session", True) is False and r.dropped == 0   # fallback: not a sample


def test_an_ended_rollout_drops_outcomes_quietly(live):
    url, run_dir, st, _ = live
    r = Rollout(url, "marketing", TOKEN)
    c = r.assign("late")
    RO.apply(run_dir, st, "rollback", {})
    assert r.report("late", True, c) is False and r.dropped == 1


def test_fetch_verifies_and_unpacks_the_harness(live):
    url, _, _, tmp = live
    blob = _tar({"domains/marketing/harness/prompts.py": b"SYSTEM = 'hi'\n"})
    _publish(tmp / "registry", "bbbbbbb", blob)
    r = Rollout(url, "marketing", TOKEN)
    out = r.fetch("bbbbbbb", tmp / "h")
    assert (out / "domains/marketing/harness/prompts.py").read_text() == "SYSTEM = 'hi'\n"
    assert r.fetch("bbbbbbb", tmp / "h") == out          # cached
    _publish(tmp / "registry", "ccccccc", blob, sha="0" * 64)
    with pytest.raises(ValueError, match="does not match"):
        r.fetch("ccccccc", tmp / "h")
    _publish(tmp / "registry", "ddddddd", _tar({"../escape.py": b"x"}))
    with pytest.raises(ValueError, match="unsafe path"):
        r.fetch("ddddddd", tmp / "h")
    assert not (tmp / "escape.py").exists()


def test_registry_route_rejects_bad_paths(live):
    from test_server import req
    url, _, _, _ = live
    port = int(url.rsplit(":", 1)[1])
    for path in ("/api/registry/marketing/zzz/manifest.json",
                 "/api/registry/marketing/bbbbbbb/secret.txt",
                 "/api/registry/..%2f/bbbbbbb/manifest.json",
                 "/api/registry/marketing/eeeeeee/manifest.json"):
        assert req(port, "GET", path)[0] == 404
