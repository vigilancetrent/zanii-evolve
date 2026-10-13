"""tree_hash takes OS paths (Windows backslashes) and never returns a non-hash."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import gitops as G  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def test_tree_hash_accepts_os_paths_and_fails_loudly(tmp_path):
    (tmp_path / "domains" / "d" / "harness").mkdir(parents=True)
    (tmp_path / "domains" / "d" / "harness" / "run.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(GIT + ["init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(GIT + ["add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(GIT + ["commit", "-qm", "c"], cwd=tmp_path, check=True)
    want = subprocess.run(["git", "rev-parse", "HEAD:domains/d/harness"], cwd=tmp_path, check=True,
                          capture_output=True, text=True).stdout.strip()[:12]
    assert G.tree_hash(tmp_path, "HEAD", "domains\\d\\harness") == want
    assert G.tree_hash(tmp_path, "HEAD", os.path.normpath("domains/d/harness")) == want
    with pytest.raises(RuntimeError):
        G.tree_hash(tmp_path, "HEAD", "domains/nope")
