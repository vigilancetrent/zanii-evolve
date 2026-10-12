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
"""Domain paragraphs for the analyst, digester, proposer and critic."""

ANALYST = """The agent answers a question about a user from a multi-session
chat history, through a long-term memory layer (ZaniiDB: capture, recall,
conversation search). Facts are planted in small talk; some are later
updated, and distractor facts about other people share the vocabulary.
Question kinds: the current value after an update, the previous value before
it, or a stable fact. Look for failure modes that recur ACROSS tasks: stale
facts winning over updates, distractors attributed to the user, retrieval
that misses the relevant turn, answers that hedge or ramble."""

DIGESTER = """The trace shows the sessions and question given to the agent,
the memory context the harness built (recall output and conversation
excerpts), the answer and the per-criterion verdicts. Attribute each failure
to a harness cause: retrieval missed the fact, ordering lost the update,
context included distractors without attribution, or the prompt mis-steered
the answer."""

PROPOSER = """The harness is agent.py (memory ingestion, retrieval, context
building, one policy call), prompts.py (model-facing text) and mechanisms.py
(inert cross-task lesson memory). Retrieval and context construction are the
main levers: query rewriting, more or better-ordered hits, session labels,
separating the user's facts from other people's. Out of bounds: task ids,
names or values from particular tasks, reading the grader, and hard-coding
the fact vocabulary of this task set (city, job, diet, ...) as special cases:
the method must generalize to any personal fact."""

CRITIC = """The harness answers questions about a user from chat history via
a memory layer. Reject edits that hard-code task ids, user facts or values
(cities, names, jobs) from particular tasks, that special-case the question
templates of this task set, that reference the grader, rubric or task files,
or that persist task content across tasks. General retrieval, ordering and
answering procedure is allowed."""
