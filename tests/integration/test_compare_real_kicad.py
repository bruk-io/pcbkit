"""Integration: `pcbkit compare` on real boards, with real pcbnew.

The tiny board (tests/tiny_board.py) is the "old" board. Each test makes a "new" one by
loading it, changing one thing with pcbnew and saving it, and then shows that exactly
that thing is reported: where it is (in layout millimetres), which rule flagged it, and
what the command exits with. The same boards are used to show what must not be flagged:
a copy saved by KiCad, and a sliver of copper below the tolerance.

These tests need KiCad's own Python, where ``import pcbnew`` works. Make that
environment once and run them with it (see tests/integration/test_kicad_core.py):

    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python they are skipped, for the reason given just below.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit import compare  # noqa: E402
from pcbkit.cli import cli  # noqa: E402
from pcbkit.compare import CompareReport, OneSidedCopper  # noqa: E402
from pcbkit.kicad import board as kb  # noqa: E402
from tests import tiny_board  # noqa: E402
from tests.tiny_board import TinyBoard  # noqa: E402

pytestmark = pytest.mark.kicad

Edit = Callable[[Any], None]


@pytest.fixture(scope="module")
def tiny(tmp_path_factory: pytest.TempPathFactory) -> TinyBoard:
    """Build the tiny board once and share it: the tests only read it."""
    return tiny_board.build(tmp_path_factory.mktemp("tiny"))


@pytest.fixture
def edited(tiny: TinyBoard, tmp_path: Path) -> Callable[[Edit], Path]:
    """Return a function that saves a changed copy of the tiny board and names it."""
    count = 0

    def make(edit: Edit) -> Path:
        nonlocal count
        count += 1
        board = pcbnew.LoadBoard(str(tiny.pcb))
        edit(board)
        path = tmp_path / f"variant{count}.kicad_pcb"
        pcbnew.SaveBoard(str(path), board)
        return path

    return make


# --- edits: each changes one thing on the tiny board --------------------------------


def net_b_track(board: Any) -> Any:
    """Return the track that joins R1 and R2 (net NET_B)."""
    (found,) = [
        t
        for t in board.GetTracks()
        if t.GetClass() == "PCB_TRACK" and t.GetNetname() == "/NET_B"
    ]
    return found


def shift_net_b(dy_mm: float) -> Edit:
    """Return an edit that moves the NET_B track down by ``dy_mm``."""

    def edit(board: Any) -> None:
        track = net_b_track(board)
        step = pcbnew.VECTOR2I(0, kb.mm(dy_mm))
        track.SetStart(track.GetStart() + step)
        track.SetEnd(track.GetEnd() + step)

    return edit


def add_via(board: Any) -> None:
    """Add a ground via at (4, 4), inside the ground pour and clear of everything."""
    kb.via(board, 4, 4, "GND")


def split_net_b(board: Any) -> None:
    """Cut the NET_B track in two at its midpoint: same copper, one more track."""
    track = net_b_track(board)
    mid = (track.GetStart() + track.GetEnd()) / 2
    second = pcbnew.PCB_TRACK(board)
    second.SetStart(mid)
    second.SetEnd(track.GetEnd())
    second.SetWidth(track.GetWidth())
    second.SetLayer(track.GetLayer())
    second.SetNet(track.GetNet())
    track.SetEnd(mid)
    board.Add(second)


def hatch_pour(board: Any) -> None:
    """Fill the ground pour as a hatch: the same outline, a much smaller fill."""
    for zone in board.Zones():
        if not zone.GetIsRuleArea():
            zone.SetFillMode(pcbnew.ZONE_FILL_MODE_HATCH_PATTERN)
            zone.SetHatchThickness(kb.mm(0.5))
            zone.SetHatchGap(kb.mm(1.0))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def add_arc_track(board: Any) -> None:
    """Add a semicircular arc track, radius 2 mm, on NET_B and clear of other copper."""
    arc = pcbnew.PCB_ARC(board)
    arc.SetStart(kb.pt(2, 5))
    arc.SetMid(kb.pt(4, 3))
    arc.SetEnd(kb.pt(6, 5))
    arc.SetWidth(kb.mm(0.25))
    arc.SetLayer(pcbnew.F_Cu)
    arc.SetNet(kb.N(board, "NET_B"))
    board.Add(arc)


def add_keepout(board: Any) -> None:
    """Add a rule area that bans nothing it could change: no copper moves."""
    kb.keepout(board, 10, 3, 14, 6, tracks=False, vias=False, pours=False)


# --- helpers over the report ------------------------------------------------------


def side(report: CompareReport, layer: str, only_in: str) -> OneSidedCopper:
    """Return the one-sided copper of ``layer`` that only ``only_in`` has."""
    (found,) = [c for c in report.copper if (c.layer, c.only_in) == (layer, only_in)]
    return found


def run_compare(old: Path, new: Path, *options: str) -> Result:
    """Run `pcbkit compare OLD NEW` in-process, on real boards."""
    return CliRunner().invoke(cli, ["compare", str(old), str(new), *options])


def near(x: float, y: float, at: tuple[float, float], tol: float = 0.05) -> bool:
    """Return True if layout point (x, y) is within ``tol`` mm of ``at``."""
    return abs(x - at[0]) <= tol and abs(y - at[1]) <= tol


# --- boards that match ------------------------------------------------------------


def test_a_board_compared_with_itself_matches_and_exits_0(tiny: TinyBoard) -> None:
    report = compare.compare_boards(tiny.pcb, tiny.pcb)
    assert report.differences == []
    assert report.old_tracks == report.new_tracks
    assert report.old_tracks.count == 2
    assert report.old_tracks.length_mm == pytest.approx(14.025, abs=0.001)
    assert report.old_vias == report.new_vias == 1
    assert [c.total_mm2 for c in report.copper] == [0.0] * 4
    result = run_compare(tiny.pcb, tiny.pcb)
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "The boards match: no differences."


def test_a_copy_loaded_and_saved_by_pcbnew_matches(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Match a board that was loaded and saved again: the same copper in a new file."""
    copy = edited(lambda board: None)
    assert run_compare(tiny.pcb, copy).exit_code == 0
    assert run_compare(copy, tiny.pcb).exit_code == 0


def test_a_board_that_differs_only_in_silkscreen_matches(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Ignore what is not copper: a label added after routing moves no copper."""
    labelled = edited(lambda board: kb.add_text(board, "HELLO", 15, 4, size=1.0))
    assert labelled.read_bytes() != tiny.pcb.read_bytes()
    result = run_compare(tiny.pcb, labelled)
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "The boards match: no differences."


def test_text_on_a_copper_layer_is_copper_and_is_flagged(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Show that the copper comparison sees any copper object, not only tracks."""
    lettered = edited(
        lambda board: kb.add_text(board, "HELLO", 15, 4, size=1.0, layer=pcbnew.F_Cu)
    )
    report = compare.compare_boards(tiny.pcb, lettered)
    assert report.differs
    assert not report.tracks_differ and not report.vias_differ
    assert side(report, "F.Cu", "new").over
    assert not side(report, "F.Cu", "old").over
    assert run_compare(tiny.pcb, lettered).exit_code == 1


def test_the_tiny_board_has_the_zones_the_other_tests_rely_on(tiny: TinyBoard) -> None:
    """Check the premise: one filled ground pour and two keep-outs, with no fill."""
    report = compare.compare_boards(tiny.pcb, tiny.pcb)
    filled = [z for z in report.zones if z.net]
    assert [(z.net, z.layer) for z in filled] == [("/GND", "B.Cu")]
    assert filled[0].new_fill_mm2 > 500
    assert [z.new_fill_mm2 for z in report.zones if not z.net] == [0.0, 0.0]


# --- one change each, flagged by the rule that exists for it ---------------------


def test_a_moved_track_is_flagged_where_it_was_and_where_it_went(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    moved = edited(shift_net_b(1.0))
    report = compare.compare_boards(tiny.pcb, moved)
    assert report.differs
    # Same number of tracks and the same length: only the copper says it moved.
    assert not report.tracks_differ and not report.vias_differ
    assert not any(z.differs for z in report.zones)
    (was,) = side(report, "F.Cu", "old").over
    (now,) = side(report, "F.Cu", "new").over
    # Layout millimetres: the track ran along y = 10 between the pads, mid-way x = 15.
    assert near(was.x_mm, was.y_mm, (15.0, 10.0)), was
    assert near(now.x_mm, now.y_mm, (15.0, 11.0)), now
    assert was.area_mm2 == pytest.approx(
        11.55 * 0.25, rel=0.01
    )  # the part between pads
    assert not side(report, "B.Cu", "old").over and not side(report, "B.Cu", "new").over
    result = run_compare(tiny.pcb, moved)
    assert result.exit_code == 1
    assert "F.Cu only in old: total 2.888 mm2 in 1 piece, 1 over 0.01 mm2" in (
        result.output
    )


def test_an_extra_via_is_flagged_by_the_count_and_by_its_copper(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    report = compare.compare_boards(tiny.pcb, edited(add_via))
    assert report.vias_differ and not report.tracks_differ
    assert (report.old_vias, report.new_vias) == (1, 2)
    assert "vias: old 1, new 2" in report.differences
    (ring,) = side(report, "F.Cu", "new").over
    assert near(ring.x_mm, ring.y_mm, (4.0, 4.0)), ring
    assert ring.area_mm2 == pytest.approx(0.497, abs=0.01)  # a 0.8 mm via
    assert not side(report, "F.Cu", "old").over
    assert not side(report, "B.Cu", "new").over  # it sits inside the ground pour


def test_a_changed_pour_is_flagged_by_its_fill_area(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    report = compare.compare_boards(tiny.pcb, edited(hatch_pour))
    (pour,) = [z for z in report.zones if z.net]
    assert pour.differs
    # The premise of the test: the outline did not change, only the fill, by a lot.
    assert (pour.net, pour.layer, pour.outline_mm2) == ("/GND", "B.Cu", 570.4)
    assert pour.delta_mm2 < -100
    assert not report.tracks_differ and not report.vias_differ
    assert any(line.startswith("zone /GND B.Cu") for line in report.differences)
    assert run_compare(tiny.pcb, edited(hatch_pour)).exit_code == 1


def test_a_track_cut_in_two_is_flagged_by_the_count_alone(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    report = compare.compare_boards(tiny.pcb, edited(split_net_b))
    assert (report.old_tracks.count, report.new_tracks.count) == (2, 3)
    # The length is the same to a micrometre, and the copper is the same, so only the
    # count can be what flags this.
    assert abs(report.old_tracks.length_mm - report.new_tracks.length_mm) < 0.001
    assert report.tracks_differ
    assert not report.vias_differ and not any(z.differs for z in report.zones)
    assert not any(c.over for c in report.copper)
    assert [line.split(":")[0] for line in report.differences] == ["tracks"]


def test_an_arc_track_counts_as_a_track_with_its_arc_length(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Count and measure arcs too: a route is not only straight segments."""
    report = compare.compare_boards(tiny.pcb, edited(add_arc_track))
    assert (report.old_tracks.count, report.new_tracks.count) == (2, 3)
    added = report.new_tracks.length_mm - report.old_tracks.length_mm
    assert added == pytest.approx(3.14159265 * 2, abs=0.001)  # half a circle of r = 2
    assert report.tracks_differ and not report.vias_differ
    (arc,) = side(report, "F.Cu", "new").over
    assert arc.area_mm2 > 1.0  # the copper of the arc: it is judged as copper too


def test_a_keep_out_that_only_one_board_has_is_flagged_though_no_copper_moves(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    added = edited(add_keepout)
    report = compare.compare_boards(tiny.pcb, added)
    assert not any(c.over or c.total_mm2 for c in report.copper)
    (extra,) = [z for z in report.zones if z.differs]
    assert extra.old_fill_mm2 is None and extra.new_fill_mm2 == 0.0
    assert extra.net == "" and extra.outline_mm2 == pytest.approx(12.0, abs=0.1)
    assert run_compare(tiny.pcb, added).exit_code == 1
    assert run_compare(added, tiny.pcb).exit_code == 1  # lost, the other way round


# --- a sliver below the tolerance: reported, not flagged ----------------------------


def test_a_sliver_below_the_tolerance_is_reported_but_not_flagged(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Move the track by half a micrometre: 11.55 mm long, so about 0.006 mm2 a side."""
    nudged = edited(shift_net_b(0.0005))
    report = compare.compare_boards(tiny.pcb, nudged)
    for only_in in ("old", "new"):
        found = side(report, "F.Cu", only_in)
        assert found.pieces == 1
        assert found.total_mm2 == pytest.approx(0.0058, abs=0.0005)
        assert found.over == ()
    assert not report.differs
    result = run_compare(tiny.pcb, nudged)
    assert result.exit_code == 0, result.output
    assert "F.Cu only in old: total 0.006 mm2 in 1 piece, 0 over 0.01 mm2" in (
        result.output
    )


def test_the_piece_tolerance_is_configurable(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Flag the same sliver once the tolerance is set below its area."""
    nudged = edited(shift_net_b(0.0005))
    assert run_compare(tiny.pcb, nudged, "--piece-tol", "0.01").exit_code == 0
    strict = run_compare(tiny.pcb, nudged, "--piece-tol", "0.005")
    assert strict.exit_code == 1
    assert "F.Cu only in old: total 0.006 mm2 in 1 piece, 1 over 0.005 mm2" in (
        strict.output
    )
    report = compare.compare_boards(tiny.pcb, nudged, piece_tol_mm2=0.005)
    assert report.tolerances.piece_mm2 == 0.005 and report.differs


def test_the_track_length_tolerance_is_configurable(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Let a caller decide how far apart two total lengths may be before they differ."""
    split = edited(split_net_b)
    assert compare.compare_boards(tiny.pcb, split).tolerances.length_mm == 0.001
    # The count differs whatever the length tolerance is.
    report = compare.compare_boards(tiny.pcb, split, length_tol_mm=5.0)
    assert report.tolerances.length_mm == 5.0 and report.tracks_differ
    # And a moved track keeps the length: a tolerance of 0 would only flag real change.
    same_length = compare.compare_boards(
        tiny.pcb, edited(shift_net_b(1.0)), length_tol_mm=0.0
    )
    assert not same_length.tracks_differ


def test_the_fill_tolerance_is_configurable(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    """Accept the hatch's fill change when the fill tolerance is larger than it."""
    hatched = edited(hatch_pour)
    lax = compare.compare_boards(tiny.pcb, hatched, fill_tol_mm2=1000.0)
    assert not any(z.differs for z in lax.zones)
    assert lax.differs  # the copper itself still differs


# --- the JSON ---------------------------------------------------------------------


def test_json_of_real_boards_lists_the_flagged_piece_with_its_position(
    tiny: TinyBoard, edited: Callable[[Edit], Path]
) -> None:
    result = run_compare(tiny.pcb, edited(shift_net_b(1.0)), "--json")
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["identical"] is False
    old_f = next(
        c for c in data["copper"] if (c["layer"], c["only_in"]) == ("F.Cu", "old")
    )
    (piece,) = old_f["over_tolerance"]
    assert piece["x_mm"] == pytest.approx(15.0, abs=0.05)
    assert piece["y_mm"] == pytest.approx(10.0, abs=0.05)
    assert data["tracks"]["differs"] is False and data["vias"]["differs"] is False


# --- a file that is not a board ---------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ["this is not a board\n", "", "(kicad_sch (version 20250114))\n"],
    ids=["text", "empty", "another-kind-of-file"],
)
def test_a_file_that_is_not_a_board_is_never_a_match(
    tiny: TinyBoard, tmp_path: Path, content: str
) -> None:
    """Exit 2, not 0: two unreadable files compared with each other say nothing."""
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text(content, encoding="utf-8")
    for old, new in ((bad, bad), (bad, tiny.pcb), (tiny.pcb, bad)):
        result = run_compare(old, new)
        assert result.exit_code == 2, (old, new, result.output)
        assert "cannot read" in result.output and "bad.kicad_pcb" in result.output
