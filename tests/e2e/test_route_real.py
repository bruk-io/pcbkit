"""End to end: route, promote, finalize and eco on a small board, with the real router.

Real KiCad (pcbnew and kicad-cli), real Java and Freerouting, on the board of
tests/route_project.py. The silkscreen and the fab export belong to other work packages,
so they are stood in for: the silkscreen by a pass that hides every part's text (a
board with no labels has no silkscreen violations), the export by a recorder.

Run it, after `pcbkit setup` or with FREEROUTING_JAR set, with the KiCad environment:

    .venv-kicad/bin/python -m pytest tests/e2e -m e2e -q
"""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

import numpy  # noqa: E402, F401  (loaded now, so restored_imports never unloads it)
import scipy.spatial  # noqa: E402, F401

from pcbkit import sch  # noqa: E402
from pcbkit.cli import cli  # noqa: E402
from pcbkit.kicad import cli as kicad_cli  # noqa: E402
from pcbkit.kicad import env  # noqa: E402
from pcbkit.project import Project  # noqa: E402
from pcbkit.route import flow, freerouting  # noqa: E402
from pcbkit.route.files import route_files  # noqa: E402
from tests import route_project as rp  # noqa: E402
from tests.board_files import restored_imports  # noqa: E402

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        env.find_freerouting_jar() is None or env.find_java() is None,
        reason="needs Java and the Freerouting jar (pcbkit setup, or FREEROUTING_JAR)",
    ),
]


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep a test's project modules from outliving it."""
    with restored_imports():
        yield


class Recorder:
    """Stands in for the silkscreen and the fab export, and records their calls."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.silk: list[Path] = []
        self.exports: list[bool] = []

    def apply_silk(self, proj: Project, pcb: Path | None = None) -> None:
        """Hide every part's text on the finished board, as a labelless silkscreen."""
        path = pcb or route_files(proj).pcb
        board = pcbnew.LoadBoard(str(path))
        for footprint in board.GetFootprints():
            footprint.Reference().SetVisible(False)
            footprint.Value().SetVisible(False)
        pcbnew.SaveBoard(str(path), board)
        # SaveBoard writes the weight it was loaded with, so nothing else to restore
        self.silk.append(path)

    def export_fab(self, proj: Project, render: bool = True) -> Any:
        """Record the export and its render flag."""
        self.exports.append(render)
        return SimpleNamespace(bom_lines=6, total_parts=6, files=[])


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Install the stand-ins for pcbkit.silk and pcbkit.fab."""
    rec = Recorder()
    silk = types.ModuleType("pcbkit.silk")
    silk.apply_silk = rec.apply_silk  # type: ignore[attr-defined]
    fab = types.ModuleType("pcbkit.fab")
    fab.export_fab = rec.export_fab  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pcbkit.silk", silk)
    monkeypatch.setitem(sys.modules, "pcbkit.fab", fab)
    return rec


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """Write the board's project, build its schematic and place its board."""
    proj = rp.write_project(tmp_path / "board")
    assert sch.build_schematic(proj).erc.errors == 0
    rp.place(proj)
    return proj


def drc_counts(proj: Project) -> tuple[int, int, int]:
    """Return DRC (with schematic parity) of the finished board: its three totals."""
    files = route_files(proj)
    report = kicad_cli.drc(files.pcb, files.drc, schematic_parity=True).report
    return report.drc_violations, report.unconnected, report.footprint_errors


def test_route_promote_finalize_and_eco_on_a_small_board(
    project: Project, recorder: Recorder
) -> None:
    """Route clean, keep the route, rebuild it from golden, then change a part."""
    said: list[str] = []
    result = flow.route(project, say=said.append)
    text = "\n".join(said)
    assert result.clean, text
    assert [a.status for a in result.loop.attempts][-1] == "clean"
    assert recorder.silk == [route_files(project).pcb]
    assert drc_counts(project) == (0, 0, 0), text

    # keep the route: golden/ now holds what finalize rebuilds from
    flow.promote(project, say=said.append)
    golden = project.golden_dir
    assert sorted(p.name for p in golden.iterdir()) == [
        "my_board.dsn",
        "my_board.ses",
        "prerouted.kicad_pcb",
        "prerouted.kicad_pro",
    ]

    # finalize from golden into a clean kicad/ folder: the same board, the same DRC
    for stale in ("my_board.kicad_pcb", "my_board.ses", "prerouted.kicad_pcb"):
        (project.kicad_dir / stale).unlink()
    recorder.silk.clear()
    flow.finalize(project, render=False, say=said.append)
    assert recorder.exports == [False]
    assert recorder.silk == [route_files(project).pcb]
    assert drc_counts(project) == (0, 0, 0)

    # change a part and route again, keeping what was routed: eco
    positions = dict(rp.layout_positions(project))
    positions["R3"] = (22.0, 22.0, 0)
    rp.place(project, positions)
    said.clear()
    again = flow.route(project, eco=golden, say=said.append)
    assert again.eco is not None and again.eco.changed == ("R3",), "\n".join(said)
    assert again.eco.kept > 0
    assert again.clean, "\n".join(said)
    assert drc_counts(project) == (0, 0, 0)


def test_the_three_commands_through_the_cli(
    project: Project, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run route, promote and finalize as the user does, from the project folder."""
    monkeypatch.chdir(project.root)
    routed = CliRunner().invoke(cli, ["route"])
    assert routed.exit_code == 0, routed.output
    assert "clean" in routed.output
    assert "DRC: 0 violations, 0 unconnected pads, 0 footprint errors" in routed.output

    promoted = CliRunner().invoke(cli, ["promote"])
    assert promoted.exit_code == 0, promoted.output
    assert "promoted to golden/" in promoted.output

    finalized = CliRunner().invoke(cli, ["finalize", "--no-render"])
    assert finalized.exit_code == 0, finalized.output
    assert "BOM lines: 6 total parts: 6" in finalized.output
    assert recorder.exports == [False]


def test_a_stalled_eco_run_falls_back_to_the_whole_board(
    project: Project, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make the first Freerouting run hang, and see the whole board routed instead."""
    said: list[str] = []
    assert flow.route(project, say=said.append).clean, "\n".join(said)
    flow.promote(project, say=said.append)
    real = freerouting.run_router
    runs: list[str] = []

    def hang(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        """Start a program that loads, prints nothing more, and never routes."""
        script = (
            "import time; print('INFO  Opening my_board.dsn', flush=True); "
            "time.sleep(99)"
        )
        return subprocess.Popen([sys.executable, "-u", "-c", script], **kwargs)

    def first_run_hangs(
        files: Any, router: Any, passes: int, stall_timeout_s: float
    ) -> Any:
        runs.append("run")
        if len(runs) == 1:
            return real(files, router, passes, 1.0, spawn=hang)
        return real(files, router, passes, stall_timeout_s)

    monkeypatch.setattr(freerouting, "run_router", first_run_hangs)
    said.clear()
    result = flow.route(project, eco=project.golden_dir, tries=1, say=said.append)
    text = "\n".join(said)
    assert "the eco run stalled" in text
    assert "routing the whole board instead" in text
    assert result.loop.fell_back
    assert result.clean, text
    assert len(runs) == 2


def test_a_board_that_cannot_be_routed_reports_every_try(
    project: Project, recorder: Recorder
) -> None:
    """Give up honestly: wall off a pad with a keep-out, and read the categories."""
    path = project.root / "routing.py"
    text = path.read_text("utf-8")
    # a keep-out over J2's two pads on both layers: SENSE can never reach it
    text = text.replace(
        "api.keepout(board, 30.0, 4.0, 34.0, 6.0, vias=False, pours=False)",
        "api.keepout(board, 28.0, 16.5, 34.0, 19.5, pours=False)",
    )
    path.write_text(text, "utf-8")
    said: list[str] = []
    result = flow.route(project, tries=2, say=said.append)
    out = "\n".join(said)
    assert not result.clean, out
    assert len(result.loop.attempts) == 2
    assert "gave up after 2 tries" in out
    assert "unconnected_items" in out
    assert result.loop.best is not None
