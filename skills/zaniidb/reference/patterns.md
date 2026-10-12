# ZaniiDB — patterns & gotchas (what models get wrong)

## Common mistakes to avoid
- **Calling `capture()` per message.** One call per completed *turn* (user +
  assistant together). The scheduler counts capture calls, not messages —
  per-message calls trigger premature extraction batches.
- **Expecting system/tool messages to be remembered.** Only `user`/`assistant`
  roles are captured, by design. Route durable rules through `seed()` instead.
- **Hard-requiring embeddings or the LLM.** Degradation is a feature: keyword
  search works with zero keys. Integration code must not crash when
  `embedder.enabled`/`llm.enabled` is False.
- **Querying superseded memories back to life.** `get_all_l1()` includes
  superseded history (for export/audit) — every *search/recall* path excludes
  it. If you see contradictory facts served, someone bypassed the store API.
- **Treating recall as an exam.** Chat defaults inject 5 memories / 4k chars.
  For "answer a hard question from memory" tasks, widen per-instance:
  `Settings(recall_max_results=20, recall_max_total_chars=8000)` — and raise
  `recall_max_chars_per_memory` (default 600) too, or long memories arrive
  truncated. Over-budget entries are skipped individually, never terminating the
  list, so a long memory can't discard the shorter ones ranked behind it.
- **Assuming a big local model fixes "extracted 0 memories".** That symptom is
  usually context overflow: an oversized prompt returns unusable text. The batch
  is char-budgeted (`pipeline_max_prompt_chars`, single messages capped at 8k)
  and unparseable output now keeps the rows and retries smaller — but if
  `log.error` says it skipped a batch after 3 tries, raise the budget or the
  backend's context (e.g. `OLLAMA_CONTEXT_LENGTH`), don't just retry.
- **Two processes, one SQLite file.** WAL allows it but the intended pattern is
  one gateway/MCP process per data dir; use the HTTP gateway for sharing.
- **Inventing multi-tenancy inside one store.** One database/data-dir per
  tenant. `scope:"team"` is shared knowledge *within* one tenant, not a tenant.
- **Copying benchmark numbers without the model.** Results are model-dependent;
  the harness prints `requested` vs `served` model (provenance) — quote both.
- **Editing extraction/persona prompts casually.** Every prompt change misses
  the LLM cache (re-pays ingestion) and shifts benchmark comparability. A/B on
  a slice first; the run-to-run noise on 25 questions is ±19pp — gate on the
  full set.

## Choosing the surface
- Own Python agent loop → SDK (`ZaniiMemory` or `AgentMemoryHooks`).
- Polyglot / multiple services → HTTP gateway.
- Claude Code / MCP-capable agent, zero code → MCP server.
- Verbose tool logs blowing the context → `AutoOffloader` / `POST /offload`.
- Org-wide SOPs every session should know → `seed(scope="team")`.
- Tamper-evident "what did it remember, when" → `[provable]` extra + ledger.

## Memory hygiene knobs (defaults are sane)
- Near-dup consolidation: `ZANII_DEDUP_MAX_DISTANCE` (0.08, stored-vector merge).
- Contradiction window: `ZANII_SUPERSEDE_MAX_DISTANCE` (0.45, same-type only).
- Episodic decay: `ZANII_RETENTION_EPISODIC_DAYS` (0 = forever;
  priority ≥ `ZANII_RETENTION_KEEP_PRIORITY` never decays; persona/instruction
  never decay).
- Scene synthesis: `ZANII_SCENE_CONDENSE_CHARS` (3000; 0 disables).
- CJK search: `ZANII_FTS_TOKENIZER=trigram` (SQLite; short queries fall back to
  substring scan) / `ZANII_PG_TEXT_SEARCH_CONFIG` (Postgres).

## Talking points (accurate — don't over-claim)
- PersonaMem (public, reproducible, stricter-than-official protocol): ~58%
  stable on gpt-5.6-luna, best run 61.3%, control ~42–47%, frontier
  full-context ~52% — quote as "+12–16 pts over no-memory, at/above
  full-context reading with ~100× less context". Never quote a single lucky
  run as the expected value.
- Provable memory proves WHAT was remembered and WHEN — never that a memory is
  *true*. Say "tamper-evident", not "verified facts".
