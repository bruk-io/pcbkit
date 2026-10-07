"""Integration: the ``kicad`` check group on a real board, with real kicad-cli.

The tiny board of tests/tiny_board.py is put in a project's ``kicad/`` folder and the
real check command line (``pcbkit.check.runner.command``) runs its ERC and DRC checks in
a child pytest. KiCad is not shy about the tiny board's two isolated pin labels, so the
ERC check fails on it, with those warnings in its message; the DRC check, with schematic
parity, passes on it, and fails once a mistake is planted on the board alone.

These need KiCad's own Python (``import pcbnew``) to build the board: see
tests/integration/test_kicad_core.py for how to make ``.venv-kicad``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit.check import runner  # noqa: E402
from pcbkit.check.results import RESULTS_FILE  # noqa: E402
from pcbkit.kicad import board as kb  # noqa: E402
from tests import tiny_board  # noqa: E402

pytestmark = pytest.mark.kicad

TOML = """\
[board]
stem = "tiny"
title = "Tiny Board"
rev = "A"
fab_name = "Tiny_revA"

[checks]
groups = ["kicad"]
"""


@pytest.fixture
def tiny_project(tmp_path: Path) -> Path:
    """Return a project whose kicad/ folder holds the tiny board and its schematic."""
    root = tmp_path / "tiny_project"
    (root / "kicad").mkdir(parents=True)
    (root / "pcbkit.toml").write_text(TOML, encoding="utf-8")
    tiny_board.build(root / "kicad")
    return root


def run_checks(root: Path) -> dict[str, dict[str, Any]]:
    """Run the project's checks in a child pytest and return its results by check id."""
    command = runner.command(root, None, ["-q"])
    done = subprocess.run(
        command, cwd=root, capture_output=True, text=True, timeout=600
    )
    path = root / "out" / "checks" / RESULTS_FILE
    assert path.is_file(), done.stdout + done.stderr
    return json.loads(path.read_text(encoding="utf-8"))["checks"]


def entry(checks: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    """Return the entry of the built-in kicad check called ``name``."""
    return checks[f"pcbkit.check.builtin.test_kicad::{name}"]


def test_drc_with_parity_passes_on_the_tiny_board_and_erc_reports_its_warnings(
    tiny_project: Path,
) -> None:
    """Pass the DRC check, record its counts, and fail ERC on the isolated labels."""
    checks = run_checks(tiny_project)
    drc = entry(checks, "test_drc_clean_with_schematic_parity")
    assert drc["outcome"] == "passed"
    assert drc["numbers"]["drc"] == {
        "DRC violations": 0,
        "unconnected pads": 0,
        "Footprint errors": 0,
    }
    erc = entry(checks, "test_erc_clean")
    assert erc["outcome"] == "failed"
    assert erc["numbers"]["erc"] == "ERC messages: 2  Errors 0  Warnings 2"
    assert "isolated_pin_label" in erc["message"]


def test_drc_check_fails_on_a_net_changed_on_the_board_alone(
    tiny_project: Path,
) -> None:
    """Fail the DRC check, naming the conflict, when the board and schematic differ."""
    pcb = tiny_project / "kicad" / "tiny.kicad_pcb"
    board = pcbnew.LoadBoard(str(pcb))
    kb.pad(board, "R1", 1).SetNet(kb.N(board, "GND"))
    pcbnew.SaveBoard(str(pcb), board)
    drc = entry(run_checks(tiny_project), "test_drc_clean_with_schematic_parity")
    assert drc["outcome"] == "failed"
    assert drc["numbers"]["drc"]["Footprint errors"] >= 1
    assert "net_conflict" in drc["message"]


def test_a_missing_board_fails_the_drc_check_naming_the_file(
    tiny_project: Path,
) -> None:
    """Say which file is missing, rather than hand kicad-cli a path that is absent."""
    (tiny_project / "kicad" / "tiny.kicad_pcb").unlink()
    drc = entry(run_checks(tiny_project), "test_drc_clean_with_schematic_parity")
    assert drc["outcome"] == "failed"
    assert "tiny.kicad_pcb not found" in drc["message"]
