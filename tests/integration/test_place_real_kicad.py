"""Integration: `pcbkit build` placement on a tiny project, with real KiCad and pcbnew.

The project is tests/tiny_project.py's: tests/fixtures/tiny_board (a header and a
resistor from the project's own library, a stock LED) with two mounting holes and a
layout.py. It needs KiCad's own Python (``import pcbnew``) and kicad-cli: see the
head of tests/integration/test_kicad_core.py for how to make .venv-kicad, then run

    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python these tests are skipped.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit import place, sch  # noqa: E402
from pcbkit.cli import cli  # noqa: E402
from pcbkit.kicad import board as kb  # noqa: E402
from pcbkit.place import PlaceResult  # noqa: E402
from pcbkit.project import ProjectError, load_project  # noqa: E402
from tests.board_files import restored_imports  # noqa: E402
from tests.tiny_project import (  # noqa: E402
    EXTRA_PARTS,
    HOLE_PART,
    LAYOUT,
    make_project,
    run_stages,
)

pytestmark = pytest.mark.kicad


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep project modules and sys.path entries from outliving a test."""
    with restored_imports():
        yield


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, PlaceResult]:
    """Build the project once; the tests below only read it, or copy it."""
    root = make_project(tmp_path_factory.mktemp("placed") / "tiny_board")
    with restored_imports():
        return root, run_stages(root)[1]


@pytest.fixture
def copy(built: tuple[Path, PlaceResult], tmp_path: Path) -> Path:
    """Return a private copy of the built project, for a test that changes it."""
    root = tmp_path / "tiny_board"
    shutil.copytree(built[0], root)
    return root


def load(pcb: Path) -> Any:
    """Load a board file with pcbnew."""
    return pcbnew.LoadBoard(str(pcb))


def at(footprint: Any) -> tuple[float, float]:
    """Return a footprint's position in layout mm."""
    return kb.to_local(footprint.GetPosition())


def edge_shapes(board: Any) -> dict[str, list[Any]]:
    """Return the Edge.Cuts lines and arcs of a board by kind."""
    shapes: dict[str, list[Any]] = {"line": [], "arc": []}
    for item in board.GetDrawings():
        if item.GetClass() == "PCB_SHAPE" and item.GetLayer() == pcbnew.Edge_Cuts:
            kind = "arc" if item.GetShape() == pcbnew.SHAPE_T_ARC else "line"
            shapes[kind].append(item)
    return shapes


# --- the parts ----------------------------------------------------------------------


def test_each_part_is_where_layout_py_puts_it(built: tuple[Path, PlaceResult]) -> None:
    board = load(built[1].pcb)
    j1, r1, hr1 = (board.FindFootprintByReference(r) for r in ("J1", "R1", "HR1"))
    assert at(j1) == pytest.approx((10.0, 8.0), abs=1e-6)
    assert at(r1) == pytest.approx((26.0, 12.0), abs=1e-6)
    assert r1.GetOrientationDegrees() == pytest.approx(90.0)
    assert j1.GetOrientationDegrees() == pytest.approx(0.0)
    assert hr1.GetOrientationDegrees() == pytest.approx(180.0)


def test_a_hole_h_n_goes_to_the_nth_position_of_holes(
    built: tuple[Path, PlaceResult],
) -> None:
    """H<n> is HOLES[n - 1]; HR1, which also starts with H, is an ordinary part."""
    board = load(built[1].pcb)
    h1, h2, hr1 = (board.FindFootprintByReference(r) for r in ("H1", "H2", "HR1"))
    assert at(h1) == pytest.approx((4.0, 4.0), abs=1e-6)
    assert at(h2) == pytest.approx((36.0, 20.0), abs=1e-6)
    assert at(hr1) == pytest.approx((18.0, 18.0), abs=1e-6)


def test_a_part_with_no_position_is_parked_below_the_board_and_reported(
    built: tuple[Path, PlaceResult],
) -> None:
    result = built[1]
    assert result.missing == ["D1"]
    assert result.placed == 6  # J1, R1, D1, HR1, H1, H2: parked D1 is on the board
    d1 = load(result.pcb).FindFootprintByReference("D1")
    # x = 5, y = H + 10 + 6 for the first parked part
    assert at(d1) == pytest.approx((5.0, 24.0 + 10.0 + 6.0), abs=1e-6)


def test_each_pad_has_the_net_the_netlist_gives_it(
    built: tuple[Path, PlaceResult],
) -> None:
    board = load(built[1].pcb)
    nets = {
        (f.GetReference(), p.GetNumber()): p.GetNetname()
        for f in board.GetFootprints()
        for p in f.Pads()
    }
    assert nets[("J1", "1")] == nets[("R1", "1")] == "/+3V3"
    assert nets[("J1", "2")] == nets[("D1", "1")] == "/GND"
    assert nets[("R1", "2")] == nets[("D1", "2")] == "/LED_A"


def test_the_footprints_keep_their_link_to_the_schematic(
    built: tuple[Path, PlaceResult],
) -> None:
    """Carry the symbol's id in the footprint's path, for DRC's schematic parity."""
    board = load(built[1].pcb)
    for ref in ("J1", "R1", "D1"):
        footprint = board.FindFootprintByReference(ref)
        assert re.fullmatch(r"/[0-9a-f-]{36}", footprint.GetPath().AsString()), ref
        assert footprint.GetSheetfile() == "tiny_board.kicad_sch"


def test_a_footprint_comes_from_the_projects_own_library_or_kicads(
    built: tuple[Path, PlaceResult],
) -> None:
    board = load(built[1].pcb)
    ids = {
        f.GetReference(): str(f.GetFPID().GetUniStringLibId())
        for f in board.GetFootprints()
    }
    assert ids["J1"] == "tiny:Header_1x02_P2.54mm"  # generated by footprints.py
    assert ids["R1"] == "tiny:R_Vendored"  # copied from footprints/
    assert ids["D1"] == "LED_SMD:LED_0603_1608Metric"  # KiCad's own


def test_reference_text_is_small_and_tidy(built: tuple[Path, PlaceResult]) -> None:
    reference = load(built[1].pcb).FindFootprintByReference("R1").Reference()
    assert pcbnew.ToMM(reference.GetTextWidth()) == pytest.approx(0.8)
    assert pcbnew.ToMM(reference.GetTextHeight()) == pytest.approx(0.8)
    assert pcbnew.ToMM(reference.GetTextThickness()) == pytest.approx(0.15)


def test_the_board_is_also_saved_as_placed_kicad_pcb(
    built: tuple[Path, PlaceResult],
) -> None:
    pcb = built[1].pcb
    assert pcb == built[0] / "kicad" / "tiny_board.kicad_pcb"
    assert (pcb.parent / "placed.kicad_pcb").read_bytes() == pcb.read_bytes()


# --- the outline --------------------------------------------------------------------


def test_the_outline_is_a_rectangle_with_rounded_corners(
    built: tuple[Path, PlaceResult],
) -> None:
    board = load(built[1].pcb)
    shapes = edge_shapes(board)
    assert (len(shapes["line"]), len(shapes["arc"])) == (4, 4)
    box = board.GetBoardEdgesBoundingBox()
    assert kb.to_local(box.GetOrigin()) == pytest.approx((-0.05, -0.05), abs=1e-3)
    assert pcbnew.ToMM(box.GetWidth()) == pytest.approx(
        40.1, abs=1e-3
    )  # + a 0.1 stroke
    assert pcbnew.ToMM(box.GetHeight()) == pytest.approx(24.1, abs=1e-3)
    # each corner is a quarter circle of radius 2 about a point 2 mm inside
    radii = [pcbnew.ToMM(arc.GetRadius()) for arc in shapes["arc"]]
    assert radii == pytest.approx([2.0] * 4, abs=1e-3)
    centres = sorted(
        tuple(round(v, 3) for v in kb.to_local(arc.GetCenter()))
        for arc in shapes["arc"]
    )
    assert centres == [(2.0, 2.0), (2.0, 22.0), (38.0, 2.0), (38.0, 22.0)]


def test_without_corner_r_the_corners_are_square(copy: Path) -> None:
    (copy / "layout.py").write_text(LAYOUT.replace("CORNER_R = 2.0\n", ""))
    result = place.place_board(load_project(copy))
    shapes = edge_shapes(load(result.pcb))
    assert (len(shapes["line"]), len(shapes["arc"])) == (4, 0)
    ends = {
        tuple(round(v, 3) for v in kb.to_local(line.GetStart()))
        for line in shapes["line"]
    }
    assert ends == {(0.0, 0.0), (40.0, 0.0), (40.0, 24.0), (0.0, 24.0)}


TRIANGLE = """\
W, H = 40.0, 24.0
HOLES = [(4.0, 4.0), (36.0, 20.0)]
P = {"J1": (10.0, 8.0, 0), "R1": (26.0, 12.0, 90)}


def outline(board, api):
    api.add_line(board, 0, 0, 40, 0)
    api.add_line(board, 40, 0, 0, 24)
    api.add_line(board, 0, 24, 0, 0)
"""


def test_an_outline_function_in_layout_py_replaces_the_rectangle(copy: Path) -> None:
    (copy / "layout.py").write_text(TRIANGLE)
    shapes = edge_shapes(load(place.place_board(load_project(copy)).pcb))
    assert (len(shapes["line"]), len(shapes["arc"])) == (3, 0)


# --- the stackup --------------------------------------------------------------------


def stackup_of(pcb: Path) -> dict[str, float]:
    """Return the thickness of each stackup layer named in the saved file."""
    text = pcb.read_text(encoding="utf-8")
    block = text[text.index("(stackup") : text.index("(dielectric_constraints")]
    return {
        m.group(1): float(m.group(2))
        for m in re.finditer(
            r'\(layer "([^"]+)" \(type [^)]*\) \(thickness ([0-9.]+)\)', block
        )
    }


def test_the_stackup_takes_its_copper_from_the_config(
    built: tuple[Path, PlaceResult],
) -> None:
    pcb = built[1].pcb
    assert stackup_of(pcb) == {
        "F.Mask": 0.01,
        "F.Cu": 0.035,
        "dielectric 1": 1.44,
        "B.Cu": 0.035,
        "B.Mask": 0.01,
    }
    assert "(thickness 1.6)" in pcb.read_text(encoding="utf-8").split("(layers")[0]


def test_two_ounce_copper_and_another_thickness_change_the_stackup(copy: Path) -> None:
    toml = (copy / "pcbkit.toml").read_text()
    (copy / "pcbkit.toml").write_text(
        toml + "\n[stackup]\nthickness_mm = 1.2\ncopper_mm = 0.07\n"
    )
    pcb = place.place_board(load_project(copy)).pcb
    layers = stackup_of(pcb)
    assert layers["F.Cu"] == layers["B.Cu"] == 0.07
    assert layers["dielectric 1"] == 1.04
    assert "(thickness 1.2)" in pcb.read_text(encoding="utf-8").split("(layers")[0]
    assert load(pcb).GetDesignSettings().GetBoardThickness() == kb.mm(1.2)


# --- what the project can get wrong ------------------------------------------------


def test_a_hole_with_no_position_is_a_message(tmp_path: Path) -> None:
    """Fail on H3 when HOLES has two entries, naming the part and the rule."""
    more = EXTRA_PARTS + HOLE_PART.replace('"H1"', '"H3"')
    root = make_project(tmp_path / "t", extra_design=more)
    proj = load_project(root)
    sch.build_schematic(proj)
    with pytest.raises(
        ProjectError, match=r"H3 but HOLES has 2 position.*HOLES\[n - 1\]"
    ):
        place.place_board(proj)


def test_a_footprint_that_is_not_in_its_library_is_a_message_naming_it(
    tmp_path: Path,
) -> None:
    gone = HOLE_PART.replace("MountingHole_3.2mm_M3", "No_Such_Hole")
    root = make_project(tmp_path / "t", extra_design=gone)
    proj = load_project(root)
    sch.build_schematic(proj)
    with pytest.raises(
        click.ClickException, match=r"MountingHole:No_Such_Hole.*MountingHole\.pretty"
    ):
        place.place_board(proj)


def test_a_library_that_does_not_exist_is_a_message_naming_it(tmp_path: Path) -> None:
    gone = HOLE_PART.replace("MountingHole:", "No_Such_Library:")
    root = make_project(tmp_path / "t", extra_design=gone)
    proj = load_project(root)
    sch.build_schematic(proj)
    with pytest.raises(
        click.ClickException,
        match="No_Such_Library:MountingHole_3.2mm_M3 not found: KiCad has no footprint",
    ):
        place.place_board(proj)


def test_without_a_netlist_it_says_to_run_sch_first(tmp_path: Path) -> None:
    root = make_project(tmp_path / "t")
    with pytest.raises(
        click.ClickException, match=r"tiny_board\.net not found: run `pcbkit sch`"
    ):
        place.place_board(load_project(root))


def test_without_layout_py_it_says_what_is_missing(copy: Path) -> None:
    (copy / "layout.py").unlink()
    with pytest.raises(ProjectError, match=r"layout\.py not found"):
        place.place_board(load_project(copy))


def test_a_layout_without_the_board_size_names_the_missing_value(copy: Path) -> None:
    (copy / "layout.py").write_text(LAYOUT.replace("W, H = 40.0, 24.0\n", "H = 24.0\n"))
    with pytest.raises(ProjectError, match=r"layout\.py: W is missing"):
        place.place_board(load_project(copy))


# --- the command --------------------------------------------------------------------


def test_pcbkit_build_runs_the_schematic_then_the_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path / "t")
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["build"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "ERC        0 errors, 0 warnings (kicad/erc.rpt)",
        "placed 6, missing: ['D1']",
        "  (parked below the board: give each a position in layout.py)",
    ]
    assert (root / "kicad" / "tiny_board.kicad_pcb").is_file()
    assert (root / "kicad" / "placed.kicad_pcb").is_file()
