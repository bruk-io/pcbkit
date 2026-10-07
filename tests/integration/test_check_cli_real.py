"""Integration: `pcbkit check`, `pcbkit mutants` and `pcbkit report` with real KiCad.

The project is tests/fixtures/tiny_board (a header, a resistor and an LED; `pcbkit sch`
gives it a clean ERC) with two checks of its own on the netlist and a `mutants.py`. The
commands run through click, and start child pytest runs and the real schematic
generator: a mistake is planted in design.py in a scratch copy, the schematic is made
again, and the check that names it has to fail.

They need KiCad's own Python (``import pcbnew``), because the commands are tier 2:
see tests/integration/test_kicad_core.py for how to make ``.venv-kicad``.
"""

from __future__ import annotations

import shutil
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit.cli import cli  # noqa: E402
from tests.board_files import restored_imports  # noqa: E402

pytestmark = pytest.mark.kicad

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_board"

CHECKS = """\
def test_resistor_is_on_the_rail(nl, record):
    record("r1 pin 1 net", nl.net("R1", 1))
    assert nl.net("R1", 1) == "+3V3"


def test_led_anode_is_fed_by_the_resistor(nl):
    assert nl.net("D1", 2) == nl.net("R1", 2) == "LED_A"
"""

RESISTOR = 'R("R1", "330", "+3V3", "LED_A", B, fp="tiny:R_Vendored")'
RESISTOR_OFF_THE_RAIL = 'R("R1", "330", "NC_X", "LED_A", B, fp="tiny:R_Vendored")'
WRONG = """

def test_wrong_from_the_start(nl):
    assert nl.net("R1", 1) == "GND"
"""
LED = 'LED("D1", "Green", "LED_A", "GND"'
LED_REVERSED = 'LED("D1", "Green", "GND", "LED_A"'


def write_mutants(root: Path, extra: str = "") -> None:
    """Write the project's mutants.py: two real mistakes, and ``extra`` entries."""
    (root / "mutants.py").write_text(
        textwrap.dedent(
            f"""\
            MUTANTS = [
                ("resistor taken off the rail",
                 [({RESISTOR!r}, {RESISTOR_OFF_THE_RAIL!r})],
                 "test_resistor_is_on_the_rail"),
                ("LED turned round",
                 [({LED!r}, {LED_REVERSED!r})],
                 "test_led_anode_is_fed_by_the_resistor"),
            ]
            {extra}
            """
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep the project's modules from outliving a test."""
    with restored_imports():
        yield


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the tiny board with two checks and a mutants.py; make it current."""
    root = tmp_path / "tiny_board"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("expected_*"))
    # no built-in group: there is no board yet, only the schematic the checks read
    with open(root / "pcbkit.toml", "a", encoding="utf-8") as handle:
        handle.write("\n[checks]\ngroups = []\n")
    (root / "checks").mkdir()
    (root / "checks" / "test_wiring.py").write_text(CHECKS, encoding="utf-8")
    write_mutants(root)
    monkeypatch.chdir(root)
    return root


def invoke(*args: str) -> Result:
    """Run the pcbkit CLI in-process with ``args``."""
    return CliRunner().invoke(cli, list(args))


def test_check_without_a_schematic_says_to_make_one_first(board: Path) -> None:
    """Refuse to run, naming the file and the command that makes it."""
    result = invoke("check")
    assert result.exit_code == 1
    assert "tiny_board.kicad_sch not found" in result.output
    assert "pcbkit sch" in result.output


def test_check_then_report_on_a_real_schematic(board: Path) -> None:
    """Run both checks against a fresh KiCad netlist, then write the report."""
    assert invoke("sch").exit_code == 0
    checked = invoke("check")
    assert checked.exit_code == 0, checked.output
    assert "Results: out/checks/results.json (pcbkit report)" in checked.output
    reported = invoke("report")
    assert reported.exit_code == 0, reported.output
    assert reported.output.splitlines()[0].startswith("2 passed")
    text = (board / "out" / "checks" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "| test_resistor_is_on_the_rail | pass | r1 pin 1 net: +3V3 |" in text
    assert "| test_led_anode_is_fed_by_the_resistor | pass |  |" in text


def test_check_k_selects_one_check(board: Path) -> None:
    """Run only the check the -k expression matches."""
    assert invoke("sch").exit_code == 0
    assert invoke("check", "-k", "rail").exit_code == 0
    report = invoke("report")
    assert report.output.splitlines()[0].startswith("1 passed")


def test_mutants_catches_each_planted_mistake_and_the_control_passes(
    board: Path,
) -> None:
    """Plant each mistake in a scratch copy and see the check that names it fail."""
    result = invoke("mutants")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith("control (no edits)")
    assert " PASSES  2 passed" in lines[0]
    assert "resistor taken off the rail" in lines[1] and "CAUGHT" in lines[1]
    assert "1 failed" in lines[1]
    assert "LED turned round" in lines[2] and "CAUGHT" in lines[2]
    assert "2/2 planted mistakes caught" in result.output
    # the project itself was not touched: its design still has the unmutated resistor
    assert RESISTOR in (board / "design.py").read_text(encoding="utf-8")


def test_mutants_counts_a_stale_entry_as_missed(board: Path) -> None:
    """Miss an entry whose text is no longer in design.py, and say why."""
    write_mutants(
        board,
        'MUTANTS.append(("stale", [("text that is gone", "x")], '
        '"test_resistor_is_on_the_rail"))',
    )
    result = invoke("mutants")
    assert result.exit_code == 1, result.output
    assert (
        "MISSED  (SETUP ERROR: pattern not found in design.py: text that is gone)"
        in (result.output)
    )
    assert "2/3 planted mistakes caught" in result.output


def test_mutants_stops_when_the_control_does_not_pass(board: Path) -> None:
    """Refuse to judge any mutant if the unedited project already fails a check."""
    (board / "checks" / "test_wiring.py").write_text(
        CHECKS + WRONG,
        encoding="utf-8",
    )
    write_mutants(
        board,
        'MUTANTS.append(("the failing check", [], "test_wrong_from_the_start"))',
    )
    result = invoke("mutants")
    assert result.exit_code == 2, result.output
    assert "FAILS" in result.output
    assert "control run did not pass cleanly" in result.output
    assert "CAUGHT" not in result.output
