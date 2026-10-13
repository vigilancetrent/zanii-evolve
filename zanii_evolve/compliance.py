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
"""Compliance evidence packs: what an auditor asks for, mapped to what Evolve can show.

    evidence-pack --framework eu_ai_act,iso_42001 [--cert certificate.json]

A framework is a data file in zanii_evolve/frameworks/ (EU AI Act, ISO/IEC
42001, the UAE AI Charter, the UAE PDPL; add your sector's rules as another
file): controls, each listing the evidence keys that support it. The pack
gathers every artifact the domain has (certificate, receipt and statement
chains, custody, deletion attestations, rollout decisions, approvals, the
judge's agreement with people, the signed rulebook, product statements), and
writes one zip:

    index.json      every included file with its sha256, and each framework's coverage
    statement.json  a signed `evidence_pack` statement over index.json
    <framework>.md  control -> evidence found (file, digest) or MISSING
    evidence/...    the files themselves

A control with no evidence is listed MISSING, never left out. This is an
evidence mapping, not legal advice.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

from . import report as R
from . import statement as ST

UNVERIFIED = "_unverified"         # evidence that failed its check: listed, never counted
FRAMEWORKS = Path(__file__).resolve().parent / "frameworks"
# statement kinds that count as each evidence key (product modules name their kinds)
KINDS = {"migration": {"migration"}, "savings": {"savings"},
         "regression": {"regression", "regress"}, "scorecard": {"scorecard", "bakeoff"},
         "redteam": {"redteam", "redscore"}, "judge_pack": {"judge_pack"},
         "live_confirmation": {"live_confirmation", "liveconfirm"}}
RUN_FILES = {"receipts.jsonl": "receipts_chain", "statements.jsonl": "statements_chain",
             "custody.jsonl": "custody", "rollout.json": "rollout_decisions",
             "rollout_decisions.jsonl": "rollout_decisions", "judge_agreement.json": "judge_kappa"}


def frameworks() -> list[str]:
    return sorted(p.stem for p in FRAMEWORKS.glob("*.json"))


def load_framework(name: str) -> dict:
    p = FRAMEWORKS / f"{name}.json"
    if not p.is_file():
        raise ValueError(f"unknown framework {name!r}; known: {', '.join(frameworks())}")
    return json.loads(p.read_text(encoding="utf-8"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gather(run_dir: Path, domain_dir: Path, cert: Path | None = None,
           statements: Path | None = None, registry: Path | None = None) -> dict[str, list]:
    """Evidence key -> [{file, sha256, detail}] for everything that exists."""
    ev: dict[str, list] = {}

    def add(key: str, p: Path, detail: str = ""):
        ev.setdefault(key, []).append({"file": p, "sha256": _sha(p), "detail": detail})

    run_dir, domain_dir = Path(run_dir), Path(domain_dir)
    for name, key in RUN_FILES.items():
        p = run_dir / name
        if p.is_file() and p.stat().st_size:
            n = sum(1 for x in p.read_text(encoding="utf-8").splitlines() if x.strip())
            add(key, p, f"{n} records" if p.suffix == ".jsonl" else "")
    p = run_dir / "data_receipts.jsonl"
    if p.is_file() and "evolve.data.retention" in p.read_text(encoding="utf-8"):
        add("data_forget", p, "signed deletion attestations")
    for p in sorted((run_dir / "approvals").rglob("*.json")):
        add("approvals", p)
    for name in ("constitution.signed.json", "constitution.json"):
        if (domain_dir / name).is_file():
            add("constitution", domain_dir / name, "signed" if "signed" in name else "unsigned")
            break
    if cert is None and registry is not None:          # the newest published certificate
        found = sorted((Path(registry) / domain_dir.name).glob("*/certificate.json"),
                       key=lambda q: q.stat().st_mtime)
        cert = found[-1] if found else None
    c = _json(Path(cert)) if cert else None
    if c is not None:
        cert = Path(cert)
        ok, why = R.verify(c) if isinstance(c, dict) else (False, "not a certificate")
        if not (ok and signed(why)):
            ev.setdefault(UNVERIFIED, []).append({"file": cert, "sha256": _sha(cert),
                                                  "detail": f"certificate: {why}"})
            c = None
    if c:
        add("certificate", cert, f"gain {c.get('gain')}, above noise {c.get('gain_exceeds_noise')}")
        held = c.get("heldout") or []
        if [h for h in held if "redteam" not in str(h.get("label"))]:
            add("heldout", cert, ", ".join(str(h.get("label")) for h in held))
        if [h for h in held if "redteam" in str(h.get("label"))]:
            add("redteam", cert, "red-team held-out evaluation")
        if c.get("tasks_manifest"):
            add("tasks_manifest", cert, f"Merkle root {c['tasks_manifest'].get('hash')}")
    sdir = Path(statements) if statements else run_dir / "statements"
    for p in sorted(sdir.glob("*.json")) if sdir.is_dir() else []:
        s = _json(p)
        if not (isinstance(s, dict) and s.get("schema") == ST.SCHEMA):
            continue
        ok, why = ST.verify(s, allow_unsigned=True)
        if not (ok and signed(why)):         # forged, edited or unsigned: shown, never counted
            ev.setdefault(UNVERIFIED, []).append({"file": p, "sha256": _sha(p),
                                                  "detail": f"{s.get('kind')} statement: {why}"})
            continue
        for key, kinds in KINDS.items():
            if s.get("kind") in kinds:
                add(key, p, f"{s['kind']} statement {s.get('digest', '')[:12]}")
    return ev


def signed(why: str) -> bool:
    """A verify() message that names a checked signature (HMAC or receipt)."""
    return "HMAC ok" in why or "receipt ok" in why or "receipt self-consistent" in why


def coverage(fw: dict, ev: dict[str, list]) -> list[dict]:
    return [{"ref": c["ref"], "title": c["title"],
             "found": [k for k in c["evidence"] if ev.get(k)],
             "missing": [k for k in c["evidence"] if not ev.get(k)]} for c in fw["controls"]]


def markdown(fw: dict, rows: list[dict], ev: dict[str, list]) -> str:
    lines = [f"# {fw['title']}", "", f"_{fw['disclaimer']}_", "", fw.get("scope", ""), "",
             "| Control | Evidence found | Missing |", "|---|---|---|"]
    for r in rows:
        found = "<br>".join(f"{k}: `{e.get('arc') or e['file'].name}` sha256 {e['sha256'][:12]}"
                            + (f" ({e['detail']})" if e["detail"] else "")
                            for k in r["found"] for e in ev[k]) or "**MISSING**"
        lines.append(f"| {r['ref']} {r['title']} | {found} | {', '.join(r['missing']) or '-'} |")
    covered = sum(1 for r in rows if r["found"])
    lines += ["", f"{covered}/{len(rows)} controls have at least one piece of evidence."]
    bad = ev.get(UNVERIFIED) or []
    if bad:
        lines += ["", "## Not counted (failed verification or unsigned)", ""]
        lines += [f"- `{e.get('arc') or e['file'].name}` sha256 {e['sha256'][:12]}: {e['detail']}"
                  for e in bad]
    return "\n".join(lines) + "\n"


def pack(names: list[str], run_dir: Path, domain_dir: Path, out: Path,
         cert: Path | None = None, statements: Path | None = None,
         registry: Path | None = None, identity: dict | None = None) -> dict:
    """Write the evidence zip; returns the index (with per-framework coverage)."""
    fws = [load_framework(n) for n in names]
    ev = gather(run_dir, domain_dir, cert, statements, registry)
    files: dict[str, dict] = {}            # archive name -> entry; one name per source file
    by_path: dict[Path, str] = {}
    for items in ev.values():
        for e in items:
            src = Path(e["file"]).resolve()
            if src not in by_path:
                arc, n = f"evidence/{e['file'].parent.name}/{e['file'].name}", 1
                while arc in files:            # same name, another folder: never overwritten
                    n += 1
                    arc = f"evidence/{e['file'].parent.name}/{n}_{e['file'].name}"
                by_path[src], files[arc] = arc, e
            e["arc"] = by_path[src]
    index = {"domain": Path(domain_dir).name, "frameworks": {},
             "files": {a: e["sha256"] for a, e in sorted(files.items())},
             "unverified": sorted(e["arc"] for e in ev.get(UNVERIFIED) or [])}
    docs = {}
    for fw in fws:
        rows = coverage(fw, ev)
        index["frameworks"][fw["id"]] = {
            "title": fw["title"], "covered": sum(1 for r in rows if r["found"]),
            "total": len(rows), "missing": [r["ref"] for r in rows if not r["found"]]}
        docs[f"{fw['id']}.md"] = markdown(fw, rows, ev)
    # the coverage tables are signed too: editing one after issue breaks the index
    index["documents"] = {n: hashlib.sha256(t.encode("utf-8")).hexdigest()
                          for n, t in sorted(docs.items())}
    stmt = ST.issue("evidence_pack", index, run_dir, identity)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.json", json.dumps(index, ensure_ascii=False, indent=1))
        z.writestr("statement.json", json.dumps(stmt, ensure_ascii=False, indent=1))
        for name, text in docs.items():
            z.writestr(name, text)
        for arc, e in files.items():
            z.write(e["file"], arc)
    return index


# --------------------------------------------------------------------- cli ---
def cli(sub) -> None:
    p = sub.add_parser("evidence-pack", help="compliance evidence pack: certificate, chains, "
                                             "custody and statements mapped to a framework's "
                                             "controls, zipped and signed")
    p.add_argument("--framework", required=True,
                   help=f"comma-separated: {', '.join(frameworks())}")
    p.add_argument("--cert", help="the certificate (default: the newest published one)")
    p.add_argument("--statements", help="folder of statement files (default runs/<d>/statements)")
    p.add_argument("--out", help="the zip (default runs/<d>/evidence_<frameworks>.zip)")
    p.set_defaults(product=handle)


def handle(args, ctx) -> int:
    names = [n.strip() for n in args.framework.split(",") if n.strip()]
    registry = Path(os.environ.get("ZANII_EVOLVE_REGISTRY") or ctx.root / "registry")
    out = Path(args.out) if args.out else ctx.run_dir / f"evidence_{'_'.join(names)}.zip"
    try:
        index = pack(names, ctx.run_dir, ctx.root / "domains" / args.domain, out,
                     Path(args.cert) if args.cert else None,
                     Path(args.statements) if args.statements else None, registry)
    except ValueError as e:
        print(str(e))
        return 2
    for fid, f in index["frameworks"].items():
        print(f"{fid}: {f['covered']}/{f['total']} controls evidenced"
              + (f"; missing {', '.join(f['missing'])}" if f["missing"] else ""))
    print(f"{len(index['files'])} files -> {out}")
    return 0
