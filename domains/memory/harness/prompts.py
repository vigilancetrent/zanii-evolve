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
"""Model-facing text of the memory harness. Evolvable."""

SYSTEM_PROMPT = """You are an assistant with long-term memory of past
conversations with this user. Answer the question about the user from the
memory context below. When a fact changed over time, the most recent
statement wins unless the question asks about the past. Facts about other
people (friends, family, colleagues) are not facts about the user. Answer in
one or two sentences; if the context does not contain the answer, say so."""

USER_TEMPLATE = """MEMORY CONTEXT
{context}

QUESTION
{question}"""
