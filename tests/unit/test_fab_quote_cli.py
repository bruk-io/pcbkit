"""Unit tests for `pcbkit quote`: its options, its failures and what it prints.

The fab files are made up (tests/fab_files.py), and the project is a throwaway folder
the command is run in, so nothing real is touched. Whether the command stays tier 1,
without pcbnew or openpyxl, is checked in a fresh Python.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from pcbkit.cli import cli
from tests.board_files import write_file, write_project
from tests.fab_files import bom_line, job_file, write_fab_outputs

LINES = [
    bom_line(1, 3, "C1,C2,C3"),
    bom_line(2, 1, "U1", "QFN-16_3x3mm"),
    bom_line(3, 2, "J1,J2", "Header_1x03", through_hole=True),
]


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make a board project with exported fab files, and work inside it."""
    write_project(tmp_path, "x = 1\n")
    write_fab_outputs(tmp_path / "out" / "fab", LINES)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def quote(*args: str) -> Result:
    """Run `pcbkit quote` with ``args`` in the current folder."""
    return CliRunner().invoke(cli, ["quote", *args])


def test_with_no_options_it_prints_the_bare_board_only(board: Path) -> None:
    result = quote()
    assert result.exit_code == 0, result.output
    assert "Bare board" in result.output
    assert "Board size           30 x 20 mm" in result.output
    assert "Assembly" not in result.output
    assert "Quantity             not given (use --fab-qty N)" in result.output


def test_assembled_adds_the_assembly_numbers(board: Path) -> None:
    result = quote("--assembled", "2", "--fab-qty", "5")
    assert result.exit_code == 0, result.output
    assert "Quantity             5" in result.output
    assert "Assembly, 2 boards (the counts are per board)" in result.output
    assert "Unique parts         3" in result.output
    assert "SMD placements       4" in result.output
    assert "BGA/QFP/QFN parts    1: U1" in result.output
    assert "Through-hole parts   2 parts, 2 designators: J1, J2" in result.output


def test_self_solder_tht_leaves_the_through_hole_lines_out(board: Path) -> None:
    result = quote("--assembled", "2", "--self-solder-tht")
    assert result.exit_code == 0, result.output
    assert "Unique parts         2 (surface-mount lines only)" in result.output
    assert "Through-hole parts   0 for PCBWay (you solder them)" in result.output
    assert "You solder           2 parts, 2 designators: J1, J2" in result.output


def test_self_solder_tht_without_assembled_is_a_usage_error(board: Path) -> None:
    result = quote("--self-solder-tht")
    assert result.exit_code == 2
    assert (
        "--self-solder-tht only applies to assembly: add --assembled N" in result.output
    )


def test_more_assembled_than_made_is_a_usage_error(board: Path) -> None:
    result = quote("--assembled", "6", "--fab-qty", "5")
    assert result.exit_code == 2
    assert "--assembled 6 is more than --fab-qty 5" in result.output


def test_notes_that_fit_are_counted(board: Path) -> None:
    write_file(board / "notes.txt", "x" * 600 + "\n")
    result = quote("--notes", "notes.txt")
    assert result.exit_code == 0, result.output
    assert "Notes: 600 of 600 characters" in result.output


def test_notes_one_character_over_fail_with_the_count_and_print_no_quote(
    board: Path,
) -> None:
    write_file(board / "notes.txt", "x" * 601)
    result = quote("--assembled", "2", "--notes", "notes.txt")
    assert result.exit_code == 1
    assert "Error: the notes in notes.txt are 601 characters" in result.output
    assert "(1 too many)" in result.output
    assert "Bare board" not in result.output


def test_a_notes_file_that_is_not_there_is_an_error(board: Path) -> None:
    result = quote("--notes", "missing.txt")
    assert result.exit_code == 1
    assert "cannot read the notes file missing.txt" in result.output


def test_with_no_fab_files_it_says_to_run_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(tmp_path, "x = 1\n")
    monkeypatch.chdir(tmp_path)
    result = quote()
    assert result.exit_code == 1
    assert "Run `pcbkit finalize` first" in result.output


def test_outside_a_project_it_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = quote()
    assert result.exit_code == 1
    assert "no pcbkit.toml" in result.output


def test_a_warning_is_printed_after_the_numbers_and_does_not_fail_the_command(
    board: Path,
) -> None:
    write_fab_outputs(board / "out" / "fab", LINES, job=job_file(copper=(0.07, 0.07)))
    result = quote()
    assert result.exit_code == 0
    assert (
        result.output.rstrip().splitlines()[-1].startswith("WARNING: the Gerbers say")
    )


# --- tier 1 --------------------------------------------------------------------------

TIER1_CHECK = textwrap.dedent(
    """
    import sys
    import pcbkit.cli
    assert "pcbkit.fab" not in sys.modules, "importing the CLI imported pcbkit.fab"
    from click.testing import CliRunner
    result = CliRunner().invoke(pcbkit.cli.cli, ["quote", "--assembled", "1"])
    assert result.exit_code == 0, result.output
    for heavy in ("pcbnew", "openpyxl", "numpy", "scipy"):
        assert heavy not in sys.modules, heavy + " was imported"
    print("tier 1 ok")
    """
)


def test_quote_runs_without_pcbnew_openpyxl_numpy_or_scipy(board: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-c", TIER1_CHECK],
        cwd=board,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "tier 1 ok" in done.stdout
