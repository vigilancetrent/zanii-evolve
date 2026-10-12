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
"""Service API: read models, path validation, Host guard and control auth.

    python3 -m pytest -q tests/test_server.py
"""

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zanii_evolve.server import build_server   # noqa: E402

TOKEN = "s3cret-token"


def _seed_run(runs: Path) -> None:
    d = runs / "marketing"
    (d / "r0" / "A").mkdir(parents=True)
    (d / "frontier.json").write_text(json.dumps({
        "incumbent": {"t": 1, "commit": "abc12345", "S": 0.62, "C": 1800.0},
        "S_star": 0.62, "trajectory": [{"t": 0, "S": 0.55, "C": 1700.0},
                                       {"t": 1, "S": 0.62, "C": 1800.0}]}))
    hist = [{"t": 0, "variant": "-", "outcome": "BASELINE", "S": 0.55},
            {"t": 0, "variant": "A", "edit_id": "C1", "component": "prompt",
             "hypothesis": "checklist before drafting", "outcome": "ACCEPTED", "accepted": True,
             "S": 0.62, "C": 1800.0, "delta_S": 0.07, "delta_C": 0.06, "detail": "gain > delta"},
            {"t": 0, "variant": "B", "edit_id": "C1", "component": "memory",
             "hypothesis": "task memory loop", "outcome": "critic_reject", "accepted": False}]
    (d / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
    (d / "r0" / "A" / "diff.patch").write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n")
    state = d / "state" / "base" / "s0"
    state.mkdir(parents=True)
    (state / "mem_semantic.jsonl").write_text(json.dumps(
        {"note": "List every requirement before drafting.", "tags": ["success"]}) + "\n")
    task_store = d / "state" / "base" / "s0" / "tasks" / "mem-001"
    task_store.mkdir(parents=True)
    (task_store / "mem_semantic.jsonl").write_text(json.dumps({"note": "TASK INPUT"}) + "\n")


@pytest.fixture
def server(tmp_path):
    _seed_run(tmp_path)
    srv, state = build_server("127.0.0.1", 0, tmp_path, token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], tmp_path, state
    srv.shutdown()
    srv.server_close()


def req(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read().decode()
    ctype = r.getheader("Content-Type") or ""
    return r.status, (json.loads(raw) if "json" in ctype else raw)


def test_read_endpoints(server):
    port, _, _ = server
    assert req(port, "GET", "/api/health")[1]["control"] is True
    st, doms = req(port, "GET", "/api/domains")
    names = {d["name"]: d for d in doms}
    assert st == 200 and {"marketing", "memory", "coding"} <= set(names)
    assert names["marketing"]["has_run"] and names["marketing"]["brief"]
    assert not names["memory"]["has_run"]
    st, d = req(port, "GET", "/api/domains/marketing")
    assert st == 200 and d["S0"] == 0.55 and d["rounds"] == 1
    assert [c["variant"] for c in d["candidates"]] == ["A", "B"]
    assert d["candidates"][0]["has_diff"] and not d["candidates"][1]["has_diff"]
    st, diff = req(port, "GET", "/api/domains/marketing/rounds/0/A/diff")
    assert st == 200 and "+new" in diff
    st, mem = req(port, "GET", "/api/domains/marketing/memory")
    assert [m["note"] for m in mem] == ["List every requirement before drafting."]
    st, page = req(port, "GET", "/")
    assert st == 200 and "<title>Zanii Evolve</title>" in page


@pytest.mark.parametrize("path", [
    "/api/domains/..%2f..%2fetc", "/api/domains/nope", "/api/domains/marketing/rounds/0/../diff",
    "/api/domains/marketing/rounds/x/A/diff", "/api/domains/marketing/rounds/0/AB/diff",
    "/api/domains/marketing/rounds/9/A/diff", "/api/other"])
def test_rejects_bad_paths(server, path):
    port, _, _ = server
    assert req(port, "GET", path)[0] == 404


def test_host_guard_blocks_rebinding(server):
    port, _, _ = server
    assert req(port, "GET", "/api/domains", host="evil.example:80")[0] == 421


def test_control_requires_token(server):
    port, runs, _ = server
    assert req(port, "POST", "/api/domains/marketing/stop")[0] == 401
    assert req(port, "POST", "/api/domains/marketing/stop",
               headers={"Authorization": "Bearer wrong"})[0] == 401
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert req(port, "POST", "/api/domains/marketing/start", {"cmd": "rm -rf"}, auth)[0] == 400
    assert req(port, "POST", "/api/domains/nope/stop", headers=auth)[0] == 404
    st, body = req(port, "POST", "/api/domains/marketing/stop", headers=auth)
    assert st == 200 and (runs / "marketing" / "STOP").exists()


def test_control_disabled_without_token(tmp_path):
    srv, _ = build_server("127.0.0.1", 0, tmp_path, token="")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert req(port, "POST", "/api/domains/marketing/stop")[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_non_loopback_needs_token_and_guards_reads(tmp_path):
    with pytest.raises(SystemExit):
        build_server("0.0.0.0", 0, tmp_path, token="")
    srv, _ = build_server("0.0.0.0", 0, tmp_path, token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert req(port, "GET", "/api/domains", host="10.0.0.5")[0] == 401
        assert req(port, "GET", "/api/domains", host="10.0.0.5",
                   headers={"Authorization": f"Bearer {TOKEN}"})[0] == 200
        assert req(port, "GET", "/api/health", host="10.0.0.5")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()
