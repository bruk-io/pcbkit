"""Integration: `pcbkit sch` on a tiny board, with the real kicad-cli.

These tests need KiCad 10 installed, so they are marked ``kicad`` and are not part of
the CI run. They need kicad-cli and KiCad's stock libraries, not pcbnew. They do not
request the ``machine`` fixture, so they see the real PATH, the real /Applications and
the real HOME.

The board is tests/fixtures/tiny_board: a header, a resistor and an LED, where the
header and the resistor use a symbol and footprints that the board builds itself (from
footprints.py and the footprints/ folder), so that a clean ERC also proves KiCad found
the project's libraries.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from pcbkit import libs, sch
from pcbkit.cli import cli
from pcbkit.design import load_design
from pcbkit.kicad import env
from pcbkit.project import load_project
from tests.board_files import restored_imports
from tests.netlist_norm import normalise

pytestmark = pytest.mark.kicad

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_board"


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep design and footprints modules from outliving a test."""
    with restored_imports():
        yield


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the tiny board into a temporary folder, make it current and return it."""
    root = tmp_path / "tiny_board"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("expected_*"))
    monkeypatch.chdir(root)
    return root


def run_sch() -> Result:
    """Run `pcbkit sch` in the current directory."""
    return CliRunner().invoke(cli, ["sch"])


def test_sch_gives_a_clean_erc_and_the_expected_netlist(board: Path) -> None:
    """Pass ERC with no findings and export exactly the nets the design describes."""
    result = run_sch()
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "libraries  tiny: 1 symbol, 2 footprints",
        "schematic  kicad/tiny_board.kicad_sch (3 parts)",
        "ERC        0 errors, 0 warnings (kicad/erc.rpt)",
        "netlist    kicad/tiny_board.net",
    ]
    report = (board / "kicad" / "erc.rpt").read_text(encoding="utf-8")
    assert "ERC messages: 0  Errors 0  Warnings 0" in report
    expected = json.loads((FIXTURE / "expected_netlist.json").read_text())
    assert normalise(board / "kicad" / "tiny_board.net") == expected


def test_the_projects_own_footprints_reach_the_library_unchanged(board: Path) -> None:
    """Copy the vendored footprint byte for byte and write the generated one."""
    assert run_sch().exit_code == 0
    pretty = board / "kicad" / "tiny.pretty"
    assert sorted(p.name for p in pretty.iterdir()) == [
        "Header_1x02_P2.54mm.kicad_mod",
        "R_Vendored.kicad_mod",
    ]
    vendored = board / "footprints" / "R_Vendored.kicad_mod"
    assert (pretty / "R_Vendored.kicad_mod").read_bytes() == vendored.read_bytes()


def test_a_second_run_changes_nothing_but_the_dates(board: Path) -> None:
    """Rebuild the same schematic, down to every UUID, from the same design."""
    assert run_sch().exit_code == 0
    first = (board / "kicad" / "tiny_board.kicad_sch").read_text()
    first_net = normalise(board / "kicad" / "tiny_board.net")
    assert run_sch().exit_code == 0
    assert (board / "kicad" / "tiny_board.kicad_sch").read_text() == first
    assert normalise(board / "kicad" / "tiny_board.net") == first_net


def test_an_erc_error_exits_1_and_is_listed(board: Path) -> None:
    """Plant a real ERC error, two power flags driving one net, and fail on it."""
    with open(board / "design.py", "a", encoding="utf-8") as handle:
        handle.write(
            "\nfor ref in ('#FLG01', '#FLG02'):\n"
            "    part(ref, 'power:PWR_FLAG', 'PWR_FLAG', '', {'1': 'GND'},\n"
            "         block=B, bom=False)\n"
        )
    result = run_sch()
    assert result.exit_code == 1, result.output
    assert "ERC        1 error, 0 warnings (kicad/erc.rpt)" in result.output
    assert "[pin_to_pin] Pins of type Power output and Power output" in result.output
    assert "#FLG01" in result.output
    assert (board / "kicad" / "tiny_board.net").is_file()


def test_a_design_that_names_a_pin_the_symbol_lacks_is_a_message(board: Path) -> None:
    """Fail with the part and pin in the message, not a traceback."""
    text = (board / "design.py").read_text(encoding="utf-8")
    (board / "design.py").write_text(
        text.replace('{"1": "+3V3", "2": "GND"}', '{"1": "+3V3", "3": "GND"}')
    )
    result = run_sch()
    assert result.exit_code == 1
    assert "J1: pin 3 not in symbol tiny:Header_1x02" in result.output


# The controls below show why pcbkit writes the library tables and a project file
# before it runs ERC: kicad-cli cannot see the project's own libraries without them.
# If a future KiCad stops needing them, these fail and that code can go.


def prepare(board: Path) -> tuple[Path, str]:
    """Do what `pcbkit sch` does before ERC; return the schematic and kicad-cli."""
    proj = load_project(board)
    design = load_design(board / "design.py")
    made = libs.write_project_libs(proj)
    schematic = sch.write_schematic(
        design, proj.config.board, proj.kicad_dir, env.symbols_dir(), made.symbol_libs
    )
    tool = env.find_kicad_cli()
    assert tool is not None
    return schematic, tool.path


def test_control_with_everything_in_place_erc_is_clean(board: Path) -> None:
    """Show the baseline the next controls are measured against."""
    schematic, cli_path = prepare(board)
    report = sch.run_erc(cli_path, schematic, board / "kicad" / "erc.rpt")
    assert (report.errors, report.warnings) == (0, 0)


@pytest.mark.parametrize(
    ("missing", "codes"),
    [
        # no project file: kicad-cli ignores both tables, so the symbol is lost too
        ("tiny_board.kicad_pro", {"footprint_link_issues", "lib_symbol_issues"}),
        ("fp-lib-table", {"footprint_link_issues"}),
    ],
)
def test_control_without_the_project_file_or_the_table_erc_loses_the_library(
    board: Path, missing: str, codes: set[str]
) -> None:
    """See ERC warn that the project's library is missing."""
    schematic, cli_path = prepare(board)
    (board / "kicad" / missing).unlink()
    report = sch.run_erc(cli_path, schematic, board / "kicad" / "erc.rpt")
    assert report.errors == 0
    assert report.warnings >= 1
    assert {v.code for v in report.violations} == codes
    assert any("'tiny'" in v.message for v in report.violations)
