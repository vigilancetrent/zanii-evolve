# Pattern Library (reference, not an allowlist)

## 1. Requirement checklist
Extract every explicit requirement of the brief (length, must-include
phrases, format, audience, tone) into a short list before drafting, then
verify the draft against it. Cheap, general, and it attacks the most common
failure: a requirement silently skipped.

## 2. Draft, then targeted revision
A second call that receives the draft plus the checklist and fixes only the
violations. Pays for itself when failures are concentrated in a few
requirements; wastes tokens when the first draft already passes. Gate it on a
cheap local check (word count, missing phrase) instead of always running it.

## 3. Deterministic length and format control
Count words or characters in code and ask for a rewrite only when a limit is
broken. Never truncate mechanically: a cut sentence fails the rubric.

## 4. Fact grounding
Instruct the model to use only facts present in the brief and to omit rather
than invent. Invented claims fail rubric criteria and are a client risk.

## 5. Cross-task lesson memory (ZaniiDB)
`TaskMemory.before(situation)` with the task TYPE (e.g. "promotional email",
"search ad") returns procedures and pitfalls from earlier tasks;
`TaskMemory.after(lesson, outcome)` records one transferable lesson. A write
path with no read path that changes behaviour is dead storage. Keep lessons
entity-free.
