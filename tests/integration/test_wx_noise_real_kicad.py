"""Integration: pcbnew's wx noise is hidden, and only the noise, with real pcbnew.

On macOS pcbnew writes wxWidgets assertion and debug lines to descriptor 2 when a board
is made or loaded. These tests run pcbkit in a child Python, because descriptor 2 is
what the terminal sees and only a parent process can read it, and they read the child's
real standard error:

* the control: plain pcbnew does print lines, and every one of them is known noise, so a
  line the patterns miss (a new wording, a new call) fails here and says so;
* the wrapped calls print nothing, and still return real boards;
* `pcbkit build` and `pcbkit compare` print nothing on standard error, and everything
  they print on standard output;
* a real error, an unreadable board, still comes through, alone;
* in one call, real pcbnew noise goes and the lines around it stay, even ones that look
  like noise to a loose filter, and what is written just before a crash still arrives.

These tests need KiCad's own Python, where ``import pcbnew`` works. Make that
environment once and run them with it (see tests/integration/test_kicad_core.py):

    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python they are skipped, for the reason given just below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit.kicad import quiet  # noqa: E402
from tests import tiny_board, tiny_project  # noqa: E402
from tests.child_process import pcbkit, run_python  # noqa: E402

pytestmark = pytest.mark.kicad


@pytest.fixture(scope="module")
def board_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the tiny board once and return its file: the tests only read it."""
    return tiny_board.build(tmp_path_factory.mktemp("tiny")).pcb


# Makes a board, then loads one three times: the calls that print noise, in the order
# that pcbkit makes them. The caller says how pcbnew is imported.
LOADS = """
import sys
{import_pcbnew}
board = pcbnew.NewBoard(sys.argv[1] + ".new")
for _ in range(3):
    board = pcbnew.LoadBoard(sys.argv[1])
print(len(list(board.GetFootprints())), "footprints")
"""


def test_control_plain_pcbnew_prints_noise_and_the_patterns_cover_all_of_it(
    board_file: Path,
) -> None:
    done = run_python(LOADS.format(import_pcbnew="import pcbnew"), str(board_file))
    assert done.returncode == 0, done.stderr
    lines = done.stderr.splitlines()
    if not lines:
        pytest.skip("this KiCad prints no wx noise here, so there is nothing to hide")
    # KiCad 10.0.6 prints 25 here (one assertion, twelve lines for each of the two
    # LoadBoard calls after the first). The counts are not asserted: the fix does not
    # depend on them, and a later 10.0.x that adds an image format is still fixed. What
    # it does depend on is that the patterns know every line.
    missed = [line for line in lines if not quiet.is_noise(line.encode())]
    assert not missed, f"pcbnew printed lines that are not known noise: {missed}"


def test_the_wrapped_calls_print_nothing_and_still_return_boards(
    board_file: Path,
) -> None:
    done = run_python(
        LOADS.format(
            import_pcbnew="from pcbkit.kicad import env\npcbnew = env.import_pcbnew()"
        ),
        str(board_file),
    )
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == "2 footprints\n"


def test_build_prints_no_noise_and_all_of_its_output(tmp_path: Path) -> None:
    root = tiny_project.make_project(tmp_path / "board")
    done = pcbkit("build", cwd=root)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert "ERC        0 errors, 0 warnings" in done.stdout
    assert "placed 6, missing: ['D1']" in done.stdout


def test_compare_prints_no_noise_and_its_report(board_file: Path) -> None:
    done = pcbkit("compare", str(board_file), str(board_file), cwd=board_file.parent)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert "The boards match: no differences." in done.stdout


# Lines that are not noise, written between real loads. A filter that dropped every
# "Debug" or "assert" line, as the shell scripts' `grep -v Debug` did, would eat these.
REAL_LINES = [
    "a real error",
    "11:44:35 PM: Debug: unrelated message from another component",
    './src/common/other.cpp(12): assert "x" failed in Foo(): something real',
]

SECTION = """
import os, sys
import pcbnew
from pcbkit.kicad import quiet

with quiet.quiet_stderr():
    for line in sys.argv[2:]:
        pcbnew.LoadBoard(sys.argv[1])  # twelve lines of noise, after the first
        os.write(2, line.encode() + b"\\n")
    pcbnew.LoadBoard(sys.argv[1])
    {ending}
"""


def test_real_noise_goes_and_the_real_lines_around_it_stay(board_file: Path) -> None:
    done = run_python(SECTION.format(ending="pass"), str(board_file), *REAL_LINES)
    assert done.returncode == 0, done.stderr
    assert done.stderr == "".join(line + "\n" for line in REAL_LINES)


def test_a_crash_after_real_noise_keeps_its_last_words(board_file: Path) -> None:
    """Abort inside the call, as a C++ failure in pcbnew would; read the terminal."""
    ending = 'os.write(2, b"last words\\n"); os.abort()'
    done = run_python(SECTION.format(ending=ending), str(board_file), *REAL_LINES)
    assert done.returncode != 0
    assert done.stderr == "".join(line + "\n" for line in REAL_LINES) + "last words\n"


def test_a_real_error_still_comes_through_and_alone(
    board_file: Path, tmp_path: Path
) -> None:
    """Plant a board pcbnew cannot read: pcbkit says so, and nothing else is printed."""
    (tmp_path / "garbage.kicad_pcb").write_text("this is not a board\n")
    done = pcbkit("compare", str(board_file), "garbage.kicad_pcb", cwd=tmp_path)
    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr == "Error: cannot read garbage.kicad_pcb as a KiCad board\n"
