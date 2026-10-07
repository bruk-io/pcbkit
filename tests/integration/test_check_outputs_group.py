"""Integration: the ``outputs`` check group on real exports, as `pcbkit check` runs it.

The tiny board of tests/tiny_board.py (two 10k resistors on a 30 x 20 mm board, with an
MPN on each in its schematic) gets a header and a mounting hole, and kicad-cli exports
its Gerbers, drill files and positions; they are zipped flat, with a BOM and a centroid
written beside them, into ``out/fab`` of a project (tests/check_group_boards.py). The
real command line (``pcbkit.check.runner.command``) runs the group in a child pytest
and ``out/checks/results.json`` is read back.

The control project passes all six checks. Each is then shown to bite by planting one
mistake in a copy of the control (a layer missing from the zip, a stackup that does not
match the job file, a board of the wrong size, a via added after the export, a part
missing from the BOM or with a stale part number, a part moved after the export) and
watching exactly the check that guards it fail. What a check parses back is compared
with what the plan built: three plated holes of 0.4 mm and 1.0 mm and one unplated of
3.2 mm, a job file of two 35 um coppers, a centroid that counts y up from the bottom
edge (so the header at layout y = 4 mm of 20 reads 16).

These need KiCad's own Python (``import pcbnew``) to build the boards and a real
kicad-cli to export them: see tests/integration/test_kicad_core.py for how to make
``.venv-kicad``, and ``pcbkit doctor`` for kicad-cli.
"""

from __future__ import annotations

import csv
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit.kicad import board as kb  # noqa: E402
from pcbkit.kicad.sexp import dump, find, findall, parse  # noqa: E402
from tests import check_group_boards as gb  # noqa: E402
from tests import tiny_board  # noqa: E402
from tests.check_group_run import Lab, Result, write_toml  # noqa: E402

pytestmark = pytest.mark.kicad

MODULE = "pcbkit.check.builtin.test_outputs"
STEM = tiny_board.STEM
FAB = gb.FAB_NAME

CONTROL = {
    "test_gerber_set_complete",
    "test_job_file_stackup",
    "test_outline_size",
    "test_drill_files_match_board",
    "test_bom_matches_schematic",
    "test_centroid_matches_board",
    "test_bom_complete",
}
GERBER_LAYERS = [
    "F_Cu",
    "B_Cu",
    "F_Mask",
    "B_Mask",
    "F_Silkscreen",
    "B_Silkscreen",
    "F_Paste",
    "B_Paste",
    "Edge_Cuts",
]


# --- projects: the control, and a copy of it with one thing changed -------------------


@dataclass(frozen=True)
class Variant:
    """A copy of the control project with a change, or a fresh build at other copper.

    ``edit`` changes the copied project in place; ``stackup`` gives the keys of that
    table of pcbkit.toml; ``copper_mm`` builds the project again, from the board and
    the exports, with that much copper in its stackup (so its job file says so too).
    """

    edit: Callable[[Path], None] | None = None
    stackup: dict[str, float] | None = None
    copper_mm: float | None = None


def build(root: Path, variant: Variant | None, base: Path) -> Path:
    """Write the project of ``variant`` (the control when None) into ``root``."""
    variant = variant or Variant()
    if variant.copper_mm is not None:
        gb.outputs_project(root, variant.copper_mm)
    else:
        shutil.copytree(base, root)
    write_toml(root, STEM, ["outputs"], variant.stackup)
    if variant.edit is not None:
        variant.edit(root)
    return root


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the control project's files: the board, the exports and layout.py."""
    return gb.outputs_project(tmp_path_factory.mktemp("outputs_control") / "project")


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory, base: Path) -> Lab:
    """Return the lab: the control and the plants are built and run on first use."""
    return Lab(tmp_path_factory.mktemp("outputs_group"), partial(build, base=base))


@pytest.fixture(scope="module")
def control(lab: Lab) -> Result:
    """Return the control project, run: every check of the group on fresh exports."""
    return lab.result("control")


def numbers(result: Result, name: str) -> dict[str, Any]:
    """Return what the one check whose id contains ``name`` recorded."""
    return result.run.numbers(name)


# --- ways to change a project ---------------------------------------------------------


def fab(root: Path, name: str) -> Path:
    """Return a file of the project's ``out/fab`` folder."""
    return root / "out" / "fab" / f"{FAB}_{name}"


def edit_board(change: Callable[[Any], None]) -> Callable[[Path], None]:
    """Return an edit that loads the project's board, changes it and saves it again.

    The exports are left as they were, so the board has moved on from them.
    """

    def edit(root: Path) -> None:
        pcb = root / "kicad" / f"{STEM}.kicad_pcb"
        board = pcbnew.LoadBoard(str(pcb))
        change(board)
        gb.save(board, pcb, origin=gb.ORIGIN)

    return edit


def edit_csv(
    name: str, change: Callable[[list[dict[str, str]]], list[dict[str, str]]]
) -> Callable[[Path], None]:
    """Return an edit that rewrites a CSV file of ``out/fab`` through ``change``."""

    def edit(root: Path) -> None:
        path = fab(root, name)
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            rows = change(list(reader))
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    return edit


def edit_layout(text: str) -> Callable[[Path], None]:
    """Return an edit that writes ``text`` as the project's layout.py."""

    def edit(root: Path) -> None:
        (root / "layout.py").write_text(text, encoding="utf-8")

    return edit


def move(ref: str, dx: float = 0.0, dy: float = 0.0) -> Callable[[Any], None]:
    """Return a change that moves a footprint ``dx``, ``dy`` mm (layout axes)."""

    def change(board: Any) -> None:
        footprint = board.FindFootprintByReference(ref)
        here = footprint.GetPosition()
        footprint.SetPosition(pcbnew.VECTOR2I(here.x + kb.mm(dx), here.y + kb.mm(dy)))

    return change


# --- the control ----------------------------------------------------------------------


def test_the_control_project_passes_every_outputs_check(control: Result) -> None:
    """Run the seven checks of the group on fresh exports, and pass them all."""
    assert control.names() == CONTROL
    assert control.failed() == set(), (control.run.outcomes(), control.run.tail())
    assert control.run.done.returncode == 0, control.run.tail()
    assert all(entry["outcome"] == "passed" for entry in control.run.checks.values())
    assert {e["group"] for e in control.run.checks.values()} == {"outputs"}
    assert all(key.startswith(MODULE) for key in control.run.checks)


def test_the_exports_hold_what_the_plan_built(control: Result) -> None:
    """Drill the holes of the plan: a via, two header pins and one mounting hole."""
    holes = numbers(control, "test_drill_files_match_board")["drills"]
    # the tiny board's via is 0.4 mm; J1's two pins are drilled 1.0 mm; H1 is 3.2 mm
    assert holes == {"pth": {"0.4": 1, "1.0": 2}, "npth": {"3.2": 1}}
    job = numbers(control, "test_job_file_stackup")["job"]
    assert job["layers"] == 2 and job["thickness"] == 1.6
    assert job["copper_mm"] == [0.035, 0.035]
    # the job file's size is the outline's 30 x 20 mm and the 0.1 mm line it is drawn in
    assert job["size"] == {"X": pytest.approx(30.1), "Y": pytest.approx(20.1)}


# --- test_gerber_set_complete ---------------------------------------------------------

GERBERS = "test_gerber_set_complete"
OUTLINE = "test_outline_size"
DRILLS = "test_drill_files_match_board"


def leave_out(name: str) -> Callable[[Path], None]:
    """Return an edit that zips the Gerbers again without the file called ``name``."""

    def edit(root: Path) -> None:
        archive = fab(root, "gerbers.zip")
        gb.zip_gerbers(root / "out" / "plot", archive, skip=[name])
        assert name not in zipfile.ZipFile(archive).namelist()

    return edit


@pytest.mark.parametrize("layer", GERBER_LAYERS)
def test_a_layer_missing_from_the_zip_fails_the_gerber_set(
    lab: Lab, layer: str
) -> None:
    """Fail the zip with one layer's Gerber left out, naming the layer."""
    result = lab.result(f"no_{layer}", Variant(leave_out(f"{STEM}-{layer}.gbr")))
    # the outline check reads the board's outline out of the Edge_Cuts Gerber, so it has
    # nothing to read either when that is the file that is gone
    result.planted(GERBERS, *([OUTLINE] if layer == "Edge_Cuts" else []))
    assert f"missing {layer}" in result.run.message(GERBERS)


@pytest.mark.parametrize("hole", ["PTH", "NPTH"])
def test_a_drill_file_missing_from_the_zip_fails_the_gerber_set(
    lab: Lab, hole: str
) -> None:
    """Fail the zip with the plated, or the unplated, drill file left out."""
    result = lab.result(f"no_{hole}", Variant(leave_out(f"{STEM}-{hole}.drl")))
    # and the drill check, which compares that file with the board, has none to read
    result.planted(GERBERS, DRILLS)
    assert "missing" not in result.run.message(GERBERS)  # every Gerber layer is there


# --- test_job_file_stackup ------------------------------------------------------------

JOB = "test_job_file_stackup"


def test_a_stackup_that_the_job_file_does_not_say_fails(lab: Lab) -> None:
    """Fail [stackup] copper_mm = 0.070 against a job file of two 35 um coppers."""
    result = lab.result("toml_2oz", Variant(stackup={"copper_mm": 0.07}))
    result.planted(JOB)
    assert numbers(result, JOB)["job"]["copper_mm"] == [0.035, 0.035]
    assert (
        "job file copper [0.035, 0.035], pcbkit.toml says 0.07"
        in result.run.message(JOB)
    )


def test_a_job_file_of_two_ounce_copper_passes_the_toml_that_says_so(lab: Lab) -> None:
    """Pass exports made from a board of 0.070 mm copper against [stackup] to match."""
    result = lab.result(
        "both_2oz", Variant(stackup={"copper_mm": 0.07}, copper_mm=0.07)
    )
    assert result.failed() == set(), result.run.outcomes()
    assert numbers(result, JOB)["job"]["copper_mm"] == [0.07, 0.07]
    assert result.run.results["copper_mm"] == 0.07


def test_a_board_thickness_that_the_job_file_does_not_say_fails(lab: Lab) -> None:
    """Fail [stackup] thickness_mm = 1.2 against a job file of a 1.6 mm board."""
    result = lab.result("toml_1.2mm", Variant(stackup={"thickness_mm": 1.2}))
    result.planted(JOB)
    assert numbers(result, JOB)["job"]["thickness"] == 1.6


# --- test_outline_size ----------------------------------------------------------------


def test_a_layout_of_the_wrong_width_fails_the_outline_check(lab: Lab) -> None:
    """Fail layout.W = 31 mm against an outline cut 30 mm wide."""
    result = lab.result(
        "wrong_width",
        Variant(edit_layout(f"W, H = {gb.BOARD_W + 1.0}, {gb.BOARD_H}\n")),
    )
    result.planted(OUTLINE)


def test_a_layout_of_the_wrong_height_fails_the_outline_and_the_centroid(
    lab: Lab,
) -> None:
    """Fail layout.H = 25 mm: the outline is 20 mm high, the centroid counts up it."""
    result = lab.result(
        "wrong_height",
        Variant(edit_layout(f"W, H = {gb.BOARD_W}, {gb.BOARD_H + 5.0}\n")),
    )
    # every y of the centroid is measured up from the bottom edge, so a taller board
    # puts the header and the resistors five millimetres from where the file says
    result.planted(OUTLINE, "test_centroid_matches_board")


# --- test_drill_files_match_board -----------------------------------------------------


def add_via(board: Any) -> None:
    """Add a ground via of 0.4 mm drill to the board."""
    kb.via(board, 15.0, 5.0, "GND")


def add_mounting_hole(board: Any) -> None:
    """Add a second mounting hole of 3.2 mm to the board."""
    gb.place(
        board,
        gb.Part("H2", gb.MOUNTING_HOLE, "Mechanical:MountingHole", "M3", (26.0, 16.0)),
    )


def test_a_via_added_after_the_export_fails_the_drill_check(lab: Lab) -> None:
    """Fail a board with two vias against drill files made when it had one."""
    result = lab.result("late_via", Variant(edit_board(add_via)))
    result.planted(DRILLS)
    # the files still hold what the control held; the board now has one more 0.4 mm hole
    assert numbers(result, DRILLS)["drills"]["pth"] == {"0.4": 1, "1.0": 2}
    message = result.run.message(DRILLS)
    assert "PTH drill file {0.4: 1, 1.0: 2} vs board {0.4: 2, 1.0: 2}" in message
    # The first check to load the board failed, so pytest shows what its setup wrote
    # to stderr: pcbnew's wx noise must not be part of it.
    output = result.run.done.stdout + result.run.done.stderr
    assert "stdpbase.cpp" not in output
    assert "Adding duplicate image handler" not in output


def test_a_mounting_hole_added_after_the_export_fails_the_drill_check(
    lab: Lab,
) -> None:
    """Fail an unplated hole the NPTH file does not have."""
    result = lab.result("late_hole", Variant(edit_board(add_mounting_hole)))
    result.planted(DRILLS)
    assert numbers(result, DRILLS)["drills"]["npth"] == {"3.2": 1}
    assert "{3.2: 1} == {3.2: 2}" in result.run.message(DRILLS)


# --- test_bom_matches_schematic -------------------------------------------------------

BOM = "test_bom_matches_schematic"


def set_symbol_flag(ref: str, flag: str, value: str) -> Callable[[Path], None]:
    """Return an edit that sets ``(flag value)`` on the schematic's symbol ``ref``."""

    def edit(root: Path) -> None:
        schematic = root / "kicad" / f"{STEM}.kicad_sch"
        tree = parse(schematic.read_text(encoding="utf-8"))
        for symbol in findall(tree, "symbol"):
            props = {str(p[1]): str(p[2]) for p in findall(symbol, "property")}
            if props.get("Reference") == ref:
                node = find(symbol, flag)
                assert node is not None
                node[1] = value
        schematic.write_text(dump(tree) + "\n", encoding="utf-8")

    return edit


def test_a_part_missing_from_the_bom_fails_the_bom_check(lab: Lab) -> None:
    """Fail a BOM that lists R1 but not R2, naming the part it is missing."""

    def only_r1(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        rows[0]["Designator"] = "R1"
        rows[0]["Qty"] = "1"
        return rows

    result = lab.result("bom_without_r2", Variant(edit_csv("BOM.csv", only_r1)))
    result.planted(BOM)
    assert "BOM extra [] missing ['R2']" in result.run.message(BOM)


def test_a_part_in_the_bom_that_the_schematic_does_not_have_fails(lab: Lab) -> None:
    """Fail a BOM with a third resistor the schematic has never drawn."""

    def add_r3(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        rows[0]["Designator"] = "R1,R2,R3"
        return rows

    result = lab.result("bom_with_r3", Variant(edit_csv("BOM.csv", add_r3)))
    result.planted(BOM)
    assert "BOM extra ['R3'] missing []" in result.run.message(BOM)


def test_a_part_the_schematic_marks_do_not_populate_fails_when_the_bom_has_it(
    lab: Lab,
) -> None:
    """Fail R2 set "do not populate" after the BOM was written with it in."""
    result = lab.result("r2_dnp", Variant(set_symbol_flag("R2", "dnp", "yes")))
    result.planted(BOM)
    assert "BOM extra ['R2'] missing []" in result.run.message(BOM)


def test_a_stale_part_number_fails_the_bom_check(lab: Lab) -> None:
    """Fail a BOM that gives both resistors a part number the schematic has dropped."""

    def stale(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        rows[0]["Manufacturer Part Number"] = "RES-OLD-0603"
        return rows

    result = lab.result("bom_stale_mpn", Variant(edit_csv("BOM.csv", stale)))
    result.planted(BOM)
    message = result.run.message(BOM)
    assert "BOM part numbers differ from the schematic" in message
    assert f"('R1', '{gb.MPN}', 'RES-OLD-0603')" in message
    assert f"('R2', '{gb.MPN}', 'RES-OLD-0603')" in message


# --- test_bom_complete ----------------------------------------------------------------


def test_a_part_with_no_part_number_in_the_design_fails_bom_complete(lab: Lab) -> None:
    """Fail R1 once design.py gives it no part number, naming it; nothing else fails.

    R1 becomes a 12k with no part number: 12k is not in the default resistor table, so
    the BOM would carry R()'s stand-in. The exports and the schematic are untouched, as
    after a design mistake not yet exported, so the BOM check still agrees.
    """

    def blank_r1(root: Path) -> None:
        design = root / "design.py"
        text = design.read_text(encoding="utf-8")
        old = f'R("R1", "10k", "NET_A", "NET_B", "Tiny", mpn={gb.MPN!r})'
        assert old in text
        design.write_text(text.replace(old, 'R("R1", "12k", "NET_A", "NET_B", "Tiny")'))

    result = lab.result("design_blank_mpn", Variant(blank_r1))
    result.planted("test_bom_complete")
    message = result.run.message("test_bom_complete")
    assert "R1 (12k)" in message and "stand-in part number" in message


# --- test_centroid_matches_board ------------------------------------------------------

CENTROID = "test_centroid_matches_board"


def test_the_control_centroid_counts_y_up_from_the_bottom_edge(control: Result) -> None:
    """Read the header at layout (5, 4) of a 20 mm board as x = 5 and y = 16."""
    with open(fab(control.board, "centroid.csv"), encoding="utf-8", newline="") as h:
        rows = {row["Designator"]: row for row in csv.DictReader(h)}
    assert set(rows) == {"R1", "R2", "J1"}  # the mounting hole is not placed
    x, y = gb.HEADER_AT
    assert float(rows["J1"]["Mid X (mm)"]) == pytest.approx(x)
    assert float(rows["J1"]["Mid Y (mm)"]) == pytest.approx(gb.BOARD_H - y)
    for ref, (rx, ry) in tiny_board.PLACEMENT.items():
        assert float(rows[ref]["Mid X (mm)"]) == pytest.approx(rx)
        assert float(rows[ref]["Mid Y (mm)"]) == pytest.approx(gb.BOARD_H - ry)
    assert numbers(control, CENTROID) == {
        "SMD placement point >0.5 mm from pad centre": []
    }


def test_a_part_moved_after_the_export_fails_the_centroid_check(lab: Lab) -> None:
    """Fail R1 moved a millimetre along x after the position file was made."""
    result = lab.result("moved_r1", Variant(edit_board(move("R1", dx=1.0))))
    result.planted(CENTROID)
    assert "the fab house expects part centres" in result.run.message(CENTROID)
    assert "'R1'" in result.run.message(CENTROID)


def test_a_part_turned_after_the_export_fails_the_centroid_check(lab: Lab) -> None:
    """Fail R1 turned a quarter turn: its centre has not moved, its rotation has."""

    def turn(board: Any) -> None:
        board.FindFootprintByReference("R1").SetOrientationDegrees(90.0)

    result = lab.result("turned_r1", Variant(edit_board(turn)))
    result.planted(CENTROID)


def test_a_through_hole_part_moved_after_the_export_is_stale(lab: Lab) -> None:
    """Fail J1 moved half a millimetre: a through-hole part is held to 0.02 mm."""
    result = lab.result("moved_j1", Variant(edit_board(move("J1", dx=0.5))))
    result.planted(CENTROID)
    assert "J1 centroid is stale" in result.run.message(CENTROID)


def test_a_surface_mount_part_missing_from_the_centroid_fails(lab: Lab) -> None:
    """Fail a centroid with no row for R2, naming it."""

    def no_r2(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return [row for row in rows if row["Designator"] != "R2"]

    result = lab.result("centroid_without_r2", Variant(edit_csv("centroid.csv", no_r2)))
    result.planted(CENTROID)
    assert "SMD parts missing from centroid: ['R2']" in result.run.message(CENTROID)


def test_a_centroid_that_counts_y_down_from_the_top_edge_fails(lab: Lab) -> None:
    """Fail J1 at y = 4 where the file means 16: the axis runs up from the bottom."""

    def from_the_top(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        for row in rows:
            row["Mid Y (mm)"] = f"{gb.BOARD_H - float(row['Mid Y (mm)']):f}"
        return rows

    result = lab.result("y_down", Variant(edit_csv("centroid.csv", from_the_top)))
    result.planted(CENTROID)
    assert "J1 centroid is stale" in result.run.message(CENTROID)
