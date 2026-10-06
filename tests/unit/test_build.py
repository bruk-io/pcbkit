"""Unit tests for `pcbkit build`: what it runs, in what order, and what it prints.

The two stages it chains, the schematic build and the placement, are replaced by fakes
that record their calls: each has its own tests (test_sch.py, test_place.py, and the
real-KiCad ones under tests/integration). What is tested here is the command itself.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import click
import pytest
from click.testing import CliRunner, Result

from pcbkit import place, sch
from pcbkit.cli import cli
from pcbkit.kicad.cli import ErcReport, Violation
from pcbkit.libs import ProjectLibs
from pcbkit.place import PlaceResult
from pcbkit.project import Project, load_project
from tests.board_files import TOML, write_file


def erc_report(errors: int = 0, warnings: int = 0) -> ErcReport:
    """Return an ERC report with the given counts and one finding per message."""
    found = tuple(
        Violation(
            "pin_not_connected",
            "Pin not connected",
            "error" if n < errors else "warning",
            "",
            "/",
            ((10.5, 20.0),),
            (),
            ("Symbol R1 Pin 1",),
        )
        for n in range(errors + warnings)
    )
    summary = f"ERC messages: {errors + warnings}  Errors {errors}  Warnings {warnings}"
    return ErcReport(errors + warnings, errors, warnings, summary, found)


class Stages:
    """Stands in for the schematic build and the placement; records what was called."""

    def __init__(self, erc: ErcReport, missing: list[str]) -> None:
        """Remember what the fakes should report."""
        self.erc = erc
        self.missing = missing
        self.calls: list[str] = []

    def build_schematic(self, proj: Project) -> sch.SchematicResult:
        """Record the call and return a result for ``proj``."""
        self.calls.append("sch")
        return sch.SchematicResult(
            schematic=proj.kicad_dir / "my_board.kicad_sch",
            netlist=proj.kicad_dir / "my_board.net",
            erc_report=proj.kicad_dir / "erc.rpt",
            parts=3,
            libs=ProjectLibs("my_board"),
            erc=self.erc,
        )

    def place_board(self, proj: Project) -> PlaceResult:
        """Record the call and return what a placement of 3 parts would."""
        self.calls.append("place")
        return PlaceResult(proj.kicad_dir / "my_board.kicad_pcb", 3, self.missing)


@pytest.fixture
def stages(monkeypatch: pytest.MonkeyPatch, fake_pcbnew: types.ModuleType) -> Stages:
    """Replace the two stages, and let pcbnew import (the fake one is installed)."""
    fake = Stages(erc_report(), [])
    monkeypatch.setattr(sch, "build_schematic", fake.build_schematic)
    monkeypatch.setattr(place, "place_board", fake.place_board)
    return fake


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make a project folder the current directory."""
    write_file(tmp_path / "pcbkit.toml", TOML)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def build() -> Result:
    """Run `pcbkit build` in the current directory."""
    return CliRunner().invoke(cli, ["build"])


def test_a_clean_build_runs_the_schematic_then_the_placement_and_says_what_it_did(
    stages: Stages, project_dir: Path
) -> None:
    """Print the ERC line and the placement line, in that order, and exit 0."""
    result = build()
    assert result.exit_code == 0, result.output
    assert stages.calls == ["sch", "place"]
    assert result.output.splitlines() == [
        "ERC        0 errors, 0 warnings (kicad/erc.rpt)",
        "placed 3, missing: []",
    ]


def test_a_part_without_a_position_is_listed_with_what_to_do(
    stages: Stages, project_dir: Path
) -> None:
    """Name each parked part, and say where to give it a position."""
    stages.missing = ["R2", "U1"]
    result = build()
    assert result.exit_code == 0
    assert "placed 3, missing: ['R2', 'U1']" in result.output
    assert "parked below the board" in result.output
    assert "layout.py" in result.output


def test_erc_errors_are_listed_the_board_is_placed_and_the_exit_code_is_1(
    stages: Stages, project_dir: Path
) -> None:
    """Carry on as build.sh did, but do not report success."""
    stages.erc = erc_report(errors=1, warnings=1)
    result = build()
    assert result.exit_code == 1
    assert stages.calls == ["sch", "place"]
    assert "ERC        1 error, 1 warning (kicad/erc.rpt)" in result.output
    assert "  error [pin_not_connected] Pin not connected" in result.output
    assert "    @(10.50 mm, 20.00 mm): Symbol R1 Pin 1" in result.output
    assert "placed 3, missing: []" in result.output
    assert result.output.rstrip().endswith("fix them in design.py before routing.")


def test_erc_warnings_alone_do_not_fail_the_build(
    stages: Stages, project_dir: Path
) -> None:
    """Exit 0 with a warning, as `pcbkit sch` does."""
    stages.erc = erc_report(warnings=1)
    result = build()
    assert result.exit_code == 0
    assert "  warning [pin_not_connected]" in result.output


def test_without_pcbnew_the_command_says_how_to_get_it_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, project_dir: Path
) -> None:
    """Check pcbnew first: a tier 2 command must not get as far as the schematic."""
    calls: list[str] = []
    monkeypatch.setattr(sch, "build_schematic", lambda proj: calls.append("sch"))
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    result = build()
    assert result.exit_code == 1
    assert "pcbnew isn't importable here" in result.output
    assert "pcbkit setup" in result.output
    assert calls == []
    assert "Traceback" not in result.output


def test_outside_a_project_the_command_says_what_to_do(
    stages: Stages, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name the missing pcbkit.toml, with exit code 1."""
    monkeypatch.chdir(tmp_path)
    result = build()
    assert result.exit_code == 1
    assert "no pcbkit.toml" in result.output
    assert stages.calls == []


def test_a_message_from_the_placement_is_printed_not_a_traceback(
    stages: Stages, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show a project mistake found while placing as one error line."""

    def refuse(proj: Project) -> PlaceResult:
        raise click.ClickException("layout.py: W is missing (board size in mm)")

    monkeypatch.setattr(place, "place_board", refuse)
    result = build()
    assert result.exit_code == 1
    assert "Error: layout.py: W is missing" in result.output
    assert "Traceback" not in result.output


def test_the_command_uses_the_project_found_from_the_current_folder(
    stages: Stages, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find pcbkit.toml above the current folder, as every command does."""
    seen: list[Path] = []

    def spy(proj: Project) -> PlaceResult:
        seen.append(proj.root)
        return PlaceResult(proj.kicad_dir / "x.kicad_pcb", 0, [])

    monkeypatch.setattr(place, "place_board", spy)
    inner = project_dir / "checks" / "deep"
    inner.mkdir(parents=True)
    monkeypatch.chdir(inner)
    assert build().exit_code == 0
    assert seen == [load_project(project_dir).root]
