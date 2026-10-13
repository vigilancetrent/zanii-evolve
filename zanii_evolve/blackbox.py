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
"""Brief tasks from a Zanii Blackbox gateway's record of real runs.

Blackbox records every model call an agent makes, judges each run (an outcome, and
findings such as a false "done" claim or a secret sent out) and keeps the request
bodies it forwarded, already redacted at the gateway. `ingest-blackbox` reads one
agent label's closed runs through Blackbox's API, admin key in the environment
(BLACKBOX_API_KEY):

    GET /v1/sessions?label=&status=closed      the runs, newest first, paged
    GET /v1/sessions/<id>/summary              outcome, finding codes, failed claims
    GET /v1/sessions/<id>/events               the record; its first llm.request
    GET /v1/bodies/<hash>                      that request's body: the user's ask
    GET /ui/faults.json                        each finding code's title

Each run becomes a task: the last user message of its first model request is the
brief (redacted again here, at most 8,000 characters), and what went wrong in that
run becomes a pass/fail rubric criterion next to the ones you give. Only findings
about the agent's own behaviour count (the catalogue's BBX families for loops and
spend, plans, claims, tool use, grounding, policy, data and side effects), not the
gateway's bookkeeping (record integrity, a silent SDK, anchoring). Failed runs come
first, duplicates are dropped, and nothing the agent answered is copied into a task.
A run the gateway can't answer for is skipped and counted, never fatal.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from . import net as NET
from .tasksets import redact

Get = Callable[[str], bytes]
MAX_BODY = 2_000_000           # bytes read from one request body
EVENT_PAGE, EVENT_PAGES = 50, 4   # the first request is looked for in the first 200 events
WORKERS = 8                    # runs read at once
# the BBX families about what the agent itself did (spec/faults/v1.json): loops and
# streaks, spend, plans, claims, tool use, grounding, policy, data, side effects
BEHAVIOUR_FAMILIES = ("BBX-21", "BBX-31", "BBX-41", "BBX-42", "BBX-43", "BBX-44", "BBX-51",
                      "BBX-91", "BBX-92")
BEHAVIOUR_CODES = frozenset({"PROMPT_INJECTION"})          # the agent followed one
NOT_BEHAVIOUR = frozenset({"MODEL_DEPRECATED", "POLICY_AUDIT"})


def about_behaviour(code: str, catalogue: dict[str, dict]) -> bool:
    """Whether a finding is about what the agent did, by its catalogue family."""
    if code in BEHAVIOUR_CODES:
        return True
    fault = str((catalogue.get(code) or {}).get("fault") or "")
    return code not in NOT_BEHAVIOUR and fault[:6] in BEHAVIOUR_FAMILIES


def http_get(url: str, key: str, timeout: float = 30.0) -> Get:
    """A GET against the gateway at `url` with the admin key; raises RuntimeError
    with the gateway's reason on 401 / 403 / 404."""
    base = url.rstrip("/")

    def get(path: str) -> bytes:
        try:                       # https, no redirects, local when offline (net.py)
            return NET.request("GET", base + path, {"authorization": f"Bearer {key}"},
                               timeout=timeout, max_bytes=MAX_BODY, truncate=True)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Blackbox answered {e.code} for {path.split('?')[0]}: "
                               f"{e.read(300).decode('utf-8', 'replace')}") from e
    return get


def _text(content) -> str | None:
    """A message's text: a string, or the text parts of a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict)
                 and isinstance(b.get("text"), str)
                 and b.get("type") in (None, "text", "input_text")]
        return "\n".join(parts) if parts else None
    return None


def user_ask(body) -> str | None:
    """The last user message of a model request (Anthropic and OpenAI chat
    `messages`, OpenAI Responses `input`, Gemini `contents`), or None."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("input"), str):
        return body["input"]
    for key in ("messages", "input"):
        items = body.get(key)
        if isinstance(items, list):
            for m in reversed(items):
                if isinstance(m, dict) and m.get("role") == "user":
                    t = _text(m.get("content"))
                    if t:
                        return t
    contents = body.get("contents")
    if isinstance(contents, list):
        for c in reversed(contents):
            if isinstance(c, dict) and c.get("role", "user") == "user":
                t = _text(c.get("parts"))
                if t:
                    return t
    return None


def fetch_runs(get: Get, label: str, limit: int) -> tuple[list[dict], int]:
    """Of the label's newest `limit` closed runs, those with a user ask, {session_id,
    outcome, codes, claims_failed, ask}, and how many could not be read. A run whose first request
    carries no user text is left out; one the gateway can't answer for is counted."""
    out: list[dict] = []
    failed_reads = read = 0
    before = None
    while read < limit:                  # `limit` runs read, found or not: a label whose
        q = {"label": label, "status": "closed",   # runs have no ask is never scanned whole
             "limit": str(min(1000, limit - read))}
        if before:
            q["before"] = before
        page = json.loads(get("/v1/sessions?" + urllib.parse.urlencode(q)))
        ids = [s["session_id"] for s in page.get("sessions") or []][: limit - read]
        read += len(ids)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:   # order kept: newest first
            for got in ex.map(lambda sid: _safe_run(get, sid), ids):
                if got == "unreadable":
                    failed_reads += 1
                elif got is not None:
                    out.append(got)
        before = page.get("next")
        if not before:
            break
    return out[:limit], failed_reads


def _safe_run(get: Get, sid: str):
    try:
        return _run(get, sid)
    except (RuntimeError, OSError, ValueError, UnicodeDecodeError):  # timeouts are OSError
        return "unreadable"


def _first_request(get: Get, sid: str) -> dict | None:
    """The run's first llm.request event, from the record's first pages only: a long
    run is never read whole for it."""
    after = -1
    for _ in range(EVENT_PAGES):
        lines = get(f"/v1/sessions/{sid}/events?after_seq={after}&limit={EVENT_PAGE}"
                    ).decode("utf-8").splitlines()
        for line in lines:
            e = json.loads(line) if line.strip() else {}
            if e.get("kind") == "llm.request":
                return e
            after = e.get("seq", after)
        if len(lines) < EVENT_PAGE:
            return None
    return None


def _run(get: Get, sid: str) -> dict | None:
    summary = json.loads(get(f"/v1/sessions/{sid}/summary"))
    ask = None
    e = _first_request(get, sid)
    if e is not None:
        raw = get(f"/v1/bodies/{e['body_hash']}")
        if len(raw) <= MAX_BODY:
            try:
                ask = user_ask(json.loads(raw))
            except (ValueError, UnicodeDecodeError):
                ask = None
    if not ask or not ask.strip():
        return None
    return {"session_id": sid, "outcome": summary.get("outcome"),
            "codes": sorted((summary.get("finding_codes") or {}).keys()),
            "claims_failed": int((summary.get("claims") or {}).get("failed") or 0),
            "ask": ask}


def catalogue(get: Get) -> dict[str, dict]:
    """Finding code -> {fault, en}, from the gateway's catalogue; empty when it can't
    be read (then only your own rubric grades the tasks)."""
    try:
        faults = json.loads(get("/ui/faults.json")).get("faults") or {}
    except (RuntimeError, OSError, ValueError):
        return {}
    return {k: v for k, v in faults.items() if isinstance(v, dict) and v.get("en")}


def failed(run: dict, behaviour: list[str]) -> bool:
    """A run that went wrong: it failed or was abandoned, a person marked an answer
    wrong, the record contradicted a claim the agent made, or Blackbox found the
    agent itself doing something wrong."""
    return (run.get("outcome") in ("failure", "abandoned") or "USER_DOWNVOTE" in run["codes"]
            or run.get("claims_failed", 0) > 0 or bool(behaviour))


def to_tasks(runs: list[dict], cat: dict[str, dict], rubric: list[str],
             limit: int = 200) -> list[dict]:
    """Runs -> brief tasks: failed runs first, the same ask once, each task graded by
    `rubric` plus a criterion for every problem Blackbox found in that run."""
    seen, fails, rest = set(), [], []
    for run in runs:
        text = redact(run["ask"].strip())[:8000]
        h = hashlib.sha256(text.lower().encode()).hexdigest()[:10]
        if h in seen:
            continue
        seen.add(h)
        found = [c for c in run["codes"] if about_behaviour(c, cat)]
        criteria = list(rubric) + [
            f"Avoids what went wrong when this was done in production: "
            f"{cat[c]['en'].rstrip('.').lower()}." for c in found]
        if run.get("claims_failed"):
            criteria.append("Says the job is done only when it is.")
        if not criteria:
            continue                      # nothing to grade it by
        bad = failed(run, found)
        task = {"id": f"bb-{h}", "split": None, "kind": "production request",
                "brief": text, "source": "blackbox",
                "rubric": [{"criterion": c} for c in dict.fromkeys(criteria)],
                "notes": {"feedback": "bad" if bad else "good",
                          "blackbox": {"session_id": run["session_id"],
                                       "outcome": run.get("outcome"), "codes": found}}}
        (fails if bad else rest).append(task)
    return (fails + rest)[:limit]


def deprecated_models(get: Get, label: str, limit: int = 100) -> dict[str, int]:
    """Model -> how many of the label's newest `limit` closed runs called it while
    Blackbox's price table marks it deprecated (MODEL_DEPRECATED findings). Not a
    task criterion (NOT_BEHAVIOUR): it is the cue for `migrate`."""
    q = {"label": label, "status": "closed", "limit": str(min(1000, limit))}
    ids = [s["session_id"] for s in
           json.loads(get("/v1/sessions?" + urllib.parse.urlencode(q))).get("sessions") or []]

    def models(sid: str) -> set[str]:
        try:
            found = json.loads(get(f"/v1/sessions/{sid}/findings")).get("findings") or []
        except (RuntimeError, OSError, ValueError):
            return set()                  # an unreadable run says nothing about models
        refs = [((f.get("meta") or {}).get("ref") or {}) for f in found
                if f.get("code") == "MODEL_DEPRECATED"]
        return {str(r["model"]) for r in refs if r.get("model")}

    out: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for ms in ex.map(models, ids[:limit]):
            for m in ms:
                out[m] = out.get(m, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
