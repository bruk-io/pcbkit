"""Unit tests for pcbkit.silk: the geometry, the project's data, the guard's logic.

The pass itself needs a real board, so it is tested with real KiCad in
tests/integration/test_silk_real_kicad.py. What is here needs no KiCad: the boxes the
text keeps clear of, the text sizes, the checks on silk.py's names, the rule that finds
a second run, and the printed result. ``add_text`` runs against the fake pcbnew.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import click
import pytest

from pcbkit import silk
from pcbkit.project import ProjectError, load_project
from tests.board_files import TOML, write_file
from tests.fake_pcbnew import Board, Vec

# --- the boxes ---------------------------------------------------------------------


def test_boxes_that_overlap_are_a_hit_and_boxes_that_only_touch_are_not() -> None:
    boxes = silk.Boxes()
    boxes.add(0, 0, 10, 10)
    assert boxes.hit(5, 5, 6, 6)  # inside
    assert boxes.hit(9, 9, 20, 20)  # a corner in
    assert boxes.hit(-5, 4, 1, 5)  # across an edge
    assert not boxes.hit(10, 0, 20, 10)  # touching along the right edge
    assert not boxes.hit(0, 10, 10, 20)  # touching along the bottom edge
    assert not boxes.hit(11, 11, 12, 12)


def test_a_pad_grows_a_box_on_every_side() -> None:
    boxes = silk.Boxes()
    boxes.add(0, 0, 10, 10, pad=0.5)
    assert boxes.hit(10.2, 5, 11, 6)
    assert boxes.hit(-0.4, 5, -0.1, 6)
    assert not boxes.hit(10.5, 5, 11, 6)


def test_a_box_set_starts_empty_and_hits_nothing() -> None:
    assert not silk.Boxes().hit(-1e9, -1e9, 1e9, 1e9)


def test_a_text_is_taken_to_fill_nine_tenths_of_its_size_per_character() -> None:
    x0, y0, x1, y1 = silk.text_box(10.0, 5.0, "ABCD", 0.8)
    assert (x1 - x0) == pytest.approx(4 * 0.8 * 0.9 + 0.3)
    assert (y1 - y0) == pytest.approx(0.8 + 0.25)
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == pytest.approx((10.0, 5.0))


def test_a_text_turned_a_quarter_turn_swaps_its_width_and_height() -> None:
    flat = silk.text_box(0.0, 0.0, "ABCD", 0.8)
    turned = silk.text_box(0.0, 0.0, "ABCD", 0.8, vertical=True)
    assert (turned[2] - turned[0], turned[3] - turned[1]) == pytest.approx(
        (flat[3] - flat[1], flat[2] - flat[0])
    )


# --- add_text, with the fake pcbnew -------------------------------------------------


def thickness_mm(item: Any) -> float:
    """Return a fake text's stroke in millimetres."""
    return item.props["SetTextThickness"][0] / 1_000_000


def test_a_text_is_put_on_the_silkscreen_at_its_place_on_the_page(
    fake_pcbnew: types.ModuleType,
) -> None:
    board = Board()
    text = silk.add_text(board, "HI", 10.0, 5.0)
    assert board.items == [text]
    assert text.props["SetText"] == ("HI",)
    assert text.props["SetLayer"] == (fake_pcbnew.F_SilkS,)
    assert text.props["SetPosition"] == (Vec(60_000_000, 55_000_000),)  # layout + 50
    assert text.props["SetTextSize"] == (Vec(800_000, 800_000),)  # 0.8 mm by default
    assert text.props["SetTextAngleDegrees"] == (0,)


@pytest.mark.parametrize(
    ("size", "bold", "stroke"),
    [
        (0.8, False, 0.15),  # 0.12 would print too thin: never under 0.15
        (0.8, True, 0.16),
        (1.0, False, 0.15),
        (1.0, True, 0.2),
        (2.0, False, 0.3),
        (1.2, True, 0.24),
    ],
)
def test_the_stroke_is_a_fraction_of_the_size_but_never_under_the_least_a_fab_prints(
    fake_pcbnew: types.ModuleType, size: float, bold: bool, stroke: float
) -> None:
    text = silk.add_text(Board(), "X", 0, 0, size, bold=bold)
    assert thickness_mm(text) == pytest.approx(stroke)


def test_a_text_can_go_on_another_layer_and_turn(
    fake_pcbnew: types.ModuleType,
) -> None:
    text = silk.add_text(Board(), "X", 0, 0, 1.0, rot=90, layer=31)
    assert text.props["SetLayer"] == (31,)
    assert text.props["SetTextAngleDegrees"] == (90,)


def test_a_text_adds_its_box_to_the_obstacles_with_a_tenth_of_a_millimetre_to_spare(
    fake_pcbnew: types.ModuleType,
) -> None:
    obstacles = silk.Boxes()
    silk.add_text(Board(), "ABCD", 10.0, 5.0, 0.8, obstacles=obstacles)
    x0, y0, x1, y1 = silk.text_box(10.0, 5.0, "ABCD", 0.8)
    assert obstacles.b == [(x0 - 0.1, y0 - 0.1, x1 + 0.1, y1 + 0.1)]


@pytest.mark.parametrize("rot", [90, -90, 270])
def test_a_quarter_turn_either_way_makes_the_box_tall(
    fake_pcbnew: types.ModuleType, rot: int
) -> None:
    obstacles = silk.Boxes()
    silk.add_text(Board(), "ABCD", 0.0, 0.0, 0.8, rot=rot, obstacles=obstacles)
    x0, y0, x1, y1 = obstacles.b[0]
    assert (y1 - y0) > (x1 - x0)


def test_without_obstacles_nothing_is_recorded(fake_pcbnew: types.ModuleType) -> None:
    assert silk.add_text(Board(), "X", 0, 0).props["SetText"] == ("X",)


# --- a second run -------------------------------------------------------------------

A = ("TINY", 5, 20.0, 20.0)
B = ("V1", 5, 33.0, 6.0)
B_ELSEWHERE = ("V1", 5, 33.0, 6.5)
B_OTHER_LAYER = ("V1", 7, 33.0, 6.0)


def test_texts_added_are_those_after_has_more_of_than_before() -> None:
    assert silk.new_texts([A], [A, B]) == [B]
    assert silk.new_texts([A], [A, A]) == [A]  # a second copy is new
    assert silk.new_texts([A, B], [A]) == []


def test_a_text_drawn_again_on_the_same_place_is_stacked() -> None:
    assert silk.stacked([A, B], [A, B, A, B]) == [A, B]
    assert silk.stacked([A, B], [A, B, A]) == [A]


def test_a_text_that_differs_in_place_layer_or_content_is_not() -> None:
    assert silk.stacked([A, B], [A, B, B_ELSEWHERE]) == []
    assert silk.stacked([A, B], [A, B, B_OTHER_LAYER]) == []
    assert silk.stacked([A], [A, ("TINY!", 5, 20.0, 20.0)]) == []


def test_two_of_a_kind_in_one_run_are_not_a_second_run() -> None:
    """Two polarity marks are both "+": equal texts at different places, or twice."""
    assert silk.stacked([], [A, A]) == []
    assert silk.stacked([B], [B, A, A]) == []


def test_a_run_that_adds_nothing_has_nothing_stacked() -> None:
    assert silk.stacked([A, B], [A, B]) == []
    assert silk.stacked([], []) == []


# --- silk.py's names ----------------------------------------------------------------


def silk_module(**names: Any) -> types.ModuleType:
    """Return a module that stands for a silk.py with the given names."""
    module = types.ModuleType("silk")
    for name, value in names.items():
        setattr(module, name, value)
    return module


def test_a_project_without_a_silk_py_has_nothing_to_draw() -> None:
    data = silk.read_silk(None)
    assert (data.labels, data.conn_labels) == ([], {})
    assert (data.hide_ref, data.keep_ref) == (frozenset(), frozenset())
    assert data.extra is None
    assert (data.company, data.comments, data.date) == ("", [], "")


def test_a_silk_py_with_no_names_is_the_same() -> None:
    assert silk.read_silk(silk_module()) == silk.read_silk(None)


def test_every_name_is_read() -> None:
    def extra(board: Any, api: Any) -> None:
        raise AssertionError("not called here")

    data = silk.read_silk(
        silk_module(
            LABELS=[("A", 1, 2.5, 1.0, 90), ["B", 3, 4, 0.8, 0]],
            CONN_LABELS={"J1": "SUPPLY"},
            HIDE_REF=["TP1", "TP2"],
            KEEP_REF=("U1",),
            COMPANY="Acme",
            COMMENTS=["one", "two"],
            DATE="2026-01-02",
            extra=extra,
            SOMETHING_ELSE=[1, 2, 3],  # the project's own data is none of the engine's
        )
    )
    assert data.labels == [("A", 1, 2.5, 1.0, 90), ("B", 3, 4, 0.8, 0)]
    assert data.conn_labels == {"J1": "SUPPLY"}
    assert data.hide_ref == frozenset({"TP1", "TP2"})
    assert data.keep_ref == frozenset({"U1"})
    assert (data.company, data.comments, data.date) == (
        "Acme",
        ["one", "two"],
        "2026-01-02",
    )
    assert data.extra is extra


BAD_SILK = [
    (
        {"LABELS": [("A", 1, 2, 1.0)]},
        r"LABELS\[0\] should be \(text, x, y, size, rot\)",
    ),
    ({"LABELS": [(5, 1, 2, 1.0, 0)]}, r"LABELS\[0\] should be"),
    ({"LABELS": [("A", 1, 2, "big", 0)]}, r"LABELS\[0\] should be"),
    ({"LABELS": ["A", 1]}, r"LABELS\[0\] should be"),
    ({"CONN_LABELS": ["J1"]}, r"CONN_LABELS should be a dict of reference to text"),
    ({"CONN_LABELS": {"J1": 3}}, r"CONN_LABELS should be a dict"),
    ({"HIDE_REF": "J1"}, r"HIDE_REF should be a set of references, got 'J1'"),
    ({"HIDE_REF": 5}, r"HIDE_REF should be a set of references"),
    ({"KEEP_REF": [1, 2]}, r"KEEP_REF should hold only references"),
    (
        {"HIDE_REF": {"J1", "TP2"}, "KEEP_REF": {"TP2", "TP3"}},
        r"both HIDE_REF and KEEP_REF: TP2",
    ),
    ({"extra": "nope"}, r"extra should be a function"),
    ({"COMPANY": 5}, r"COMPANY and DATE should be strings"),
    ({"DATE": 20260102}, r"COMPANY and DATE should be strings"),
    ({"COMMENTS": "one"}, r"COMMENTS should be a list of up to 9 strings"),
    (
        {"COMMENTS": [str(n) for n in range(10)]},
        r"COMMENTS should be a list of up to 9",
    ),
    ({"COMMENTS": ["ok", 3]}, r"COMMENTS should be a list of up to 9"),
]


@pytest.mark.parametrize(("names", "message"), BAD_SILK)
def test_a_mistake_in_silk_py_is_a_message_that_names_the_value(
    names: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProjectError, match=message):
        silk.read_silk(silk_module(**names))


# --- running it without a board ------------------------------------------------------


def test_without_a_board_it_says_to_build_first(
    tmp_path: Path, fake_pcbnew: types.ModuleType
) -> None:
    write_file(tmp_path / "pcbkit.toml", TOML)
    with pytest.raises(
        click.ClickException, match=r"my_board\.kicad_pcb not found: run"
    ):
        silk.apply_silk(load_project(tmp_path))


def test_a_board_that_is_named_is_the_one_looked_for(
    tmp_path: Path, fake_pcbnew: types.ModuleType
) -> None:
    write_file(tmp_path / "pcbkit.toml", TOML)
    with pytest.raises(click.ClickException, match=r"elsewhere\.kicad_pcb not found"):
        silk.apply_silk(load_project(tmp_path), tmp_path / "elsewhere.kicad_pcb")


# --- the printed result -------------------------------------------------------------


def test_a_clean_result_is_one_line() -> None:
    result = silk.SilkResult(Path("/p/kicad/b.kicad_pcb"), 48, [], [])
    assert silk.format_result(result, Path("/p")) == (
        "silk       kicad/b.kicad_pcb: 48 text(s) added"
    )


def test_moved_references_and_warnings_are_listed_under_it() -> None:
    result = silk.SilkResult(
        Path("/p/kicad/b.kicad_pcb"),
        3,
        ["C31", "D2"],
        ["no room for label J4 SENSOR", "label collides: 5V"],
    )
    assert silk.format_result(result, Path("/p")).splitlines() == [
        "silk       kicad/b.kicad_pcb: 3 text(s) added",
        "  refs moved to fab (see assembly drawing): C31 D2",
        "  warning: no room for label J4 SENSOR",
        "  warning: label collides: 5V",
    ]


def test_a_board_outside_the_project_is_shown_in_full() -> None:
    result = silk.SilkResult(Path("/elsewhere/b.kicad_pcb"), 0, [], [])
    assert "/elsewhere/b.kicad_pcb" in silk.format_result(result, Path("/p"))
