"""Unit tests for pcbkit.compare and `pcbkit compare`: what is judged, said and exited.

Nothing here loads a board. The measuring half of compare.py (pcbnew, polygons) is
proved against real boards in tests/integration/test_compare_real_kicad.py; these tests
hand the judging half the numbers that half would measure, so each rule can be shown to
fire on its own. The CLI tests fake ``compare_boards`` (the unit under test is the
command: its arguments, its output and its exit code) and fake pcbnew for the tier 2
check.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from pcbkit import compare
from pcbkit.cli import cli
from pcbkit.compare import (
    BoardSummary,
    CompareError,
    CompareReport,
    OneSidedCopper,
    Piece,
    Tolerances,
    TrackStats,
)

GND = ("/GND", "B.Cu", 100.0)
KEEP_OUT = ("", "F.Cu", 20.0)


def summary(
    tracks: tuple[int, float] = (10, 100.0),
    vias: int = 5,
    zones: dict[tuple[str, str, float], float] | None = None,
) -> BoardSummary:
    """Return a board's numbers; by default 10 tracks, 5 vias and one pour."""
    return BoardSummary(
        TrackStats(*tracks), vias, {GND: 80.0} if zones is None else zones
    )


def side(
    layer: str = "F.Cu",
    only_in: str = "old",
    total: float = 0.0,
    pieces: int = 0,
    over: tuple[Piece, ...] = (),
) -> OneSidedCopper:
    """Return the copper of one layer that one board alone has."""
    return OneSidedCopper(layer, only_in, total, pieces, over)


def no_copper() -> list[OneSidedCopper]:
    """Return the four empty one-sided results of two boards with the same copper."""
    return [
        side(layer, only_in) for layer in ("F.Cu", "B.Cu") for only_in in ("old", "new")
    ]


def judge(
    old: BoardSummary | None = None,
    new: BoardSummary | None = None,
    copper: list[OneSidedCopper] | None = None,
    tolerances: Tolerances | None = None,
) -> CompareReport:
    """Judge two boards' numbers; the default is the same board twice."""
    return compare.build_report(
        "old.kicad_pcb",
        "new.kicad_pcb",
        old or summary(),
        new or summary(),
        no_copper() if copper is None else copper,
        tolerances or Tolerances(),
    )


# --- what is a difference ----------------------------------------------------------


def test_the_same_numbers_twice_are_no_difference() -> None:
    report = judge()
    assert report.differences == []
    assert not report.differs
    assert not report.tracks_differ and not report.vias_differ


def test_a_different_track_count_alone_is_a_difference() -> None:
    """Flag one more track of the same total length: a split, not a move."""
    report = judge(new=summary(tracks=(11, 100.0)))
    assert report.tracks_differ
    assert not report.vias_differ
    assert report.differences == ["tracks: old 10 (100.000 mm), new 11 (100.000 mm)"]


@pytest.mark.parametrize(
    ("change", "flagged"),
    [(0.0, False), (0.0005, False), (-0.0005, False), (0.002, True), (-0.002, True)],
)
def test_track_length_moves_the_verdict_only_beyond_the_rounding_tolerance(
    change: float, flagged: bool
) -> None:
    """Treat lengths within a micrometre as equal: their sums depend on item order."""
    report = judge(new=summary(tracks=(10, 100.0 + change)))
    assert report.tracks_differ is flagged
    assert report.differs is flagged


def test_a_different_via_count_alone_is_a_difference() -> None:
    report = judge(new=summary(vias=6))
    assert report.vias_differ and not report.tracks_differ
    assert report.differences == ["vias: old 5, new 6"]


@pytest.mark.parametrize(
    ("change", "flagged"),
    [
        (0.0, False),
        (0.499, False),
        (-0.499, False),
        (0.5, True),
        (-0.5, True),
        (30, True),
    ],
)
def test_a_zone_fill_is_a_difference_when_it_moves_by_the_tolerance_or_more(
    change: float, flagged: bool
) -> None:
    """Flag at 0.5 mm2 exactly: the rule is "less than the tolerance is the same"."""
    report = judge(new=summary(zones={GND: 80.0 + change}))
    (zone,) = report.zones
    assert zone.differs is flagged
    assert report.differs is flagged
    assert zone.delta_mm2 == pytest.approx(change)


@pytest.mark.parametrize(
    ("old_zones", "new_zones", "where"),
    [
        ({GND: 80.0}, {GND: 80.0, KEEP_OUT: 0.0}, "only in new"),
        ({GND: 80.0, KEEP_OUT: 0.0}, {GND: 80.0}, "only in old"),
    ],
)
def test_a_zone_only_one_board_has_is_a_difference_even_with_no_fill(
    old_zones: dict[tuple[str, str, float], float],
    new_zones: dict[tuple[str, str, float], float],
    where: str,
) -> None:
    """Catch a keep-out that was added or lost: no copper moves, but it is a change."""
    report = judge(old=summary(zones=old_zones), new=summary(zones=new_zones))
    assert report.differs
    assert report.differences == [
        f"zone keep-out F.Cu (outline 20.0 mm2): {where} (fill 0.0 mm2)"
    ]
    (changed,) = [z for z in report.zones if z.differs]
    assert changed.delta_mm2 is None


def test_zones_are_matched_by_net_layer_and_outline_and_listed_in_that_order() -> None:
    zones = {
        ("/VIN", "F.Cu", 50.0): 10.0,
        GND: 80.0,
        ("/GND", "F.Cu", 100.0): 70.0,
        KEEP_OUT: 0.0,
    }
    report = judge(old=summary(zones=zones), new=summary(zones=zones))
    assert [(z.net, z.layer, z.outline_mm2) for z in report.zones] == [
        KEEP_OUT,
        ("/GND", "B.Cu", 100.0),
        ("/GND", "F.Cu", 100.0),
        ("/VIN", "F.Cu", 50.0),
    ]


def test_a_zone_that_changed_outline_shows_as_one_lost_and_one_new() -> None:
    old = summary(zones={("/GND", "B.Cu", 100.0): 80.0})
    new = summary(zones={("/GND", "B.Cu", 100.5): 80.0})
    report = judge(old=old, new=new)
    assert [z.differs for z in report.zones] == [True, True]
    assert [z.old_fill_mm2 for z in report.zones] == [80.0, None]
    assert [z.new_fill_mm2 for z in report.zones] == [None, 80.0]


def test_copper_pieces_over_the_tolerance_are_a_difference() -> None:
    big = Piece(1.5, 12.34, 5.0)
    report = judge(copper=[side("F.Cu", "new", 1.6, 4, (big,)), *no_copper()[1:]])
    assert report.differs
    assert report.differences == [
        "F.Cu: 1 piece(s) over 0.01 mm2 only in new (largest 1.500 mm2 at (12.3, 5.0))"
    ]


def test_a_layer_full_of_slivers_is_reported_but_is_not_a_difference() -> None:
    """Show 0.016 mm2 in 119 pieces, as a refilled pour does, without flagging it."""
    slivers = side("F.Cu", "old", total=0.016, pieces=119, over=())
    report = judge(copper=[slivers, *no_copper()[1:]])
    assert not report.differs
    text = compare.format_report(report)
    assert "F.Cu only in old: total 0.016 mm2 in 119 pieces, 0 over 0.01 mm2" in text
    assert text.splitlines()[-1] == "The boards match: no differences."


def test_the_copper_tolerance_is_a_property_of_the_measurement_not_the_judging() -> (
    None
):
    """Keep over-tolerance pieces in the data: build_report must not re-judge them."""
    tiny = Piece(0.0001, 1.0, 1.0)
    report = judge(
        copper=[side("B.Cu", "old", 0.0001, 1, (tiny,)), *no_copper()[1:]],
        tolerances=Tolerances(piece_mm2=0.00001),
    )
    assert report.differs
    assert report.tolerances.piece_mm2 == 0.00001


def test_each_kind_of_difference_is_reported_once_and_in_a_fixed_order() -> None:
    report = judge(
        new=summary(tracks=(12, 90.0), vias=1, zones={GND: 10.0}),
        copper=[side("B.Cu", "new", 2.0, 1, (Piece(2.0, 1.0, 2.0),)), *no_copper()],
    )
    kinds = [line.split(":")[0].split(" ")[0] for line in report.differences]
    assert kinds == ["tracks", "vias", "zone", "B.Cu"]


# --- the text report --------------------------------------------------------------


def test_the_text_report_marks_every_difference_and_counts_them() -> None:
    report = judge(
        new=summary(tracks=(12, 90.0), vias=7, zones={GND: 30.0, KEEP_OUT: 0.0}),
        copper=[
            side("F.Cu", "old", 3.0, 2, (Piece(2.5, 10.0, 20.0),)),
            *no_copper()[1:],
        ],
    )
    text = compare.format_report(report)
    lines = text.splitlines()
    assert "Tracks  old 10 (100.0 mm)  new 12 (90.0 mm)  DIFFERENT" in lines
    assert "Vias    old 5  new 7  DIFFERENT" in lines
    assert any(
        line.split()[:3] == ["/GND", "B.Cu", "100.0"] and line.endswith("DIFFERENT")
        for line in lines
    )
    assert any(
        line.split() == ["keep-out", "F.Cu", "20.0", "-", "0.0", "-", "DIFFERENT"]
        for line in lines
    )
    assert "      2.500 mm2 at (10.0, 20.0)" in text
    assert lines[-1] == f"The boards differ: {len(report.differences)} difference(s)."
    assert len(report.differences) == 5


def test_the_text_report_of_matching_boards_has_no_marker() -> None:
    text = compare.format_report(judge())
    assert "DIFFERENT" not in text
    assert "  same" in text
    assert text.splitlines()[-1] == "The boards match: no differences."


def test_top_limits_the_listed_pieces_and_says_how_many_were_left_out() -> None:
    pieces = tuple(Piece(5.0 - i, float(i), 1.0) for i in range(5))
    report = judge(copper=[side("F.Cu", "old", 20.0, 9, pieces), *no_copper()[1:]])
    text = compare.format_report(report, top=2)
    listed = [line for line in text.splitlines() if " mm2 at (" in line]
    assert len(listed) == 2
    assert "... and 3 more (--top N lists more, --json lists all)" in text
    assert "... and" not in compare.format_report(report, top=5)


def test_a_change_that_rounds_to_nothing_is_shown_as_zero_not_signed() -> None:
    assert compare._signed(0.004) == "0.0"
    assert compare._signed(-0.004) == "0.0"
    assert compare._signed(-0.06) == "-0.1"
    assert compare._signed(12.34) == "+12.3"
    report = judge(new=summary(zones={GND: 80.004}))
    row = next(
        line for line in compare.format_report(report).splitlines() if "/GND" in line
    )
    assert row.split() == ["/GND", "B.Cu", "100.0", "80.0", "80.0", "0.0"]


def test_the_report_with_no_zones_says_so() -> None:
    text = compare.format_report(judge(old=summary(zones={}), new=summary(zones={})))
    assert "neither board has a zone" in text


def test_the_text_report_names_both_files_and_the_tolerances_used() -> None:
    text = compare.format_report(judge(tolerances=Tolerances(0.02, 1.5, 0.001)))
    head = text.splitlines()[:4]
    assert head[1:3] == ["  old  old.kicad_pcb", "  new  new.kicad_pcb"]
    assert "over 0.02 mm2 is a difference" in head[3]
    assert "moved by 1.5 mm2 or more" in head[3]


# --- the JSON ---------------------------------------------------------------------


def test_the_json_is_plain_data_and_says_what_the_text_says() -> None:
    report = judge(
        new=summary(tracks=(11, 100.12345), vias=5, zones={GND: 70.0, KEEP_OUT: 0.0}),
        copper=[
            side("F.Cu", "old", 3.123456, 2, (Piece(2.123456, 10.126, 20.124),)),
            *no_copper()[1:],
        ],
    )
    data = json.loads(json.dumps(compare.report_to_dict(report)))
    assert data["identical"] is False
    assert data["differences"] == report.differences
    assert data["tolerances"] == {
        "piece_mm2": 0.01,
        "fill_mm2": 0.5,
        "length_mm": 0.001,
    }
    assert data["tracks"] == {
        "old": {"count": 10, "length_mm": 100.0},
        "new": {"count": 11, "length_mm": 100.1235},
        "differs": True,
    }
    assert data["vias"] == {"old": 5, "new": 5, "differs": False}
    keep_out, gnd = data["zones"]
    assert keep_out == {
        "net": "",
        "layer": "F.Cu",
        "outline_mm2": 20.0,
        "old_fill_mm2": None,
        "new_fill_mm2": 0.0,
        "delta_mm2": None,
        "differs": True,
    }
    assert gnd["delta_mm2"] == -10.0 and gnd["differs"] is True
    first = data["copper"][0]
    assert (first["layer"], first["only_in"], first["pieces"]) == ("F.Cu", "old", 2)
    assert first["total_mm2"] == 3.1235
    assert first["over_tolerance"] == [
        {"area_mm2": 2.1235, "x_mm": 10.13, "y_mm": 20.12}
    ]
    assert [(c["layer"], c["only_in"]) for c in data["copper"]] == [
        ("F.Cu", "old"),
        ("F.Cu", "new"),
        ("B.Cu", "old"),
        ("B.Cu", "new"),
    ]


def test_the_json_of_matching_boards_says_identical() -> None:
    data = compare.report_to_dict(judge())
    assert data["identical"] is True and data["differences"] == []


# --- loading ----------------------------------------------------------------------


def test_a_file_pcbnew_cannot_read_is_an_error_not_an_empty_board(
    fake_pcbnew: types.ModuleType, tmp_path: Path
) -> None:
    """Refuse a None from LoadBoard: two unreadable files must never "match"."""
    fake_pcbnew.LoadBoard = lambda path: None  # type: ignore[attr-defined]
    with pytest.raises(CompareError, match="cannot read .*bad.kicad_pcb as a KiCad"):
        compare.load_board(tmp_path / "bad.kicad_pcb")


def test_a_load_that_raises_is_an_error_too(
    fake_pcbnew: types.ModuleType, tmp_path: Path
) -> None:
    def boom(path: str) -> None:
        raise OSError("disk on fire")

    fake_pcbnew.LoadBoard = boom  # type: ignore[attr-defined]
    with pytest.raises(CompareError, match="disk on fire"):
        compare.load_board(tmp_path / "bad.kicad_pcb")


def test_could_not_compare_exits_2_and_not_1() -> None:
    assert CompareError("x").exit_code == 2


# --- the command ------------------------------------------------------------------


@pytest.fixture
def boards(tmp_path: Path) -> tuple[Path, Path]:
    """Return two files that exist; the command only needs them to be there."""
    old, new = tmp_path / "old.kicad_pcb", tmp_path / "new.kicad_pcb"
    old.write_text("(kicad_pcb)", encoding="utf-8")
    new.write_text("(kicad_pcb)", encoding="utf-8")
    return old, new


def run_compare(boards: tuple[Path, Path], *options: str) -> Result:
    """Run `pcbkit compare OLD NEW` in-process."""
    return CliRunner().invoke(
        cli, ["compare", str(boards[0]), str(boards[1]), *options]
    )


def fake_compare_boards(
    monkeypatch: pytest.MonkeyPatch, report: CompareReport
) -> list[dict[str, object]]:
    """Make compare_boards return ``report``; return the calls it gets."""
    calls: list[dict[str, object]] = []

    def fake(old: Path, new: Path, **options: float) -> CompareReport:
        calls.append({"old": old, "new": new, **options})
        return report

    monkeypatch.setattr(compare, "compare_boards", fake)
    return calls


def test_compare_needs_pcbnew_and_says_how_to_get_it(
    monkeypatch: pytest.MonkeyPatch, boards: tuple[Path, Path]
) -> None:
    """Fail with the setup message, before touching a board, where pcbnew is absent."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    calls = fake_compare_boards(monkeypatch, judge())
    result = run_compare(boards)
    assert result.exit_code == 1
    assert "pcbnew isn't importable here. In the board project, run: pcbkit setup" in (
        result.output
    )
    assert calls == []


def test_compare_exits_0_and_prints_the_report_when_the_boards_match(
    monkeypatch: pytest.MonkeyPatch,
    fake_pcbnew: types.ModuleType,
    boards: tuple[Path, Path],
) -> None:
    calls = fake_compare_boards(monkeypatch, judge())
    result = run_compare(boards)
    assert result.exit_code == 0
    assert result.output.splitlines()[-1] == "The boards match: no differences."
    assert calls == [
        {"old": boards[0], "new": boards[1], "piece_tol_mm2": 0.01, "fill_tol_mm2": 0.5}
    ]


def test_compare_exits_1_when_the_boards_differ(
    monkeypatch: pytest.MonkeyPatch,
    fake_pcbnew: types.ModuleType,
    boards: tuple[Path, Path],
) -> None:
    fake_compare_boards(monkeypatch, judge(new=summary(vias=6)))
    result = run_compare(boards)
    assert result.exit_code == 1
    assert "Vias    old 5  new 6  DIFFERENT" in result.output
    assert result.output.splitlines()[-1] == "The boards differ: 1 difference(s)."


def test_compare_json_prints_nothing_but_the_json_and_keeps_the_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    fake_pcbnew: types.ModuleType,
    boards: tuple[Path, Path],
) -> None:
    fake_compare_boards(monkeypatch, judge(new=summary(tracks=(11, 100.0))))
    result = run_compare(boards, "--json")
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["identical"] is False
    assert data["tracks"]["differs"] is True
    matching = fake_compare_boards(monkeypatch, judge())
    result = run_compare(boards, "--json")
    assert result.exit_code == 0 and json.loads(result.output)["identical"] is True
    assert len(matching) == 1


def test_compare_passes_the_tolerances_on_and_top_limits_the_listing(
    monkeypatch: pytest.MonkeyPatch,
    fake_pcbnew: types.ModuleType,
    boards: tuple[Path, Path],
) -> None:
    pieces = tuple(Piece(3.0 - i, float(i), 0.0) for i in range(3))
    report = judge(copper=[side("F.Cu", "old", 6.0, 3, pieces), *no_copper()[1:]])
    calls = fake_compare_boards(monkeypatch, report)
    result = run_compare(
        boards, "--piece-tol", "0.002", "--fill-tol", "2.5", "--top", "1"
    )
    assert calls[0]["piece_tol_mm2"] == 0.002 and calls[0]["fill_tol_mm2"] == 2.5
    assert result.output.count(" mm2 at (") == 1
    assert "... and 2 more" in result.output


def test_compare_exits_2_when_a_board_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    fake_pcbnew: types.ModuleType,
    boards: tuple[Path, Path],
) -> None:
    def refuse(old: Path, new: Path, **options: float) -> CompareReport:
        raise CompareError(f"cannot read {new} as a KiCad board")

    monkeypatch.setattr(compare, "compare_boards", refuse)
    result = run_compare(boards)
    assert result.exit_code == 2
    assert f"Error: cannot read {boards[1]} as a KiCad board" in result.output


@pytest.mark.parametrize(
    "options",
    [["--piece-tol", "-1"], ["--fill-tol", "x"], ["--top", "-2"]],
)
def test_compare_rejects_bad_option_values_as_usage_errors(
    boards: tuple[Path, Path], options: list[str]
) -> None:
    assert run_compare(boards, *options).exit_code == 2


def test_compare_names_a_missing_file_instead_of_comparing_nothing(
    tmp_path: Path, boards: tuple[Path, Path]
) -> None:
    result = CliRunner().invoke(
        cli, ["compare", str(boards[0]), str(tmp_path / "nowhere.kicad_pcb")]
    )
    assert result.exit_code == 2
    assert "nowhere.kicad_pcb" in result.output and "does not exist" in result.output


def test_compare_help_says_what_the_three_exit_codes_mean() -> None:
    output = CliRunner().invoke(cli, ["compare", "--help"]).output
    for option in ("--json", "--piece-tol", "--fill-tol", "--top"):
        assert option in output
    flat = " ".join(output.split())
    assert "Exits 0 when the boards match, 1 when they differ and 2" in flat
