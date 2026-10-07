"""Integration: the blinky example, and a project made from it, with real KiCad.

No router runs: both start from the route that `golden/` keeps. `build` makes the
schematic, ERC, the netlist and the placement; `finalize` rebuilds the board from
`golden/`, adds the silkscreen, runs DRC with schematic parity and exports the fab
files; `check` runs the built-in checks of the groups blinky switches on and its own.
That the example and `pcbkit new` give the same board is what lets the template stand
in for the example. It needs KiCad's own Python (``import pcbnew``) and kicad-cli: see
the head of tests/integration/test_kicad_core.py for how to make .venv-kicad, then run

    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python these tests are skipped. The whole flow with the router, `setup` and
`mutants` is tests/e2e/test_blinky_flow.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

import numpy  # noqa: E402, F401  (loaded now, so restored_imports never unloads it)
import scipy.spatial  # noqa: E402, F401

from pcbkit import compare, scaffold  # noqa: E402
from pcbkit.cli import cli  # noqa: E402
from pcbkit.project import load_project  # noqa: E402
from tests.board_files import restored_imports  # noqa: E402
from tests.scaffold_files import EXAMPLE, materialise  # noqa: E402

pytestmark = pytest.mark.kicad

CLEAN_DRC = "DRC: 0 violations, 0 unconnected pads, 0 footprint errors"
# The two built-in `fab` checks that read tables blinky leaves empty: no IC to
# decouple, no I2C bus.
SKIPPED = {
    "pcbkit.check.builtin.test_fab::test_decoupling_caps_are_close[NOTSET]",
    "pcbkit.check.builtin.test_fab::test_i2c_rise_time[NOTSET]",
}


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep a test's project modules and sys.path entries from outliving it."""
    with restored_imports():
        yield


def example_copy(root: Path) -> Path:
    """Copy the example's files, without what commands generate, into ``root``."""
    return materialise(scaffold.template_files(EXAMPLE), root)


def new_project(parent: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make my-board with `pcbkit new` in ``parent``, and return its folder."""
    parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(parent)
    result = CliRunner().invoke(cli, ["new", "my-board"])
    assert result.exit_code == 0, result.output
    return parent / "my-board"


def run(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> Result:
    """Run a pcbkit command in the project at ``root``, as the user would."""
    monkeypatch.chdir(root)
    return CliRunner().invoke(cli, list(args))


def finalized(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build and finalize the project, and return its finished board."""
    built = run(root, monkeypatch, "build")
    assert built.exit_code == 0, built.output
    assert "ERC        0 errors, 0 warnings" in built.output
    assert "placed 3, missing: []" in built.output
    done = run(root, monkeypatch, "finalize", "--no-render")
    assert done.exit_code == 0, done.output
    assert CLEAN_DRC in done.output
    assert "BOM lines: 3 total parts: 3" in done.output
    assert "warning" not in done.output  # no silkscreen warning, no unorderable part
    stem = load_project(root).config.board.stem
    return root / "kicad" / f"{stem}.kicad_pcb"


@pytest.fixture(params=["the example", "a project made by new"])
def project(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Return the example, or a project made by `pcbkit new`, ready to build."""
    if request.param == "the example":
        return example_copy(tmp_path / "blinky")
    return new_project(tmp_path / "made", monkeypatch)


def test_it_builds_finalizes_from_golden_and_exports_the_fab_files(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Take golden/ to a finished board with DRC 0/0/0, Gerbers, BOM and centroid."""
    finalized(project, monkeypatch)
    config = load_project(project).config.board
    fab = project / "out" / "fab"
    for name in (
        f"{config.fab_name}_gerbers.zip",
        f"{config.fab_name}_BOM.csv",
        f"{config.fab_name}_BOM.xlsx",
        f"{config.fab_name}_centroid.csv",
    ):
        assert (fab / name).is_file(), name
    assert (project / "out" / "docs").is_dir()


def test_check_passes_every_check_and_skips_the_two_with_nothing_to_read(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass 17 checks of the three groups and the two of blinky's own; skip two."""
    finalized(project, monkeypatch)
    result = run(project, monkeypatch, "check")
    assert result.exit_code == 0, result.output
    data = json.loads((project / "out" / "checks" / "results.json").read_text("utf-8"))
    assert data["groups"] == ["kicad", "outputs", "fab"]
    assert data["counts"] == {"passed": 17, "skipped": 2}
    checks: dict[str, Any] = data["checks"]
    assert {k for k, v in checks.items() if v["outcome"] == "skipped"} == SKIPPED
    own = sorted(k.split("::")[1] for k, v in checks.items() if v["group"] == "project")
    assert own == ["test_led_current_window", "test_resistor_power_margin"]
    assert all(v["outcome"] == "passed" for k, v in checks.items() if k not in SKIPPED)


def test_the_led_runs_between_1_and_11_ma_across_the_supply_range(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record the LED's current at each end of the supply, as the README says."""
    finalized(project, monkeypatch)
    assert (
        run(project, monkeypatch, "check", "-k", "test_led_current_window").exit_code
        == 0
    )
    data = json.loads((project / "out" / "checks" / "results.json").read_text("utf-8"))
    (entry,) = data["checks"].values()
    assert entry["numbers"]["LED mA"] == {"D1 dimmest": 1.51, "D1 brightest": 10.88}


def test_the_project_made_by_new_has_the_example_s_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finalize both: no difference in the copper, so the template is the example."""
    example = finalized(example_copy(tmp_path / "blinky"), monkeypatch)
    made = finalized(new_project(tmp_path / "made", monkeypatch), monkeypatch)
    report = compare.compare_boards(example, made)
    assert not report.differs, "\n".join(report.differences)
    assert report.old_vias == report.new_vias > 0
    assert report.old_tracks.count == report.new_tracks.count > 0
