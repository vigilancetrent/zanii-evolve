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
"""Service API and dashboard for Zanii Evolve runs.

    python3 zanii_evolve.py serve [--host 127.0.0.1] [--port 8765]

Read endpoints (JSON unless noted):
    GET  /                                    dashboard
    GET  /api/health
    GET  /api/domains                         every domain with its run summary
    GET  /api/domains/<d>                     frontier, delta, trajectory, candidates
    GET  /api/domains/<d>/rounds/<t>/<V>/diff candidate diff (text/plain)
    GET  /api/domains/<d>/memory              lessons in the run's state dirs
    GET  /api/domains/<d>/log                 tail of the server-started process log
    GET  /api/domains/<d>/pareto              score/cost Pareto frontier
    GET  /api/domains/<d>/report              generalization certificate
    GET  /api/registry                        published harnesses (manifests)
Control endpoints (Authorization: Bearer $ZANII_EVOLVE_API_TOKEN):
    POST /api/domains/<d>/start  {"cmd": "baseline" | "run" | "smoke"}
    POST /api/domains/<d>/stop   touches runs/<d>/STOP (stops after the round)
    POST /api/domains/<d>/rollout/outcome  {"key", "commit", "ok": bool} one live outcome
Live rollout (see zanii_evolve.rollout; read auth as for every GET):
    GET  /api/domains/<d>/rollout              state + current-stage counts
    GET  /api/domains/<d>/rollout/assign?key=K harness commit for session K (sticky)
    POST /api/domains            {"name", "tasks": [...], "heldout": 0.2} creates a brief
                                 domain (tasks validated first; body <= 2 MB)

Security: binds loopback by default. A non-loopback bind requires a token
and then every /api/ request must carry it, because run data includes
harness diffs and traces. Control endpoints are disabled without a token.
On loopback the Host header must name the loopback address (DNS-rebinding
guard). Domain, round and variant are validated
against the filesystem before any path is built.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from zanii_evolve import rollout as RO

from zanii_evolve.pareto import frontier, points
from zanii_evolve.registry import default_root as default_registry
from zanii_evolve.registry import entries as registry_entries
from zanii_evolve.report import build as build_report
from zanii_evolve.tasksets import create_domain

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = Path(__file__).resolve().parent / "dashboard" / "index.html"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_VARIANT = re.compile(r"^[A-Z]$")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
COMMANDS = {"baseline", "run", "smoke"}


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_jsonl(p: Path, limit: int = 5000) -> list:
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
                if len(out) >= limit:
                    break
    except OSError:
        pass
    return out


class State:
    """Filesystem view of domains and runs, plus processes this server started."""

    def __init__(self, runs: Path, token: str | None):
        self.runs = Path(runs)
        self.registry = default_registry(ROOT)
        self.token = token
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    # ---- discovery ---------------------------------------------------------
    def domains(self) -> list[str]:
        d = ROOT / "domains"
        return sorted(p.name for p in d.iterdir()
                      if p.is_dir() and _NAME.match(p.name) and (p / "adapter.py").is_file())

    def check_domain(self, name: str) -> str:
        if not _NAME.match(name or "") or name not in self.domains():
            raise LookupError(f"unknown domain {name!r}")
        return name

    def running(self, name: str) -> bool:
        with self.lock:
            p = self.procs.get(name)
            return p is not None and p.poll() is None

    # ---- read models -------------------------------------------------------
    def summary(self, name: str) -> dict:
        run = self.runs / name
        fr = _read_json(run / "frontier.json")
        cfg = _read_json(ROOT / "domains" / name / "zanii_evolve.json", {}) or {}
        out = {"name": name, "brief": (ROOT / "domains" / name / "tasks.jsonl").is_file(),
               "has_run": fr is not None, "running": self.running(name),
               "stop_requested": (run / "STOP").exists(), "T": cfg.get("T")}
        if fr:
            traj = fr.get("trajectory") or []
            out.update({"S": fr["incumbent"].get("S"), "S0": traj[0]["S"] if traj else None,
                        "S_star": fr.get("S_star"), "rounds": max(0, len(traj) - 1)})
        return out

    def detail(self, name: str) -> dict:
        run = self.runs / name
        fr = _read_json(run / "frontier.json")
        cal = _read_json(run / "calibration.json", {}) or {}
        cfg = _read_json(ROOT / "domains" / name / "zanii_evolve.json", {}) or {}
        hist = _read_jsonl(run / "history.jsonl")
        cands: dict[tuple, dict] = {}
        for r in hist:
            if r.get("variant") in (None, "-"):
                continue
            key = (r["t"], r["variant"])
            c = cands.setdefault(key, {
                "t": r["t"], "variant": r["variant"], "outcome": r.get("outcome"),
                "accepted": r.get("accepted"), "S": r.get("S"), "C": r.get("C"),
                "delta_S": r.get("delta_S"), "delta_C": r.get("delta_C"),
                "detail": r.get("detail"), "edits": [],
                "has_diff": (run / f"r{r['t']}" / r["variant"] / "diff.patch").is_file()})
            c["edits"].append({k: r.get(k) for k in
                               ("edit_id", "component", "hypothesis", "targets_mode")})
        rounds = {}
        for t in sorted({c["t"] for c in cands.values()}):
            rounds[t] = _read_json(run / f"r{t}" / "directives.json", {}) or {}
        return {**self.summary(name),
                "delta": cfg.get("delta") if cfg.get("delta") is not None else cal.get("delta"),
                "incumbent": (fr or {}).get("incumbent"),
                "trajectory": (fr or {}).get("trajectory", []),
                "candidates": sorted(cands.values(), key=lambda c: (c["t"], c["variant"])),
                "directives": rounds}

    def diff(self, name: str, t: str, variant: str) -> str:
        if not t.isdigit() or not _VARIANT.match(variant):
            raise LookupError("bad round or variant")
        p = self.runs / name / f"r{int(t)}" / variant / "diff.patch"
        if not p.is_file():
            raise LookupError("no diff for this candidate")
        return p.read_text(encoding="utf-8", errors="replace")[:400_000]

    def memory(self, name: str, limit: int = 300) -> list:
        """Cross-task lessons (JSONL and ZaniiDB) under runs/<d>/state.
        Per-task stores (state/**/tasks/) are task input, not lessons."""
        root = self.runs / name / "state"
        rows: list = []
        if not root.is_dir():
            return rows
        for p in sorted(root.rglob("*.jsonl")):
            if "tasks" in p.relative_to(root).parts:
                continue
            kind = "episodic" if "episodic" in p.name else "semantic"
            for r in _read_jsonl(p, limit):
                rows.append({"store": "jsonl", "job": str(p.parent.relative_to(root)),
                             "kind": kind, "note": r.get("note"), "tags": r.get("tags", [])})
        for d in sorted(root.rglob("zaniidb")):
            if d.is_dir() and "tasks" not in d.relative_to(root).parts:
                rows += _zaniidb_rows(d, root, limit)
        return rows[:limit]

    def log_tail(self, name: str, lines: int = 200) -> str:
        p = self.runs / name / "logs" / "server.log"
        try:
            return "".join(p.read_text(encoding="utf-8", errors="replace").splitlines(True)[-lines:])
        except OSError:
            return ""

    # ---- control -----------------------------------------------------------
    def start(self, name: str, cmd: str) -> dict:
        if cmd not in COMMANDS:
            raise ValueError(f"cmd must be one of {sorted(COMMANDS)}")
        with self.lock:
            p = self.procs.get(name)
            if p is not None and p.poll() is None:
                raise RuntimeError(f"{name} is already running (pid {p.pid})")
            run = self.runs / name
            (run / "logs").mkdir(parents=True, exist_ok=True)
            (run / "STOP").unlink(missing_ok=True)
            log = open(run / "logs" / "server.log", "a", encoding="utf-8")
            self.procs[name] = subprocess.Popen(
                [sys.executable, str(ROOT / "zanii_evolve.py"), "--domain", name,
                 "--runs", str(self.runs), cmd],
                cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
            log.close()
            return {"started": cmd, "pid": self.procs[name].pid}

    def stop(self, name: str) -> dict:
        run = self.runs / name
        run.mkdir(parents=True, exist_ok=True)
        (run / "STOP").touch()
        return {"stop_requested": True}


def _zaniidb_rows(d: Path, root: Path, limit: int) -> list:
    try:
        import asyncio

        from zanii_memory import Settings, ZaniiMemory
    except ImportError:
        return []

    async def go():
        m = ZaniiMemory(Settings(data_dir=str(d), database_url=""))
        await m.initialize()
        try:
            return m.store.get_l1_filtered(limit=limit)
        finally:
            await m.close()

    try:
        recs = asyncio.run(go())
    except Exception:  # noqa: BLE001 - a broken store never breaks the dashboard
        return []
    out = []
    for r in recs:
        try:
            meta = json.loads(r.get("metadata") or "{}")
        except ValueError:
            meta = {}
        out.append({"store": "zaniidb", "job": str(d.parent.relative_to(root)),
                    "kind": "semantic" if r.get("type") == "instruction" else r.get("type"),
                    "note": r.get("content"),
                    "tags": [t for t in (r.get("scene_name") or "").split(",") if t]
                    + ([meta["outcome"]] if meta.get("outcome") else [])})
    return out


def make_handler(state: State, allowed_hosts: set[str] | None, read_auth: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ZaniiEvolve"

        def log_message(self, fmt, *args):            # quiet; errors still surface
            return

        # ---- plumbing ------------------------------------------------------
        def _send(self, code: int, body, ctype: str = "application/json"):
            data = (json.dumps(body, ensure_ascii=False) if ctype == "application/json"
                    else body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype == "text/html":
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; style-src 'self' 'unsafe-inline' "
                                 "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                                 "script-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            if allowed_hosts is None:
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in allowed_hosts

        def _authorized(self) -> bool:
            if not state.token:
                return False
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got.encode(), f"Bearer {state.token}".encode())

        def _route(self) -> list[str]:
            return [p for p in urlparse(self.path).path.split("/") if p]

        # ---- verbs ---------------------------------------------------------
        def do_GET(self):
            if not self._host_ok():
                return self._send(421, {"error": "unexpected Host header"})
            parts = self._route()
            if (read_auth and parts[:1] == ["api"] and parts != ["api", "health"]
                    and not self._authorized()):
                return self._send(401, {"error": "missing or wrong bearer token"})
            try:
                if not parts:
                    return self._send(200, DASHBOARD.read_text(encoding="utf-8"), "text/html")
                if parts == ["api", "health"]:
                    return self._send(200, {"ok": True, "control": bool(state.token),
                                            "read_auth": read_auth})
                if parts == ["api", "domains"]:
                    return self._send(200, [state.summary(n) for n in state.domains()])
                if parts == ["api", "registry"]:
                    return self._send(200, registry_entries(state.registry))
                if len(parts) >= 3 and parts[:2] == ["api", "domains"]:
                    name = state.check_domain(parts[2])
                    rest = parts[3:]
                    if not rest:
                        return self._send(200, state.detail(name))
                    if rest == ["memory"]:
                        return self._send(200, state.memory(name))
                    if rest == ["pareto"]:
                        return self._send(200, frontier(points(
                            _read_jsonl(state.runs / name / "history.jsonl"))))
                    if rest == ["report"]:
                        return self._send(200, build_report(state.runs / name))
                    if rest[:1] == ["rollout"]:
                        return self._rollout_get(name, rest[1:])
                    if rest == ["log"]:
                        return self._send(200, state.log_tail(name), "text/plain")
                    if len(rest) == 4 and rest[0] == "rounds" and rest[3] == "diff":
                        return self._send(200, state.diff(name, rest[1], rest[2]), "text/plain")
                return self._send(404, {"error": "not found"})
            except LookupError as e:
                return self._send(404, {"error": str(e)})

        def do_POST(self):
            if not self._host_ok():
                return self._send(421, {"error": "unexpected Host header"})
            parts = self._route()
            create = parts == ["api", "domains"]
            outcome = (len(parts) == 5 and parts[:2] == ["api", "domains"]
                       and parts[3:] == ["rollout", "outcome"])
            if not (create or outcome or (len(parts) == 4 and parts[:2] == ["api", "domains"]
                                          and parts[3] in ("start", "stop"))):
                return self._send(404, {"error": "not found"})
            if not state.token:
                return self._send(403, {"error": "control is disabled: start the server "
                                                 "with ZANII_EVOLVE_API_TOKEN set"})
            if not self._authorized():
                return self._send(401, {"error": "missing or wrong bearer token"})
            try:
                if create:
                    return self._create_domain()
                name = state.check_domain(parts[2])
                if outcome:
                    return self._rollout_outcome(name)
                if parts[3] == "stop":
                    return self._send(200, state.stop(name))
                n = int(self.headers.get("Content-Length") or 0)
                if n > 10_000:
                    return self._send(413, {"error": "body too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
                return self._send(200, state.start(name, str(body.get("cmd", ""))))
            except LookupError as e:
                return self._send(404, {"error": str(e)})
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            except (RuntimeError, FileExistsError) as e:
                return self._send(409, {"error": str(e)})

        def _rollout_get(self, name: str, rest: list[str]):
            run_dir = state.runs / name
            st = RO.load(run_dir)
            if st is None:
                raise LookupError(f"no rollout for {name}")
            if rest == ["assign"]:
                key = (parse_qs(urlparse(self.path).query).get("key") or [""])[0]
                if not 0 < len(key) <= 200:
                    return self._send(400, {"error": "key (1-200 chars) is required"})
                return self._send(200, RO.assign(st, key))
            if rest:
                return self._send(404, {"error": "not found"})
            view = {k: v for k, v in st.items() if k != "history"}
            view["counts"] = RO.counts(run_dir, st) if st["status"] == "running" else None
            view["last_decisions"] = st["history"][-5:]
            return self._send(200, view)

        def _rollout_outcome(self, name: str):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10_000:
                return self._send(413, {"error": "body too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            run_dir = state.runs / name
            with state.lock:                   # one writer at a time on the outcomes file
                st = RO.load(run_dir)
                if st is None:
                    raise LookupError(f"no rollout for {name}")
                row = RO.record(run_dir, st, body.get("key"), str(body.get("commit", "")),
                                body.get("ok"))
            return self._send(200, row)

        def _create_domain(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                return self._send(413, {"error": "body too large (max 2 MB)"})
            body = json.loads(self.rfile.read(n) or b"{}")
            tasks = body.get("tasks")
            if not isinstance(tasks, list) or not tasks or not all(isinstance(t, dict) for t in tasks):
                raise ValueError("tasks must be a non-empty list of task objects")
            heldout = float(body.get("heldout", 0.2))
            if not 0 <= heldout < 1:
                raise ValueError("heldout must be in [0, 1)")
            dest = create_domain(str(body.get("name", "")), tasks, heldout)
            return self._send(201, {"created": dest.name, "tasks": len(tasks)})

    return Handler


def build_server(host: str, port: int, runs: Path, token: str | None = None):
    token = token if token is not None else (os.environ.get("ZANII_EVOLVE_API_TOKEN") or None)
    if host not in _LOOPBACK and not token:
        raise SystemExit("refusing a non-loopback bind without ZANII_EVOLVE_API_TOKEN "
                         "(run data includes diffs and traces)")
    state = State(runs, token)
    loopback = host in _LOOPBACK
    srv = ThreadingHTTPServer((host, port), make_handler(
        state, _LOOPBACK if loopback else None, read_auth=not loopback))
    srv.daemon_threads = True
    return srv, state


def serve(host: str, port: int, runs: Path) -> None:
    srv, state = build_server(host, port, runs)
    print(f"Zanii Evolve dashboard on http://{host}:{port}/  "
          f"(control {'enabled' if state.token else 'disabled: set ZANII_EVOLVE_API_TOKEN'})",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
