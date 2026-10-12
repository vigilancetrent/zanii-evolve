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
"""The harness memory lever: Memory() is ZaniiDB-backed when installed and
falls back to JSONL, with the same write/read/digest contract in all three
harnesses. Skips the ZaniiDB half when zaniidb-agent-memory is absent.

    python3 -m pytest -q tests/test_memory.py
"""

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESSES = ["third_party/harbor_terminus2/mechanisms.py",
             "third_party/archipelago/harness_eng/mechanisms.py",
             "third_party/archipelago/harness_workspace/mechanisms.py"]

if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:  # Windows dev box: locking is irrelevant to this contract
        sys.modules["fcntl"] = types.SimpleNamespace(
            flock=lambda *a: None, LOCK_EX=2, LOCK_SH=1, LOCK_UN=8)


def _load(rel):
    spec = importlib.util.spec_from_file_location(
        "mech_" + rel.replace("/", "_").removesuffix(".py"), ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _contract(mem):
    assert mem.write("semantic", "Run the build before the test suite.", tags=["build"])
    assert not mem.write("semantic", "Run the build before the test suite.")  # duplicate
    assert not mem.write("semantic", "answer: 42")                            # leaky
    assert mem.write("episodic", "On flaky network steps, retry with backoff.")
    assert [r["note"] for r in mem.read("semantic")] == ["Run the build before the test suite."]
    assert mem.read("semantic")[0]["tags"] == ["build"]
    assert mem.read("episodic", match="retry")[0]["note"].startswith("On flaky")
    assert "[MEMORY — semantic lessons" in mem.digest("semantic")


@pytest.mark.parametrize("rel", HARNESSES)
def test_jsonl_backend(rel, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_MEMORY", "jsonl")
    mech = _load(rel)
    with tempfile.TemporaryDirectory() as td:
        mem = mech.Memory(td)
        assert type(mem) is mech.JsonlMemory
        _contract(mem)


@pytest.mark.parametrize("rel", HARNESSES)
def test_zaniidb_backend(rel, monkeypatch):
    pytest.importorskip("zanii_memory")
    monkeypatch.delenv("ZANII_EVOLVE_MEMORY", raising=False)
    monkeypatch.setenv("ZANII_DATABASE_URL", "postgresql://must-be-ignored/db")
    mech = _load(rel)
    td = tempfile.mkdtemp()
    mem = mech.Memory(td)
    assert type(mem) is mech.ZaniiDBMemory
    _contract(mem)
    assert (Path(td) / "zaniidb").is_dir()   # run-scoped store, not the shared DB


def test_falls_back_when_zaniidb_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "zanii_memory", None)   # import raises
    mech = _load(HARNESSES[0])
    with tempfile.TemporaryDirectory() as td:
        assert type(mech.Memory(td)) is mech.JsonlMemory


def _task_loop(mech, mem):
    tm = mech.TaskMemory(mem)
    assert tm.before("") == ""
    assert tm.after("For build-and-test tasks, run the build before the test suite.")
    assert tm.after("Flaky network installs: retry with backoff before giving up.",
                    outcome="failure")
    assert not tm.after("answer: 42")                                     # leaky
    block = tm.before("a build and test task with flaky network installs")
    assert "Procedures that worked" in block and "run the build" in block
    assert "Pitfalls" in block and "retry with backoff" in block
    return tm


@pytest.mark.parametrize("rel", HARNESSES)
def test_task_memory_jsonl(rel, monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_MEMORY", "jsonl")
    mech = _load(rel)
    with tempfile.TemporaryDirectory() as td:
        tm = _task_loop(mech, mech.Memory(td))
        assert tm.before("unrelated cooking recipe") == ""
        assert tm.consolidate() == {}


@pytest.mark.parametrize("rel", HARNESSES)
def test_task_memory_zaniidb_outcome_metadata(rel, monkeypatch):
    pytest.importorskip("zanii_memory")
    monkeypatch.delenv("ZANII_EVOLVE_MEMORY", raising=False)
    mech = _load(rel)
    mem = mech.Memory(tempfile.mkdtemp())
    assert type(mem) is mech.ZaniiDBMemory
    tm = _task_loop(mech, mem)

    async def _rows():
        return mem._core.store.get_l1_filtered(limit=50)

    meta = {r["content"][:20]: json.loads(r["metadata"] or "{}") for r in mem._run(_rows())}
    assert meta["For build-and-test t"] == {"outcome": "success"}
    assert meta["Flaky network instal"] == {"outcome": "failure"}
    assert isinstance(tm.consolidate(), dict)


def test_task_memory_counts_as_memory_component():
    sys.path.insert(0, str(ROOT))
    from zanii_evolve.components import normalize
    assert normalize("memory", "+    tm = mechanisms.TaskMemory()\n", []) == "memory"
