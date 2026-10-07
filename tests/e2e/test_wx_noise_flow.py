"""End to end: the whole board flow prints no wx noise, and keeps what it prints.

`pcbkit route` and `pcbkit finalize` each loaded boards four times and showed 49 lines
of wxWidgets assertion and debug output on standard error around their own. This runs
the flow as a user does, one command after another in a child process, on the board of
tests/route_project.py with the real router, and reads each command's real standard
error: it must be empty, and the real output must still be there.

Run it, after `pcbkit setup` or with FREEROUTING_JAR set, with the KiCad environment:

    .venv-kicad/bin/python -m pytest tests/e2e -m e2e -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit.kicad import env  # noqa: E402
from tests import route_project as rp  # noqa: E402
from tests.child_process import pcbkit  # noqa: E402

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        env.find_freerouting_jar() is None or env.find_java() is None,
        reason="needs Java and the Freerouting jar (pcbkit setup, or FREEROUTING_JAR)",
    ),
]


def test_build_route_promote_finalize_and_compare_print_nothing_on_standard_error(
    tmp_path: Path,
) -> None:
    root = rp.write_project(tmp_path / "board").root
    said: dict[str, str] = {}

    def step(name: str, *args: str) -> None:
        done = pcbkit(*args, cwd=root, timeout=900)
        assert done.returncode == 0, f"{name}: {done.stdout}\n{done.stderr}"
        assert done.stderr == "", f"{name} wrote to standard error:\n{done.stderr}"
        said[name] = done.stdout

    step("build", "build")
    step("route", "route")
    step("promote", "promote")
    step("finalize", "finalize", "--no-render")
    board = str(root / "kicad" / f"{rp.STEM}.kicad_pcb")
    step("compare", "compare", board, board)

    assert "DRC: 0 violations, 0 unconnected pads, 0 footprint errors" in said["route"]
    assert (
        "DRC: 0 violations, 0 unconnected pads, 0 footprint errors" in said["finalize"]
    )
    assert "BOM lines:" in said["finalize"]
    assert "The boards match: no differences." in said["compare"]
