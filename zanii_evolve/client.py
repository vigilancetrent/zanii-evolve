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
"""Client for a live rollout, for any agent in any domain. Standard library only.

    from zanii_evolve.client import Rollout

    r = Rollout("https://evolve.internal:8765", "support", token=os.environ["ZANII_EVOLVE_API_TOKEN"])
    with r.session(ticket_id) as s:            # s.commit: the harness to run
        harness = r.fetch(s.commit, "/srv/harnesses")   # cached, sha256-checked
        reply = run_agent(harness, ticket)
        s.outcome = ticket_resolved            # bool, a score, or anything your rule reads
    # leaving the block reports the outcome; an exception reports a failure and re-raises

What counts as success is yours: pass a bool, a score in [0, 1] (counted a
success at `threshold`), or give `Rollout(success=fn)` a function that turns
whatever you record (a dict of signals, a status string) into True / False.

The client never breaks a session. If the server can't be reached, `assign`
returns the last stable commit it saw (or `default`), and outcomes it could not
deliver are dropped and counted in `r.dropped`, never raised.

Give it the agent's own Zanii identity (`Rollout(..., identity=load_identity(path))`,
needs `zanii-evolve[provable]`) and each session also gets `s.recorder`, which
signs a receipt for every action (zanii_evolve.live.SessionRecorder). Hook it into
your framework in one line: `s.recorder.wrap_tools(tools)`,
`s.recorder.langchain_callbacks()`, `s.recorder.openai_agents_hooks()` or
`s.recorder.instrument_crew(crew)`. The receipts travel with the outcome, so the
rollout also rolls back a canary that breaks the domain's rules or behaves unlike
stable, even when it reports success.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable


class Rollout:
    def __init__(self, base_url: str, domain: str, token: str | None = None, *,
                 success: Callable[[Any], bool] | None = None, threshold: float = 0.5,
                 default: str | None = None, timeout: float = 2.0, retries: int = 2,
                 cache_size: int = 10_000, identity: dict | None = None):
        if urllib.parse.urlparse(base_url).scheme not in ("http", "https"):
            raise ValueError("base_url must be an http:// or https:// URL")
        self.base = base_url.rstrip("/")
        self.domain = domain
        self.token = token
        self.success = success
        self.threshold = threshold
        self.default = default                  # commit to run when nothing is known
        self.timeout = timeout
        self.retries = retries
        self.stable: str | None = None          # last stable commit the server named
        self.dropped = 0                        # outcomes that could not be delivered
        self.identity = identity                # the agent's own Zanii identity, optional
        self._seen: OrderedDict[str, dict] = OrderedDict()   # key -> assignment, bounded
        self._cache_size = cache_size
        self._lock = threading.Lock()

    # ---- transport ---------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        """JSON over HTTP with retries on network errors and 5xx; 4xx raise at once."""
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return raw if path.endswith(".tar.gz") else json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == self.retries:
                    raise
            except (urllib.error.URLError, OSError, TimeoutError):
                if attempt == self.retries:
                    raise
            time.sleep(0.2 * 2 ** attempt)
        raise AssertionError("unreachable")

    def _api(self, tail: str) -> str:
        return f"/api/domains/{urllib.parse.quote(self.domain)}/rollout{tail}"

    # ---- routing -----------------------------------------------------------
    def assign(self, key: str) -> str | None:
        """The harness commit for session `key`: sticky, and never raises. Falls
        back to the last stable commit seen, then `default`."""
        key = str(key)
        with self._lock:
            if key in self._seen:
                return self._seen[key]["commit"]
        try:
            a = self._call("GET", self._api("/assign?key=" + urllib.parse.quote(key, safe="")))
        except (urllib.error.URLError, OSError, ValueError):
            return self.stable or self.default
        if a.get("arm") == "stable":
            self.stable = a["commit"]
        with self._lock:
            self._seen[key] = a
            while len(self._seen) > self._cache_size:
                self._seen.popitem(last=False)
        return a["commit"]

    def ok(self, outcome: Any) -> bool:
        """Your outcome as a success / failure."""
        if self.success is not None:
            return bool(self.success(outcome))
        if isinstance(outcome, bool):
            return outcome
        if isinstance(outcome, (int, float)):
            return outcome >= self.threshold
        raise TypeError("outcome is not a bool or a score: pass Rollout(success=fn) "
                        "to say what success means for it")

    def assignment(self, key: str) -> dict | None:
        """The server's full answer for a key assigned in this process."""
        with self._lock:
            return self._seen.get(str(key))

    def report(self, key: str, outcome: Any, commit: str | None = None,
               receipts: list[dict] | None = None) -> bool:
        """Report how session `key` went. Returns whether it was delivered; never
        raises for transport or a rollout that has ended."""
        key = str(key)
        good = self.ok(outcome)                 # a bad success rule is your bug: it raises
        with self._lock:
            seen = self._seen.pop(key, None)
        commit = commit or (seen or {}).get("commit")
        if not commit or seen is None and commit in (self.stable, self.default):
            return False                        # a fallback session is not part of the test
        try:
            body = {"key": key, "commit": commit, "ok": good}
            if receipts:
                body["receipts"] = receipts
            self._call("POST", self._api("/outcome"), body)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            self.dropped += 1                   # ended rollout (409), stale stage (400), network
            return False

    def session(self, key: str) -> _Session:
        return _Session(self, str(key))

    # ---- harness download --------------------------------------------------
    def fetch(self, commit: str, dest: str | Path) -> Path:
        """Download and unpack the published harness for `commit` into
        dest/<commit>/, once; the archive is checked against the manifest's sha256."""
        out = Path(dest) / commit
        if (out / ".zanii_ok").exists():
            return out
        base = f"/api/registry/{urllib.parse.quote(self.domain)}/{commit}"
        manifest = self._call("GET", base + "/manifest.json")
        blob = self._call("GET", base + "/harness.tar.gz")
        if hashlib.sha256(blob).hexdigest() != manifest["harness_sha256"]:
            raise ValueError(f"harness for {commit} does not match its manifest")
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / "harness.tar.gz"
        tmp.write_bytes(blob)
        with tarfile.open(tmp) as tf:
            root = out.resolve()
            for m in tf.getmembers():          # no absolute paths, .. or links out of dest
                p = (out / m.name).resolve()
                if not (p == root or root in p.parents) or m.issym() or m.islnk():
                    raise ValueError(f"unsafe path in harness archive: {m.name}")
            if hasattr(tarfile, "data_filter"):   # 3.12+ (and patched 3.10/3.11)
                tf.extractall(out, filter="data")
            else:
                tf.extractall(out)
        tmp.unlink()
        (out / ".zanii_ok").write_text(manifest["harness_sha256"], encoding="utf-8")
        return out


class _Session:
    """`with rollout.session(key) as s:` assigns on entry and reports on exit."""

    def __init__(self, rollout: Rollout, key: str):
        self.rollout, self.key = rollout, key
        self.commit: str | None = None
        self.outcome: Any = None
        self.recorder = None                    # set when the Rollout has an identity

    def __enter__(self) -> _Session:
        self.commit = self.rollout.assign(self.key)
        if self.rollout.identity and self.commit:
            import hashlib

            from zanii_evolve.live import SessionRecorder
            a = self.rollout.assignment(self.key) or {}
            self.recorder = SessionRecorder(
                self.rollout.identity, self.commit, a.get("harness_sha256"),
                a.get("constitution"), hashlib.sha256(self.key.encode()).hexdigest())
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        receipts = self.recorder.receipts if self.recorder else None
        if exc_type is not None:
            self.rollout.report(self.key, False, self.commit, receipts)   # a crash is a failure
        elif self.outcome is not None:
            self.rollout.report(self.key, self.outcome, self.commit, receipts)
        return False                            # never swallow your exception
