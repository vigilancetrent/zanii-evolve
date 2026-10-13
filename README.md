<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/banner.svg" width="100%" alt="Zanii Evolve">
</p>

<p align="center">
  <a href="https://pypi.org/project/zanii-evolve/"><img src="https://img.shields.io/pypi/v/zanii-evolve?color=0f766e" alt="PyPI"></a>
  <a href="https://pypi.org/project/zanii-evolve/"><img src="https://img.shields.io/pypi/pyversions/zanii-evolve" alt="Python versions"></a>
  <a href="https://github.com/vigilancetrent/zanii-evolve/actions/workflows/tests.yml"><img src="https://github.com/vigilancetrent/zanii-evolve/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/vigilancetrent/zanii-evolve/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="License"></a>
</p>

Zanii Evolve improves an LLM agent by editing its **harness** (the prompts,
control flow, tools, skills and memory around a frozen model), and only keeps
the edits that survive a regularized search. A gain has to clear the
measurement noise, pay for any extra tokens it costs, and pass a leakage screen
before it becomes the new baseline. The result is an agent that gets better on
work it has not seen, not one that memorized its test set.

Every accepted change is a git commit with a stated hypothesis and a measured
effect. A run ends with a certificate you can hand to a reviewer, optionally
signed so that anyone can verify who issued it without trusting you.

On the same core it ships twelve services an organisation in any industry can rent:
model migration with a certificate, signed savings in AED, a CI regression gate,
procurement bake-offs, compliance evidence packs (EU AI Act, ISO/IEC 42001,
UAE AI Charter, PDPL), Arabic dialect certification, a monthly red-team score,
an air-gapped edition and more. See [Products](#products).

Maintained by [zaniiai agency](https://github.com/zaniiai).

---

## Contents

- [Why](#why)
- [Install](#install)
- [Quickstart](#quickstart)
- [Bring your own tasks](#bring-your-own-tasks)
- [Continuous evolution](#continuous-evolution)
- [Certificates, signing and the registry](#certificates-signing-and-the-registry)
- [Trust and evidence](#trust-and-evidence)
- [Memory](#memory)
- [Dashboard and HTTP API](#dashboard-and-http-api)
- [Configuration](#configuration)
- [Products](#products)
- [Command reference](#command-reference)
- [Project layout](#project-layout)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## Why

Searching over harness edits against a fixed task set works, and it overfits:
the harness learns the tasks, and the in-distribution gain shrinks or vanishes
on anything new. Zanii Evolve keeps the edit space open and constrains *how the
search moves* instead.

<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/pipeline.svg" width="100%" alt="One round: analyze, propose, screen, evaluate, select; evidence out">
</p>

**On the proposal side**

- An annealed edit budget limits how many independent changes one candidate may bundle, so late gains are attributable.
- The proposer sees the full edit history, so a hypothesis that already failed is not tried again.
- When progress stalls, slots are reserved for components the run has never touched.

**On the selection side**

- A critic screens every diff for task-specific logic before any evaluation is spent on it.
- A noise-adjusted floor rejects gains that sit inside evaluation variance.
- The round's winner is measured again on fresh trials before it is accepted. The best of several noisy
  measurements is biased upward (the winner's curse), so only the fresh, unbiased measurement decides,
  and it becomes the incumbent's score. A winner that does not hold up is recorded `UNCONFIRMED`.
- A cost rule requires added inference tokens to be paid for by measured gain.
- Components that stop contributing are flagged for pruning.

Each rule maps to one place in the code:

| Rule | Code |
|---|---|
| Score and cost estimate | `zanii_evolve/evaluate.py` (`aggregate`; a missing trial scores 0 with the full denominator) |
| Annealed edit budget | `zanii_evolve/schedule.py` (`edit_budget`), enforced in `propose.py` |
| Edit history, tried set, recent yield, stall flag | `zanii_evolve/history.py` |
| Failure analysis | `zanii_evolve/analyst.py`, `digester.py` |
| Leakage critic | `zanii_evolve/critic.py` (domain denylist plus LLM review, bounded repair) |
| Noise floor, cost rule, score or cost objective | `zanii_evolve/selection.py` |
| Noise band calibration | `zanii_evolve/calibrate.py` (bootstrap over base trials, or repeated base runs) |
| Confirmation run (winner's curse) | `selection.confirm`, run by `Run._confirm` in `loop.py` (`confirm_k`, default `k`; 0 is off) |
| Judge gate | `zanii_evolve/judgecal.py` (`gate`, `record`; `min_judge_kappa`) |
| Non-compensatory domain guards | `Domain.guards` (for example a valid-rate drop in engineering design) |

## Install

```bash
pip install zanii-evolve                   # search core, providers, certificates, brief engine
pip install "zanii-evolve[provable]"       # + signed certificate receipts (zanii)
pip install "zanii-evolve[memory]"         # + ZaniiDB memory backend (Python 3.11+)
```

The command line, the bundled domains and the starting harnesses live in the
repository. To run evolutions, clone it:

```bash
git clone https://github.com/vigilancetrent/zanii-evolve.git
cd zanii-evolve
pip install -e ".[dev]"
python -m pytest tests
```

Requires Python 3.10 or newer. Benchmark runners keep their own environments:
harbor for the coding domain, and a Python 3.11 environment with
`pip install -e ".[agentic]"` for the workspace and engineering domains, pointed
to by `ZANII_EVOLVE_AGENT_PYTHON`.

## Quickstart

Pick a model provider. The model string names it:

| Model string | Backend | Credentials |
|---|---|---|
| `anthropic/<model>` | Anthropic API | `ANTHROPIC_API_KEY` |
| `vertex/<model>` | Vertex AI, round-robin over projects | `gcloud auth application-default login`, `ZANII_EVOLVE_VERTEX_PROJECTS` |
| `openai/<model>` | Any OpenAI-compatible endpoint (OpenAI, LiteLLM, vLLM) | `ZANII_EVOLVE_OPENAI_BASE_URL`, `ZANII_EVOLVE_OPENAI_API_KEY` |
| `ollama/<model>` | A local Ollama server, for on-prem and air-gapped use | `ZANII_EVOLVE_OLLAMA_BASE_URL` (no key is ever sent) |

Custom backends register with `ZANII_EVOLVE_PROVIDERS="name=module:Class"`.

Every domain then follows the same lifecycle:

```bash
python zanii_evolve.py --domain marketing smoke       # liveness check on a couple of tasks
python zanii_evolve.py --domain marketing baseline    # measure the starting harness H0
python zanii_evolve.py --domain marketing run         # evolve; resumable, `touch runs/marketing/STOP` to stop
python zanii_evolve.py --domain marketing status      # incumbent against H0
python zanii_evolve.py --domain marketing heldout --label final   # held-out split, never used for selection
```

Each round drafts candidates in separate git worktrees on branches off
`evolve/<domain>`, screens and evaluates them, and fast-forwards the branch to
the winner, so the current best harness is always a commit.

### Bundled domains

| Domain | Evolve set | Out-of-distribution checks | Notes |
|---|---|---|---|
| [`coding`](https://github.com/vigilancetrent/zanii-evolve/tree/main/domains/coding) | Terminal-Bench 2.1 | SWE-bench Verified | Docker and harbor |
| [`workspace`](https://github.com/vigilancetrent/zanii-evolve/tree/main/domains/workspace) | Harvey LAB | JobBench, GDPval, APEX-Agents | Harvey LAB checkout at a pinned commit |
| [`eng`](https://github.com/vigilancetrent/zanii-evolve/tree/main/domains/eng) | EngDesign | EngDesign v1, Frontier-Eng | grading venv, jailed tool gateway |
| [`marketing`](https://github.com/vigilancetrent/zanii-evolve/tree/main/domains/marketing) | 48 briefs, 8 fictional clients | held-out split | brief domain, no adapter code |
| [`memory`](https://github.com/vigilancetrent/zanii-evolve/tree/main/domains/memory) | 40 multi-session histories | held-out split | exercises ZaniiDB recall and supersede |

Results are reported per domain for the evolve, held-out and out-of-distribution
splits, always against H0 measured in the same window with the same frozen
model. Published numbers will ship with signed certificates.

## Bring your own tasks

Any work that is "a brief in, a text deliverable out, graded by checks and a
rubric" needs no adapter code.

```bash
python zanii_evolve.py --domain acme init-domain                    # scaffold domains/acme
python zanii_evolve.py --domain acme import-tasks briefs.csv --replace
python zanii_evolve.py --domain acme redteam --n 10                 # prompt-injection split
python zanii_evolve.py --domain acme ingest-traces prod.jsonl --rubric "Answers the question|No made-up facts"
```

A spreadsheet needs one column, `brief`. Optional columns are `id`, `split`,
`kind`, `must_include` and `must_not_include` (`|`-separated), `max_words`,
`min_words`, `max_chars`, and `rubric` (`|`-separated pass/fail criteria). Any
other column (`client`, `audience`, ...) reaches the harness as context. Rows
without a split get a seeded evolve/held-out draw, and the whole file is
validated before anything is written.

The underlying task format is one JSON object per line:

```json
{"id": "acme-001", "split": "evolve", "kind": "promotional email", "brief": "...",
 "checks": [{"type": "max_words", "value": 150}, {"type": "must_include", "values": ["Book now"]}],
 "rubric": [{"criterion": "Matches the brand voice.", "weight": 1}]}
```

Checks: `max_words`, `min_words`, `max_chars`, `must_include`, `must_include_any`,
`must_not_include`, `regex`, `regex_count`, `field_max_chars`. Rubric criteria are
judged pass/fail by `judge_model`. Checks, rubric and expected answers never
reach the harness, and grading runs outside the candidate's editable tree, so a
candidate cannot edit its own judge.

**Production traces.** `ingest-traces` accepts records with `input`, `prompt`
or an OpenTelemetry `gen_ai.prompt` attribute. Records with negative feedback
come first, duplicates are dropped, emails, phone numbers and card-like numbers
are redacted, and re-running on the same file adds nothing. Production outputs
are never copied into a task.

**From a Zanii Blackbox gateway.** If your agents run behind
[Zanii Blackbox](https://pypi.org/project/zanii-blackbox/), mint tasks straight
from one agent's recorded runs:

```bash
BLACKBOX_API_KEY=... python zanii_evolve.py --domain acme ingest-blackbox \
    --url https://blackbox.example.com --label refunds --rubric "Answers the question"
```

It reads the label's newest closed runs (`--limit` runs read, at most 1,000; a
run whose model calls carry no user text gives no task) through Blackbox's API
with the admin key, because it reads request bodies, eight at a time. The user's
ask in each run's first model request becomes the brief, redacted again here
after the gateway's own redaction. What Blackbox found the agent itself doing
wrong in that run (a success the record contradicts, an unsupported fact, a
secret sent out; by the finding's family in the gateway's catalogue, not its
bookkeeping such as a silent SDK) becomes a pass/fail criterion of its own, next
to your rubric, and marks the run as one that went wrong. A run the gateway can't
answer for is skipped and counted. Runs that failed come first, the same
ask is kept once, nothing the agent answered is copied, and each task names its
Blackbox session so you can open the evidence. When the improved harness ships,
the agent can name the certificate's digest in Blackbox
(`session.selfChange("prompt", {certificate})`) so its live numbers sit next to it.

**Red-team split.** Each variant embeds an instruction carrying a unique canary
string that the deliverable must not contain, keeps the original checks, and
lives in its own `redteam` split: `heldout --label rt --set redteam`.

**Judge calibration.** Before trusting a rubric judge with a run, grade a
handful of deliverables by hand and compare:

```bash
python zanii_evolve.py --domain acme judge-agreement --labels human.jsonl
```

It reports raw agreement, Cohen's kappa and per-criterion agreement, weakest first.
With `--record` the result is kept, and the certificate names the judge model and
its kappa. Setting `"min_judge_kappa": 0.6` in the domain's `zanii_evolve.json`
(or `--min-judge-kappa 0.6`) turns that into a gate: `baseline`, `round`, `run`
and `heldout` refuse to measure until a recorded agreement for the same judge
model reaches it.

**Custom domains.** Anything else is one module, `domains/<name>/adapter.py`,
exporting a `zanii_evolve.domain.Domain` that runs and scores a harness, renders
traces for the analyst, and supplies the domain's leakage denylist and guards.
The core never reads a benchmark format itself.

## Continuous evolution

`watch` runs the whole loop next to agents that are already in production: it
learns from their failures, ships what it can prove, and rolls it out to live
traffic only as far as live results allow.

```bash
BLACKBOX_API_KEY=... python zanii_evolve.py --domain acme watch \
    --blackbox-url https://blackbox.example.com --label refunds \
    --min-failures 20 --every 3600 \
    --rollout 0.05,0.25,0.5 --on-event ./page.sh
```

Each tick it ingests new runs (a trace file or a Blackbox gateway, as above).
Once enough new failures have built up it runs a cycle: a fresh baseline of the
current incumbent on today's tasks, then `T` rounds. New tasks change what is
measured, so the previous cycle's evidence moves to `runs/<domain>/cycles/c<N>/`;
the receipt chain, the judge record and memory carry over. A gain that clears the
noise band is evaluated on the held-out split, certified, signed (with
`ZANII_EVOLVE_IDENTITY`) and published. A gain inside the noise band ships nothing.

<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/rollout.svg" width="100%" alt="A certified gain is rolled out to growing shares of sessions; an outcome gate and a behaviour gate decide each tick between promote and roll back">
</p>

**Live rollout.** With `--rollout`, a published harness becomes a canary that
serves each listed share of sessions in turn, next to the stable harness it
would replace. Your agent runtime asks which harness a session gets, and reports
how the session went:

```bash
curl "$HOST/api/domains/acme/rollout/assign?key=$SESSION_ID"
#   {"arm": "canary", "commit": "7421c53", "stage": 0}   sticky per session
curl -X POST "$HOST/api/domains/acme/rollout/outcome" \
     -H "Authorization: Bearer $ZANII_EVOLVE_API_TOKEN" \
     -d '{"key": "'$SESSION_ID'", "commit": "7421c53", "ok": true}'
```

From Python, `zanii_evolve.client` does both calls for any domain. It uses only the
standard library, so it adds nothing to your agent's dependencies:

```python
from zanii_evolve.client import Rollout

r = Rollout("https://evolve.internal:8765", "acme", token=os.environ["ZANII_EVOLVE_API_TOKEN"],
            success=lambda o: o["resolved"] and not o["escalated"])
with r.session(ticket.id) as s:
    harness = r.fetch(s.commit, "/srv/harnesses")      # downloaded once, sha256-checked
    s.outcome = run_agent(harness, ticket)               # e.g. {"resolved": True, "escalated": False}
```

Success means whatever your domain says it means: pass a bool, a score in
[0, 1] (`threshold=`, default 0.5), or a `success=` function over anything you
record. A session that raises counts as a failure, and the exception still
propagates. The client never breaks a session: if the server can't be reached,
it serves the last stable commit it saw (or `default=`), that session is left
out of the test, and outcomes it could not deliver are counted in `r.dropped`,
never raised. Other languages call the same two endpoints.

Give the client the agent's own Zanii identity (`identity=load_identity(path)`)
and every session also gets `s.recorder`, which signs a receipt for each action.
One line hooks it into your framework: `s.recorder.wrap_tools(tools)`,
`s.recorder.langchain_callbacks()`, `s.recorder.openai_agents_hooks()` or
`s.recorder.instrument_crew(crew)`. The receipts travel with the outcome; see
[Live evidence](#live-evidence).

The harness for a commit is also served at
`/api/registry/<domain>/<commit>/harness.tar.gz` with its `manifest.json`. Every
tick tests the canary for harm: because sessions are assigned at random with a
known share, a canary that is no worse than stable receives at most that share
of the failures, and a mixture likelihood ratio on where failures land stays
valid however often it is checked (Ville's inequality; the false-rollback budget
is `--rollout-alpha`, split across stages). Harm rolls the canary back at once and
moves `evolve/<domain>` back to the stable harness, so the next cycle starts from
what is serving. A stage passes after `--rollout-min-requests` canary outcomes
without harm; passing the last one promotes the canary to stable.
`GET /api/domains/<d>/rollout` shows the state and the current stage's counts.
`--on-publish` still runs on every publish, with `ZANII_EVOLVE_MANIFEST` set, for
anything that has to stage the harness first.

**Failures.** Nothing waits on a person:

| What fails | What `watch` does |
|---|---|
| The trace file or gateway | logs `ingest_failed`, tries again next tick |
| A cycle (baseline error, a round that did not settle) | resumes it with backoff (`--every`, doubling, at most a day); new tasks wait so the cycle finishes on the task set it started with |
| The same cycle `--max-cycle-failures` times | abandons it: archives it as `cycles/c<N>-abandoned`, resets `evolve/<domain>` to the cycle's baseline, raises `cycle_abandoned` |
| Publishing a certified gain | retries with backoff before any new cycle; after `--max-retries`, raises `publish_gave_up` |
| A rollout stage gathering too few outcomes for `--stall-hours` | raises `rollout_stalled` once and keeps waiting |

**Approvals.** `--require-approval publish,promote` makes those steps wait for
the owner. `watch` raises `approval_required` with an id, the owner signs that
exact action (`zanii_evolve.py --domain acme approve <id> --owner-key owner.json`),
and the next tick carries it out. The approval is an owner-signed confirmation
bound to the hash of that one action (the human gate of `zanii.runtime`, kept on
disk so it survives between ticks): it cannot unlock a different action, a
signature from anyone but the owner does not count, and an unanswered request
expires after `--approval-ttl-hours` and is asked again. Rollback never waits.
`approvals` lists what is pending.

`--on-event` runs on every event except a quiet wait, with the event as JSON in
`ZANII_EVOLVE_EVENT`: point it at a pager, a chat webhook or a ticket. Hooks run
without a shell and with `--hook-timeout`. Every decision is appended to
`runs/<domain>/watch.json`. `--once` runs a single tick for cron, and
`touch runs/<domain>/STOP` ends the loop.

**Learning inside a deployment.** Harness changes arrive per cycle, never in the
middle of a conversation: a change counts only after it beats the incumbent
across repeated trials, and one conversation cannot show that. Between cycles a
deployed harness can still learn from each conversation through its memory
(`mechanisms.TaskMemory`: recall before a task, record a lesson after). Run it with
a persistent `ZANII_EVOLVE_STATE_DIR` if the search has switched the memory loop
on, and the live rollout guards that behaviour like any other.

## Certificates, signing and the registry

```bash
python zanii_evolve.py --domain acme report --out cert     # cert.json + cert.md
python zanii_evolve.py verify-report cert.json
python zanii_evolve.py --domain acme pareto                # score/cost trade-off of every measured harness
python zanii_evolve.py --domain acme publish               # harness archive + certificate -> registry/
```

A certificate records the incumbent against H0, the noise band, every accepted
edit with its hypothesis and measured score and cost change, the outcome of
every candidate, and every held-out evaluation. Since schema 2 it also lists
every confirmation run (the selected score next to the fresh one, and whether it
held), the average amount by which selected scores overstated confirmed ones,
and the rubric judge as checked against people. It carries a sha256 digest of
its body, so any edit after issue is detected.

Signing comes in two strengths:

- **HMAC** (`ZANII_EVOLVE_SIGNING_KEY`) proves the issuer to anyone who holds the same key.
- **Proof-of-action receipt** (`pip install "zanii-evolve[provable]"`) is an Ed25519
  signature by a `did:key` identity that an owner key delegated `evolve.*` to.
  Anyone can verify it offline against the owner's public DID, with no shared
  secret and no network call.

```bash
export ZANII_EVOLVE_IDENTITY_PASSPHRASE=...                  # keys are sealed at rest (scrypt + AES-GCM)
python zanii_evolve.py identity --out keys/ [--pq]          # owner.json (keep offline) + identity.json
export ZANII_EVOLVE_IDENTITY=keys/identity.json
python zanii_evolve.py --domain acme report --out cert
python zanii_evolve.py verify-report cert.json --owner did:key:z6Mk...
python zanii_evolve.py --domain acme verify-receipts        # no issued certificate removed or altered
```

Receipts are hash-linked in `runs/<domain>/receipts.jsonl`. Verification without
`--owner` still checks the signature but reports that the issuer is not pinned,
because a valid signature alone only proves that *some* key signed.

To optimize for cost instead of score, set `"objective": "cost"` in the domain's
`zanii_evolve.json` (or pass `--objective cost`). Admissibility is unchanged;
among the candidates that pass, the cheapest wins. Together with a new
`ZANII_EVOLVE_POLICY_MODEL` and a fresh `baseline`, this moves a harness to a
cheaper model without losing quality.

## Trust and evidence

<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/trust.svg" width="100%" alt="Five layers of evidence: keys, certificates, ledger, live sessions, data and business, each with the Zanii packages it uses and what it proves">
</p>

Every artifact below is built on the [Zanii](https://ledger.zanii.agency/docs)
packages (`pip install "zanii-evolve[provable]"`), verifies offline, and is
opt-in: without an identity, nothing changes.

### Keys

`identity` refuses to write unencrypted keys: with
`ZANII_EVOLVE_IDENTITY_PASSPHRASE` set, both private keys are sealed with
scrypt and AES-256-GCM (`zanii.kms`) and opened only in memory. `--pq` adds an
ML-DSA-65 key bound to the `did:key`, and every certificate is then dual-signed,
Ed25519 and post-quantum (`pip install "zanii-evolve[pq]"`); both signatures
must verify. `anchor-identity` records the post-quantum key binding on the ledger once,
so its timestamp proves the binding predates any future Ed25519 break;
`anchor-identity --check pq_transition.json` re-verifies it offline.

```bash
python zanii_evolve.py rotate-identity --owner-key keys/owner.json \
    --identity keys/identity.json --out keys2/ [--reason compromise --compromised-at 2026-10-01T00:00:00Z]
python zanii_evolve.py --domain acme verify-receipts --owner did:key:z6Mk... \
    [--revocations keys2/revocations.json]
```

A rotation is signed by the owner (and countersigned by the old key unless it
was compromised), so the receipt chain continues across keys. After a
compromise the old delegation is revoked, and receipts it signed fail
`verify-receipts --revocations`.

### Certificates others can check

```bash
python zanii_evolve.py --domain acme report --out cert --public domain,gain,noise_band,gain_exceeds_noise
python zanii_evolve.py verify-report cert.public.json --owner did:key:z6Mk...
```

Every field of a signed certificate is also committed with its own salt
(`zanii.redact`). The public view proves the chosen fields against the signed
commitment and reveals nothing else: a client sees that the gain is real
without seeing another client's tasks. A certificate whose gain clears the
noise band is receipted on its own target, `evolve.report.gain`, so the ledger
alone can tell a certified gain from any other certificate.

**On a ledger.** With `ZANII_LEDGER_URL` and an ingest key (`ZANII_API_KEY`, or
`ZANII_API_KEY_FILE` pointing at a file that holds it), the certificate receipt
is recorded on that Zanii ledger, which fixes its time and order;
`verify-report --ledger <url>` proves its Merkle inclusion. Run `ledger-check`
once to audit the ledger's history, and `watch` then proves on every tick that
the log only grew (`zanii.monitor`); a rewritten log raises `ledger_violation`.

### The rulebook

```bash
python zanii_evolve.py --domain acme constitution sign       # domains/acme/constitution.json -> signed
python zanii_evolve.py --domain acme constitution check crm.export.csv
```

`constitution.json` lists rules (`forbid`, `require_approval`, `allow`) over
action targets (`crm.export.*`), first match wins. The signed rulebook's hash is
written into every certificate, live session receipts are stamped with it, and
editing a rule after signing breaks the signature.

### Live evidence

With session receipts flowing in, the live rollout gains a second gate: a canary
is rolled back, even when its sessions report success, if it takes an action the
rulebook forbids or drifts from how stable behaves (`zanii.sentinel`: targets
stable never touches, rate spikes, and the read, archive, send-outside sequence
of an exfiltration). Uploaded receipts are verified before an outcome counts,
and must name the harness the session's arm serves (`zanii.attest`).

With `ZANII_EVOLVE_IDENTITY` set on the server, each outcome also gets a signed
decision receipt (`zanii.decisions`): which arm served the session, under which
rollout plan. `audit-rollout` re-checks the decision chain, that every arm
follows from its plan's share, that two plans never governed at once, and the
session receipts under the rulebook.

### Client data

```bash
python zanii_evolve.py data prove --cert cert.json --text "the client's original request" --out proof.json
python zanii_evolve.py data verify --proof proof.json --cert cert.json
python zanii_evolve.py --domain acme data forget --text "the client's original request" --out deleted.json
```

**Where a record went.** Client records (tasks minted from traces or a Blackbox
gateway) are not left untouched: they are stored, and every evaluation and
analysis sends them to a model. Each of those touches is a signed custody
receipt (`zanii.data_custody`): `store` on ingest, `share` when a hosted model
receives the record (naming the provider, with a `zanii.subprocessor` transfer
under the agreement the domain declares), `read` when a local Ollama model
used it, and `delete` on `forget`. The receipts form one hash chain, so a touch
cannot be hidden, and name the record only by an HMAC ref. The signing identity
is delegated exactly those operations, so an export or a transform cannot even
produce a valid receipt.

```json
"subprocessors": {"anthropic": {"type": "zero-retention"}, "openai": {"type": "dpa", "id": "DPA-2026-07"}}
```

```bash
python zanii_evolve.py --domain acme data custody --text "the client's original request"
```

`data custody` shows a client every recorded touch of their record, every
recipient, whether it was deleted, and whether each recipient was declared
(add `subprocessors` to the domain's `zanii_evolve.json`; a model provider that
is not listed is flagged). What it cannot show is a copy taken outside the
system, by someone with disk access; that takes an attested enclave.

Every certificate commits to a sorted Merkle manifest of the exact tasks its
cycle trained on (`zanii.train`), so a record can be proven in or out of that
set against the signed hash. `forget` deletes a record from the task set and
every run file that names it, then signs a deletion attestation
(`zanii.retention`) that names the subject only by a salted commitment; the
client keeps the salt to check it.

### Business artifacts

```bash
python zanii_evolve.py cv --out cv.json                      # signed AgentCV: every published certificate
python zanii_evolve.py risk-profile                          # underwriting facts, never a score
python zanii_evolve.py sla create --client did:key:... --start 2026-11-01T00:00:00Z \
    --end 2026-12-01T00:00:00Z --min-certificates 2 --price 5000 --out sla.json
python zanii_evolve.py --domain acme sla assess --file sla.json
python zanii_evolve.py escrow create --payer did:key:... --amount 5000.00 --deadline 2026-12-31T00:00:00Z --out escrow.json
python zanii_evolve.py escrow check --file escrow.json --cert cert.json   # needs ZANII_LEDGER_URL
python zanii_evolve.py --domain acme audit --owner did:key:... --out audit.md
```

The CV's summary comes from the ledger's own count when certificates are
anchored there, and is labelled as unattested otherwise. An SLA is assessed
from the provider's receipt chain, where a hidden certificate breaks the chain
instead of disappearing. An escrow pays per certified gain: it releases only
against an `evolve.report.gain` receipt that is Merkle-proven on the ledger, and
Zanii never holds the funds. `audit` writes one auditor's report over the
domain's certificates, receipt chains, rulebook, rollouts, client data and,
with `--memory-dir`, a deployment's memory; `--bundle` appends the ledger's own
compliance report (`zanii.compliance`).

## Memory

The structural `memory` component is `mechanisms.Memory()` in each starting
harness. It is inert until the proposer wires it in, and it exposes the same
`write` / `read` / `digest` surface whichever backend runs:

| Backend | When | Storage |
|---|---|---|
| ZaniiDB | default when `zaniidb-agent-memory` is installed | `<state_dir>/zaniidb` (SQLite), hybrid BM25 and vector recall |
| JSONL | ZaniiDB missing, or `ZANII_EVOLVE_MEMORY=jsonl` | locked `*.jsonl` under `<state_dir>` |

Memory is scoped to one run on purpose (`ZANII_DATABASE_URL` is ignored), so
lessons cannot leak between runs. `mechanisms.TaskMemory` adds a
recall-before, record-after loop that stores successes as procedures and
failures as pitfalls. Brief harnesses ship it wired but switched off, so H0 is
unchanged and the search decides whether it pays.

**Provable memory.** With `ZANII_EVOLVE_PROVABLE_MEMORY=1`, every stored lesson
also gets a hash-chained `zanii.memory` entry holding a salted commitment to it.
`verify-memory <state_dir>` then shows any lesson edited, deleted or slipped in
afterwards, and `s.recorder.attest_memory(state_dir)` signs the chain head into a
live session, so the whole chain cannot be rewritten to match.

**Lesson packs** are the explicit, opt-in exception. `lessons export` writes a
run's general lessons after the leakage guard, a task-id screen, PII redaction
and de-duplication. `lessons import` merges packs into
`domains/<name>/shared_lessons.jsonl`, which every trial of that domain then
starts from, H0 included. A pack switches the domain to the JSONL backend;
re-run `baseline` after importing one.

The [`skills/zaniidb`](https://github.com/vigilancetrent/zanii-evolve/tree/main/skills/zaniidb)
and [`skills/zanii`](https://github.com/vigilancetrent/zanii-evolve/tree/main/skills/zanii)
folders are agent skills that document the ZaniiDB and Zanii SDKs for coding assistants.

## Dashboard and HTTP API

```bash
python zanii_evolve.py serve                                # http://127.0.0.1:8765/
ZANII_EVOLVE_API_TOKEN=... python zanii_evolve.py serve     # also enables control endpoints
```

The dashboard shows the incumbent against H0 and the noise band, every candidate
with its verdict and diff, the accepted edits and the stored lessons.

| Endpoint | Purpose |
|---|---|
| `GET /api/domains`, `GET /api/domains/<d>` | run summaries, trajectory, candidates |
| `GET /api/domains/<d>/rounds/<t>/<V>/diff` | a candidate's diff |
| `GET /api/domains/<d>/memory` | lessons in the run's stores |
| `GET /api/domains/<d>/pareto` | score/cost frontier |
| `GET /api/domains/<d>/report` | certificate (the server never signs) |
| `GET /api/registry` | published harnesses |
| `POST /api/domains/<d>/start`, `/stop` | run control (token required) |
| `POST /api/domains` | create a brief domain from a task list (token required, 2 MB limit) |

The server binds to loopback by default and checks the `Host` header. Binding
any other address requires `ZANII_EVOLVE_API_TOKEN`, which every API request
must then send as a bearer token, because run data includes diffs and traces.

## Configuration

Hyperparameters live in `domains/<name>/zanii_evolve.json` and can be overridden
on the command line (`--T`, `--k`, `--m`, `--delta`, `--beta0`, `--beta1`,
`--objective`, ...). A null `delta` is estimated by `calibrate`.

| Variable | Purpose |
|---|---|
| `ZANII_EVOLVE_SEARCH_MODEL` | proposer, analyst and critic model |
| `ZANII_EVOLVE_POLICY_MODEL`, `ZANII_EVOLVE_JUDGE_MODEL` | brief-domain agent and judge |
| `ZANII_EVOLVE_SIGNING_KEY` | HMAC key for certificates |
| `ZANII_EVOLVE_IDENTITY` | identity file for signed receipts |
| `ZANII_EVOLVE_IDENTITY_PASSPHRASE` | opens sealed identity and owner keys |
| `ZANII_LEDGER_URL` | Zanii ledger certificates are anchored on and monitored |
| `ZANII_API_KEY`, `ZANII_API_KEY_FILE` | the ledger ingest key, or a file holding it |
| `ZANII_EVOLVE_PROVABLE_MEMORY` | `1`: hash-chain every stored lesson |
| `ZANII_EVOLVE_REGISTRY` | registry location (default `./registry`) |
| `ZANII_EVOLVE_API_TOKEN` | API control and non-loopback binds |
| `ZANII_EVOLVE_MEMORY` | `zaniidb` (default) or `jsonl` |
| `BLACKBOX_API_KEY` | admin key of the Zanii Blackbox gateway `ingest-blackbox` reads |

Keep keys in the environment or a secrets manager. `runs/`, `registry/` and
identity files are ignored by git.

## Products

<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/products.svg" width="100%" alt="Twelve products in four groups: move and save, gate and choose, prove, live and local; one core measures any harness with any model and signs the result">
</p>

Twelve services built on the same evaluation core: any harness, any model, any
split, compared task by task with a 95% range, and every result handed out as a
signed statement (`zanii_evolve/statement.py`: digest, HMAC with
`ZANII_EVOLVE_SIGNING_KEY`, and with an identity a zanii receipt chained onto
`runs/<d>/statements.jsonl`). A copy of each statement is kept in
`runs/<d>/statements/`, where `evidence-pack` finds it. Comparisons refuse two
measurements of different tasks, and fewer than 5 shared tasks give `too_few`,
never a verdict.

| Command | What a customer gets | Exit 1 when |
|---|---|---|
| `migrate --from A --to B` | Both models on the same commit, judge and held-out / red-team / incident tasks; "safe to migrate" only if B is non-inferior on every split. `--plan --blackbox-url U --label L` lists the models Blackbox flags `MODEL_DEPRECATED` in live runs. | B is worse beyond the margin |
| `savings --prices p.json --runs-per-month N` | Real input/output tokens at your contract prices (exact decimals), money saved a month in the currency's minor unit (AED 2 decimals); certified only when quality held. | quality dropped, or no saving |
| `regress --base origin/main` (+ `action.yml`) | A no-regression gate for every prompt or harness change, on held-out, incident and red-team tasks; Markdown for the PR summary. `uses: vigilancetrent/zanii-evolve@v0.4` with `domain:` (needs `fetch-depth: 0`). | head is worse, or nothing was measured |
| `rulebook draft --file sop.md` / `rulebook coverage` | Your SOP, policy or regulation split into clauses (id + sha256); a model drafts test tasks per clause, each rubric line naming its clause. Never written to `tasks.jsonl` until a person reviews it (`import-tasks`). | |
| `evidence-pack --framework eu_ai_act,iso_42001,uae_ai_charter,uae_pdpl` | Each control mapped to the evidence this domain actually has; only verified signatures count; a zip with a sha256 index and a signed `evidence_pack` statement. An evidence mapping, not legal advice. | |
| `bakeoff --vendors vendors.json` | Every vendor (a harness folder, or an HTTPS endpoint via `templates/vendor_http`) on the same hidden tasks with the same judge; vendors inside the noise share a rank; a signed scorecard that names the task set only by hash. Give untrusted vendors a URL, not code. | |
| `domains/arabic_service` + `dialects --job J --key dialect` | 36 service tasks in Gulf, Egyptian, Levantine, MSA, code-switching and Arabizi across 12 industries; one score split per dialect or industry (any `notes` key) with 95% ranges. Native-speaker review before a certificate is sold. | |
| `redteam-round --round 2026-10` / `redteam-score` | Fresh attacks every month (ten kinds, incl. Arabic) with secret per-round canaries (`ZANII_EVOLVE_REDTEAM_SALT`); resistance scored apart from task quality, per attack, with the trend against last month; missing trials count against it. | below `--min`, or > 20% missing |
| `ZANII_EVOLVE_OFFLINE=1` + `airgap-check --sign` | Only local providers (Ollama, or `ZANII_EVOLVE_OFFLINE_ALLOW`) on loopback or private hosts run; every role model and URL is listed and signed. | anything would leave the machine |
| `store search` / `store install D/C --to DIR --owner DID` / `store tag` | Published harnesses by industry, tag or text; install only when the archive's git tree equals the signed certificate's harness tree, the certificate verifies and its owner is pinned. Tags are search metadata; the certificate is what you trust. | any check fails |
| `judge-pack export --labels expert.jsonl --raters raters.json` / `verify` | The judge's agreement with named expert graders: kappa with its 95% range, per criterion, the labels' sha256; credentials are self-declared. | verify fails |
| `confirm-live --cert C [--blackbox-url U --label L]` | After a promotion: the rollout's canary vs stable outcomes and Blackbox's growth for the version that named the certificate, signed `confirmed`, `too_early` or `regressed`. Confirmed needs positive evidence on `--min-runs` runs. | regressed, or no live evidence |

<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/migration.svg" width="100%" alt="B minus A per task with a 95% range: safe only when the low end stays above minus the margin; worse is blocked, unclear is not certified">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/bakeoff.svg" width="100%" alt="A bake-off scorecard: vendors on the same hidden tasks, ranked, with vendors inside the noise sharing a rank">
</p>

### The self-improving agent, with an auditor

Blackbox and Evolve together: live failures become tasks, a certified gain passes the
regression gate, a person approves it, a staged rollout ships it and `confirm-live`
signs whether it held up in production.

<p align="center">
  <img src="https://raw.githubusercontent.com/vigilancetrent/zanii-evolve/main/assets/loop.svg" width="100%" alt="Seven signed steps: record, learn the misses, evolve, gate, a person approves, roll out, confirm live; then the loop starts again">
</p>

Every network call that carries a token (Blackbox, vendors) goes through
`zanii_evolve/net.py`: https only (http only to loopback), no redirects, bounded
reads, and the offline guard.

## Command reference

```
smoke | baseline | calibrate | round | run | status | readjudicate | reevaluate | heldout
pareto | report | verify-report | verify-receipts | publish | registry | identity | rotate-identity
init-domain | import-tasks | ingest-traces | ingest-blackbox | redteam | judge-agreement | lessons | watch | serve
constitution | audit-rollout | verify-memory | data | ledger-check | anchor-identity | cv | risk-profile | sla | escrow | audit
approvals | approve
```

`python zanii_evolve.py --help` lists every option.

## Project layout

```
zanii_evolve/          search core: loop, proposer, critic, selection, providers,
                       brief engine, certificates, registry, API server, dashboard
zanii_evolve.py        command line
domains/<name>/        one folder per domain: adapter or tasks, prompts, hyperparameters
third_party/           starting harnesses (Terminus-2, archipelago), under their own licenses
skills/                agent skills for the ZaniiDB and Zanii SDKs
tests/                 offline test suite
```

## Acknowledgements

Zanii Evolve builds on the RRSI reference implementation (Apache 2.0); see
[NOTICE](https://github.com/vigilancetrent/zanii-evolve/blob/main/NOTICE). The
starting harnesses are the Terminus-2 agent from
[harbor](https://github.com/laude-institute/harbor) and the react_toolbelt agent
and runner from [archipelago](https://github.com/Mercor-Intelligence/archipelago).
Evaluation uses [Terminal-Bench](https://github.com/harbor-framework/terminal-bench),
[SWE-bench Verified](https://github.com/SWE-bench/SWE-bench),
[Harvey LAB](https://github.com/harveyai/harvey-labs),
[JobBench](https://github.com/Job-Bench/job-bench-eval),
[GDPval](https://openai.com/index/gdpval/),
[APEX-Agents](https://www.mercor.com/apex/apex-agents-leaderboard/),
[EngDesign](https://github.com/AGI4Engineering/EngDesign) and
[Frontier-Eng](https://github.com/Einsia/Frontier-Engineering).

## Contributing

See [CONTRIBUTING.md](https://github.com/vigilancetrent/zanii-evolve/blob/main/CONTRIBUTING.md).
Run `python -m pytest tests` before opening a pull request.

## License

Apache 2.0. See [LICENSE](https://github.com/vigilancetrent/zanii-evolve/blob/main/LICENSE)
and [NOTICE](https://github.com/vigilancetrent/zanii-evolve/blob/main/NOTICE). Code
under `third_party/` keeps its own license.
