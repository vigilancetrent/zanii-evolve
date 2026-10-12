# Zanii — optional ecosystem packages

Install only what a task needs. TS on npm; Python modules ship inside `zanii` (0.25.1).
All packages below are **published** — exact per-package versions live in `docs/PUBLISHING.md` (the registry). 60 optional npm packages: the five ecosystem
(`ai,webhooks,testing,monitor,react`), the ten standalone, four framework/privacy
(`langchain,openai-agents,retention,redact`), three standards-landing
(`a2a-directory,x402,erc8004`), four UAE-compliance
(`consent,admissibility,fta,walls`), `memory` (provable hash-chained agent memory), and
three trust-boundary (`kya,attest,swarm`), `subject` (per-subject auditability), `custody-verdicts` (the one verifier: five custody verdict rows), three
real-world provenance (`provenance,custody,decisions`), `sentinel` (runtime behavioral monitoring), two
institutional-trust (`credentials,gov`), `health` (medical audit trails), `cv` (AgentCV), three
agent-economy (`escrow` conditional payments, `insurance` machine-verifiable underwriting,
`federation` cross-ledger interop), four lifecycle/governance (`succession` signed key-rotation
continuity, `sla` receipt-computed service agreements, `constitution` rules as signed policy,
`pq` post-quantum hybrid signing), three custody/interop/legal (`broker` receipt-or-nothing
credential broker, `vc` W3C VC 2.0 bridge, `kyb` owner-to-legal-entity binding), and
`chain-bridge` (the Zanii L1 ↔ ledger identity bridge), and seven data-custody/sovereignty (`data-custody`, `residency`, `subprocessor`, `minimization`, `age-assurance`, `train`, `identity-bridge`).

## `@zanii/ai` — one-line framework tool instrumentation (npm)
Wrap a Vercel-AI-SDK-style tools map (or any tool with `execute`) so every call is receipted.
```ts
import { withZanii } from '@zanii/ai';        // peerDep @zanii/sdk
const tools = withZanii(myTools, { agent });  // every tool.execute → a receipt
```
Python: no separate package — use the SDK's `zanii.wrap_tool('email.send', fn)` on the callable.

## `@zanii/webhooks` / `zanii.webhooks` — receive + verify webhooks
```ts
import { createWebhookReceiver } from '@zanii/webhooks';   // zero-dep, Node 18+
const receive = createWebhookReceiver({ secret, on: { 'receipt.recorded': fn, 'receipt.rejected': fn } });
const { status } = await receive(rawBody, req.header('x-zanii-signature')); // 401 on bad sig, never dispatches
```
```python
from zanii.webhooks import create_webhook_receiver
receive = create_webhook_receiver(secret, on={"receipt.recorded": save})
res = receive(raw_body, request.headers.get("X-Zanii-Signature"))   # {"ok","status","event"}
```
Always pass the **raw** body (the exact bytes that were signed). Get `secret` from `POST /v1/account/webhooks`.

## `@zanii/testing` / `zanii.testing` — test agents offline
```ts
import { createTestLedger, makeIdentity, makeCert } from '@zanii/testing';
const ledger = createTestLedger();
const zanii = new ZaniiAgent({ /* … */, fetchImpl: ledger.fetch });
await zanii.record({ target: 'crm.lookup', payload: {} }); await zanii.flush();
expect(ledger.hasRecorded('crm.lookup')).toBe(true);
```
```python
from zanii.testing import fake_ledger, make_identity, make_cert
with fake_ledger() as ledger:              # monkeypatches the SDK's HTTP to an in-memory ledger
    z = ZaniiAgent(server_url="http://test", agent_did=agent.did, agent_private_key=agent.private_key, delegation=[cert])
    z.record(target="crm.lookup", payload={}); z.flush()
    assert ledger.has_recorded("crm.lookup") and ledger.verify_all()
```
Real Merkle proofs — `fetchAndVerifyProof` / `verify_audit_bundle` verify against it.

## `@zanii/monitor` / `zanii.monitor` — independent append-only + anchor watchdog
```sh
zanii-monitor --server https://ledger.zanii.agency --once            # exit 1 on violation
python -m zanii.monitor --server https://ledger.zanii.agency --once
```
```ts
import { checkOnce } from '@zanii/monitor';
const r = await checkOnce(server, prevState);   // persist r.state; r.violations lists any rewrite/anchor failure
```
Proves the log never rewrote history (consistency proof from the last size) — the "trust no one" guarantee, enforced.
Since v0.2.0 proofs are **pinned to the monitor's signed snapshot** (`?first=m&second=n`, per SPEC), so a log receiving
writes mid-check can no longer produce false "rewritten" alarms (TOCTOU); same-size/different-root anchors = equivocation.

## `@zanii/react` — client-side trust UI (npm, JS only)
```tsx
import { VerifiedBadge, ProofViewer, AgentProfile, LedgerTicker } from '@zanii/react';
<VerifiedBadge did={did} /> <ProofViewer hash={hash} />   // verify in the browser, zero trust
```

---

## The ten standalone packages (published — npm · Python parity)

TS source in `packages/<name>`; Python parity ships inside `zanii` (9 of 10 — `gateway` is
TS-only). Reach for one only when the task fits.

- **`@zanii/cli`** / **`zanii.cli`** (the `zanii` console script) — `keygen`, `did`, `delegate`,
  `revoke`, `verify`, `bundle`, `export`, `stats`, `agent`. `--json` on any command; `verify`/`bundle`
  exit non-zero on failure (drops into CI). TS also `import { run } from '@zanii/cli'`. Since v0.2.0:
  **`zanii audit <tag>`** — a data subject's slice through `@zanii/custody-verdicts`, writing a
  self-contained evidence bundle; `--bundle <file>` re-runs the identical verdicts **offline**
  (zero network, enforced) so an auditor reaches the same five rows as the subject page.
- **`@zanii/gateway`** (TS-only) — transparent HTTP proxy; receipts every forwarded call as `http.<method>.<path>`.
  `createZaniiHandler({ upstream, agent })` (fetch/edge), `createZaniiGateway` (Node), `zanii-gateway` CLI.
  *Python:* wrap your HTTP client function with the SDK's `wrap_tool`.
- **`@zanii/compliance`** / **`zanii.compliance`** — audit bundle → auditor report.
  `buildComplianceReport(bundle, { controls })` / `build_compliance_report(bundle, controls=…)`
  (offline verify + action breakdown + anchoring coverage + flags), `renderComplianceMarkdown` / `render_compliance_markdown`.
- **`@zanii/kms`** / **`zanii.kms`** — seal keys at rest (scrypt + AES-256-GCM). `sealIdentity`/`seal_identity`,
  `openIdentity`/`open_identity` → `{ did, privateKey }` for a new agent. Sealed-key format is cross-language compatible.
- **`@zanii/witness`** / **`zanii.witness`** — independent co-signer. `createWitness(keypair).cosign(sth, { consistencyProof })`
  verifies append-only then counter-signs; `verifyCosignature`/`verify_cosignature`. (Contrast `@zanii/monitor`, which only watches.)
- **`@zanii/policy`** / **`zanii.policy`** — pre-action `allow`/`deny`/`require_approval` on payload conditions.
  `createPolicyEngine({ rules, default }).evaluate(...)` / `create_policy_engine(...)`. Ops: `eq ne lt lte gt gte in nin`; fixed-window `rateLimit`.
- **`@zanii/otel`** / **`zanii.otel`** (`zanii[otel]`) — `withTracing(agent)` / `with_tracing(agent)` makes every
  `record` an OpenTelemetry span (`zanii.target`/`action`/`hash`). Peers: `@opentelemetry/api`, `opentelemetry-api`.
- **`@zanii/payments`** / **`zanii.payments`** — correct money: `parseMoney('49.99','USD')` / `parse_money(...)` → integer
  minor units, never a float. `buildPayment` / `build_payment` → validated payload for `recordPayment`/`record_payment`. `registerCurrency` for new codes.
- **`@zanii/embed`** / **`zanii.embed`** — framework-free "Verified by Zanii" badges (escaped SVG + snippet).
  `agentBadge`/`agent_badge`, `badgeSVG`/`badge_svg`, `proofBadgeSVG`/`proof_badge_svg`, `embedSnippet`/`embed_snippet`.
- **`@zanii/connectors`** / **`zanii.connectors`** — one-call proof recording for LLM tool calls.
  `wrapToolbox`/`wrap_toolbox`, `runToolCalls`/`run_tool_calls` (OpenAI + Anthropic shapes), `normalizeToolCall`/`normalize_tool_call`.

---

## Framework adapters + privacy (published — npm · Python parity)

Integrate via each framework's **own hook** (not per-tool wrapping) — attach once, the whole run is receipted.

- **`@zanii/langchain`** / **`zanii.langchain`** (`zanii[langchain]`) — receipt every tool call in a
  LangChain **or LangGraph** run via one callback handler. `new ZaniiCallbackHandler({ agent })` /
  `zanii_callbacks(agent)` → pass in `config.callbacks`. TS also `withZaniiConfig(config, agent)`.
- **`@zanii/openai-agents`** / **`zanii.openai_agents`** (`zanii[openai-agents]`) — OpenAI Agents SDK
  lifecycle hooks. `new ZaniiRunHooks(agent)` → `Runner.run(agent, input, { hooks })` / `hooks=`. Records tool + output.
- **`zanii.crewai`** (`zanii[crewai]`, Python-only) — `instrument_crew(crew, agent)` attaches a receipting
  `step_callback` (chains any existing). CrewAI has no JS SDK.
- **`@zanii/retention`** / **`zanii.retention`** (v0.4.0) — GDPR Art. 17 deletion **attestations** (you can't prove
  absence; a signed timestamped receipt is the evidence). `buildRetentionAttestation({ subject, policy, executedAt })`
  → `data.retention.<action>` receipt with a salted subject commitment (no raw PII). `verifyRetention`, `commitSubject`, `verifySubject`.
  Also the **inverse** — `buildRetentionHold({ subject, category, legalBasis, retainUntil })` / `build_retention_hold`
  → a `data.retention.hold` receipt proving records were *kept* (UAE 5-yr rule requires proving this). `verifyRetentionHold`.
- **`@zanii/redact`** / **`zanii.redact`** — **selective disclosure** (SPEC §11). `commit(fields)` → an envelope
  (Merkle root + field names) you record as the payload; `disclose(result, key)` → a proof; `verifyDisclosure(d, envelope)`.
  Prove an agent acted on specific data without revealing the rest. No server change (root is opaque to the log).

---

## Standards-landing packages (published — npm · zanii 0.25.1 Python)

For the A2A / agentic-commerce era. TS + Python parity.

- **`@zanii/a2a-directory`** / **`zanii.a2a_directory`** — resolve an agent DID (e.g. from an A2A
  agent card) to its **verified** history. `resolveAgent(did)` (trust summary via `/v1/reputation`),
  `resolveAndVerify(did)` (pulls the audit bundle + `verifyAuditBundle` offline — trust the maths,
  not the counts), `summarize`, `agentDidFromCard`. Python: `resolve_agent`, `resolve_and_verify`.
- **`@zanii/x402`** / **`zanii.x402`** — bind a payment receipt to its on-chain settlement.
  `verifySettlement({ txHash, to, minValue }, txFetcher)` (tx exists/succeeded/right recipient/value),
  `buildX402Payment(...)` (reuses `@zanii/payments`), zero-dep `jsonRpcTxFetcher(rpcUrl)` for any EVM
  chain. Chain reads injected → testable offline. Python: `verify_settlement`, `json_rpc_tx_fetcher`.
- **`@zanii/erc8004`** / **`zanii.erc8004`** — register/resolve agents as ERC-8004 identities wired to
  Zanii proofs. `buildRegistrationFile(...)`, `toDataUri`, `resolveRegistration(agentUri)`,
  `verifyResolvedAgent(agentUri)` (verifies the resolved agent's history offline). Mint tx is your
  wallet's job. Python: `build_registration_file`, `resolve_registration`, `verify_resolved_agent`.

---

## UAE-compliance packages (published — npm · zanii 0.25.1 Python)

Regional law as product. TS + Python parity.

- **`@zanii/consent`** / **`zanii.consent`** — **PDPL consent receipts**. `buildConsentReceipt({ subject, purpose, scope, action, basis })`
  (`action` = `'granted'` | `'withdrawn'`) → a `consent.<action>` receipt with a salted subject commitment; `verifyConsent`. Zero-dep.
- **`@zanii/admissibility`** / **`zanii.admissibility`** — court-ready **bilingual (Arabic/English)** evidence pack for UAE
  Electronic Transactions Law 46/2021. `buildEvidencePack(bundle)` (verifies via `verifyAuditBundle`, emits the 6
  verification steps written for a judge/expert + the legal basis + offline reproduce commands);
  `renderEvidencePackMarkdown(pack, 'en'|'ar'|'both')`. Dep `@zanii/core`.
- **`@zanii/fta`** / **`zanii.fta`** — Federal Tax Authority filing evidence (Meezan/Books). `buildFilingPrepReceipt(...)`
  → `fta.filing.prepared` inside the `FTA_WALL` ("prepares, never files/advises"); `buildTaxAgentHandoff(...)`
  → `fta.filing.handoff` (the licensed Tax-Agent handoff, as its own receipt); `verifyFilingEvidence`. Zero-dep.
- **`@zanii/walls`** / **`zanii.walls`** — UAE vertical **wall presets** as enforceable, auditable artifacts. `WALLS`
  (`sca-trading` "education only, never buy this", `rera-realty`, `tdra-messaging`, `legal-drafting`, `consumer-due`,
  and the `difc-dp`/`adgm-dp` free-zone variants — which differ from federal PDPL); `wallPolicy(wall)` → a `@zanii/policy`
  deny config; `wallManifestHash(wall)` → receipt the enforced rulebook per deploy; `checkOutput(wall, text)` → heuristic
  crossing detector; `buildEvalReceipt(wall, { passed, failed })`. Dep `@zanii/core`.

---

## Provable agent memory (published — npm · zanii 0.25.1 Python)

- **`@zanii/memory`** / **`zanii.memory`** — hash-chained agent memory: a `memory.write` receipt records what an
  agent remembered and when, with a **salted content commitment** (raw memory never leaves your system) and a
  tamper-evident `prev`→`entry_hash` link. `appendMemory(prev, { content, kind, ts })` / `append_memory(prev, ...)`
  auto-links + increments `seq` (pass `null` for the first entry); `buildMemoryWrite(...)` for manual `prev`/`seq`;
  `verifyMemory(payload)` checks one entry's `entry_hash` is self-consistent (catches an edited entry);
  `verifyMemoryChain(entries)` also checks the links + `seq` (catches an inserted/deleted entry); `commitContent` /
  `verifyContent` prove what a specific entry remembered. `entry_hash` is RFC 8785 canonical (`@zanii/core` `jcsHash`),
  so a chain verifies **byte-identically** in TS and Python. Answers "why did it decide that?" for long-running agents.
  Dep `@zanii/core`.

---

## Trust-boundary packages (published — npm · zanii 0.25.1 Python)

Close the accountability gaps: who you transact with, which code ran, and N-party teams.

- **`@zanii/kya`** / **`zanii.kya`** — "Know Your Agent": screen a counterparty **before** transacting.
  `screenAgent(did, { denyList, provider })` / `screen_agent(...)` checks a caller deny-list + an
  **injected** sanctions/KYB `provider` (x402-style — the list is yours, the rails are ours);
  `buildScreeningReceipt` → `kya.screening`; `verifyScreening`. `resolveAndScreen` pulls history via
  `@zanii/a2a-directory` first. Honest limit: a `did:key` is only as screenable as its owner is disclosed.
  Dep `@zanii/core` + `@zanii/a2a-directory`.
- **`@zanii/attest`** / **`zanii.attest`** — bind *which code ran* to a receipt. `attestationField(a)` adds an
  `attestation_hash` provenance field (the TEE down payment on `runtime_hash`); `verifyAttestation(a, { verifyQuote })`
  checks structure and **delegates SGX/Nitro/TPM quote verification to an injected verifier** — no verifier ⇒
  `quoteVerified: null` (unknown), never a false "verified". Dep `@zanii/core`.
- **`@zanii/swarm`** / **`zanii.swarm`** — N-party (3+) co-signed receipts for agent teams: genuine **M-of-N
  Ed25519 threshold**. Offline: `buildSwarmBody`, `signSwarm`, `verifySwarm` (signatures only). **On-ledger
  (SPEC §14, live at `POST /v1/swarm`):** `swarmSigner`/`swarm_signer` + `buildSwarmReceipt` + the
  authority-complete `verifySwarmReceipt`/`verify_swarm_receipt` — per signer: signature + owner-rooted
  unrevoked/unexpired delegation + target-in-scope + prev == chain head; then threshold; then **distinct-owner
  segregation** (default on — one owner's agents can't fake an M-of-N). TS↔Python↔server verified byte-for-byte.
  Dep `@zanii/core` (+ `@noble/curves`, `@noble/hashes`). *Don't confuse `verifySwarm` (offline) with
  `verifySwarmReceipt` (authority).*

---

## Per-subject auditability (published — npm · zanii 0.25.1 Python)

- **`@zanii/subject`** / **`zanii.subject`** — the **end user's own slice** of the ledger: a platform's
  millions of end users each hold their own `did:key` and independently verify what agents did on *their*
  account, without seeing anyone else's. The platform stamps a pseudonymous, platform-scoped tag on each
  receipt: `subjectTag(subjectDid, platformId)` (RFC 8785 — byte-identical TS/Python), passed as
  `record({ subjectTag })` / `record(..., subject_tag=...)` — a **signature-covered** SPEC §3 field
  (core/sdk ≥0.4.0). The subject computes the same tag and pulls `GET /v1/subjects/{tag}`, then
  `fetchSubjectHistory` / `fetch_subject_history` **offline-verifies every receipt** (signature +
  delegation + tag match — foreign/invalid receipts are flagged, never silently shown).
  `subjectIdentity`/`subject_identity`, `signSubjectClaim`/`verifySubjectClaim` ("this is my slice").
  Same subject ⇒ **different tag per platform** (no cross-platform linkage); a tag is not reversible to an
  identity. Honest limit: the slice is only as complete as the platform's stamping. Dep `@zanii/core`.

- **`@zanii/custody-verdicts`** (TS, npm) — **one verifier, two surfaces.** `await computeVerdicts(history,
  evidence?, options?)` computes the five custody verdict rows — `purpose`, `self_sovereign`, `human_read`,
  `trained_on`, `copy_kept` — from an offline-verified `SubjectHistory`; the subject page and `zanii audit`
  call this same function (two implementations drift, and the drift always favours the vendor). Verdicts are
  **computed, never stored**, in four states — `proven` / `not covered` / `failed` / `cannot determine` —
  where a missing proof is never a clean result and a bad receipt is a red, not a grey. Rows 3–5 consume an
  optional evidence bag with real offline checks (`@zanii/train` absence proofs, `@zanii/retention` shred
  receipts, signed coverage attestations via `@zanii/attest`); never throws — malformed input yields honest
  `cannot determine` rows with reasons. Since v0.2.0 the coverage attestation must bind its **signing
  key** to the attested image (`attestation.nonce === signer`, so a published attestation cannot be
  lifted and re-signed), and `coverage` also accepts an ordered **chain** of windows for deployments
  that restart or redeploy — every window authenticates on its own and they must abut, because a gap
  in coverage is not coverage. Deps `@zanii/{data-custody,train,retention,attest}`.

---

## Real-world provenance (published — npm · zanii 0.25.1 Python)

The same receipt that makes an AI agent accountable makes a shipment, an artwork, and an
algorithm accountable. TS + Python parity.

- **`@zanii/provenance`** / **`zanii.provenance`** — content credentials (anti-deepfake): the artifact's hash
  signed by the creating agent's did:key. `buildContentCredential({ artifact | artifactHash, agentDid, createdAt, meta })`,
  `verifyContentCredential(cred, artifact?)` (offline), `credentialReceipt` → `content.created` (anti-backdating),
  `resolveCreator` → the creator's **verified** history via a2a-directory. Limits: who-*signed* not who-*authored*;
  absence proves nothing; hash breaks on re-encode (`meta.derivative_of` for derivatives); **C2PA not v1**.
- **`@zanii/custody`** / **`zanii.custody`** — supply-chain custody chains. `itemTag(itemId, namespace)`
  (pseudonymous — serials never on-ledger; the item's slice is `GET /v1/subjects/{tag}`); `buildHandoff`
  (a2a 2-party co-sign via `/v1/interactions`, tag inside the signed body, evidence always salt-committed);
  `buildCustodyEvent` (swarm N-party via `/v1/swarm`); `verifyCustodyChain(entries, { tag })` — **continuity**:
  receiver of *n* = giver of *n+1*, ts monotonic, tag-bound; `custodySummary`. Limit: **attestations, not atoms** —
  fraud becomes attributable, not impossible.
- **`@zanii/decisions`** / **`zanii.decisions`** — auditable algorithmic decisions (gig/creator fairness).
  `buildDecisionReceipt({ subjectDid, platform, kind, manifestHash, outcome, factors, ts })` — **`manifestHash`
  required** (no rulebook ⇒ rejected); factors salt-committed (dispute disclosure is provably what was committed);
  `verifyDecision`; `ruleConsistency(payloads)` → rulebook windows; **interleaved rulebooks = the red flag**
  (rules differed between people at the same time). Proves completeness + rule-consistency + committed factors —
  **never "the algorithm is fair"**. Deps `@zanii/core` + `@zanii/subject`.

---

## Runtime behavioral monitoring (published — npm · zanii 0.25.1 Python)

- **`@zanii/sentinel`** / **`zanii.sentinel`** — "antivirus for agents": the behavioral layer between
  `@zanii/monitor` (log integrity) and `@zanii/policy` (pre-action rules). `buildBaseline(agentId, { receipts, delegation })`
  → declared scopes (from the delegation chain) + learned habits (target prefixes, rate, intent ratio, active
  hours; **float-free integer milli-units** so `baselineHash` is byte-identical TS/Python).
  `scan(receipts, baseline, opts)` → findings from six detectors — `novel-target` (high when outside declared
  scopes), `scope-edge` (fed by `receipt.rejected` webhooks), `rate-spike`, `intent-gap`, `sequence` (default:
  the exfil shape read→archive→external-send), `off-hours` — plus `wall-crossing` in **operator mode** (inject
  `payloadOf` + `checkContent`, e.g. `walls.checkOutput` over raw payloads pre-hash).
  `createWatcher({ server, baselines, cursor, onFinding })` polls `/v1/recent` with a **rolling window** (so a
  multi-step exfil arriving across polls still fires), de-duplicates findings, and takes a **resumable cursor**
  (persist it — otherwise a restart re-alerts). `escalationRule(f)` → a `require_approval` speed bump (returns
  **null** when the finding names no targets — it will not auto-gate the agent's whole surface).
  `buildAlertReceipt` → **`sentinel.alert`**, recorded by the sentinel's OWN did:key (watcher ≠ watched): detection
  is tamper-evident, and silence is checkable. Limits: detection ≠ prevention; false positives are structural;
  in-scope compromise is the hard case. For production prefer webhooks over polling (`/v1/recent` is a global
  feed capped at 100). Dep `@zanii/core`.

---

## Institutional trust (published — npm · zanii 0.25.1 Python)

- **`@zanii/credentials`** / **`zanii.credentials`** — verifiable institutional credentials (diplomas,
  licences, certifications), offline-verifiable in ms with no call to the registrar. **The package is
  not the credential — it is the root of trust.** `verifyCredential` runs **four** checks and the
  fourth is the one that matters: signature → not expired → not revoked (signed list with an
  **explicit freshness window**) → **the issuer DID resolves to a named institution**, bound to a
  domain it controls (`resolveIssuer` → `https://<domain>/.well-known/zanii-issuer.json`; the doc
  must be served from the domain it claims — no cross-domain vouching).
  `buildCredential`, `buildRevocationList`, `buildPresentation`/`verifyPresentation` — a credential is
  **not a bearer token**: a thief with the file but not the key fails, and presentations don't replay.
  **Nothing is silently assumed** — no resolver ⇒ "institutional identity NOT verified"; no list ⇒
  "revocation NOT checked"; `ok` is never true on an unchecked assumption. *Zanii verifies domain
  control; it is **never** the accreditor.* Dep `@zanii/core`.
- **`@zanii/gov`** / **`zanii.gov`** — public-sector algorithmic accountability (benefits, visas, fines,
  licences). **No new crypto** — a preset over `decisions` + `subject` + the bilingual `admissibility`
  discipline. **`buildGovDecision` THROWS on a missing `manifestHash`** — a state decision with no
  governing rulebook cannot be constructed. **`appealPack(tag)`** is the hero: pull the citizen's slice,
  verify every decision offline, test rule-consistency, and `renderAppealPackMarkdown` a bilingual pack
  for an administrative judge — *the state proves what its algorithm did to you, and you hold the proof*.
  Flags **interleaved rulebooks** (different rules decided people at the same time) and **unruled
  decisions**. The pack states, in both languages, that it proves the **process, never the justice**.
  Deps `@zanii/core` + `@zanii/decisions` + `@zanii/subject`.

---

## Medical audit trails (published — npm · zanii 0.25.1 Python)

- **`@zanii/health`** / **`zanii.health`** — medical audit trails **without leaking the pattern**. For
  medicine the **metadata IS the sensitive data** (access frequency to an oncology or psychiatric
  record is itself PHI), so this package deliberately breaks Zanii's usual stable per-subject tag.
  - **Per-episode unlinkable tags** — `episodeTag(seed, n)`. An insider sees N unrelated tags, not
    "this person was here 14 times". *An episode is verifiable; a life is not linkable except by the
    patient.* Cross-language identical.
  - **🔴 THE RULE: the patient supplies the TAG, never the SEED.** A provider holding the seed could
    enumerate `n` and reconstruct the entire cross-institution history — privacy gain zero, and
    *illusory*. A **red test in both languages** guards this.
  - **The four-party question:** `buildAccessReceipt` (who — `purpose` **mandatory**),
    `buildRecommendation` (which model + **`manifestHash` REQUIRED** — no protocol, no receipt),
    `buildClinicianConfirmation` (**which named human** signed — the model did not decide, a person did).
  - **`protocolTrail` is deliberately NOT `ruleConsistency`.** "Same rulebook for everyone" is a
    fairness property in `@zanii/gov` and a **clinical error** in medicine — individualised care is
    correct, protocols legitimately coexist. **No uniformity verdict exists to fail.** Deviation is
    recorded, never a defect; a **missing** protocol is the defect.
  - **Break-glass: loud, not impossible.** Mandatory receipt + required reason + the clinician's
    **personal signature** (the deterrent). `pendingBreakGlass()` makes un-reviewed events countable and
    visible to the patient. **The metric, not the mandate** — we cannot enforce an institution's review
    policy, but we make ignoring it undeniable. *A hole we deliberately keep open: a closed one kills patients.*
  - `fetchEpisodes(seed)` — the multi-tag fetch `@zanii/subject` lacks; **only the seed-holder can link
    the chain.** Deps `@zanii/core` + `@zanii/subject`. **Zero server changes.**

---

## AgentCV (published — npm · zanii 0.25.1 Python)

- **`@zanii/cv`** / **`zanii.cv`** — AgentCV: a signed, portable CV over data the ledger **already holds**
  (a *view*, not a new datastore) — `@zanii/kya`'s "read an agent's verified history" made portable.
  The design's load-bearing idea: a CV has **two kinds of content, NOT equally trustworthy**, and it says
  so. `summary` is the ledger's **own aggregate** (from `/v1/reputation` — receipts, first/last seen,
  counterparties, revoked, anchored) — **the agent cannot inflate it**. `work`/`authority`/`benchmarks`/
  `skills` are **curated**: each entry points at a verifiable receipt/cert hash (nothing fabricable), but
  the agent chooses **which** to include. **A CV proves its entries are real; it does NOT prove the history
  is complete** (the omission asterisk). `verifyCV` runs **offline** (structure + the subject's signature —
  altered-after-signing or signed-by-another-key fails) and surfaces curation **warnings** a reader should
  see: a `work` list longer than `summary.receipts`, a `skill` with no backing authority scope, a `revoked`
  subject. `buildCV`, `signCV`, `generateCV` (pull the ledger aggregate), `cvHash`, `summarize`.
  Cross-language: a Python-signed CV verifies in TS. Deps `@zanii/core` + `@zanii/a2a-directory`.

## Agent economy (published — npm · zanii 0.25.1 Python)

- **`@zanii/escrow`** / **`zanii.escrow`** — conditional payments as **verifiable agreements, never held
  funds**. Three artifacts: co-signed **terms** (`buildEscrowBody` — payer/payee/`Money` in integer minor
  units/receipt criteria/optional M-of-N `arbiters`; self-arbitration banned), a deterministic **release
  verdict** (`checkRelease` — receipt valid + matches EVERY criterion + **Merkle inclusion proof required**:
  "a receipt without evidence is a claim, not a trigger"), and signed **settlement instructions**
  authorized by whoever gives something up (`buildRelease` → payer or arbiter threshold; `buildRefund` →
  payee or threshold; `verifyInstruction` ignores signatures from DIDs with no role). Settlement then moves
  on x402 rails — tie the tx back with `@zanii/x402` `verifySettlement`. Limit: Zanii cannot force a
  transfer — a payer refusing a valid release is **provably in breach, attributable not impossible**.
  Deps `@zanii/core` + `@zanii/payments`.
- **`@zanii/insurance`** / **`zanii.insurance`** — machine-verifiable underwriting: the "insurable agent"
  unlock. `riskProfile`/`buildRiskProfile` derive underwriting **FACTS** from the same un-inflatable
  `/v1/reputation` aggregate AgentCV uses + delegated authority — **deliberately never a score** (a Zanii
  score gets gamed and creates liability; insurers price). Deterministic flags: `REVOKED`,
  `NOT_FULLY_ANCHORED`, `THIN_HISTORY` (<50 receipts), `STALE` (>30d), `WILDCARD_AUTHORITY`. A **policy**
  insurer+holder co-sign (`buildPolicyBody` — premium/limit same-currency minor units, covered scopes,
  period, optional `risk_profile_hash` pinning the underwriting basis). A **claim** cites incidents by
  receipt hash; `assessClaim` is deterministic — terms verdict (`ok`) plus `proven`/`unproven` lists from
  supplied receipt+inclusion evidence (**unproven is a fact for the adjuster, not a failure**). Limit:
  **ledger silence is unknown, not good** — thin history is flagged, not rewarded.
  Deps `@zanii/core` + `@zanii/a2a-directory` + `@zanii/payments`.
- **`@zanii/federation`** / **`zanii.federation`** — cross-ledger interop: what makes Zanii a *protocol*.
  Kills the split-view attack client-side: (1) `createWitnessPolicy` — the log signs its own bar ("my
  checkpoints count only when ≥M of these named witnesses co-sign"; a log cannot witness itself);
  `verifyFederatedSth` enforces it (M **distinct** recognised witnesses, strangers ignored). (2)
  `gossipOnce(logId, sources)` — fetch the checkpoint from several vantage points and compare.
  (3) **The equivocation proof** (`buildEquivocationProof`/`verifyEquivocationProof`): two validly signed
  STHs, same log key, same size, **different roots** — self-contained, offline-checkable, no innocent
  explanation; canonical ordering so both assembly orders hash identically. Different sizes are NOT
  equivocation (honest growth — use `@zanii/monitor` for append-only). Limit: proves consistency between
  views, not completeness — split views are caught when views are compared; what dies is *quiet*
  equivocation. Deps `@zanii/core` + `@zanii/witness`.

## Lifecycle, agreements, governance, post-quantum (published — npm · zanii 0.25.1 Python)

- **`@zanii/succession`** / **`zanii.succession`** — key rotation with **signed continuity**: the owner
  (the root authority whose delegation empowered the old key) attests "B succeeds A", so
  reputation/CV history survives a rotation. The **owner's signature is required** (without it,
  succession is just a claim); the predecessor's countersignature is optional (`cosigned` — strictly
  stronger for planned rotations, deliberately NOT required after a compromise). The load-bearing rule:
  `reason:'compromise'` **REQUIRES `compromised_at`** — it brackets the trusted window, and
  `combinedSummary` marks post-compromise receipts as suspect instead of silently inheriting them.
  `verifyLineage` (one owner per lineage — a sale is not a succession; refuses cycles/disconnects),
  `combinedSummary` (conservative: sums labeled an upper bound, counterparties not summed). Limit:
  continuity of **authority**, not identity of code (`attest` is which-code-ran).
- **`@zanii/sla`** / **`zanii.sla`** — machine-verifiable service agreements: co-signed terms (scope,
  window, min/max actions, `max_gap_ms` uptime proxy, optional `Money` price) with compliance
  **computed from receipts** (`assessCompliance` — pure; breaches cite receipts; `asOf` for fair
  mid-window checks). The honest-metrics property: the per-agent **hash chain proves completeness** —
  the slice must be consecutive (`prev`-linked), so hiding the bad hour is an `INCOMPLETE_RECORD`
  breach and trimming the ends shows as fewer actions + bigger edge gaps (gaps measured to window
  boundaries). Breach kinds: `INVALID_RECEIPT`, `INCOMPLETE_RECORD`, `TOO_FEW_ACTIONS`,
  `TOO_MANY_ACTIONS`, `MAX_GAP_EXCEEDED`. Pairs with `escrow` (breach = refund evidence). Limit:
  receipts prove what was *recorded*; a gap breach proves ledger silence, which the parties' agreement
  defines as breach.
- **`@zanii/constitution`** / **`zanii.constitution`** — the walls/fta pattern generalized: **the
  agent's rules as a signed, content-addressed document**, enforced by reuse of the existing
  `manifest_hash` receipt field (zero server changes). Rules: `forbid`/`require_approval`/`allow`
  over scope patterns, FIRST MATCH WINS, configurable default (deny-by-default = strict form).
  `verifyGovernance`: a stamped receipt hitting `forbid` is a **self-incriminating violation**; a
  missing/foreign stamp is an **ungoverned** action (visible, not silently fine); refuses to audit
  against an unsigned rulebook. `governanceTrail` flags interleaved rulebooks (A,B,A — the `gov` red
  flag, generalized). Limit: the stamp proves *recorded as governed* — act-time enforcement is
  `runtime`/`policy`; approval *verification* stays with the runtime; credential custody unchanged.
- **`@zanii/pq`** / **`zanii.pq`** (`pip install "zanii[pq]"`) — post-quantum migration rails: hybrid
  **Ed25519 + ML-DSA-65 (FIPS 204)**. `bindPqKey` — a binding signed by BOTH keys over the same body
  (possession of both proven); `dualSign`/`verifyDual` — **both must verify**, a missing ML-DSA sig is
  a failure, never an Ed25519-only fallback; `transitionPayload`/`verifyTransition` — anchor the
  binding **unsalted** into the SHA-256 Merkle log now, so the anchored timestamp proves it predates
  any future Ed25519 break. Cross-language verified BOTH directions (final FIPS 204: noble ↔
  dilithium-py; locked by `tests/pq_vector.json`). Limit: pre-migration receipts are **not re-signed**
  — their post-quantum protection is the anchored timestamp, not the signature.

## Custody, interop, legal identity (published — npm · zanii 0.25.1 Python)

- **`@zanii/broker`** / **`zanii.broker`** — the credential broker that closes the ecosystem's own
  stated hole ("an agent still holding raw credentials can act off the rails"). One invariant: **the
  agent never holds the credential — the broker injects it ONLY into calls that produced a receipt
  first.** Fail-closed by construction (NOT configurable): allow-list before anything (exact https
  **origin** equality — no `api.openai.com.evil.com` masquerade; segment-bounded path prefixes —
  `/v1` allows `/v1/chat`, never `/v1abc`), **receipt BEFORE call** (the injected `record` must
  durably succeed or the upstream is never contacted; ledger down = no call), secret injected only
  after the payload hash (never in a preimage), responses scrubbed (`set-cookie`), optional
  `@zanii/policy` gate that refuses pre-receipt. `createBroker({credentials, record, gate})` →
  `execute(req)`. Limits: isolation requires a **separate process**, and the operator must delete
  raw keys from the agent's env — the package removes the *need*, not the operator's choice.
- **`@zanii/vc`** / **`zanii.vc`** — the W3C Verifiable Credentials 2.0 bridge, not cosmetic:
  Zanii's did:key + Ed25519 + JCS **is** the W3C `eddsa-jcs-2022` cryptosuite, so `toVC` (a
  `credentials` credential re-issued by the same issuer key) and `cvToVC` (a self-issued AgentCV)
  emit spec-conformant VCs with genuine `DataIntegrityProof`s any standards wallet verifies —
  and `verifyVC` verifies *anyone's* did:key eddsa-jcs-2022 VC. Guardrails: the Zanii artifact is
  verified before bridging (forgeries refused — the bridge cannot launder), validity window checked
  only against an explicit `at`. Cross-language: **the entire TS-issued VC — proofValue included —
  reproduces byte-identically in Python** (Ed25519 determinism). Limits: bridges the **format, not
  the trust model** — revocation freshness + domain root-of-trust stay Zanii-side; W3C StatusList
  deliberately not implemented.
- **`@zanii/kyb`** / **`zanii.kyb`** — owner-to-legal-entity binding: who is legally on the hook?
  (`kya` screens agents; `credentials` binds institutions; this binds the **owner** did:key —
  what `escrow`/`insurance`/`sla` need to be commercially real.) Deliberately **two-way**: the
  company serves `/.well-known/zanii-owner.json` (owner DIDs + entity) AND the owner signs an
  attestation naming that exact domain/entity — one-sided claims refused, no cross-domain
  vouching, entity names match exactly (`jurisdiction`/`registration` must agree where both
  sides state them; silence ≠ mismatch). `buildOwnerAttestation`, `verifyOwnerBinding`,
  `fetchAndVerifyOwner`. Limit: **domain control ≠ corporate registration** — real KYB stays
  off-protocol; a false claim becomes signed, permanent, attributable — never certified.

## The Zanii L1 bridge (published — npm · zanii 0.25.1 Python)

- **`@zanii/chain-bridge`** / **`zanii.chain_bridge`** (`pip install "zanii[chain]"`) — **one keypair,
  two products**: the Zanii L1 (blockchain.zanii.agency) and the ledger both use Ed25519, so a chain
  wallet's pubkey IS a ledger `did:key` and the `zan1…` address derives from the same bytes
  (`bech32("zan", blake3(pubkey)[:20])` — verified against the live testnet registry). The chain's
  agent registry stores self-asserted `{"did": …}` metadata — a string anyone can paste;
  `verifyChainAgentBinding` closes it: the claimed DID's key must derive EXACTLY the registered
  wallet address (the reg tx was signed by that wallet ⇒ the registrant controls the DID's key —
  a **name-dropped identity fails**). `resolveChainAgent(nameOrAddress)` = find on chain (paginated)
  → verify binding → pull ledger reputation; **a failed binding never reaches the ledger**. Plus
  provisioning utilities: `chainAddressFromDid`, `chainAddressFromPublicKey`, `didFromChainPublicKey`.
  Limit: the binding proves **key identity only** — metadata stays self-asserted, a chain name is
  first-come not a trademark, full offline acceptance still follows `audit_bundle`.
- **`@zanii/x402` v0.2.0** — `zaniiChainTxFetcher(rpcUrl)` / `zanii_chain_tx_fetcher`: verify ZAN
  settlements through the same `verifySettlement` used for EVM (`GET /tx/{txid}`; visible =
  deterministic finality = success; 404 = not included → null; amounts in integer zi).
- **`@zanii/payments` v0.2.0** — **ZAN** (9 decimals) built in: `parseMoney('1.5','ZAN')` →
  `{minor:'1500000000'}` zi, flowing into escrow terms, SLA prices, and insurance limits.

## Data custody & training provenance (published — npm · zanii 0.25.1 Python)

- **`@zanii/train`** / **`zanii.train`** — training-data provenance + **subject-absence proofs**.
  The claim buyers want is negative ("my data was never used to train your model"); for a declared
  run this makes it a *yes*: commit the set to a **sorted-Merkle manifest**, then a non-membership
  proof reveals the one committed gap `(lo,hi)` bracketing the subject's `ref` + its inclusion
  proof — a covering gap that verifies means nothing sits between. Membership is the same on a
  member leaf. Uses core's own `inclusionProof`/`verifyInclusion`; **byte-identical roots
  TS↔Python**. `buildManifest`, `proveAbsence`/`verifyAbsence`, `proveMembership`/`verifyMembership`,
  `manifestReceipt`. Limit: proves absence from the **manifest**, not what a GPU secretly saw —
  pair with `attest` (attested enclave).
- **`@zanii/identity-bridge`** / **`zanii.identity_bridge`** — consume enterprise identity, don't
  compete with it. Bind a **Microsoft Entra Agent ID** or **SPIFFE SVID** to a `did:key`, two-way:
  the did:key signs the binding, an external token proves the other side. Token validation is
  **injected** (the `attest` discipline — you supply the Entra JWKS / SPIFFE chain check; no cloud
  deps, no live tenant needed). Since v0.2.0 the injected verifier MUST return `bound_did` — the
  did:key the token itself attests — and `verifyBinding` fails unless subject + issuer match AND
  the token names the binding's did:key (a captured token alone can no longer be paired with an
  attacker-signed binding). `buildBinding`, `verifyBindingSignature`, `verifyBinding`,
  `bindingReceipt`. Limit: only as strong as the injected verifier.
- **`@zanii/retention` v0.4.0** — **crypto-shredding**: `buildKeyDestruction`/`verifyKeyDestruction`
  (+ `keyCommitment`) — a signed `data.retention.shred` receipt proving a per-document key was
  destroyed (provable; byte deletion never is). `verifyKeyDestruction(payload, {keyBytes})` catches
  both lies — matching live bytes = NOT destroyed; mismatching bytes = a **decoy-key shred** (the
  destroyed key is not the one that protected your object).
- **`@zanii/sentinel` v0.3.0** — an **`unknown-window`** finding (watcher downtime reads as UNKNOWN,
  never "clean" — silence is never "nothing happened"), **`blastRadius(baseline)`** (could-reach
  vs did-reach; standing unused authority — facts, not a score), and since v0.3.0: `scan()` checks
  `prev` hash-chain contiguity (a suppressed receipt = HIGH `chain-gap` finding) and `buildBaseline`
  **throws** on a failed delegation chain instead of silently weakening detection.

See also **`docs/CLAIMS.md`** — the ecosystem-wide claims ladder (claim → mechanism → package →
where it stops), the purpose-binding convention (`data.<class>.<op>` via `scopesAllow`, zero new
code), and the standing "Base carries the trust claim, the Zanii L1 does not" rule.

## The data-sovereignty suite (published — npm · zanii 0.25.1 Python)

Five composable primitives, all client-side, **zero server changes** — receipt builders + verifiers
over `/v1/receipts`, `verifyReceiptChain` unchanged, read via the tag-agnostic `/v1/subjects/{tag}`.

- **`@zanii/data-custody`** / **`zanii.data_custody`** — prove what happened to the DATA an agent
  touched (**NOT** supply-chain `@zanii/custody`). `deriveRef` (`hmac:v1:…`, versioned) is the object's
  `subject_tag`; the receipt `target` **is** `data.<class>.<op>`, so the ledger's scope check
  **enforces purpose** — a read-scoped cert physically cannot record an export. `verifyObjectChain`
  → `{ok, error, failedIndex}` (mirrors `verifyReceiptChain`, never throws); `custodyView` renders the
  "your data, right now" summary incl. `destroyed_at`. Byte-identical ref TS↔Python. Limit: proves
  recorded touches, not that no off-record copy was taken (needs an attested enclave).
- **`@zanii/residency`** / **`zanii.residency`** — "did my data leave the country?" Bind a region
  attestation to each touch, confirm it's in a **declared** jurisdiction allow-set (presets UAE/GCC/EU
  are convenience — you own the list), `detectCrossings` finds exit/return borders. Geo signal is
  **injected** (`buildResidencyClaim`, `verifyResidency`, `detectCrossings`). Limit: only as strong as
  the region attestation source.
- **`@zanii/subprocessor`** / **`zanii.subprocessor`** — "who else touched it?" First-class onward-
  transfer graph; `undeclared` (a receiver not on the declared subprocessor list) is a provable
  **GDPR Art. 28** violation; missing dpa/scc agreement ids flagged. `buildTransfer`, `verifyTransfers`,
  `subprocessorGraph`. Limit: proves recorded transfers, not an off-record leak.
- **`@zanii/minimization`** / **`zanii.minimization`** — "only what you needed?" **GDPR Art. 5(1)(c)**.
  `commitFieldSet` (`minfields:v1:…`, byte-identical), `buildAccessProof`, `verifyMinimization` → `excess`
  flags over-collection; a set widened after the fact won't match the committed commitment. Composes
  `redact` + purpose binding. Limit: proves declared-vs-accessed, not that the minimum was well-chosen.
- **`@zanii/age-assurance`** / **`zanii.age_assurance`** — "prove the age gate happened — without
  storing the ID" (UK OSA / EU / US state). Records a **salted subject commitment + threshold + result +
  method + provider** — no DOB, no document. Provider check **injected**; spoofing + failed gates caught.
  `buildAgeAssertion`, `verifyAgeAssertion`. Limit: proves an assertion was made, not that the person
  truly meets the threshold.

`@zanii/zk` stays **deferred** — a hash-commitment package named "zk" would be a lie; it ships only when
it can be real zero-knowledge (a proving-system build, not a relabel).
