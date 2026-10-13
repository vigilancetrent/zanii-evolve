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
"""The product subcommands (migrate, savings, regress, bake-off, ...).

Each product module has `cli(sub)`: it adds its subparser(s) and sets
`product=<handler>`. A handler takes (args, ctx) and returns the exit code.
zanii_evolve.py calls `register(sub)` once and `dispatch(args, root)` before
its own commands, so a product never edits the main CLI.
"""

from __future__ import annotations

import importlib
from functools import cached_property
from pathlib import Path

MODULES = ("migrate", "savings", "regress", "rulebook", "compliance", "bakeoff", "dialects",
           "redscore", "airgap", "store", "judgepack", "liveconfirm")


class Ctx:
    """What a product handler needs, built only when it asks for it."""

    def __init__(self, args, root: Path):
        self.args, self.root = args, Path(root)

    @cached_property
    def domain(self):
        from .domain import load_domain
        return load_domain(self.args.domain)

    @cached_property
    def cfg(self):
        from .config import ZaniiEvolveConfig
        return ZaniiEvolveConfig.load(self.domain.root / "zanii_evolve.json")

    @cached_property
    def run(self):
        from .loop import Run
        return Run(self.domain, self.cfg, self.root, Path(self.args.runs))

    @property
    def run_dir(self) -> Path:
        return Path(self.args.runs) / self.args.domain

    def checkout(self, label: str, ref: str | None) -> Path:
        """A worktree of `ref` (default: the domain's evolve branch tip)."""
        self.run.ensure_branch()
        return self.run.checkout(label, ref or self.run.branch)


def register(sub) -> None:
    for name in MODULES:
        importlib.import_module(f"zanii_evolve.{name}").cli(sub)


def dispatch(args, root: Path) -> int | None:
    """The product's exit code, or None when args.cmd is not a product."""
    handler = getattr(args, "product", None)
    return None if handler is None else int(handler(args, Ctx(args, root)) or 0)
