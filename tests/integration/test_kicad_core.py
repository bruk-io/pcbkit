"""Integration: pcbkit.kicad.board and pcbkit.kicad.cli against a real KiCad.

A tiny board (tests/tiny_board.py) is built with the board helpers, saved, reloaded and
checked by pcbnew and by kicad-cli itself: DRC with schematic parity, ERC, the netlist,
and the Gerber, drill, position, SVG and 3D exports. A planted mistake shows the checks
can fail. The hazards the helpers shield are shown too, where that can be done safely:
the ones that break a process run in a child Python, so they cannot take the test run
down with them.

These tests need KiCad's own Python, where ``import pcbnew`` works, and kicad-cli.
Make that environment once and run them with it:

    KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
    uv venv --python $KICAD_PY --system-site-packages .venv-kicad
    VIRTUAL_ENV=.venv-kicad uv pip install -e . pytest
    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python they are skipped, for the reason given just below.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit.kicad import board as kb  # noqa: E402
from pcbkit.kicad import cli  # noqa: E402
from pcbkit.kicad.sexp import find, findall, parse  # noqa: E402
from tests import tiny_board  # noqa: E402
from tests.tiny_board import TinyBoard  # noqa: E402

pytestmark = pytest.mark.kicad

SIGNATURE = b"\x89PNG\r\n\x1a\n"


class NpFloat(float):
    """Stands for np.float64: a float subclass, which pcbnew refuses."""

    def __add__(self, other: Any) -> NpFloat:
        """Add, staying an NpFloat, as numpy's scalars do."""
        return NpFloat(float(self) + other)

    __radd__ = __add__


@pytest.fixture(scope="module")
def tiny(tmp_path_factory: pytest.TempPathFactory) -> TinyBoard:
    """Build the tiny board once and share it: the tests only read it, or copy it."""
    return tiny_board.build(tmp_path_factory.mktemp("tiny"))


@pytest.fixture
def copy(tiny: TinyBoard, tmp_path: Path) -> TinyBoard:
    """Return a private copy of the tiny board's folder, for a test that changes it."""
    folder = tmp_path / "copy"
    shutil.copytree(tiny.directory, folder, ignore=shutil.ignore_patterns("*.rpt"))
    return TinyBoard(
        folder, folder / "tiny.kicad_pcb", folder / "tiny.kicad_sch", tiny.symbols
    )


def load(path: Path) -> Any:
    """Load a board file with pcbnew."""
    return pcbnew.LoadBoard(str(path))


def near(point: tuple[float, float], x: float, y: float) -> bool:
    """Return True if layout point is within a micrometre of (x, y)."""
    return abs(point[0] - x) < 1e-3 and abs(point[1] - y) < 1e-3


# --- what the helpers made, read back after a save and a reload ---------------


def test_the_nets_and_pads_come_back_after_a_save_and_reload(tiny: TinyBoard) -> None:
    board = load(tiny.pcb)
    for (ref, number), net in tiny_board.PAD_NETS.items():
        assert kb.pad(board, ref, number).GetNetname() == net
    assert kb.N(board, "NET_B").GetNetname() == "/NET_B"
    with pytest.raises(KeyError):
        kb.N(board, "NOT_A_NET")
    with pytest.raises(KeyError, match="R1:9"):
        kb.pad(board, "R1", 9)


def test_ppos_and_to_local_report_layout_millimetres(tiny: TinyBoard) -> None:
    board = load(tiny.pcb)
    # a 0603's pads sit 0.825 mm either side of its centre
    assert near(kb.ppos(board, "R1", 1), 8.0 - 0.825, 10.0)
    assert near(kb.ppos(board, "R1", 2), 8.0 + 0.825, 10.0)
    assert near(kb.ppos(board, "R2", 2), 22.0 + 0.825, 10.0)
    footprint = board.FindFootprintByReference("R2")
    assert near(kb.to_local(footprint.GetPosition()), 22.0, 10.0)


def test_pt_and_to_local_round_trip_through_real_pcbnew() -> None:
    for x, y in [(0.0, 0.0), (12.345, 67.89), (-3.25, 100.5)]:
        assert kb.to_local(kb.pt(x, y)) == pytest.approx((x, y), abs=1e-6)
    assert kb.pt(1, 2) == pcbnew.VECTOR2I(51_000_000, 52_000_000)


def test_the_tracks_come_back_locked_on_their_nets_with_their_widths(
    tiny: TinyBoard,
) -> None:
    board = load(tiny.pcb)
    tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
    assert len(tracks) == 2
    by_net = {t.GetNetname(): t for t in tracks}
    b_track, gnd_track = by_net["/NET_B"], by_net["/GND"]
    for t in tracks:
        assert t.IsLocked()
        assert t.GetLayer() == pcbnew.F_Cu
        assert pcbnew.ToMM(t.GetWidth()) == pytest.approx(0.25)
    assert near(kb.to_local(b_track.GetStart()), 8.825, 10.0)
    assert near(kb.to_local(b_track.GetEnd()), 21.175, 10.0)
    assert near(kb.to_local(gnd_track.GetEnd()), *tiny_board.GND_VIA)


def test_the_via_comes_back_locked_with_its_size_and_drill(tiny: TinyBoard) -> None:
    board = load(tiny.pcb)
    (via,) = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    assert via.GetNetname() == "/GND"
    assert via.IsLocked()
    assert pcbnew.ToMM(via.GetWidth(pcbnew.F_Cu)) == pytest.approx(0.8)
    assert pcbnew.ToMM(via.GetDrillValue()) == pytest.approx(0.4)
    assert near(kb.to_local(via.GetPosition()), *tiny_board.GND_VIA)


def test_the_pour_comes_back_on_the_bottom_layer_with_thermal_reliefs_and_a_fill(
    tiny: TinyBoard,
) -> None:
    board = load(tiny.pcb)
    (pour,) = [z for z in board.Zones() if not z.GetIsRuleArea()]
    assert pour.GetNetname() == "/GND"
    assert list(pour.GetLayerSet().Seq()) == [pcbnew.B_Cu]
    assert pour.GetAssignedPriority() == 0
    assert pcbnew.ToMM(pour.GetMinThickness()) == pytest.approx(0.25)
    assert pcbnew.ToMM(pour.GetLocalClearance()) == pytest.approx(0.25)
    assert pour.GetPadConnection() == pcbnew.ZONE_CONNECTION_THERMAL
    assert pcbnew.ToMM(pour.GetThermalReliefGap()) == pytest.approx(0.4)
    assert pcbnew.ToMM(pour.GetThermalReliefSpokeWidth()) == pytest.approx(0.6)
    assert pour.GetIslandRemovalMode() == pcbnew.ISLAND_REMOVAL_MODE_ALWAYS
    assert pour.HasFilledPolysForLayer(pcbnew.B_Cu)


def test_the_rule_areas_come_back_with_their_switches_layers_and_corners(
    tiny: TinyBoard,
) -> None:
    board = load(tiny.pcb)
    areas = [z for z in board.Zones() if z.GetIsRuleArea()]
    assert len(areas) == 2

    def corners(z: Any) -> list[tuple[float, float]]:
        ring = z.Outline().COutline(0)
        return [kb.to_local(ring.CPoint(i)) for i in range(ring.PointCount())]

    rect = next(z for z in areas if len(corners(z)) == 4)
    triangle = next(z for z in areas if len(corners(z)) == 3)
    for z in areas:
        assert z.GetDoNotAllowTracks() and z.GetDoNotAllowVias()
        assert not z.GetDoNotAllowPads() and not z.GetDoNotAllowFootprints()
        assert set(z.GetLayerSet().Seq()) == {pcbnew.F_Cu, pcbnew.B_Cu}
    assert not rect.GetDoNotAllowZoneFills()  # keepout(..., pours=False)
    assert triangle.GetDoNotAllowZoneFills()  # the default bans pours too
    x0, y0, x1, y1 = tiny_board.KEEPOUT_RECT
    assert corners(rect) == pytest.approx([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    assert corners(triangle) == pytest.approx(
        tiny_board.KEEPOUT_TRIANGLE
    )  # the pts= form


def test_the_outline_and_the_text_come_back_where_they_were_put(
    tiny: TinyBoard,
) -> None:
    board = load(tiny.pcb)
    edges = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    assert len(edges) == 8  # four sides and four rounded corners
    box = board.GetBoardEdgesBoundingBox()
    # the box includes half the 0.1 mm line on every side
    assert kb.to_local(box.GetOrigin()) == pytest.approx((-0.05, -0.05), abs=1e-3)
    assert kb.to_local(box.GetEnd()) == pytest.approx((30.05, 20.05), abs=1e-3)
    # each corner is a quarter turn of radius 2 about its own centre: not the long way
    arcs = [e for e in edges if e.GetShape() == pcbnew.SHAPE_T_ARC]
    assert len(arcs) == 4
    centres = {tuple(round(v, 3) for v in kb.to_local(a.GetCenter())) for a in arcs}
    assert centres == {(2.0, 2.0), (28.0, 2.0), (2.0, 18.0), (28.0, 18.0)}
    for arc in arcs:
        assert pcbnew.ToMM(arc.GetRadius()) == pytest.approx(2.0, abs=1e-3)
        assert abs(arc.GetArcAngle().AsDegrees()) == pytest.approx(90.0, abs=1e-3)
    (text,) = [d for d in board.GetDrawings() if d.GetClass() == "PCB_TEXT"]
    assert text.GetText() == "TINY"
    assert text.GetLayer() == pcbnew.F_SilkS
    assert near(kb.to_local(text.GetPosition()), 15.0, 16.0)
    assert pcbnew.ToMM(text.GetTextThickness()) == pytest.approx(0.15)
    assert pcbnew.ToMM(text.GetTextSize().x) == pytest.approx(1.0)


def test_the_saved_file_holds_layout_coordinates_plus_the_page_offset(
    tiny: TinyBoard,
) -> None:
    """Read the file itself: layout (x, y) lands at (x + 50, y + 50)."""
    assert (kb.OX, kb.OY) == (50.0, 50.0)
    segments = sorted(tiny_board.coordinates_in(tiny.pcb, "segment"))
    assert segments == pytest.approx([(58.825, 60.0), (72.825, 60.0)], abs=1e-6)
    (via,) = tiny_board.coordinates_in(tiny.pcb, "via")
    assert via == pytest.approx((74.5, 60.0), abs=1e-6)
    assert (52.0, 50.0) in [
        (round(x, 6), round(y, 6))
        for x, y in tiny_board.coordinates_in(tiny.pcb, "gr_line")
    ]
    tree = parse(tiny.pcb.read_text(encoding="utf-8"))
    positions = sorted(
        (float(find(fp, "at")[1]), float(find(fp, "at")[2]))
        for fp in findall(tree, "footprint")
    )
    assert positions == [(58.0, 60.0), (72.0, 60.0)]  # R1 and R2, at 8, 10 and 22, 10


def test_numpy_like_scalars_work_through_the_helpers_on_real_pcbnew() -> None:
    """The real FromMM refuses a float subclass; mm and pt, and so track, do not."""
    with pytest.raises(TypeError, match="FromMM"):
        pcbnew.FromMM(NpFloat(1.5))
    assert kb.mm(NpFloat(1.5)) == 1_500_000
    assert kb.pt(NpFloat(1.0), NpFloat(2.0)) == pcbnew.VECTOR2I(51_000_000, 52_000_000)


def test_track_and_via_accept_numpy_like_numbers(tiny: TinyBoard) -> None:
    board = load(tiny.pcb)
    kb.track(
        board,
        [(NpFloat(3), NpFloat(4)), (NpFloat(5), NpFloat(4))],
        NpFloat(0.3),
        "NET_A",
    )
    kb.via(board, NpFloat(6), NpFloat(4), "NET_A", d=NpFloat(0.7), drill=NpFloat(0.3))
    added = [t for t in board.GetTracks() if t.GetNetname() == "/NET_A"]
    assert len(added) == 2
    track = next(t for t in added if t.GetClass() == "PCB_TRACK")
    assert pcbnew.ToMM(track.GetWidth()) == pytest.approx(0.3)
    assert near(kb.to_local(track.GetEnd()), 5.0, 4.0)


# --- KiCad's own checks, through pcbkit.kicad.cli -----------------------------


def test_drc_with_schematic_parity_finds_the_tiny_board_clean(
    tiny: TinyBoard, tmp_path: Path
) -> None:
    result = cli.drc(
        tiny.pcb, tmp_path / "drc.rpt", schematic_parity=True, severity_all=True
    )
    assert result.report.counts == {
        "DRC violations": 0,
        "unconnected pads": 0,
        "Footprint errors": 0,
    }
    assert result.report.clean
    assert result.report.board == "tiny.kicad_pcb"
    assert Path(result.report_path).is_file()
    assert result.run.args[1:] == (
        "pcb",
        "drc",
        "--severity-all",
        "--schematic-parity",
        "-o",
        str(tmp_path / "drc.rpt"),
        str(tiny.pcb),
    )


def test_a_planted_clearance_mistake_is_reported_with_its_nets_and_place(
    copy: TinyBoard, tmp_path: Path
) -> None:
    """A /NET_A track 0.1 mm from the /NET_B track must show up as a clearance error."""
    board = load(copy.pcb)
    kb.track(board, [(12.0, 10.35), (16.0, 10.35)], 0.25, "NET_A")
    pcbnew.SaveBoard(str(copy.pcb), board)
    report = cli.drc(copy.pcb, tmp_path / "drc.rpt", schematic_parity=True).report
    assert not report.clean
    assert report.categories["clearance"] == 1
    clearance = next(v for v in report.violations if v.category == "clearance")
    assert clearance.severity == "error"
    assert "actual 0.1000 mm" in clearance.message
    assert set(clearance.nets) == {"/NET_A", "/NET_B"}
    # KiCad points at each track by its start; the page offset is still in the report.
    in_layout = {
        (round(x - kb.OX, 3), round(y - kb.OY, 3)) for x, y in clearance.positions
    }
    assert in_layout == {(8.825, 10.0), (12.0, 10.35)}
    assert report.drc_violations == sum(
        1 for v in report.violations if v.section == "DRC violations"
    )


def test_a_planted_net_mistake_is_a_schematic_parity_error(
    copy: TinyBoard, tmp_path: Path
) -> None:
    """Move R1's pad 1 onto /GND on the board only: the schematic says /NET_A."""
    board = load(copy.pcb)
    gnd = kb.N(board, "GND")
    kb.pad(board, "R1", 1).SetNet(gnd)
    pcbnew.SaveBoard(str(copy.pcb), board)
    with_parity = cli.drc(copy.pcb, tmp_path / "with.rpt", schematic_parity=True).report
    without = cli.drc(copy.pcb, tmp_path / "without.rpt").report
    assert with_parity.footprint_errors >= 1
    assert "net_conflict" in with_parity.categories
    conflict = next(v for v in with_parity.violations if v.category == "net_conflict")
    assert conflict.section == "Footprint errors"
    assert "/GND" in conflict.nets
    assert without.footprint_errors == 0  # nothing to compare with unless asked


def test_erc_reads_the_two_isolated_pin_labels_as_warnings(
    tiny: TinyBoard, tmp_path: Path
) -> None:
    """NET_A and GND each reach one pin only: KiCad warns, and nothing is an error."""
    result = cli.erc(tiny.sch, tmp_path / "erc.rpt", severity_all=True)
    report = result.report
    assert report.summary == "ERC messages: 2  Errors 0  Warnings 2"
    assert report.categories == {"isolated_pin_label": 2}
    assert {v.items[0] for v in report.violations} == {"Label 'NET_A'", "Label 'GND'"}
    assert all(v.severity == "warning" and v.section == "/" for v in report.violations)
    assert not report.clean


def test_the_netlist_has_the_parts_and_the_nets_the_board_was_built_with(
    tiny: TinyBoard, tmp_path: Path
) -> None:
    run = cli.export_netlist(tiny.sch, tmp_path / "tiny.net")
    tree = parse(Path(run.files[0]).read_text(encoding="utf-8"))
    components = findall(find(tree, "components") or [], "comp")
    assert sorted(str(find(c, "ref")[1]) for c in components) == ["R1", "R2"]
    footprints = {str(find(c, "footprint")[1]) for c in components}
    assert footprints == {"Resistor_SMD:R_0603_1608Metric"}
    nets = {}
    for net in findall(find(tree, "nets") or [], "net"):
        nodes = findall(net, "node")
        nets[str(find(net, "name")[1])] = sorted(
            (str(find(n, "ref")[1]), str(find(n, "pin")[1])) for n in nodes
        )
    assert nets == {
        "/NET_A": [("R1", "1")],
        "/NET_B": [("R1", "2"), ("R2", "1")],
        "/GND": [("R2", "2")],
    }


# --- the exports --------------------------------------------------------------


def test_gerbers_come_out_as_the_nine_layers_and_a_job_file(
    tiny: TinyBoard, tmp_path: Path
) -> None:
    run = cli.export_gerbers(tiny.pcb, tmp_path / "gerbers")
    names = sorted(Path(f).name for f in run.files)
    assert names == [
        "tiny-B_Cu.gbr",
        "tiny-B_Mask.gbr",
        "tiny-B_Paste.gbr",
        "tiny-B_Silkscreen.gbr",
        "tiny-Edge_Cuts.gbr",
        "tiny-F_Cu.gbr",
        "tiny-F_Mask.gbr",
        "tiny-F_Paste.gbr",
        "tiny-F_Silkscreen.gbr",
        "tiny-job.gbrjob",
    ]
    (job_file,) = [f for f in run.files if f.endswith(".gbrjob")]
    job = json.loads(Path(job_file).read_text(encoding="utf-8"))
    assert job["GeneralSpecs"]["LayerNumber"] == 2
    assert job["GeneralSpecs"]["Size"]["X"] == pytest.approx(30.1, abs=0.05)
    assert job["GeneralSpecs"]["Size"]["Y"] == pytest.approx(20.1, abs=0.05)


def test_drill_files_follow_the_gerbers_into_the_same_folder(
    tiny: TinyBoard, tmp_path: Path
) -> None:
    cli.export_gerbers(tiny.pcb, tmp_path / "gerbers")
    run = cli.export_drill(tiny.pcb, tmp_path / "gerbers")
    names = {Path(f).name for f in run.files}
    assert {"tiny-PTH.drl", "tiny-NPTH.drl", "tiny-PTH-drl_map.gbr"} <= names
    plated = (tmp_path / "gerbers" / "tiny-PTH.drl").read_text(encoding="utf-8")
    assert "0.400" in plated  # the one 0.4 mm via drill


def test_positions_are_in_kicad_file_coordinates_with_y_flipped(
    tiny: TinyBoard, tmp_path: Path
) -> None:
    run = cli.export_positions(tiny.pcb, tmp_path / "pos.csv")
    with open(run.files[0], newline="", encoding="utf-8") as handle:
        rows = {row["Ref"]: row for row in csv.DictReader(handle)}
    assert set(rows) == {"R1", "R2"}
    assert (float(rows["R1"]["PosX"]), float(rows["R1"]["PosY"])) == (58.0, -60.0)
    assert (float(rows["R2"]["PosX"]), float(rows["R2"]["PosY"])) == (72.0, -60.0)
    assert rows["R1"]["Side"] == "top"
    assert rows["R2"]["Package"] == "R_0603_1608Metric"


def test_positions_follow_the_drill_origin_unless_told_not_to(
    copy: TinyBoard, tmp_path: Path
) -> None:
    """With the origin at the board's bottom-left, a part is at (x, height - y)."""
    board = load(copy.pcb)
    origin = pcbnew.VECTOR2I(kb.mm(kb.OX), kb.mm(kb.OY + tiny_board.HEIGHT))
    board.GetDesignSettings().SetAuxOrigin(origin)
    pcbnew.SaveBoard(str(copy.pcb), board)

    def r1(**options: bool) -> tuple[float, float]:
        run = cli.export_positions(copy.pcb, tmp_path / "pos.csv", **options)
        with open(run.files[0], newline="", encoding="utf-8") as handle:
            row = next(r for r in csv.DictReader(handle) if r["Ref"] == "R1")
        return float(row["PosX"]), float(row["PosY"])

    assert r1() == (8.0, 10.0)  # layout x, and height 20 less y 10
    assert r1(use_drill_origin=False) == (58.0, -60.0)


def test_svg_and_render_write_a_vector_and_a_png(
    tiny: TinyBoard, tmp_path: Path
) -> None:
    svg = cli.export_svg(
        tiny.pcb, tmp_path / "top.svg", ["F.Cu", "F.SilkS", "Edge.Cuts"]
    )
    assert "<svg" in Path(svg.files[0]).read_text(encoding="utf-8")
    png = cli.render_3d(
        tiny.pcb,
        tmp_path / "iso.png",
        rotate=(-40, 0, -20),
        width=320,
        height=240,
        quality="basic",
        zoom=1.1,
    )
    assert Path(png.files[0]).read_bytes().startswith(SIGNATURE)


def test_a_missing_board_is_a_kicad_cli_error_with_the_tools_words(
    tmp_path: Path,
) -> None:
    with pytest.raises(cli.KicadCliError) as raised:
        cli.drc(tmp_path / "missing.kicad_pcb", tmp_path / "drc.rpt")
    assert "kicad-cli pcb drc failed" in raised.value.message
    assert not (tmp_path / "drc.rpt").exists()


# --- set_copper, end to end ---------------------------------------------------

STACKUP = (
    '\t(stackup\n\t\t(layer "F.SilkS" (type "Top Silk Screen"))\n'
    '\t\t(layer "F.Paste" (type "Top Solder Paste"))\n'
    '\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "F.Cu" (type "copper") (thickness 0.035))\n'
    '\t\t(layer "dielectric 1" (type "core") (thickness 1.44) (material "FR4")'
    " (epsilon_r 4.5) (loss_tangent 0.02))\n"
    '\t\t(layer "B.Cu" (type "copper") (thickness 0.035))\n'
    '\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "B.Paste" (type "Bottom Solder Paste"))\n'
    '\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))\n'
    '\t\t(copper_finish "HAL lead-free")\n\t\t(dielectric_constraints no)\n\t)\n'
)


def copper_thickness(pcb: Path, folder: Path) -> list[float]:
    """Return the copper thicknesses KiCad writes into the Gerber job file."""
    run = cli.export_gerbers(pcb, folder)
    (job_file,) = [f for f in run.files if f.endswith(".gbrjob")]
    job = json.loads(Path(job_file).read_text(encoding="utf-8"))
    return [
        layer["Thickness"]
        for layer in job["MaterialStackup"]
        if layer["Type"] == "Copper"
    ]


def test_set_copper_changes_the_copper_kicad_exports_and_nothing_else(
    copy: TinyBoard, tmp_path: Path
) -> None:
    # A saved board has no stackup of its own; the board build puts one in as text, and
    # pcbnew then saves it in its multi-line form, which is what set_copper meets.
    text = copy.pcb.read_text(encoding="utf-8")
    start = text.index("\n", text.index("(setup")) + 1
    copy.pcb.write_text(text[:start] + STACKUP + text[start:], encoding="utf-8")
    pcbnew.SaveBoard(str(copy.pcb), load(copy.pcb))
    saved = copy.pcb.read_text(encoding="utf-8")
    assert re.search(
        r'\(layer "F\.Cu"\n\t+\(type "copper"\)\n\t+\(thickness 0\.035\)', saved
    )
    assert copper_thickness(copy.pcb, tmp_path / "before") == [0.035, 0.035]

    kb.set_copper(str(copy.pcb), 0.07)

    assert copper_thickness(copy.pcb, tmp_path / "after") == [0.07, 0.07]
    after = copy.pcb.read_text(encoding="utf-8")
    assert after == saved.replace("(thickness 0.035)", "(thickness 0.07)")
    assert isinstance(load(copy.pcb), pcbnew.BOARD)  # still a board KiCad can open
    assert cli.drc(copy.pcb, tmp_path / "drc.rpt", schematic_parity=True).report.clean


def test_set_copper_refuses_a_saved_board_that_has_no_stackup(copy: TinyBoard) -> None:
    before = copy.pcb.read_bytes()
    with pytest.raises(ValueError, match="expected two copper layers"):
        kb.set_copper(str(copy.pcb), 0.07)
    assert copy.pcb.read_bytes() == before


# --- clear_spot ---------------------------------------------------------------


def test_clear_spot_in_open_space_is_the_nearest_candidate(tiny: TinyBoard) -> None:
    board = load(tiny.pcb)
    # nothing but the GND pour near (15, 15): the first candidate is 0.5 mm up and left
    assert kb.clear_spot(board, (15.0, 15.0)) == pytest.approx((14.5, 14.5))


def test_clear_spot_gives_up_when_nothing_is_within_reach(tiny: TinyBoard) -> None:
    board = load(tiny.pcb)
    with pytest.raises(SystemExit, match=r"no clear GND via spot near \(15.0, 15.0\)"):
        kb.clear_spot(board, (15.0, 15.0), reach=0.5)  # candidates start at 0.6 mm


def spot_report(
    copy: TinyBoard, spot: tuple[float, float], report: Path
) -> cli.DrcReport:
    """Put a GND via at ``spot``, and a GND track to it from (15, 15); run DRC."""
    board = load(copy.pcb)
    kb.track(board, [(15.0, 15.0), spot], 0.3, "GND")
    kb.via(board, *spot, "GND", d=0.6, drill=0.3)
    pcbnew.SaveBoard(str(copy.pcb), board)
    return cli.drc(copy.pcb, report).report


def test_clear_spot_steers_round_other_nets_copper_and_drc_agrees(
    copy: TinyBoard, tmp_path: Path
) -> None:
    """A /NET_A track 0.5 mm above the nearest candidate: it must pick another spot."""
    board = load(copy.pcb)
    kb.track(board, [(13.0, 14.0), (17.0, 14.0)], 0.25, "NET_A")
    pcbnew.SaveBoard(str(copy.pcb), board)
    spot = kb.clear_spot(load(copy.pcb), (15.0, 15.0))
    assert spot != pytest.approx((14.5, 14.5))
    assert 0.6 <= ((spot[0] - 15.0) ** 2 + (spot[1] - 15.0) ** 2) ** 0.5 <= 2.5
    # The spot passes KiCad's own clearance rule (0.2 mm; clear_spot asks for 0.25)...
    placed = spot_report(copy, spot, tmp_path / "spot.rpt")
    assert "clearance" not in placed.categories
    # ...and the candidate it skipped does not: a via there is 0.075 mm from the track.
    skipped = spot_report(copy, (14.5, 14.5), tmp_path / "skipped.rpt")
    assert skipped.categories["clearance"] >= 1
    assert (
        "/NET_A"
        in next(v for v in skipped.violations if v.category == "clearance").nets
    )


# --- the hazards, each in a child Python so a broken process cannot spoil the run


def run_child(code: str, *args: str) -> str:
    """Run ``code`` in a fresh interpreter; return its line that starts RESULT."""
    done = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT")]
    assert lines, f"the child printed no RESULT line:\n{done.stdout}\n{done.stderr}"
    return lines[-1]


REMOVE_CHILD = """
import gc, sys
import pcbnew
shielded = sys.argv[2] == "shielded"
if shielded:
    from pcbkit.kicad import board as kb
board = pcbnew.LoadBoard(sys.argv[1])
victim = list(board.GetTracks())[0]
if shielded:
    kb.remove(board, victim)
else:
    board.Remove(victim)
del victim
gc.collect()
try:
    count = len(board.GetTracks())
    again = type(pcbnew.LoadBoard(sys.argv[1])).__name__
    print("RESULT ok", count, again)
except TypeError as err:
    print("RESULT broken", err)
"""


def test_without_remove_freeing_a_removed_track_breaks_pcbnew(tiny: TinyBoard) -> None:
    """The control: the next test only means something while this one holds."""
    result = run_child(REMOVE_CHILD, str(tiny.pcb), "bare")
    assert result.startswith("RESULT broken"), (
        "freeing a removed track no longer breaks pcbnew on this KiCad, so remove() "
        "may be simplified (and the shield test below proves nothing): " + result
    )
    assert "SwigPyObject" in result


def test_remove_keeps_pcbnew_working_after_the_removed_track_is_let_go(
    tiny: TinyBoard,
) -> None:
    result = run_child(REMOVE_CHILD, str(tiny.pcb), "shielded")
    assert result == "RESULT ok 2 BOARD"  # two tracks and a via, less the one removed


HITTEST_CHILD = """
import math, random, sys
import pcbnew
from pcbkit.kicad import board as kb

def mm(v):
    return pcbnew.FromMM(float(v))

board = pcbnew.NewBoard(sys.argv[1])
board.Add(pcbnew.NETINFO_ITEM(board, "/NET_A"))
tracks = []
shapes = [(0, 0, 10, 0, 0.25), (0, 5, 8, 11, 0.5), (20, 20, 20, 12, 1.0),
          (30, 0, 30.3, 0.4, 0.2), (40, 40, 40, 40, 0.3)]
for x0, y0, x1, y1, w in shapes:
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(mm(50 + x0), mm(50 + y0)))
    t.SetEnd(pcbnew.VECTOR2I(mm(50 + x1), mm(50 + y1)))
    t.SetWidth(mm(w))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNet(board.FindNet("/NET_A"))
    board.Add(t)
    tracks.append(t)
rng = random.Random(7)
checked = mismatched = 0
for t in tracks:
    a, b, w = t.GetStart(), t.GetEnd(), t.GetWidth()
    for accuracy in (0, w // 2, 100000):
        limit = accuracy + w / 2
        for _ in range(400):
            u = rng.choice([0.0, 0.25, 0.5, 1.0, rng.random()])
            ref = (a.x + u * (b.x - a.x), a.y + u * (b.y - a.y))
            theta = rng.random() * 2 * math.pi
            dist = rng.uniform(0, 3) * limit
            px = int(ref[0] + dist * math.cos(theta))
            py = int(ref[1] + dist * math.sin(theta))
            p = pcbnew.VECTOR2I(px, py)
            if abs(kb.segment_distance(a, b, p) - limit) < 5:  # KiCad rounds to nm
                continue
            checked += 1
            if t.HitTest(p, accuracy) != kb.point_in_track(t, p, accuracy):
                mismatched += 1
print("RESULT", checked, mismatched)
"""


def test_point_in_track_agrees_with_kicads_own_hit_test_off_the_boundary(
    tmp_path: Path,
) -> None:
    """Compare the two on thousands of points, skipping any within 5 nm of the edge."""
    result = run_child(HITTEST_CHILD, str(tmp_path / "scratch.kicad_pcb")).split()
    checked, mismatched = int(result[1]), int(result[2])
    assert checked > 5000
    assert mismatched == 0
