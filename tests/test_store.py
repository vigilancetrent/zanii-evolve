"""Certified harness store: search, verified install, metadata."""

import hashlib
import hmac
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import gitops as G  # noqa: E402
from zanii_evolve import report as R  # noqa: E402
from zanii_evolve import store as S  # noqa: E402

KEY = "store-test-key"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "-c", "core.autocrlf=false"]


def _tar(members: dict[str, bytes], link: str | None = None, mode: int = 0o644) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), mode
            tf.addfile(ti, io.BytesIO(data))
        if link:
            ti = tarfile.TarInfo("domains/claims/harness/evil")
            ti.type, ti.linkname = tarfile.SYMTYPE, link
            tf.addfile(ti)
    return buf.getvalue()


def _cert(domain: str, commit: str, gain: float, tree: str | None, sign: bool = True) -> dict:
    body = {"schema": R.SCHEMA, "domain": domain, "gain": gain, "gain_exceeds_noise": gain > 0.05,
            "incumbent": {"commit": commit, "S": 0.8, "harness_tree": tree}}
    digest = hashlib.sha256(R._canonical(body)).hexdigest()
    cert = {**body, "digest": digest}
    if sign:
        cert["signature"] = hmac.new(KEY.encode(), digest.encode(), hashlib.sha256).hexdigest()
    return cert


def _entry(root: Path, domain: str, commit: str, gain: float, blob: bytes,
           tree: str | None = "0" * 12, sign: bool = True, **meta) -> Path:
    cert = _cert(domain, commit, gain, tree, sign)
    d = root / domain / commit[:8]
    d.mkdir(parents=True)
    (d / "harness.tar.gz").write_bytes(blob)
    (d / "certificate.json").write_text(json.dumps(cert), encoding="utf-8")
    m = {"domain": domain, "commit": commit, "harness_path": f"domains/{domain}/harness",
         "S": 0.8, "gain": gain, "gain_exceeds_noise": gain > 0.05,
         "certificate_digest": cert["digest"],
         "harness_sha256": hashlib.sha256(blob).hexdigest(), "published_at": "2026-10-13T00:00:00Z",
         "industry": None, "tags": [], "description": None, **meta}
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return d


@pytest.fixture
def signing(monkeypatch):
    monkeypatch.setenv("ZANII_EVOLVE_SIGNING_KEY", KEY)


@pytest.fixture
def published(tmp_path, signing):
    """A real git commit's harness, archived like registry.publish does."""
    repo = tmp_path / "repo"
    h = repo / "domains" / "claims" / "harness"
    (h / "sub").mkdir(parents=True)
    (h / "agent.py").write_bytes(b"def solve(t, call):\r\n    return {}\n")   # CRLF kept
    (h / "sub" / "util.py").write_bytes(b"X = 1\n")
    (h / "run.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    (repo / "README").write_bytes(b"not part of the harness\n")
    subprocess.run(GIT + ["init", "-q", str(repo)], check=True)
    subprocess.run(GIT + ["-C", str(repo), "add", "-A"], check=True)
    subprocess.run(GIT + ["-C", str(repo), "update-index", "--chmod=+x",
                          "domains/claims/harness/run.sh"], check=True)
    subprocess.run(GIT + ["-C", str(repo), "commit", "-qm", "h"], check=True)
    commit = G.rev(repo, "HEAD")
    tree = G.tree_hash(repo, commit, "domains/claims/harness")
    tar = tmp_path / "h.tar.gz"
    subprocess.run(GIT + ["-C", str(repo), "archive", "--format=tar.gz", "-o", str(tar), commit,
                          "--", "domains/claims/harness"], check=True)
    reg = tmp_path / "reg"
    _entry(reg, "claims", commit, 0.12, tar.read_bytes(), tree=tree)
    return reg, commit


GOOD = {"domains/claims/harness/agent.py": b"def solve(t, call): return {}\n"}


def test_search_and_tag(tmp_path):
    _entry(tmp_path, "claims", "a" * 40, 0.12, _tar(GOOD), industry="insurance",
           tags=["claims"], description="first notice of loss")
    _entry(tmp_path, "booking", "b" * 40, 0.02, _tar(GOOD), industry="health")
    _entry(tmp_path, "claims2", "c" * 40, 0.30, _tar(GOOD), industry="insurance")
    assert [m["domain"] for m in S.search(tmp_path)] == ["claims2", "claims", "booking"]
    assert [m["domain"] for m in S.search(tmp_path, industry="Insurance")] == ["claims2", "claims"]
    assert [m["domain"] for m in S.search(tmp_path, q="notice")] == ["claims"]
    S.tag(tmp_path, "booking/bbbbbbbb", tags=["arabic", "appointments"])
    assert [m["domain"] for m in S.search(tmp_path, tag="ARABIC")] == ["booking"]
    with pytest.raises(LookupError):
        S.tag(tmp_path, "nope/12345678", industry="x")


def test_git_tree_matches_git(published):
    reg, commit = published
    tar = reg / "claims" / commit[:8] / "harness.tar.gz"
    files, tree = S._read_archive(tar, "domains/claims/harness")
    assert tree.startswith(json.loads((tar.parent / "certificate.json").read_text(
        encoding="utf-8"))["incumbent"]["harness_tree"])
    assert files["domains/claims/harness/run.sh"][1] is True       # executable bit carried


def test_install_verifies_then_unpacks(published, tmp_path):
    reg, commit = published
    ref = f"claims/{commit[:8]}"
    info = S.install(reg, ref, tmp_path / "out")
    agent = tmp_path / "out/domains/claims/harness/agent.py"
    assert agent.read_bytes() == b"def solve(t, call):\r\n    return {}\n"
    assert (tmp_path / "out/domains/claims/harness/sub/util.py").is_file()
    assert info["verified"].startswith("digest ok") and info["issuer_pinned"] is False
    assert json.loads((tmp_path / "out/.zanii_store.json").read_text(encoding="utf-8"))["commit"]
    with pytest.raises(FileExistsError):
        S.install(reg, ref, tmp_path / "out")
    with pytest.raises(ValueError, match="no receipt"):     # pinned owner needs a receipt
        S.install(reg, ref, tmp_path / "out2", owner="did:key:zOwner")


def test_a_real_certificate_cannot_vouch_for_swapped_files(published, tmp_path):
    """Copy a signed certificate, swap the tarball, fix the manifest's sha256."""
    reg, commit = published
    d = reg / "claims" / commit[:8]
    evil = _tar({"domains/claims/harness/agent.py": b"import os; os.system('x')\n"})
    (d / "harness.tar.gz").write_bytes(evil)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["harness_sha256"] = hashlib.sha256(evil).hexdigest()
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="not the certified one"):
        S.install(reg, f"claims/{commit[:8]}", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_install_refuses_unsigned_and_unbound(tmp_path, signing):
    reg = tmp_path / "reg"
    _entry(reg, "claims", "a" * 40, 0.12, _tar(GOOD), sign=False)
    with pytest.raises(ValueError, match="unsigned"):
        S.install(reg, "claims/aaaaaaaa", tmp_path / "o1")
    with pytest.raises(ValueError, match="not the certified one"):   # opted in: next check
        S.install(reg, "claims/aaaaaaaa", tmp_path / "o1", allow_unsigned=True)
    _entry(reg, "bare", "b" * 40, 0.12, _tar({"domains/bare/harness/a.py": b"x"}), tree=None)
    with pytest.raises(ValueError, match="no harness tree"):
        S.install(reg, "bare/bbbbbbbb", tmp_path / "o2")
    d = _entry(reg, "moved", "c" * 40, 0.12, _tar({"domains/moved/harness/a.py": b"x"}))
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["commit"] = "d" * 40                                   # manifest names another commit
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="commit"):
        S.install(reg, "moved/cccccccc", tmp_path / "o3")


def test_an_unchecked_signature_is_not_a_signature(published, tmp_path, monkeypatch):
    reg, commit = published
    monkeypatch.delenv("ZANII_EVOLVE_SIGNING_KEY")         # the installer holds no key
    with pytest.raises(ValueError, match="not checked"):
        S.install(reg, f"claims/{commit[:8]}", tmp_path / "out")


def test_install_refuses_tampering(tmp_path, signing):
    reg = tmp_path / "reg"
    d = _entry(reg, "claims", "a" * 40, 0.12, _tar(GOOD))
    (d / "harness.tar.gz").write_bytes(_tar({**GOOD, "x.py": b"evil"}))
    with pytest.raises(ValueError, match="sha256"):
        S.install(reg, "claims/aaaaaaaa", tmp_path / "o1")
    d = _entry(reg, "claims_b", "b" * 40, 0.12, _tar(GOOD))
    cert = json.loads((d / "certificate.json").read_text(encoding="utf-8"))
    cert["gain"] = 0.9                                         # edited after issue
    (d / "certificate.json").write_text(json.dumps(cert), encoding="utf-8")
    with pytest.raises(ValueError, match="certificate rejected"):
        S.install(reg, "claims_b/bbbbbbbb", tmp_path / "o2")
    assert not (tmp_path / "o1").exists() and not (tmp_path / "o2").exists()


@pytest.mark.parametrize("bad", [{"../escape.py": b"x"}, {"/abs.py": b"x"}, "link",
                                 {"domains/claims/other.py": b"x"}])
def test_install_refuses_unsafe_members(tmp_path, signing, bad):
    reg = tmp_path / "reg"
    blob = _tar(GOOD, link="/etc/passwd") if bad == "link" else _tar(bad)
    _entry(reg, "claims", "a" * 40, 0.1, blob)
    with pytest.raises(ValueError, match="unsafe|outside"):
        S.install(reg, "claims/aaaaaaaa", tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "escape.py").exists()


def test_archive_size_cap_and_modes(tmp_path, monkeypatch):
    big = tmp_path / "big.tar.gz"
    big.write_bytes(_tar({"domains/claims/harness/a.bin": b"0" * 4096}))
    monkeypatch.setattr(S, "MAX_BYTES", 1024)
    with pytest.raises(ValueError, match="more than 1024 bytes"):
        S._read_archive(big, "domains/claims/harness")
    monkeypatch.setattr(S, "MAX_BYTES", 10 ** 6)
    suid = tmp_path / "suid.tar.gz"
    suid.write_bytes(_tar({"domains/claims/harness/a.py": b"x"}, mode=0o4755))
    files, _ = S._read_archive(suid, "domains/claims/harness")
    assert files["domains/claims/harness/a.py"] == (b"x", True)   # only "executable" is kept


def test_publish_keeps_store_metadata(tmp_path, monkeypatch):
    from zanii_evolve import registry as REG
    cert = {"domain": "claims", "digest": "d" * 64, "gain": 0.1, "noise_band": 0.02,
            "gain_exceeds_noise": True, "heldout": [],
            "incumbent": {"commit": "e" * 40, "S": 0.9, "C": 10}}
    seen = []

    def fake_git(repo, *a, **k):
        seen.append(a)
        Path(a[a.index("-o") + 1]).write_bytes(b"tar")

    monkeypatch.setattr(R, "issue", lambda *a, **k: cert)
    monkeypatch.setattr(REG.G, "git", fake_git)
    m = REG.publish(tmp_path, tmp_path, "domains/claims/harness", tmp_path / "reg",
                    industry="insurance", tags=["claims", "claims"], description="FNOL")
    assert (m["industry"], m["tags"], m["description"]) == ("insurance", ["claims"], "FNOL")
    assert seen[0][:2] == ("-c", "core.autocrlf=false")     # committed bytes, no smudging
    assert REG.publish(tmp_path, tmp_path, "h", tmp_path / "reg2")["tags"] == []
