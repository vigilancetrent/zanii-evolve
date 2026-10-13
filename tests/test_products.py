"""Every product registers cleanly next to the main CLI's global flags."""

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import products  # noqa: E402

GLOBAL = ["T", "k", "m", "b_min", "b_max", "w", "m_draft", "delta", "delta_z", "beta0", "beta1",
          "w_s", "w_c", "w_n", "n_prune", "eval_parallel", "objective", "confirm_k",
          "min_judge_kappa"]


def _parser():
    ap = argparse.ArgumentParser(prog="zanii_evolve.py")
    ap.add_argument("--domain")
    ap.add_argument("--runs", default="runs")
    for k in GLOBAL:
        ap.add_argument("--" + k.replace("_", "-"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    products.register(sub)
    return ap, sub


def test_products_register_without_clashes():
    _, sub = _parser()
    names = set(sub.choices)
    for want in ("migrate", "savings", "regress", "rulebook", "evidence-pack", "bakeoff",
                 "dialects", "redteam-round", "redteam-score", "airgap-check", "store",
                 "judge-pack", "confirm-live"):
        assert want in names, want
    for name, p in sub.choices.items():      # no product redefines a global hyperparameter
        flags = {o for a in p._actions for o in a.option_strings}
        assert not flags & {"--" + k.replace("_", "-") for k in GLOBAL}, name
        assert p.get_default("product") or any(
            isinstance(a, argparse._SubParsersAction) for a in p._actions), name


@pytest.mark.parametrize("cmd", ["migrate", "savings", "regress", "bakeoff", "store",
                                 "confirm-live", "airgap-check"])
def test_help_runs(cmd, capsys):
    ap, _ = _parser()
    with pytest.raises(SystemExit) as e:
        ap.parse_args(["--domain", "d", cmd, "--help"])
    assert e.value.code == 0 and cmd in capsys.readouterr().out
