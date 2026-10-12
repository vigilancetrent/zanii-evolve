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
"""Harness registry: publish an evolved harness with the evidence behind it.

    registry/<domain>/<commit8>/
        harness.tar.gz     git archive of the incumbent's harness directory
        certificate.json   zanii_evolve.report certificate (digest, optional HMAC /
                           Zanii proof-of-action receipt)
        manifest.json      score, gain, held-out results, tarball sha256

The registry root is ZANII_EVOLVE_REGISTRY or <repo>/registry. Installing is
`tar -xzf harness.tar.gz` at a repository root.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import gitops as G
from . import report as R


def default_root(repo: Path) -> Path:
    return Path(os.environ.get("ZANII_EVOLVE_REGISTRY") or Path(repo) / "registry")


def publish(repo: Path, run_dir: Path, harness_rel: str, root: Path,
            key: str | None = None, identity: dict | None = None) -> dict:
    """identity None: ZANII_EVOLVE_IDENTITY when set (see report.issue)."""
    cert = R.issue(run_dir, identity=identity, key=key)
    commit = cert["incumbent"]["commit"]
    if not commit:
        raise LookupError("the incumbent has no commit to publish")
    dest = Path(root) / cert["domain"] / str(commit)[:8]
    dest.mkdir(parents=True, exist_ok=True)
    tar = dest / "harness.tar.gz"
    G.git(repo, "archive", "--format=tar.gz", "-o", str(tar), str(commit), "--",
          harness_rel, check=True)
    (dest / "certificate.json").write_text(json.dumps(cert, indent=1), encoding="utf-8")
    manifest = {
        "domain": cert["domain"], "commit": commit, "harness_path": harness_rel,
        "S": cert["incumbent"]["S"], "C": cert["incumbent"]["C"], "gain": cert["gain"],
        "noise_band": cert["noise_band"], "gain_exceeds_noise": cert["gain_exceeds_noise"],
        "heldout": cert["heldout"], "certificate_digest": cert["digest"],
        "signed": "signature" in cert,
        "receipt_owner": cert["receipt"]["delegation"][0]["issuer"] if "receipt" in cert else None,
        "harness_sha256": hashlib.sha256(tar.read_bytes()).hexdigest(),
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def entries(root: Path) -> list[dict]:
    out = []
    for p in sorted(Path(root).glob("*/*/manifest.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue                     # a half-written entry never breaks the listing
    return sorted(out, key=lambda m: m.get("published_at") or "", reverse=True)
