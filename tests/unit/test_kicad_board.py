"""Unit tests for the pcbnew helpers in pcbkit.kicad.board, against a fake pcbnew.

The fake (tests/fake_pcbnew.py) refuses what KiCad 10's pcbnew refuses and records what
the helpers ask of it, so these tests check the arithmetic and the choices the helpers
make: the offset, the numpy coercion, the polygon, the layer set, which setter name to
call. What KiCad itself then does with the items is the integration tests' business.
"""

from __future__ import annotations

import inspect
import math
import sys
import types
from typing import Any

import pytest

from pcbkit.kicad import board as kb
from tests import fake_pcbnew as fp


class NpFloat(float):
    """Stands for np.float64: a float subclass, which pcbnew's exact type check refuses.

    Like numpy's, its sums stay its own type, so ``50.0 + NpFloat(1)`` is still one.
    """

    def __add__(self, other: Any) -> NpFloat:
        """Add, staying an NpFloat."""
        return NpFloat(float(self) + other)

    __radd__ = __add__


class NpInt:
    """Stands for np.int64: not an int subclass, but it converts to a float."""

    def __init__(self, value: int) -> None:
        """Hold the value."""
        self.value = value

    def __float__(self) -> float:
        """Return the value as a float."""
        return float(self.value)

    def __radd__(self, other: float) -> NpFloat:
        """Add, as numpy does: an int64 plus a float is a float64."""
        return NpFloat(other + self.value)


@pytest.fixture
def board() -> fp.Board:
    """Return an empty fake board."""
    return fp.Board()


# --- units --------------------------------------------------------------------


def test_the_fake_refuses_numpy_scalars_as_kicad_10_does(
    fake_pcbnew: types.ModuleType,
) -> None:
    """The control for the coercion tests: without ``mm`` the call fails."""
    for value in (NpFloat(1.5), NpInt(2), True):
        with pytest.raises(TypeError, match="FromMM"):
            fake_pcbnew.FromMM(value)  # type: ignore[attr-defined]


def test_mm_converts_millimetres_to_nanometres_as_an_int(
    fake_pcbnew: types.ModuleType,
) -> None:
    assert kb.mm(1) == 1_000_000
    assert kb.mm(0.25) == 250_000
    assert kb.mm(-2.5) == -2_500_000
    assert type(kb.mm(0.25)) is int


def test_mm_turns_numpy_scalars_into_plain_floats(
    fake_pcbnew: types.ModuleType,
) -> None:
    assert kb.mm(NpFloat(1.5)) == 1_500_000
    assert kb.mm(NpInt(2)) == 2_000_000
    assert type(kb.mm(NpFloat(1.5))) is int


def test_mm_still_refuses_what_is_not_a_number(
    fake_pcbnew: types.ModuleType,
) -> None:
    """A bool or a string is a bug upstream: do not paper over it."""
    for value in (True, "1.5", None):
        with pytest.raises(TypeError):
            kb.mm(value)


def test_mm_passes_a_vector_through_to_pcbnew(fake_pcbnew: types.ModuleType) -> None:
    assert kb.mm(fp.Vec(1_000_000, 2_000_000)) == (1_000_000_000_000, 2_000_000_000_000)


def test_pt_adds_the_offset_to_layout_millimetres(
    fake_pcbnew: types.ModuleType,
) -> None:
    assert (kb.OX, kb.OY) == (50.0, 50.0)
    assert kb.pt(0, 0) == fp.Vec(50_000_000, 50_000_000)
    assert kb.pt(5.5, 12) == fp.Vec(55_500_000, 62_000_000)
    assert kb.pt(-1, -2) == fp.Vec(49_000_000, 48_000_000)


def test_pt_accepts_numpy_scalars_even_after_the_offset_is_added(
    fake_pcbnew: types.ModuleType,
) -> None:
    """``OX + np.float64`` is still numpy, so the coercion has to happen in ``mm``."""
    x, y = NpFloat(1.0), NpInt(2)
    assert type(kb.OX + x) is NpFloat
    assert kb.pt(x, y) == fp.Vec(51_000_000, 52_000_000)


def test_to_local_takes_the_offset_back_off(fake_pcbnew: types.ModuleType) -> None:
    assert kb.to_local(fp.Vec(55_500_000, 62_000_000)) == (5.5, 12.0)
    for x, y in [(0.0, 0.0), (12.345, 67.89), (-3.25, 100.5)]:
        assert kb.to_local(kb.pt(x, y)) == pytest.approx((x, y), abs=1e-6)


# --- nets and pads ------------------------------------------------------------


def test_N_finds_a_net_by_its_name_without_the_leading_slash(board: fp.Board) -> None:
    assert kb.N(board, "NET_A") == "net-a"
    assert kb.N(board, "GND") == "net-gnd"


def test_N_raises_KeyError_naming_the_net(board: fp.Board) -> None:
    with pytest.raises(KeyError, match="NOPE"):
        kb.N(board, "NOPE")
    with pytest.raises(KeyError):
        kb.N(board, "/NET_A")  # the slash is added by N, so this looks for "//NET_A"


def test_pad_and_ppos_find_a_pad_by_number_and_report_layout_millimetres(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    first = fp.Pad("1", kb.pt(10, 20))
    second = fp.Pad("2", kb.pt(11.5, 20))
    board.footprints["R1"] = fp.Footprint([first, second])
    assert kb.pad(board, "R1", 2) is second  # a number is matched as text
    assert kb.pad(board, "R1", "1") is first
    assert kb.ppos(board, "R1", 2) == pytest.approx((11.5, 20.0), abs=1e-6)


def test_pad_raises_KeyError_for_a_pad_that_is_not_there(board: fp.Board) -> None:
    board.footprints["R1"] = fp.Footprint([fp.Pad("1", fp.Vec(0, 0))])
    with pytest.raises(KeyError, match="R1:3"):
        kb.pad(board, "R1", 3)


# --- tracks, vias, zones, rule areas ------------------------------------------


def test_track_adds_one_locked_segment_per_leg_on_the_net(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.track(board, [(0, 0), (10, 0), (10, 5)], 0.25, "NET_A")
    assert len(board.items) == 2
    first, second = board.items
    assert first.props["SetStart"] == (fp.Vec(50_000_000, 50_000_000),)
    assert first.props["SetEnd"] == (fp.Vec(60_000_000, 50_000_000),)
    assert second.props["SetStart"] == (fp.Vec(60_000_000, 50_000_000),)
    assert second.props["SetEnd"] == (fp.Vec(60_000_000, 55_000_000),)
    for segment in board.items:
        assert segment.props["SetWidth"] == (250_000,)
        assert segment.props["SetNet"] == ("net-a",)
        assert segment.props["SetLocked"] == (True,)
        assert segment.props["SetLayer"] == (fp.F_CU,)


def test_track_takes_a_layer_and_a_single_point_makes_nothing(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.track(board, [(0, 0), (1, 1)], 1.0, "GND", fp.B_CU)
    assert board.items[0].props["SetLayer"] == (fp.B_CU,)
    kb.track(board, [(5, 5)], 1.0, "GND")
    assert len(board.items) == 1


def test_track_accepts_numpy_coordinates_and_width(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.track(
        board, [(NpFloat(1), NpFloat(2)), (NpInt(3), NpInt(2))], NpFloat(0.5), "GND"
    )
    assert board.items[0].props["SetWidth"] == (500_000,)
    assert board.items[0].props["SetEnd"] == (fp.Vec(53_000_000, 52_000_000),)


def test_track_on_an_unknown_net_raises_before_adding_anything(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    with pytest.raises(KeyError, match="NOPE"):
        kb.track(board, [(0, 0), (1, 1)], 0.25, "NOPE")
    assert board.items == []


def test_via_is_locked_with_the_given_size_and_drill(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.via(board, 3, 4, "NET_A")
    kb.via(board, 5, 6, "GND", d=0.7, drill=0.3)
    default, small = board.items
    assert default.props["SetPosition"] == (fp.Vec(53_000_000, 54_000_000),)
    assert (default.props["SetWidth"], default.props["SetDrill"]) == (
        (800_000,),
        (400_000,),
    )
    assert default.props["SetNet"] == ("net-a",) and default.props["SetLocked"] == (
        True,
    )
    assert (small.props["SetWidth"], small.props["SetDrill"]) == (
        (700_000,),
        (300_000,),
    )
    assert small.props["SetNet"] == ("net-gnd",)


def test_keepout_makes_a_rule_area_over_a_rectangle(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.keepout(board, 1, 2, 4, 6)
    (z,) = board.items
    assert z.props["SetIsRuleArea"] == (True,)
    assert z.props["SetDoNotAllowTracks"] == (True,)
    assert z.props["SetDoNotAllowVias"] == (True,)
    assert z.props["SetDoNotAllowZoneFills"] == (True,)
    assert z.props["SetDoNotAllowPads"] == (False,)
    assert z.props["SetDoNotAllowFootprints"] == (False,)
    assert z.props["SetLayerSet"][0].layers == [fp.F_CU, fp.B_CU]
    assert z.Outline().outlines == [
        [
            fp.Vec(51_000_000, 52_000_000),
            fp.Vec(54_000_000, 52_000_000),
            fp.Vec(54_000_000, 56_000_000),
            fp.Vec(51_000_000, 56_000_000),
        ]
    ]


def test_keepout_switches_follow_the_arguments_and_the_layers_are_by_name(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.keepout(
        board, 0, 0, 1, 1, tracks=False, vias=True, pours=False, layers=("B.Cu",)
    )
    kb.keepout(board, 0, 0, 1, 1, tracks=True, vias=False, pours=True)
    only_vias, no_vias = board.items
    assert only_vias.props["SetDoNotAllowTracks"] == (False,)
    assert only_vias.props["SetDoNotAllowVias"] == (True,)
    assert only_vias.props["SetDoNotAllowZoneFills"] == (False,)
    assert only_vias.props["SetLayerSet"][0].layers == [fp.B_CU]
    assert no_vias.props["SetDoNotAllowTracks"] == (True,)
    assert no_vias.props["SetDoNotAllowVias"] == (False,)
    assert no_vias.props["SetDoNotAllowZoneFills"] == (True,)


def test_keepout_with_pts_follows_the_polygon_and_ignores_the_rectangle(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    triangle = [(0, 0), (8, 0), (4, 6)]
    kb.keepout(board, 99, 99, 99, 99, pts=triangle)
    (z,) = board.items
    assert z.Outline().outlines == [[kb.pt(x, y) for x, y in triangle]]


def test_keepout_uses_the_older_pour_setter_when_the_zone_has_no_new_one(
    monkeypatch: pytest.MonkeyPatch, board: fp.Board
) -> None:
    """KiCad 9 called the switch SetDoNotAllowCopperPour; KiCad 10 renamed it."""
    monkeypatch.setitem(sys.modules, "pcbnew", fp.make_pcbnew(9))
    kb.keepout(board, 0, 0, 1, 1, pours=False)
    (z,) = board.items
    assert z.props["SetDoNotAllowCopperPour"] == (False,)
    assert "SetDoNotAllowZoneFills" not in z.props
    kb.keepout(board, 0, 0, 1, 1, pours=True)
    assert board.items[1].props["SetDoNotAllowCopperPour"] == (True,)


def test_zone_is_a_pour_with_thermal_reliefs_unless_full(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    z = kb.zone(board, "GND", fp.B_CU, kb.rect(0, 0, 10, 5))
    assert board.items == [z]
    assert z.props["SetLayer"] == (fp.B_CU,)
    assert z.props["SetNet"] == ("net-gnd",)
    assert z.props["SetAssignedPriority"] == (0,)
    assert z.props["SetMinThickness"] == (250_000,)
    assert z.props["SetLocalClearance"] == (250_000,)
    assert z.props["SetPadConnection"] == ("thermal",)
    assert z.props["SetThermalReliefGap"] == (400_000,)
    assert z.props["SetThermalReliefSpokeWidth"] == (600_000,)
    assert z.props["SetIslandRemovalMode"] == ("always",)
    assert z.Outline().outlines == [
        [kb.pt(0, 0), kb.pt(10, 0), kb.pt(10, 5), kb.pt(0, 5)]
    ]


def test_zone_options_set_priority_width_clearance_and_solid_pads(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    z = kb.zone(
        board,
        "NET_A",
        fp.F_CU,
        kb.rect(0, 0, 1, 1),
        priority=3,
        full=True,
        min_w=1.1,
        clearance=0.3,
    )
    assert z.props["SetAssignedPriority"] == (3,)
    assert z.props["SetPadConnection"] == ("full",)
    assert z.props["SetMinThickness"] == (1_100_000,)
    assert z.props["SetLocalClearance"] == (300_000,)


# --- outline and text ---------------------------------------------------------


def test_add_line_defaults_to_the_board_outline_layer_and_a_tenth_of_a_millimetre(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.add_line(board, 0, 0, 30, 0)
    (line,) = board.items
    assert line.props["SetShape"] == ("segment",)
    assert line.props["SetStart"] == (kb.pt(0, 0),)
    assert line.props["SetEnd"] == (kb.pt(30, 0),)
    assert line.props["SetLayer"] == (fp.EDGE_CUTS,)
    assert line.props["SetWidth"] == (100_000,)
    kb.add_line(board, 0, 0, 1, 1, layer=fp.F_SILKS, w=0.2)
    assert board.items[1].props["SetLayer"] == (fp.F_SILKS,)
    assert board.items[1].props["SetWidth"] == (200_000,)


def arc_points(board: fp.Board) -> tuple[Any, Any, Any]:
    """Return the start, middle and end of the last arc added, in layout millimetres."""
    start, mid, end = board.items[-1].props["SetArcGeometry"]
    return kb.to_local(start), kb.to_local(mid), kb.to_local(end)


def test_add_arc_puts_its_middle_point_halfway_round_a_quarter_turn(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.add_arc(board, 0, 0, 10, 0, 0, 10)
    start, mid, end = arc_points(board)
    assert start == pytest.approx((10, 0), abs=1e-6)
    assert end == pytest.approx((0, 10), abs=1e-6)
    assert mid == pytest.approx(
        (10 * math.cos(math.pi / 4), 10 * math.sin(math.pi / 4)), abs=1e-6
    )
    assert board.items[-1].props["SetShape"] == ("arc",)
    assert board.items[-1].props["SetLayer"] == (fp.EDGE_CUTS,)


def test_add_arc_goes_the_short_way_in_either_direction_and_across_the_seam(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    kb.add_arc(board, 0, 0, 0, 10, 10, 0)  # the same quarter, drawn backwards
    assert arc_points(board)[1] == pytest.approx((7.0711, 7.0711), abs=1e-4)
    # from 170 to -170 degrees the short way passes through 180: the middle is (-r, 0)
    r = 10.0
    a, b = math.radians(170), math.radians(-170)
    kb.add_arc(
        board, 0, 0, r * math.cos(a), r * math.sin(a), r * math.cos(b), r * math.sin(b)
    )
    assert arc_points(board)[1] == pytest.approx((-10, 0), abs=1e-6)


def test_add_arc_is_about_its_centre_so_the_offset_does_not_move_it(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    """A rounded corner: centre (27, 3), radius 3, from (27, 0) round to (30, 3)."""
    kb.add_arc(board, 27, 3, 27, 0, 30, 3)
    start, mid, end = arc_points(board)
    assert start == pytest.approx((27, 0), abs=1e-6)
    assert end == pytest.approx((30, 3), abs=1e-6)
    assert mid == pytest.approx(
        (27 + 3 * math.cos(-math.pi / 4), 3 + 3 * math.sin(-math.pi / 4)), abs=1e-6
    )


def test_add_text_sets_size_stroke_rotation_and_returns_the_item(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    t = kb.add_text(board, "J1", 5, 6)
    assert board.items == [t]
    assert t.props["SetText"] == ("J1",)
    assert t.props["SetPosition"] == (kb.pt(5, 6),)
    assert t.props["SetLayer"] == (fp.F_SILKS,)
    assert t.props["SetTextSize"] == (fp.Vec(1_000_000, 1_000_000),)
    assert t.props["SetTextThickness"] == (150_000,)
    assert t.props["SetTextAngleDegrees"] == (0,)
    assert "SetHorizJustify" not in t.props


def test_add_text_stroke_is_a_fraction_of_the_size_and_bold_is_thicker(
    fake_pcbnew: types.ModuleType, board: fp.Board
) -> None:
    plain = kb.add_text(board, "A", 0, 0, size=0.8)
    bold = kb.add_text(board, "B", 0, 0, size=0.8, bold=True, rot=90, layer=fp.F_CU)
    assert fp.to_mm(plain.props["SetTextThickness"][0]) == pytest.approx(
        0.8 * 0.15, abs=1e-5
    )
    assert fp.to_mm(bold.props["SetTextThickness"][0]) == pytest.approx(
        0.8 * 0.2, abs=1e-5
    )
    assert bold.props["SetTextAngleDegrees"] == (90,)
    assert bold.props["SetLayer"] == (fp.F_CU,)


@pytest.mark.parametrize(
    ("justify", "expected"), [("left", "left"), ("right", "right"), ("centre", None)]
)
def test_add_text_justification_left_or_right_else_centred(
    fake_pcbnew: types.ModuleType, board: fp.Board, justify: str, expected: str | None
) -> None:
    t = kb.add_text(board, "x", 0, 0, justify=justify)
    if expected is None:
        assert "SetHorizJustify" not in t.props
    else:
        assert t.props["SetHorizJustify"] == (expected,)


# --- the contract -------------------------------------------------------------

REQUIRED = inspect.Parameter.empty

# Each helper's parameters as the board generators wrote them (name, default). The one
# change is a layer default of None for pcbnew.F_Cu, Edge_Cuts or F_SilkS, so that the
# module imports without pcbnew; None means that same layer.
SIGNATURES: dict[str, list[tuple[str, Any]]] = {
    "mm": [("v", REQUIRED)],
    "pt": [("x", REQUIRED), ("y", REQUIRED)],
    "to_local": [("v", REQUIRED)],
    "N": [("board", REQUIRED), ("name", REQUIRED)],
    "pad": [("board", REQUIRED), ("ref", REQUIRED), ("num", REQUIRED)],
    "ppos": [("board", REQUIRED), ("ref", REQUIRED), ("num", REQUIRED)],
    "rect": [("x0", REQUIRED), ("y0", REQUIRED), ("x1", REQUIRED), ("y1", REQUIRED)],
    "track": [
        ("board", REQUIRED),
        ("pts", REQUIRED),
        ("width", REQUIRED),
        ("netname", REQUIRED),
        ("layer", None),
    ],
    "via": [
        ("board", REQUIRED),
        ("x", REQUIRED),
        ("y", REQUIRED),
        ("netname", REQUIRED),
        ("d", 0.8),
        ("drill", 0.4),
    ],
    "keepout": [
        ("board", REQUIRED),
        ("x0", REQUIRED),
        ("y0", REQUIRED),
        ("x1", REQUIRED),
        ("y1", REQUIRED),
        ("tracks", True),
        ("vias", True),
        ("pours", True),
        ("layers", ("F.Cu", "B.Cu")),
        ("pts", None),
    ],
    "zone": [
        ("board", REQUIRED),
        ("netname", REQUIRED),
        ("layer", REQUIRED),
        ("pts", REQUIRED),
        ("priority", 0),
        ("full", False),
        ("min_w", 0.25),
        ("clearance", 0.25),
    ],
    "clear_spot": [
        ("board", REQUIRED),
        ("near", REQUIRED),
        ("net", "GND"),
        ("via_d", 0.6),
        ("drill", 0.3),
        ("reach", 2.5),
        ("gap", 0.25),
    ],
    "add_line": [
        ("board", REQUIRED),
        ("x0", REQUIRED),
        ("y0", REQUIRED),
        ("x1", REQUIRED),
        ("y1", REQUIRED),
        ("layer", None),
        ("w", 0.1),
    ],
    "add_arc": [
        ("board", REQUIRED),
        ("cx", REQUIRED),
        ("cy", REQUIRED),
        ("sx", REQUIRED),
        ("sy", REQUIRED),
        ("ex", REQUIRED),
        ("ey", REQUIRED),
        ("layer", None),
        ("w", 0.1),
    ],
    "add_text": [
        ("board", REQUIRED),
        ("text", REQUIRED),
        ("x", REQUIRED),
        ("y", REQUIRED),
        ("size", 1.0),
        ("layer", None),
        ("rot", 0),
        ("bold", False),
        ("justify", None),
    ],
}


@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_each_helper_keeps_the_signature_the_generators_call_it_with(name: str) -> None:
    """Fail if a parameter is renamed, reordered or given another default.

    docs/project-interface.md calls these signatures a contract, and routing hooks in a
    board project are written against them.
    """
    found = [
        (p.name, p.default)
        for p in inspect.signature(getattr(kb, name)).parameters.values()
    ]
    assert found == SIGNATURES[name]
