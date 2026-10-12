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
"""Domain paragraphs for the analyst, digester, proposer and critic.
Edit these to describe your domain; keep the out-of-bounds rules."""

ANALYST = """The agent is a marketing copywriter: each task is a client brief
(fictional brand, audience, voice, product facts, offer, banned words) and a
deliverable type (search ad, promotional email, LinkedIn post, Instagram
caption, SMS, landing page hero). The agent turns a short brief into one text deliverable. Each task
is graded by deterministic checks (length, required and banned phrases,
format) and a strict per-criterion rubric judged by an LLM. Look for failure
modes that recur ACROSS tasks: requirements the agent skips, format or length
violations, invented facts, tone mismatches. A failure on one task only is
noise unless its cause is general."""

DIGESTER = """The trace shows the task given to the agent, every policy call
the harness made, the final deliverable and the per-criterion verdicts with
the judge's reasons. Attribute each failed criterion to a harness cause
(prompt gap, missing self-check, length control, format handling) rather than
to the model being weak."""

PROPOSER = """The harness is small: agent.py calls a frozen policy model once
per task (plus optional extra calls), prompts.py holds the model-facing text,
mechanisms.py offers inert memory primitives. Grading is per criterion, so
satisfying MORE requirements on tasks that already mostly pass is worth as
much as rescuing a failure. Out of bounds, enforced before measurement:
reading or gaming the grader, referencing task ids, and any task-specific
content (names, products, numbers, phrases from particular briefs). Prefer
mechanisms a competent professional would use on ANY brief: read every
requirement, plan, check length and format, verify facts against the brief."""

CRITIC = """The harness drives a frozen policy model through brief-to-
deliverable tasks graded by checks and a rubric. Reject edits that hard-code
task ids, names, products, numbers or phrases from particular tasks, that
special-case individual tasks, that reference the grader, rubric or task
files, or that store task-specific content in memory. General procedure,
self-checks and format discipline are allowed."""
