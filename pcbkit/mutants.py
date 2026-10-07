"""Plant known mistakes in a scratch copy of the design; the checks must catch them.

A check that has never been seen to fail is not trusted. A project lists planted
mistakes in its ``mutants.py``:

    MUTANTS = [
        (
            "pull-up resistor missing",  # what the mistake is
            [  # edits to design.py: each is (text to find, text to put there)
                ('R("R7", "10k", "EN", "+3V3", B)', 'R("R7", "10k", "EN", "NC_X", B)'),
            ],
            "test_boot_state_is_safe",  # the pytest -k expression that must then fail
        ),
    ]

For each mutant the runner copies the project to a scratch folder, applies the edits to
``design.py`` there, makes the schematic again, and runs the checks selected by the
expression. The mutant is caught when pytest fails; it is missed when every selected
check still passes, or when the edit cannot be applied (the text is not in design.py).
The board layout is left as it is, so mutants target the schematic, netlist and
circuit checks; the layout checks are shown to fail by other means.

First a control: the same checks on an unedited copy must pass, or a catch means
nothing. The collection is exactly that of ``pcbkit check`` (see
``pcbkit.check.runner``): the built-in checks of the groups the project switched on,
and the project's own ``checks/``.

Lists whose name ends in ``_MUTANTS`` other than ``MUTANTS`` are parked: they are
counted and named, and not run.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from pcbkit.check import runner
from pcbkit.project import Project, import_project_module

Edit = tuple[str, str]
Mutant = tuple[str, Sequence[Edit], str]

# Not worth copying into the scratch folder (and .venv can be huge).
SKIP = (".venv*", ".git", "archive", "__pycache__", ".pytest_cache", ".ruff_cache")

# Seconds one scratch run (schematic, then pytest) may take.
TIMEOUT = 900.0

NAME_WIDTH = 62

# What regenerating the schematic runs in the scratch folder. Only an exception counts
# as a failure: ERC findings on a mutated design are expected; the checks judge them.
REGENERATE = (
    "from pcbkit import sch\n"
    "from pcbkit.project import load_project\n"
    "sch.build_schematic(load_project())\n"
)


@dataclass(frozen=True)
class Verdict:
    """What one scratch run came to.

    ``caught`` is True when pytest failed; ``summary`` is pytest's last line (or what
    went wrong); ``error`` is True when the run could not be set up.
    """

    name: str
    caught: bool
    summary: str
    error: bool = False

    def line(self) -> str:
        """Return the verdict as one report line: CAUGHT or MISSED, then the summary."""
        word = "CAUGHT" if self.caught else "MISSED"
        return f"{word}  ({self.summary})"


def load(project: Project) -> tuple[list[Mutant], dict[str, int]]:
    """Return the project's MUTANTS and a count of each parked ``*_MUTANTS`` list."""
    module = import_project_module(project.root, "mutants")
    mutants = getattr(module, "MUTANTS", None)
    if not mutants:
        raise click.ClickException(
            f"{project.root / 'mutants.py'} defines no MUTANTS: add the planted "
            "mistakes the checks must catch (docs/project-interface.md shows the form)"
        )
    parked = {
        name: len(value)
        for name, value in vars(module).items()
        if name.endswith("_MUTANTS") and name != "MUTANTS" and isinstance(value, list)
    }
    return [(str(n), list(e), str(t)) for n, e, t in mutants], parked


def copy_project(project: Project, dest: Path) -> Path:
    """Copy the project into ``dest``, leaving out the bulky folders."""
    shutil.copytree(
        project.root, dest, ignore=shutil.ignore_patterns(*SKIP), dirs_exist_ok=True
    )
    return dest


def _tail(text: str, lines: int = 3) -> str:
    """Return the last few non-empty lines of ``text``, joined for one line."""
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(kept[-lines:])


def apply_edits(design: Path, edits: Sequence[Edit]) -> str | None:
    """Edit ``design`` in place; return a message if a text to replace is not in it."""
    text = design.read_text(encoding="utf-8")
    for old, new in edits:
        if old not in text:
            return f"pattern not found in design.py: {old[:60]}"
        text = text.replace(old, new)
    design.write_text(text, encoding="utf-8")
    return None


def run_mutant(
    project: Project, name: str, edits: Sequence[Edit], expression: str
) -> Verdict:
    """Run the checks selected by ``expression`` on a copy edited by ``edits``."""
    scratch = Path(tempfile.mkdtemp(prefix="pcbkit_mutant_"))
    try:
        copy_project(project, scratch)
        problem = apply_edits(scratch / "design.py", edits)
        if problem:
            return Verdict(name, False, f"SETUP ERROR: {problem}", error=True)
        made = subprocess.run(
            [sys.executable, "-c", REGENERATE],
            cwd=scratch,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        if made.returncode:
            return Verdict(
                name, False, f"SETUP ERROR: {_tail(made.stderr, 6)}", error=True
            )
        command = runner.command(scratch, expression, ["-q", "--no-header"])
        done = subprocess.run(
            command, cwd=scratch, capture_output=True, text=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return Verdict(name, False, f"SETUP ERROR: timed out after {TIMEOUT:g} s", True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    lines = done.stdout.strip().splitlines()
    summary = lines[-1].strip() if lines else _tail(done.stderr, 1)
    return Verdict(name, done.returncode != 0 and "failed" in summary, summary)


def control_expression(mutants: Sequence[Mutant]) -> str:
    """Return the ``-k`` expression that selects every check a mutant relies on."""
    return " or ".join(f"({expression})" for _, _, expression in mutants)


def run_all(
    project: Project,
    echo: Callable[[str], Any] = click.echo,
) -> int:
    """Run the control, then every mutant; print a line each; return the exit code.

    0 when the control passes and every mutant is caught, 1 when a mutant is missed,
    2 when the control does not pass (nothing else is run).
    """
    runner.require_pytest()
    if not (project.root / "design.py").is_file():
        raise click.ClickException(f"{project.root / 'design.py'} not found")
    mutants, parked = load(project)
    control = run_mutant(project, "control (no edits)", [], control_expression(mutants))
    ok = not control.caught and " passed" in f" {control.summary}"
    if control.error or not ok:
        echo(f"{control.name:<{NAME_WIDTH}} FAILS   {control.summary}")
        echo("\ncontrol run did not pass cleanly: fix that first")
        return 2
    echo(f"{control.name:<{NAME_WIDTH}} PASSES  {control.summary}")
    missed = 0
    for name, edits, expression in mutants:
        verdict = run_mutant(project, name, edits, expression)
        missed += not verdict.caught
        echo(f"{verdict.name:<{NAME_WIDTH}} {verdict.line()}")
    echo(f"\n{len(mutants) - missed}/{len(mutants)} planted mistakes caught")
    for name, count in sorted(parked.items()):
        echo(f"{count} parked in {name} (not run)")
    return 1 if missed else 0
