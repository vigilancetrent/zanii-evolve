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
"""The certified harness store: find a published harness, install it only if
its evidence holds.

    store search [--industry insurance] [--tag claims] [--q "first notice"]
    store install claims/1a2b3c4d --to ./vendor/claims [--owner did:key:...]
    store tag claims/1a2b3c4d --industry insurance --tags claims,arabic --description "..."

Entries are registry.publish output (registry/<domain>/<commit8>/). `install`
refuses unless the tarball matches the manifest's sha256, the certificate's
digest is the one the manifest names, report.verify accepts the certificate
(signed: an unsigned one needs --allow-unsigned; with --owner, only a receipt
issued under that owner) and the archive's harness files hash to the
certificate's signed incumbent.harness_tree, so a real certificate cannot vouch
for swapped files. Members with absolute paths, `..`, links or outside the
harness folder are refused, the archive is capped at
ZANII_EVOLVE_STORE_MAX_BYTES (200 MB) and files are written 0644/0755.
industry, tags and description live in the manifest, which is not signed: they
help you find a harness, the certificate is what you trust.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

from . import registry as REG
from . import report as R

MAX_BYTES = int(os.environ.get("ZANII_EVOLVE_STORE_MAX_BYTES") or 200 * 2 ** 20)


def search(root: Path, industry: str | None = None, tag: str | None = None,
           q: str | None = None) -> list[dict]:
    """Matching manifests, the largest certified gain first."""
    out = []
    for m in REG.entries(root):
        if industry and (m.get("industry") or "").lower() != industry.lower():
            continue
        if tag and tag.lower() not in [t.lower() for t in m.get("tags") or []]:
            continue
        hay = " ".join(str(m.get(k) or "") for k in ("domain", "description", "industry"))
        if q and q.lower() not in (hay + " " + " ".join(m.get("tags") or [])).lower():
            continue
        out.append(m)
    return sorted(out, key=lambda m: (bool(m.get("gain_exceeds_noise")), m.get("gain") or 0),
                  reverse=True)


def _entry(root: Path, ref: str) -> Path:
    domain, _, commit = ref.partition("/")
    d = Path(root) / domain / commit[:8]
    if not (domain and commit) or not (d / "manifest.json").is_file():
        raise LookupError(f"no store entry {ref!r} (use DOMAIN/COMMIT8, see `store search`)")
    return d


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def tag(root: Path, ref: str, industry: str | None = None, tags: list[str] | None = None,
        description: str | None = None) -> dict:
    p = _entry(root, ref) / "manifest.json"
    m = _read(p)
    if industry is not None:
        m["industry"] = industry
    if tags is not None:
        m["tags"] = sorted(set(tags))
    if description is not None:
        m["description"] = description
    p.write_text(json.dumps(m, indent=1), encoding="utf-8")
    return m


def _git_tree(node: dict) -> str:
    """Git's tree hash of {name: (mode, blob_sha) | subtree}: what `git rev-parse
    <commit>:<path>` prints for the same files."""
    ents = []
    for name, v in node.items():
        mode, sha = ("40000", _git_tree(v)) if isinstance(v, dict) else v
        ents.append((name + "/" if isinstance(v, dict) else name, mode, name, sha))
    body = b"".join(f"{mode} {name}".encode() + b"\0" + bytes.fromhex(sha)
                    for _, mode, name, sha in sorted(ents, key=lambda e: e[0].encode()))
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def _read_archive(tar: Path, prefix: str) -> tuple[dict[str, tuple[bytes, bool]], str]:
    """The archive's files ({name: (data, executable)}) and the git tree hash of
    those under `prefix`. Refuses links, devices, absolute paths, `..`, files outside
    `prefix` (they would not be covered by the certificate) and more than MAX_BYTES."""
    files: dict[str, tuple[bytes, bool]] = {}
    tree: dict = {}
    total = 0
    with tarfile.open(tar) as tf:
        for m in tf.getmembers():
            parts = m.name.replace("\\", "/").split("/")
            if (m.name.startswith(("/", "\\")) or ":" in parts[0] or ".." in parts
                    or m.issym() or m.islnk() or m.isdev()):
                raise ValueError(f"unsafe path in harness archive: {m.name}")
            if m.isdir():
                continue
            if not m.isfile():
                raise ValueError(f"unsafe member in harness archive: {m.name}")
            name = "/".join(x for x in parts if x not in ("", "."))
            if not name.startswith(prefix + "/"):
                raise ValueError(f"{m.name} is outside the certified harness {prefix}")
            total += m.size
            if total > MAX_BYTES:
                raise ValueError(f"harness archive unpacks to more than {MAX_BYTES} bytes")
            f = tf.extractfile(m)
            data = f.read() if f else b""
            exe = bool(m.mode & 0o100)
            files[name] = (data, exe)
            node = tree
            rel = name[len(prefix) + 1:].split("/")
            for d in rel[:-1]:
                node = node.setdefault(d, {})
            node[rel[-1]] = ("100755" if exe else "100644",
                             hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest())
    return files, _git_tree(tree)


def install(root: Path, ref: str, to: Path, owner: str | None = None,
            allow_unsigned: bool = False) -> dict:
    """Verify an entry and unpack its harness into `to` (empty or new). The bytes
    unpacked are bound to the signed certificate: their git tree hash must be the
    certificate's incumbent.harness_tree, and the entry's commit and domain its own."""
    d = _entry(root, ref)
    m, cert = _read(d / "manifest.json"), _read(d / "certificate.json")
    tar = d / "harness.tar.gz"
    if hashlib.sha256(tar.read_bytes()).hexdigest() != m.get("harness_sha256"):
        raise ValueError("harness.tar.gz does not match the manifest's sha256: tampered")
    if cert.get("digest") != m.get("certificate_digest"):
        raise ValueError("the certificate is not the one the manifest names")
    ok, why = R.verify(cert, owner=owner)
    if not ok:
        raise ValueError(f"certificate rejected: {why}")
    # a signature nobody checked (no ZANII_EVOLVE_SIGNING_KEY here) proves nothing either
    if not ("HMAC ok" in why or "receipt ok" in why or "receipt self-consistent" in why) \
            and not allow_unsigned:
        raise ValueError(f"certificate is unsigned or its signature was not checked ({why}): "
                         "anyone could have written it (pass --allow-unsigned to install anyway)")
    inc = cert.get("incumbent") or {}
    commit, want_tree = str(inc.get("commit") or ""), str(inc.get("harness_tree") or "")
    if not commit or m.get("commit") != commit or d.name != commit[:8]:
        raise ValueError("the entry's commit is not the certified incumbent's")
    if m.get("domain") != cert.get("domain") or d.parent.name != cert.get("domain"):
        raise ValueError("the entry's domain is not the certificate's")
    if len(want_tree) < 7:
        raise ValueError("the certificate names no harness tree: nothing binds the archive "
                         "to it (republish with this version)")
    prefix = str(m.get("harness_path") or "").replace("\\", "/").strip("/")
    files, got_tree = _read_archive(tar, prefix)
    if not got_tree.startswith(want_tree):
        raise ValueError(f"the archive's harness (tree {got_tree[:12]}) is not the certified "
                         f"one ({want_tree})")
    to = Path(to)
    if to.exists() and any(to.iterdir()):
        raise FileExistsError(f"{to} is not empty; install into a new folder")
    to.mkdir(parents=True, exist_ok=True)
    try:
        for name, (data, exe) in files.items():
            p = to / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            p.chmod(0o755 if exe else 0o644)    # never setuid or other archived mode bits
    except Exception:
        shutil.rmtree(to, ignore_errors=True)   # it was empty or new: leave nothing half-done
        raise
    info = {"domain": m["domain"], "commit": commit, "harness_path": prefix,
            "harness_tree": want_tree, "certificate_digest": cert["digest"], "verified": why,
            "issuer_pinned": bool(owner), "gain": cert.get("gain"),
            "gain_exceeds_noise": cert.get("gain_exceeds_noise")}
    (to / ".zanii_store.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def _handler(args, ctx) -> int:
    root = REG.default_root(ctx.root)
    try:
        if args.store_cmd == "search":
            hits = search(root, args.industry, args.tag, args.q)
            for m in hits:
                ok = "certified gain" if m.get("gain_exceeds_noise") else "no certified gain"
                print(f"{m['domain']}/{str(m['commit'])[:8]}  S={m.get('S') or 0:.3f} "
                      f"gain={m.get('gain') or 0:+.3f} ({ok})  {m.get('industry') or '-'} "
                      f"{','.join(m.get('tags') or [])}  {m.get('description') or ''}")
            print(f"{len(hits)} harness(es)")
            return 0
        if args.store_cmd == "tag":
            tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
            tag(root, args.ref, args.industry, tags, args.description)
            print(f"{args.ref}: metadata updated (manifest only, not the signed certificate)")
            return 0
        info = install(root, args.ref, Path(args.to), args.owner, args.allow_unsigned)
        print(f"installed {args.ref} -> {args.to} ({info['verified']})")
        if not args.owner:
            print("  WARNING: issuer NOT pinned: any key could have signed this certificate. "
                  "Pass --owner <did> to require who certified it.")
        return 0
    except (LookupError, ValueError, OSError, tarfile.TarError) as e:
        print(f"store: {e}")
        return 1


def cli(sub) -> None:
    p = sub.add_parser("store", help="certified harness store: search, install (verified), tag")
    s = p.add_subparsers(dest="store_cmd", required=True)
    q = s.add_parser("search", help="published harnesses, largest certified gain first")
    q.add_argument("--industry")
    q.add_argument("--tag")
    q.add_argument("--q", help="text in domain, industry, tags or description")
    i = s.add_parser("install", help="verify sha256 + certificate, then unpack")
    i.add_argument("ref", help="DOMAIN/COMMIT8")
    i.add_argument("--to", required=True, help="an empty or new folder")
    i.add_argument("--owner", help="require a receipt issued under this owner DID")
    i.add_argument("--allow-unsigned", action="store_true",
                   help="install even when the certificate carries no signature or receipt")
    t = s.add_parser("tag", help="set store metadata (manifest only)")
    t.add_argument("ref", help="DOMAIN/COMMIT8")
    t.add_argument("--industry")
    t.add_argument("--tags", help="comma-separated")
    t.add_argument("--description")
    p.set_defaults(product=_handler)
