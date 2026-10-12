# Changelog

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
