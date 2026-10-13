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
"""Same quality, cheaper: a signed savings statement.

    savings --prices prices.json --runs-per-month 120000 [--base-job base] [--new-job JOB]

prices.json, your contract prices (exact decimal strings, never floats):

    {"currency": "AED", "models": {"claude-sonnet-5-5":
        {"input_per_mtok": "11.02", "output_per_mtok": "55.08"}}}

A job's cost per run is the mean over its trials of input and output tokens
(each trial's result.json) times the model's prices. The saving per month is
(base - new) x runs per month, rounded to the currency's minor unit. The new
harness is compared with the base on the tasks both ran (compare.paired); a
saving is certified only when quality is non-inferior within the noise band,
so the exit code is 1 for a cheaper harness that got worse.
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from . import compare as CMP
from . import statement as ST
from .evaluate import EvalResult

MTOK = Decimal(1_000_000)
MINOR = {"JPY": 0, "KRW": 0, "BHD": 3, "KWD": 3, "OMR": 3, "JOD": 3}   # default 2


def load_prices(path: Path) -> dict:
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    cur = str(p.get("currency") or "")
    if len(cur) != 3 or not cur.isalpha():
        raise ValueError("prices: currency must be a 3-letter code such as AED")
    models = {}
    for m, v in (p.get("models") or {}).items():
        try:
            models[m] = {k: Decimal(v[k]) for k in ("input_per_mtok", "output_per_mtok")
                         if isinstance(v[k], str)}
        except (KeyError, ArithmeticError, TypeError) as e:
            raise ValueError(f"prices: {m} needs input_per_mtok and output_per_mtok") from e
        if len(models[m]) != 2 or any(not x.is_finite() or x < 0 for x in models[m].values()):
            raise ValueError(f"prices: {m}: prices are non-negative decimal strings")
    if not models:
        raise ValueError("prices: no models")
    return {"currency": cur.upper(), "models": models}


def price_of(prices: dict, model: str) -> dict:
    for name in (model, model.split("/", 1)[-1]):     # "anthropic/x" matches "x"
        if name in prices["models"]:
            return prices["models"][name]
    raise ValueError(f"no price for model {model!r} in the price file")


def job_cost(runs_dir: Path, job: str, price: dict,
             ids: set[str] | None = None) -> tuple[Decimal, int]:
    """(mean cost of one run, trials counted) over the result.json of the tasks in
    the job's eval.json (or `ids`). A result without token counts is refused: it
    would cost 0 and make the job look cheaper than it was."""
    d = Path(runs_dir) / "jobs" / job
    if ids is None:
        ids = set(EvalResult.load(d / "eval.json").per_task)
    total, n = Decimal(0), 0
    for rp in sorted(d.glob("s*/*/result.json")):
        if rp.parent.name not in ids:
            continue
        u = (json.loads(rp.read_text(encoding="utf-8")).get("usage") or {})
        tin, tout = u.get("input_tokens"), u.get("output_tokens")
        if not (isinstance(tin, int) and isinstance(tout, int)) or tin < 0 or tout < 0                 or tin + tout == 0:
            raise ValueError(f"job {job}: {rp.parent.parent.name}/{rp.parent.name} has no "
                             "input/output token counts; it cannot be priced")
        total += (Decimal(tin) * price["input_per_mtok"]
                  + Decimal(tout) * price["output_per_mtok"]) / MTOK
        n += 1
    if not n:
        raise ValueError(f"job {job}: no result.json with token usage")
    return total / n, n


def money(x: Decimal, currency: str, extra: int = 0) -> str:
    """Rounded to the currency's minor unit; `extra` more places for unit prices
    (one run usually costs a fraction of a fils)."""
    q = Decimal(1).scaleb(-MINOR.get(currency, 2) - extra)
    return str(x.quantize(q, rounding=ROUND_HALF_EVEN))


def statement_body(domain: str, runs_dir: Path, prices: dict, runs_per_month: int,
                   base_job: str, new_job: str, base_model: str, new_model: str,
                   margin: float) -> dict:
    cur = prices["currency"]
    ev_base = EvalResult.load(Path(runs_dir) / "jobs" / base_job / "eval.json")
    ev_new = EvalResult.load(Path(runs_dir) / "jobs" / new_job / "eval.json")
    quality = CMP.paired(ev_base, ev_new, margin)      # raises unless the same tasks
    ids = set(ev_base.per_task)                        # both jobs priced on exactly these
    base, nb = job_cost(runs_dir, base_job, price_of(prices, base_model), ids)
    new, nn = job_cost(runs_dir, new_job, price_of(prices, new_model), ids)
    saved = (base - new) * runs_per_month
    pct = ((base - new) / base * 100) if base else Decimal(0)
    return {"domain": domain, "currency": cur, "runs_per_month": runs_per_month,
            "base": {"job": base_job, "model": base_model, "trials": nb,
                     "cost_per_run": money(base, cur, 4)},
            "new": {"job": new_job, "model": new_model, "trials": nn,
                    "cost_per_run": money(new, cur, 4)},
            "saved_per_month": money(saved, cur), "saved_percent": str(pct.quantize(Decimal("0.1"))),
            "quality": quality, "certified": quality["non_inferior"] and saved > 0}


def _incumbent_job(run_dir: Path) -> str:
    fr = json.loads((Path(run_dir) / "frontier.json").read_text(encoding="utf-8"))
    return fr["incumbent"]["job"]


def handler(args, ctx) -> int:
    if args.runs_per_month < 1:
        print("--runs-per-month must be >= 1")
        return 2
    default_model = os.environ.get("ZANII_EVOLVE_POLICY_MODEL", (getattr(
        ctx.domain, "cfg", None) or {}).get("policy_model", "claude-opus-5-5"))
    try:
        b = statement_body(ctx.args.domain, ctx.run_dir, load_prices(args.prices),
                           args.runs_per_month, args.base_job,
                           args.new_job or _incumbent_job(ctx.run_dir),
                           args.base_model or default_model, args.new_model or default_model,
                           args.margin if args.margin is not None
                           else CMP.margin_from(ctx.run_dir))
    except (ValueError, OSError, KeyError) as e:
        print(f"savings: {e}")
        return 2
    out = ST.save(ST.issue("savings", b, ctx.run_dir),
                  Path(args.out) if args.out else ctx.run_dir / "savings.json")
    q = b["quality"]
    print(f"{b['base']['cost_per_run']} -> {b['new']['cost_per_run']} {b['currency']} per run; "
          f"{b['saved_per_month']} {b['currency']} a month ({b['saved_percent']}%) at "
          f"{b['runs_per_month']} runs; quality {q['S_a']:.3f} -> {q['S_b']:.3f} "
          f"({'non-inferior' if q['non_inferior'] else 'WORSE'})")
    print(("CERTIFIED saving; " if b["certified"] else "NOT certified; ") + f"statement {out}")
    return 0 if b["certified"] else 1


def cli(sub) -> None:
    p = sub.add_parser("savings", help="signed savings statement: cost per run before and "
                       "after, money saved a month, certified only if quality held")
    p.add_argument("--prices", required=True, help="price file (currency + per-model prices)")
    p.add_argument("--runs-per-month", type=int, required=True)
    p.add_argument("--base-job", default="base")
    p.add_argument("--new-job", help="default: the incumbent's job in frontier.json")
    p.add_argument("--base-model", help="default: the domain's policy model")
    p.add_argument("--new-model", help="default: the domain's policy model")
    p.add_argument("--margin", type=float, help="accepted quality loss (default: noise band)")
    p.add_argument("--out", help="statement path (default runs/<d>/savings.json)")
    p.set_defaults(product=handler)
