# Changelog

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
