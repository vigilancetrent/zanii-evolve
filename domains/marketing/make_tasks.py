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
"""Generate the marketing task set (deterministic for a given seed).

    python3 domains/marketing/make_tasks.py [--seed 7] [--out domains/marketing/tasks.jsonl]

Every task pairs a fictional client (voice, audience, product facts, banned
words, offer) with a deliverable type. Deliverable types carry deterministic
checks (length, required phrases, format fields); every task carries rubric
criteria for voice, audience, grounding and call to action. Clients and
deliverables are crossed so each split mixes both, and the held-out split
uses client x deliverable pairs never seen in evolve.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
from pathlib import Path

CLIENTS = [
    {"name": "Fernleaf Dental", "industry": "dental clinic",
     "audience": "busy parents booking check-ups for their children",
     "voice": "reassuring, friendly, plain-spoken",
     "facts": ["Saturday appointments", "gentle care for nervous kids",
               "online booking in under two minutes"],
     "offer": "a free first check-up for children under 12", "cta": "Book a visit",
     "banned": ["painless", "best in town"]},
    {"name": "Quarry & Co.", "industry": "B2B accounting software",
     "audience": "finance managers at companies with 50-500 employees",
     "voice": "precise, confident, no hype",
     "facts": ["closes the month in three days instead of ten",
               "connects to 40 banks", "SOC 2 Type II certified"],
     "offer": "a 30-day trial with guided onboarding", "cta": "Start your trial",
     "banned": ["revolutionary", "game-changer"]},
    {"name": "Lumen Yoga Studio", "industry": "yoga studio",
     "audience": "beginners in their 30s and 40s who feel stiff after desk work",
     "voice": "calm, warm, encouraging",
     "facts": ["classes capped at 12 people", "beginner series every Monday",
               "mats and props provided"],
     "offer": "two weeks of unlimited classes for 29 dollars", "cta": "Claim your two weeks",
     "banned": ["cure", "guaranteed"]},
    {"name": "Brightline Solar", "industry": "residential solar installation",
     "audience": "homeowners comparing quotes for rooftop solar",
     "voice": "straightforward, trustworthy, practical",
     "facts": ["installation in one day for most homes", "25-year panel warranty",
               "free roof assessment"],
     "offer": "a free roof assessment and written quote", "cta": "Get your free quote",
     "banned": ["free electricity", "zero bills"]},
    {"name": "Copperpot Kitchen", "industry": "meal-kit delivery",
     "audience": "couples who want to cook more at home on weeknights",
     "voice": "playful, appetizing, upbeat",
     "facts": ["recipes ready in 25 minutes", "skip or pause any week",
               "ingredients pre-portioned to cut waste"],
     "offer": "half price on the first box", "cta": "Get half off your first box",
     "banned": ["healthiest", "chef-quality guaranteed"]},
    {"name": "Harbor Legal Partners", "industry": "small-business law firm",
     "audience": "founders forming or restructuring a small company",
     "voice": "authoritative, clear, approachable",
     "facts": ["fixed-fee company formation packages", "contracts reviewed within 48 hours",
               "free 20-minute consultation"],
     "offer": "a free 20-minute consultation", "cta": "Schedule a consultation",
     "banned": ["win your case", "guarantee"]},
    {"name": "Trailhead Outfitters", "industry": "outdoor gear retailer",
     "audience": "weekend hikers planning their first multi-day trip",
     "voice": "adventurous, knowledgeable, down-to-earth",
     "facts": ["gear rental for first-timers", "free trail-planning workshops",
               "lifetime repairs on own-brand packs"],
     "offer": "15 percent off rentals booked this month", "cta": "Plan your trip",
     "banned": ["indestructible", "unbeatable"]},
    {"name": "Nimbus Cloud Backup", "industry": "cloud backup for small offices",
     "audience": "office managers without a dedicated IT team",
     "voice": "calm, competent, jargon-free",
     "facts": ["automatic nightly backups", "restore any file in minutes",
               "data encrypted in transit and at rest"],
     "offer": "the first three months free", "cta": "Protect your files",
     "banned": ["hack-proof", "100 percent secure"]},
]

# kind -> (instructions template, checks factory)
DELIVERABLES = {
    "search ad": (
        "Write a Google search ad with exactly these lines: 'Headline 1:', "
        "'Headline 2:', 'Headline 3:' (each at most 30 characters) and "
        "'Description:' (at most 90 characters).",
        lambda c: [{"type": "field_max_chars", "field": f"Headline {i}", "value": 30} for i in (1, 2, 3)]
        + [{"type": "field_max_chars", "field": "Description", "value": 90},
           {"type": "must_not_include", "values": c["banned"]}]),
    "promotional email": (
        "Write a promotional email: first line 'Subject:' (at most 60 characters), "
        "then the body, 90 to 160 words, ending with the call to action.",
        lambda c: [{"type": "field_max_chars", "field": "Subject", "value": 60},
                   {"type": "min_words", "value": 90}, {"type": "max_words", "value": 175},
                   {"type": "must_include", "values": [c["cta"]]},
                   {"type": "must_not_include", "values": c["banned"]}]),
    "LinkedIn post": (
        "Write a LinkedIn post of 50 to 120 words with at most 3 hashtags.",
        lambda c: [{"type": "min_words", "value": 50}, {"type": "max_words", "value": 120},
                   {"type": "regex_count", "pattern": r"#\w+", "min": 0, "max": 3},
                   {"type": "must_not_include", "values": c["banned"]}]),
    "Instagram caption": (
        "Write an Instagram caption of at most 70 words followed by 3 to 5 hashtags.",
        lambda c: [{"type": "max_words", "value": 80},
                   {"type": "regex_count", "pattern": r"#\w+", "min": 3, "max": 5},
                   {"type": "must_not_include", "values": c["banned"]}]),
    "SMS": (
        "Write one SMS of at most 160 characters including the call to action.",
        lambda c: [{"type": "max_chars", "value": 160},
                   {"type": "must_include", "values": [c["cta"]]},
                   {"type": "must_not_include", "values": c["banned"]}]),
    "landing page hero": (
        "Write a landing page hero with lines 'Headline:' (at most 10 words), "
        "'Subheadline:' (one sentence) and 'Button:' (the call to action).",
        lambda c: [{"type": "regex", "pattern": r"(?im)^\s*headline\s*:"},
                   {"type": "regex", "pattern": r"(?im)^\s*subheadline\s*:"},
                   {"type": "regex", "pattern": r"(?im)^\s*button\s*:"},
                   {"type": "must_include", "values": [c["cta"]]},
                   {"type": "must_not_include", "values": c["banned"]}]),
}


def task(n: int, client: dict, kind: str, split: str, rng: random.Random) -> dict:
    instr, checks = DELIVERABLES[kind]
    facts = rng.sample(client["facts"], k=2)
    brief = (f"Client: {client['name']} ({client['industry']}). Audience: {client['audience']}. "
             f"Brand voice: {client['voice']}. Product facts you may use: {facts[0]}; {facts[1]}. "
             f"Current offer: {client['offer']}. Call to action: '{client['cta']}'. "
             f"Never use these words: {', '.join(client['banned'])}. {instr}")
    return {
        "id": f"mkt-{n:03d}", "split": split, "kind": kind, "brief": brief,
        "checks": checks(client),
        "rubric": [
            {"criterion": f"The voice matches the brand voice: {client['voice']}."},
            {"criterion": f"The copy speaks to the stated audience ({client['audience']})."},
            {"criterion": f"It uses at least one of the given product facts: {facts[0]}; {facts[1]}."},
            {"criterion": f"It presents the current offer accurately: {client['offer']}."},
            {"criterion": "It makes no claims, numbers or features beyond the brief.", "weight": 2},
            {"criterion": "It reads as finished, publishable copy with one clear call to action."},
        ],
    }


def build(seed: int) -> list[dict]:
    rng = random.Random(seed)
    pairs = list(itertools.product(range(len(CLIENTS)), DELIVERABLES))
    rng.shuffle(pairs)
    # 48 pairs: 2 smoke, 12 held-out, 34 evolve
    splits = ["smoke"] * 2 + ["heldout"] * 12 + ["evolve"] * (len(pairs) - 14)
    return [task(n, CLIENTS[ci], kind, split, rng)
            for n, ((ci, kind), split) in enumerate(zip(pairs, splits), 1)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "tasks.jsonl"))
    args = ap.parse_args()
    tasks = build(args.seed)
    Path(args.out).write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks),
                              encoding="utf-8")
    print(f"wrote {len(tasks)} tasks to {args.out}")


if __name__ == "__main__":
    main()
