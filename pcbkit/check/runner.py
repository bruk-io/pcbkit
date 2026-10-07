"""The one pytest command line behind ``pcbkit check`` and ``pcbkit mutants``.

Both commands collect exactly the same checks: the built-in modules of the groups the
project switched on, and the project's own ``checks/`` folder, in a pytest process
that loads ``pcbkit.check.plugin`` and nothing else from its surroundings (its own
ini file, ``--rootdir`` at the project, no cache folder). ``pcbkit mutants`` points the
same command at a scratch copy of the project.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import click

from pcbkit.project import Project

INI = Path(__file__).resolve().with_name("pytest.ini")
BUILTIN = "pcbkit.check.builtin"


def command(
    root: Path,
    expression: str | None = None,
    extra: Sequence[str] = (),
) -> list[str]:
    """Return the pytest command line that runs the checks of the project at ``root``.

    ``expression`` is pytest's ``-k`` expression; ``extra`` is added before the
    places to collect (so ``-q`` or ``--no-header`` can be passed). The project's
    ``checks/`` folder is collected only if it exists.
    """
    root = Path(root)
    args = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(INI),
        "--rootdir",
        str(root),
        "-p",
        "pcbkit.check.plugin",
        "-p",
        "no:cacheprovider",
        "--pcbkit-project",
        str(root),
    ]
    if expression:
        args += ["-k", expression]
    args += list(extra)
    args += ["--pyargs", BUILTIN]
    if (root / "checks").is_dir():
        args.append(str(root / "checks"))
    return args


def require_pytest() -> None:
    """Raise a ClickException unless pytest can be imported here."""
    try:
        import pytest  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "pytest isn't installed in this environment: the checks run on it. "
            "Add it to the project's dependencies (uv add pytest) and run again."
        ) from None


def run(
    project: Project,
    expression: str | None = None,
    extra: Sequence[str] = ("-q", "-rfEs"),
) -> int:
    """Run the project's checks with output on the terminal; return pytest's exit code.

    Raise a ClickException if the schematic has not been made yet, since every check
    that reads the netlist would only say so one by one.
    """
    require_pytest()
    board = project.config.board
    schematic = project.kicad_dir / f"{board.stem}.kicad_sch"
    if not schematic.is_file():
        raise click.ClickException(
            f"{schematic} not found: run `pcbkit sch` (or `pcbkit build`) first"
        )
    done = subprocess.run(command(project.root, expression, extra), cwd=project.root)
    return done.returncode
