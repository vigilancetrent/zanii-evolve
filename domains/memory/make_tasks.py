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
"""Generate the long-term memory task set (deterministic for a given seed).

    python3 domains/memory/make_tasks.py [--seed 11] [--out domains/memory/tasks.jsonl]

Each task is a multi-session chat history between one user and an assistant,
followed by a question about the user. Facts are planted inside small talk;
some are later UPDATED (the old value is superseded), and distractor facts
about other people share the same vocabulary. Question kinds:

    current   the value after the latest update      (stale memory fails)
    previous  the value before the update            (over-eager overwrite fails)
    stable    a fact stated once, never changed       (plain recall)
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

# slot: values, statement, update (old -> new), question per kind, distractor
SLOTS = {
    "city": {
        "values": ["Lisbon", "Porto", "Valencia", "Leeds", "Lyon", "Krakow", "Utrecht", "Ghent"],
        "say": "I live in {v} these days.",
        "update": "Big news: we moved from {old} to {v} last month.",
        "q": {"current": "Which city does the user live in now?",
              "previous": "Which city did the user live in before their most recent move?",
              "stable": "Which city does the user live in?"},
        "distract": "My sister just found a flat in {v}.",
    },
    "job": {
        "values": ["nurse", "data analyst", "high-school teacher", "architect", "sous-chef",
                   "physiotherapist", "UX designer", "civil engineer"],
        "say": "Work as a {v} keeps me busy.",
        "update": "I finally changed careers: I'm a {v} now, no longer a {old}.",
        "q": {"current": "What is the user's current job?",
              "previous": "What was the user's job before they changed careers?",
              "stable": "What does the user do for work?"},
        "distract": "My neighbour is a {v} and works nights.",
    },
    "gym_day": {
        "values": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
        "say": "I always hit the gym on {v}.",
        "update": "I switched my gym day from {old} to {v} because of my new schedule.",
        "q": {"current": "On which day does the user go to the gym now?",
              "previous": "Which day did the user go to the gym before switching?",
              "stable": "On which day does the user go to the gym?"},
        "distract": "My brother only trains on {v}.",
    },
    "diet": {
        "values": ["vegetarian", "vegan", "pescatarian", "gluten-free", "dairy-free"],
        "say": "I've been {v} for a while now.",
        "update": "Quick update on food: I stopped being {old}, I'm {v} now.",
        "q": {"current": "What diet does the user follow now?",
              "previous": "What diet did the user follow before their latest change?",
              "stable": "What diet does the user follow?"},
        "distract": "My flatmate went {v} this year.",
    },
    "language": {
        "values": ["Spanish", "Japanese", "German", "Italian", "Korean", "Dutch"],
        "say": "I'm learning {v} on my lunch breaks.",
        "update": "I dropped {old} and started learning {v} instead.",
        "q": {"current": "Which language is the user learning now?",
              "previous": "Which language was the user learning before switching?",
              "stable": "Which language is the user learning?"},
        "distract": "My manager is fluent in {v}.",
    },
    "pet": {
        "values": ["Biscuit", "Pepper", "Mochi", "Rufus", "Olive", "Juniper"],
        "say": "My dog {v} chewed my headphones again.",
        "update": None,
        "q": {"stable": "What is the name of the user's dog?"},
        "distract": "My cousin's dog is called {v}.",
    },
}

ASKS = ["Can you suggest a quick dinner idea?", "Any tips for sleeping better?",
        "What's a good way to plan my week?", "Can you recommend a podcast genre?",
        "How do I keep houseplants alive?", "Any ideas for a rainy weekend?",
        "How should I start budgeting?", "What's a good stretch after sitting all day?"]
REPLIES = ["Happy to help! Here are a few simple ideas you could try.",
           "Good question. A small, consistent routine usually works best.",
           "Sure. Start small and adjust as you go.",
           "Here is a short list you can pick from."]
OPENERS = ["Hey!", "Morning.", "Hi again.", "Long week.", "Quick one today."]


def _turn(rng, statement: str | None) -> list[dict]:
    user = " ".join(x for x in (rng.choice(OPENERS), statement, rng.choice(ASKS)) if x)
    return [{"role": "user", "content": user},
            {"role": "assistant", "content": rng.choice(REPLIES)}]


def task(n: int, split: str, rng: random.Random) -> dict:
    kind = rng.choice(["current", "current", "previous", "stable"])
    updatable = [s for s, v in SLOTS.items() if v["update"]]
    target = rng.choice(updatable if kind != "stable" else list(SLOTS))
    others = rng.sample([s for s in SLOTS if s != target], k=2)
    events = []                                 # (session index, statement)
    old, new = rng.sample(SLOTS[target]["values"], k=2)
    if kind == "stable":
        events.append((rng.randint(0, 3), SLOTS[target]["say"].format(v=old)))
        expected, wrong = old, None
    else:
        events.append((rng.randint(0, 1), SLOTS[target]["say"].format(v=old)))
        events.append((rng.randint(3, 5), SLOTS[target]["update"].format(old=old, v=new)))
        expected, wrong = (new, old) if kind == "current" else (old, new)
    for s in others:
        events.append((rng.randint(0, 5), SLOTS[s]["say"].format(v=rng.choice(SLOTS[s]["values"]))))
    decoy = rng.choice([v for v in SLOTS[target]["values"] if v not in (old, new)])
    events.append((rng.randint(0, 5), SLOTS[target]["distract"].format(v=decoy)))
    sessions = []
    for i in range(6):
        turns = []
        for _, st in sorted((e for e in events if e[0] == i), key=lambda e: e[1]):
            turns += _turn(rng, st)
        if not turns:
            turns = _turn(rng, None)
        sessions.append({"session": i + 1, "turns": turns})
    question = SLOTS[target]["q"][kind]
    rubric = [{"criterion": f"The answer gives {expected} as the answer to: {question}", "weight": 3}]
    if wrong:
        rubric.append({"criterion": f"The answer does not present {wrong} as the answer to the question."})
    rubric += [{"criterion": f"The answer does not confuse the user with another person "
                             f"(e.g. {decoy}, which belongs to someone else)."},
               {"criterion": "The answer is one or two sentences and does not invent details."}]
    return {"id": f"mem-{n:03d}", "split": split, "kind": f"{kind} {target}",
            "sessions": sessions, "question": question,
            "checks": [{"type": "must_include", "values": [expected]},
                       {"type": "max_words", "value": 60}],
            "rubric": rubric}


def build(seed: int) -> list[dict]:
    rng = random.Random(seed)
    splits = ["smoke"] * 2 + ["heldout"] * 10 + ["evolve"] * 28
    return [task(n, split, rng) for n, split in enumerate(splits, 1)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "tasks.jsonl"))
    args = ap.parse_args()
    tasks = build(args.seed)
    Path(args.out).write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks),
                              encoding="utf-8")
    print(f"wrote {len(tasks)} tasks to {args.out}")


if __name__ == "__main__":
    main()
