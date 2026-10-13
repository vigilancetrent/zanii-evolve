# Changelog

## 0.3.1 (2026-10-13)

- **Custody of client records** (`zanii.data_custody`, `zanii.subprocessor`). Every
  touch of a record minted from traces or Blackbox is a signed, hash-chained receipt:
  `store` on ingest, `share` to each hosted model that evaluates, judges or analyses
  it (with a subprocessor transfer under the agreement the domain declares), `read`
  for a local model, `delete` on `forget`. Records appear only as HMAC refs. New
  identities are delegated exactly these operations, so an export cannot produce a
  valid receipt. `data custody` shows a client their record's history and flags any
  recipient the domain did not declare.
- **Approvals** (the `zanii.runtime` human gate, persisted). `watch
  --require-approval publish,promote` parks those steps until the owner signs a
  confirmation bound to that exact action (`approve <id>`); unanswered requests
  expire and are asked again; rollback never waits. `approvals` lists them.
- Fixes: `rollout.save` creates the run directory; the trace ingest helpers take the
  runs directory explicitly.

## 0.3.0 (2026-10-13)

Trust and evidence on the Zanii packages; everything is opt-in and verifies offline.

- **Keys.** `identity` seals both private keys at rest (`zanii.kms`, scrypt +
  AES-256-GCM, `ZANII_EVOLVE_IDENTITY_PASSPHRASE`) and refuses to write them
  unencrypted without `--plaintext`. `--pq` adds an ML-DSA-65 key; certificates
  are then dual-signed Ed25519 + post-quantum (`zanii.pq`). `rotate-identity`
  continues the receipt chain on a new key through an owner-signed succession
  (`zanii.succession`); after a compromise the old delegation is revoked and
  `verify-receipts --revocations` rejects what it signed.
- **Certificate schema 3.** Each field is committed with its own salt
  (`zanii.redact`) and the receipt signs digest and commitment together:
  `report --public` writes a view that proves chosen fields and reveals nothing
  else. A certified gain is receipted on `evolve.report.gain`. The certificate
  names the domain's signed rulebook and commits to a Merkle manifest of the
  exact tasks its cycle trained on (`zanii.train`).
- **Ledger anchoring.** With `ZANII_LEDGER_URL` (and `ZANII_API_KEY` or
  `ZANII_API_KEY_FILE`) the certificate receipt is recorded on a Zanii ledger;
  `verify-report --ledger` proves inclusion. `ledger-check` audits the ledger
  once and `watch` proves on every tick that it only grew (`zanii.monitor`).
- **`anchor-identity`** records the identity's post-quantum key binding on the ledger
  (`zanii.pq` transition), built directly because zanii 0.25.1's `record()` encodes
  payloads differently from `pq.verify_transition`; `--check` re-verifies it offline.
- **Rulebook.** `constitution sign | check`: a domain's rules as a signed,
  content-addressed document (`zanii.constitution`).
- **Live evidence.** `zanii_evolve.live.SessionRecorder` signs a receipt for each
  action of a session, stamped with the harness that ran (`zanii.attest`) and the
  rulebook (`manifest_hash`), with one-line hooks for LangChain, the OpenAI Agents
  SDK, CrewAI and plain tool maps (`zanii.connectors`). The client uploads them
  with the outcome; the server verifies them first and, with an identity, signs a
  decision receipt per session (`zanii.decisions`). The rollout also rolls back a
  canary that breaks the rulebook or drifts from stable (`zanii.sentinel`), even
  when it reports success. `audit-rollout` re-checks all of it.
- **Provable memory.** `ZANII_EVOLVE_PROVABLE_MEMORY=1` hash-chains every stored
  lesson (`zanii.memory`); `verify-memory` shows any lesson edited, deleted or
  inserted outside the chain.
- **Client data.** `data prove | verify` proves a record in or out of a
  certificate's training set; `data forget` deletes it and signs a deletion
  attestation (`zanii.retention`).
- **Business.** `cv` (signed AgentCV of published certificates), `risk-profile`
  (`zanii.insurance` underwriting facts), `sla create | sign | assess`
  (`zanii.sla`, assessed from the receipt chain), `escrow create | sign | check |
  release` (`zanii.escrow`: pay per certified gain, released only against a
  ledger-proven `evolve.report.gain` receipt) and `audit` (one auditor's report,
  with `zanii.compliance` over a ledger bundle).
- Extras `pq`, `langchain`, `openai-agents`, `crewai`. The `skills/zanii` docs now
  link to the Zanii documentation instead of files that are not in this repo.
- Garbled em dashes in the brief harness template fixed.

## 0.2.2 (2026-10-13)

- **`zanii_evolve.client`**, a rollout client for any domain, standard library
  only. `session()` assigns on entry and reports on exit (an exception counts as a
  failure). Success can be a bool, a score with a threshold, or your own
  `success=` rule. If the server is down it falls back to the last stable commit
  (or `default=`) and never raises. `fetch()` downloads, sha256-verifies and
  safely unpacks a published harness.
- **`GET /api/registry/<d>/<commit>/manifest.json | harness.tar.gz`** serves one
  published harness, with every path part validated.

## 0.2.1 (2026-10-13)

- **`watch`: continuous evolution.** Ingests a trace file or a Blackbox gateway on
  a schedule. Once `--min-failures` new failures have built up, it runs a cycle
  (a fresh baseline on the current tasks, then `T` rounds) and archives the
  previous one under `runs/<domain>/cycles/`. It publishes only a gain above the
  noise band, after a held-out evaluation, and calls `--on-publish` with
  `ZANII_EVOLVE_MANIFEST` for your deploy or canary. Failed cycles resume on the
  next tick; every decision is logged to `watch.json`. `--once` is for cron.
- **Live rollout with automatic rollback** (`watch --rollout 0.05,0.25,0.5`). A
  published harness serves growing shares of live sessions next to the stable
  one. The routing is sticky (`GET /api/domains/<d>/rollout/assign?key=`), and
  outcomes are reported with `POST .../rollout/outcome` (token required). Each
  tick runs an anytime-valid harm test: a mixture likelihood ratio on which arm
  the failures land on, under randomized assignment, bounded by Ville's
  inequality. On harm it rolls back and resets `evolve/<domain>` to stable; a
  canary that passes every stage is promoted. Outcomes are keyed by rollout id,
  so a re-run of the same canary starts clean.
- **`watch` recovers on its own.** A failed publish is retried with backoff before
  any new cycle. A failing cycle backs off, and after `--max-cycle-failures` it
  is abandoned, archived and its uncertified rounds undone. `--on-event` receives
  every rollout, failure and publish event as JSON, for alerting.
- **Fix: rounds crashed on Windows.** Files were read and written with the platform
  codec (cp1252), so any harness or trace holding non-Latin-1 text broke the
  proposer. Every text read and write in the package is now UTF-8.

## 0.2.0 (2026-10-13)

- **Confirmation run (the winner's curse).** The round's winner is measured again
  on fresh trials (`confirm_k` trials per task, default `k`; `0` turns it off) and
  judged by the same floor, cost rule and guards on that measurement alone. Only a
  winner that holds up is accepted, and the incumbent's score is the confirmed one,
  so S* is never inflated by a lucky draw. A winner that falls back is recorded
  `UNCONFIRMED`. `readjudicate` applies a stored confirmation and does not accept a
  winner that was never confirmed.
- **Judge gate.** `judge-agreement --record` keeps the result; `min_judge_kappa`
  makes `baseline`, `round`, `run` and `heldout` refuse to measure until a recorded
  agreement for the same judge model reaches it.
- **Certificate schema 2.** Adds `confirmations`, `selection_shrinkage` (how much
  selected scores overstated confirmed ones) and `judge`.
- **`ingest-blackbox`.** Brief tasks from a Zanii Blackbox gateway's record of an
  agent's runs; what the agent did wrong in each run (by finding family) becomes a
  rubric criterion. Eight runs read at once, at most 200 events into each record,
  an unreadable run skipped and counted. Checked against a live gateway: 30 runs
  read in 12 s.
- Tests for the selection rules' invariants over thousands of random rounds.

## 0.1.0

- First release.
