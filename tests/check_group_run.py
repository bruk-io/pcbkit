"""Write a board project to disk and run its built-in checks, for the group tests.

The copper, fab and outputs groups read a real board, so their tests put a small
project in a folder (a ``pcbkit.toml``, ``specs.py``, a board in ``kicad/``, ...), build
the real pytest command line of ``pcbkit.check.runner.command`` and run it in a child
process, as ``pcbkit check`` would. Results are read back from
``out/checks/results.json``.

``write_toml`` writes the project file with any of its tables. ``write_netlist`` puts a
netlist next to a ``fake_nl`` pytest plugin that serves it as the ``nl`` fixture, so a
run needs no schematic and no kicad-cli (load it with ``-p fake_nl`` after the pcbkit
plugin: a later plugin's fixture wins, and ``run`` does that whenever the project has a
``fake_nl.py``).

``Lab`` builds variants of a control project and runs each once: a test names a variant
(the control with one thing changed) and asks what its run found. Importing this module
needs no pcbnew.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pcbkit.check import runner
from pcbkit.check.results import RESULTS_FILE
from tests.check_toy import Toy, netlist_text

FAKE_NL = '''\
"""Serve the project's netlist as the nl fixture, so a run needs no KiCad."""
from pathlib import Path

import pytest

from pcbkit.check.netlist import Netlist


@pytest.fixture(scope="session")
def nl():
    return Netlist.from_file(Path(__file__).with_name("project.net"))
'''

# The checks never run longer than this on a board this small; a hang is a failure.
TIMEOUT_S = 300


@dataclass
class Run:
    """One run of the checks: the child process and the results it left."""

    done: subprocess.CompletedProcess[str]
    results: dict[str, Any]

    @property
    def checks(self) -> dict[str, dict[str, Any]]:
        """Return the checks by id."""
        return self.results.get("checks", {})

    def entry(self, name: str) -> dict[str, Any]:
        """Return the results entry of the one check whose id contains ``name``."""
        found = [v for k, v in self.checks.items() if name in k]
        assert len(found) == 1, f"{name}: {len(found)} matches in {sorted(self.checks)}"
        return found[0]

    def outcome(self, name: str) -> str:
        """Return the outcome of the one check whose id contains ``name``."""
        return str(self.entry(name)["outcome"])

    def numbers(self, name: str) -> dict[str, Any]:
        """Return what the one check whose id contains ``name`` recorded."""
        return dict(self.entry(name)["numbers"])

    def message(self, name: str) -> str:
        """Return the failure message of the one check whose id contains ``name``."""
        return str(self.entry(name).get("message", ""))

    def outcomes(self) -> dict[str, str]:
        """Return every check's outcome by id, for a message that shows them all."""
        return {k: v["outcome"] for k, v in self.checks.items()}

    def tail(self) -> str:
        """Return the end of the child's output, to say what went wrong."""
        return (self.done.stdout + self.done.stderr)[-2500:]


def run(root: Path, expression: str | None = None, extra: tuple[str, ...] = ()) -> Run:
    """Run the project's checks in a child pytest; return it and its results.

    ``expression`` is pytest's ``-k``. The project folder goes first on PYTHONPATH (so
    ``-p fake_nl`` finds the plugin) and the caller's own PYTHONPATH stays after it.
    """
    args = ["-q", *extra]
    if (root / "fake_nl.py").is_file():
        args += ["-p", "fake_nl"]
    command = runner.command(root, expression, args)
    path = os.pathsep.join(
        part for part in (str(root), os.environ.get("PYTHONPATH", "")) if part
    )
    done = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        env={**os.environ, "PYTHONPATH": path},
    )
    results_file = root / "out" / "checks" / RESULTS_FILE
    found = results_file.read_text(encoding="utf-8") if results_file.is_file() else "{}"
    return Run(done, json.loads(found))


def write_toml(
    root: Path,
    stem: str,
    groups: list[str],
    stackup: dict[str, float] | None = None,
    stitch: dict[str, float] | None = None,
) -> Path:
    """Write ``pcbkit.toml`` into ``root``: the board names, the groups and the tables.

    ``stackup`` and ``stitch`` give the keys of those tables (``copper_mm``,
    ``pitch_mm``, ...); a table that is not given is left out, so it takes its default.
    The fab name is ``<stem>_revA``.
    """
    root.mkdir(parents=True, exist_ok=True)
    quoted = ", ".join(f'"{g}"' for g in groups)
    lines = [
        "[board]",
        f'stem = "{stem}"',
        'title = "Group Test Board"',
        'rev = "A"',
        f'fab_name = "{stem}_revA"',
        "",
    ]
    for table, keys in (("stackup", stackup), ("stitch", stitch)):
        if keys:
            lines += [f"[{table}]", *(f"{k} = {v!r}" for k, v in keys.items()), ""]
    lines += ["[checks]", f"groups = [{quoted}]", ""]
    path = root / "pcbkit.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_netlist(root: Path, toy: Toy) -> None:
    """Write the netlist of ``toy`` and the ``fake_nl`` plugin that serves it."""
    (root / "project.net").write_text(netlist_text(toy), encoding="utf-8")
    (root / "fake_nl.py").write_text(textwrap.dedent(FAKE_NL), encoding="utf-8")


def replace_once(path: Path, old: str, new: str) -> None:
    """Replace ``old`` by ``new`` in a project file, which must hold ``old`` once."""
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{path.name}: {old!r} found {text.count(old)} times"
    path.write_text(text.replace(old, new), encoding="utf-8")


@dataclass(frozen=True)
class Result:
    """A project that has been built and run: what was built, and the results."""

    board: Any
    run: Run

    def failed(self) -> set[str]:
        """Return the names of the checks that failed or errored, module left off."""
        return {
            key.split("::")[1]
            for key, entry in self.run.checks.items()
            if entry["outcome"] in ("failed", "error")
        }

    def names(self) -> set[str]:
        """Return the names of every check that ran, module left off."""
        return {key.split("::")[1] for key in self.run.checks}

    def planted(self, *names: str) -> None:
        """Assert that exactly the checks called ``names`` failed (exit status 1)."""
        assert self.failed() == set(names), (self.run.outcomes(), self.run.tail())
        assert self.run.done.returncode == 1, self.run.tail()


class Lab:
    """Build variants of a control project and run the checks on each, once.

    ``build(folder, variant)`` writes the project of a variant into ``folder`` and
    returns what a test wants to keep of it (its board, say).
    """

    def __init__(self, root: Path, build: Callable[[Path, Any], Any]) -> None:
        """Keep each variant's folder under ``root``."""
        self.root = root
        self.build = build
        self.done: dict[str, Result] = {}

    def result(self, name: str, variant: Any = None) -> Result:
        """Return the run of the variant called ``name``, building it the first time."""
        if name not in self.done:
            folder = self.root / name
            board = self.build(folder, variant)
            self.done[name] = Result(board, run(folder))
        return self.done[name]
