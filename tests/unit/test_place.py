"""Unit tests for pcbkit.place: what it reads, and the parts that need no real pcbnew.

The netlist reader, the layout.py checks, the stackup text and the parking rule are
plain functions. The outline is tested against the fake pcbnew (what shapes it draws
and where). Placing a whole board needs real pcbnew and KiCad's footprint libraries, so
that is tests/integration/test_place_real_kicad.py.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import click
import numpy as np
import pytest

from pcbkit import place
from pcbkit.kicad import board as kb
from pcbkit.place import Component
from pcbkit.project import ProjectError, load_project
from tests.board_files import TOML, write_file
from tests.fake_pcbnew import Board, Vec

NETLIST = """\
(export (version "E")
  (design (source "my_board.kicad_sch") (tool "Eeschema 10.0.6"))
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0603_1608Metric")
      (fields (field (name "Footprint") "Resistor_SMD:R_0603_1608Metric"))
      (libsource (lib "Device") (part "R") (description "Resistor"))
      (sheetpath (names "/") (tstamps "/"))
      (tstamps "aaaaaaaa-0000-4000-8000-000000000001"))
    (comp (ref "TP1") (value "Test point")
      (libsource (lib "Connector") (part "TestPoint") (description "Test point"))
      (sheetpath (names "/") (tstamps "/"))
      (tstamps "aaaaaaaa-0000-4000-8000-000000000002")))
  (nets
    (net (code "1") (name "/GND")
      (node (ref "R1") (pin "2") (pinfunction "~") (pintype "passive"))
      (node (ref "TP1") (pin "1") (pinfunction "~") (pintype "passive")))
    (net (code "2") (name "/NET_A")
      (node (ref "R1") (pin "1") (pinfunction "~") (pintype "passive")))))
"""


# --- the netlist ------------------------------------------------------------------


def test_a_netlist_gives_each_part_its_value_footprint_and_schematic_id() -> None:
    netlist = place.parse_netlist(NETLIST)
    assert netlist.components == {
        "R1": Component(
            "10k",
            "Resistor_SMD:R_0603_1608Metric",
            # the part's own id, not the sheet's "/"
            "aaaaaaaa-0000-4000-8000-000000000001",
        ),
        "TP1": Component("Test point", "", "aaaaaaaa-0000-4000-8000-000000000002"),
    }


def test_a_netlist_gives_each_pin_its_net_with_the_leading_slash() -> None:
    assert place.parse_netlist(NETLIST).pin_nets == {
        ("R1", "2"): "/GND",
        ("TP1", "1"): "/GND",
        ("R1", "1"): "/NET_A",
    }


@pytest.mark.parametrize(
    "text",
    ["", "just words", '(export (version "E"))', "(export (components) (design))"],
)
def test_text_that_is_not_a_netlist_is_a_message_naming_the_file(text: str) -> None:
    with pytest.raises(
        click.ClickException, match=r"kicad/x\.net is not a KiCad netlist"
    ):
        place.parse_netlist(text, "kicad/x.net")


def test_a_component_without_its_schematic_id_is_a_message() -> None:
    """Each part has to carry the id that links its footprint to the schematic."""
    without = NETLIST.replace('(tstamps "aaaaaaaa-0000-4000-8000-000000000001")', "")
    with pytest.raises(click.ClickException, match="a component has no ref, value or"):
        place.parse_netlist(without, "kicad/x.net")


def test_an_unclosed_netlist_is_a_message_not_a_parse_error() -> None:
    cut = NETLIST[: NETLIST.index("(nets")]
    with pytest.raises(click.ClickException, match="not a KiCad netlist"):
        place.parse_netlist(cut)


# --- layout.py --------------------------------------------------------------------


def layout_module(**names: Any) -> types.ModuleType:
    """Return a module that stands for a layout.py with the given names."""
    module = types.ModuleType("layout")
    for name, value in names.items():
        setattr(module, name, value)
    return module


def test_a_full_layout_is_read_with_its_numbers_as_floats() -> None:
    layout = place.read_layout(
        layout_module(
            W=40,
            H=24.5,
            CORNER_R=2,
            P={"R1": (10, 8, -90), "J1": [1.5, 2.5, 0]},
            HOLES=[(4, 4), [36, 20]],
        )
    )
    assert (layout.width, layout.height, layout.corner) == (40.0, 24.5, 2.0)
    assert layout.parts == {"R1": (10.0, 8.0, -90.0), "J1": (1.5, 2.5, 0.0)}
    assert layout.holes == [(4.0, 4.0), (36.0, 20.0)]
    assert layout.outline is None


def test_the_corner_and_the_holes_default_to_none() -> None:
    layout = place.read_layout(layout_module(W=10, H=10, P={}))
    assert layout.corner == 0.0
    assert layout.holes == []


def test_numpy_numbers_are_numbers_and_a_bool_is_not() -> None:
    layout = place.read_layout(
        layout_module(W=np.float64(40), H=np.int64(24), P={"R1": (np.int64(1), 2, 0)})
    )
    assert (layout.width, layout.height) == (40.0, 24.0)
    assert layout.parts["R1"] == (1.0, 2.0, 0.0)
    assert not place.is_number(True)
    assert not place.is_number("3")
    assert place.is_number(np.float32(1.5))


def test_an_outline_function_is_kept_for_placement_to_call() -> None:
    def outline(board: Any, api: Any) -> None:
        raise AssertionError("not called here")

    layout = place.read_layout(layout_module(W=10, H=10, P={}, outline=outline))
    assert layout.outline is outline


BAD_LAYOUTS = [
    ({"H": 10, "P": {}}, r"layout\.py: W is missing"),
    ({"W": 10, "P": {}}, r"layout\.py: H is missing"),
    ({"W": "10", "H": 10, "P": {}}, r"W should be a number, got '10'"),
    ({"W": True, "H": 10, "P": {}}, r"W should be a number, got True"),
    ({"W": 0, "H": 10, "P": {}}, r"W and H should be above 0"),
    ({"W": 10, "H": 10, "P": {}, "CORNER_R": 6}, r"CORNER_R should be from 0 to half"),
    ({"W": 10, "H": 10, "P": {}, "CORNER_R": -1}, r"CORNER_R should be from 0"),
    ({"W": 10, "H": 10}, r"P should be a dict"),
    ({"W": 10, "H": 10, "P": [1]}, r"P should be a dict"),
    ({"W": 10, "H": 10, "P": {"R1": (1, 2)}}, r"P\['R1'\] should be \(x, y, rotation"),
    ({"W": 10, "H": 10, "P": {"R1": (1, 2, "up")}}, r"P\['R1'\] should be \(x, y"),
    (
        {"W": 10, "H": 10, "P": {}, "HOLES": [(1, 2, 3)]},
        r"HOLES\[0\] should be \(x, y\)",
    ),
    ({"W": 10, "H": 10, "P": {}, "HOLES": [(1, 2), 5]}, r"HOLES\[1\] should be"),
    ({"W": 10, "H": 10, "P": {}, "outline": 3}, r"outline should be a function"),
]


@pytest.mark.parametrize(("names", "message"), BAD_LAYOUTS)
def test_a_mistake_in_layout_py_is_a_message_that_names_the_value(
    names: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProjectError, match=message):
        place.read_layout(layout_module(**names))


def test_the_board_size_alone_can_be_read_for_the_silk_pass() -> None:
    assert place.layout_size(layout_module(W=40, H=24)) == (40.0, 24.0)
    with pytest.raises(ProjectError, match="W is missing"):
        place.layout_size(layout_module(H=24))


# --- mounting holes and parked parts ----------------------------------------------


@pytest.mark.parametrize(
    ("ref", "number"),
    [("H1", 1), ("H2", 2), ("H12", 12), ("H0", 0)],
)
def test_h_followed_by_a_number_is_a_mounting_hole(ref: str, number: int) -> None:
    assert place.hole_number(ref) == number


@pytest.mark.parametrize("ref", ["HS1", "H", "HR1", "R1", "h1", "H1A", "AH1", ""])
def test_any_other_reference_is_an_ordinary_part(ref: str) -> None:
    assert place.hole_number(ref) is None


def test_unplaced_parts_are_parked_in_a_column_below_the_board() -> None:
    """The first goes 16 mm under the bottom edge, and each next one 6 mm further."""
    assert place.parking_spot(68.0, 1) == (5.0, 84.0, 0.0)
    assert place.parking_spot(68.0, 2) == (5.0, 90.0, 0.0)
    assert place.parking_spot(20.0, 3) == (5.0, 48.0, 0.0)


# --- the stackup ------------------------------------------------------------------

# What tools/gen_pcb.py has always written for 1 oz copper, character for character.
ONE_OUNCE = (
    "\t(stackup\n"
    '\t\t(layer "F.SilkS" (type "Top Silk Screen"))\n'
    '\t\t(layer "F.Paste" (type "Top Solder Paste"))\n'
    '\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "F.Cu" (type "copper") (thickness 0.035))\n'
    '\t\t(layer "dielectric 1" (type "core") (thickness 1.44) (material "FR4") '
    "(epsilon_r 4.5) (loss_tangent 0.02))\n"
    '\t\t(layer "B.Cu" (type "copper") (thickness 0.035))\n'
    '\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "B.Paste" (type "Bottom Solder Paste"))\n'
    '\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))\n'
    '\t\t(copper_finish "HAL lead-free")\n'
    "\t\t(dielectric_constraints no)\n"
    "\t)\n"
)


def test_the_default_stackup_is_what_the_board_scripts_always_wrote() -> None:
    assert place.stackup_text(0.035, 1.6) == ONE_OUNCE


def test_two_ounce_copper_and_another_thickness_change_only_their_numbers() -> None:
    two = place.stackup_text(0.07, 1.6)
    assert two == ONE_OUNCE.replace("(thickness 0.035)", "(thickness 0.07)")
    thin = place.stackup_text(0.035, 1.2)
    assert thin == ONE_OUNCE.replace("(thickness 1.44)", "(thickness 1.04)")


SAVED = (
    "(kicad_pcb\n\t(version 20260206)\n\t(general\n\t\t(thickness 1.6)\n\t)\n"
    '\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)\n\t(net 0 "")\n)\n'
)


def test_the_stackup_goes_in_right_after_the_setup_line(tmp_path: Path) -> None:
    path = tmp_path / "b.kicad_pcb"
    path.write_text(SAVED, encoding="utf-8")
    place.add_stackup(path, 0.035, 1.6)
    text = path.read_text(encoding="utf-8")
    assert text == SAVED.replace(
        "\t(setup\n", "\t(setup\n" + ONE_OUNCE
    )  # nothing else moved


def test_a_stackup_that_is_there_already_keeps_its_layers_and_gets_the_copper(
    tmp_path: Path,
) -> None:
    path = tmp_path / "b.kicad_pcb"
    path.write_text(SAVED.replace("\t(setup\n", "\t(setup\n" + ONE_OUNCE), "utf-8")
    place.add_stackup(path, 0.07, 1.6)
    assert path.read_text(encoding="utf-8") == SAVED.replace(
        "\t(setup\n", "\t(setup\n" + place.stackup_text(0.07, 1.6)
    )


def test_a_stackup_with_odd_copper_layers_is_a_message(tmp_path: Path) -> None:
    path = tmp_path / "b.kicad_pcb"
    odd = ONE_OUNCE.replace('(layer "B.Cu" (type "copper") (thickness 0.035))\n', "")
    path.write_text(SAVED.replace("\t(setup\n", "\t(setup\n" + odd), "utf-8")
    with pytest.raises(click.ClickException, match="expected two copper layers"):
        place.add_stackup(path, 0.07, 1.6)


# --- the outline ------------------------------------------------------------------


def shapes_of(board: Board, kind: str) -> list[Any]:
    """Return the shapes on the fake board of one kind ("segment" or "arc")."""
    return [s for s in board.items if s.props["SetShape"][0] == kind]


def mm_point(x: float, y: float) -> Vec:
    """Return layout (x, y) mm as KiCad file coordinates in internal units."""
    return Vec(int((50 + x) * 1_000_000), int((50 + y) * 1_000_000))


def test_a_rounded_rectangle_is_four_sides_and_four_quarter_circle_corners(
    fake_pcbnew: types.ModuleType,
) -> None:
    board = Board()
    place.rounded_outline(board, 40.0, 24.0, 2.0)
    lines, arcs = shapes_of(board, "segment"), shapes_of(board, "arc")
    assert (len(lines), len(arcs)) == (4, 4)
    sides = {(s.props["SetStart"][0], s.props["SetEnd"][0]) for s in lines}
    assert sides == {
        (mm_point(2, 0), mm_point(38, 0)),
        (mm_point(40, 2), mm_point(40, 22)),
        (mm_point(38, 24), mm_point(2, 24)),
        (mm_point(0, 22), mm_point(0, 2)),
    }
    # each arc starts and ends where a side stops
    ends = {(s.props["SetArcGeometry"][0], s.props["SetArcGeometry"][2]) for s in arcs}
    assert ends == {
        (mm_point(38, 0), mm_point(40, 2)),
        (mm_point(40, 22), mm_point(38, 24)),
        (mm_point(2, 24), mm_point(0, 22)),
        (mm_point(0, 2), mm_point(2, 0)),
    }
    assert all(s.props["SetLayer"] == (25,) for s in board.items)  # Edge.Cuts


def test_with_a_radius_of_zero_the_sides_meet_and_there_are_no_arcs(
    fake_pcbnew: types.ModuleType,
) -> None:
    board = Board()
    place.rounded_outline(board, 40.0, 24.0, 0.0)
    assert (len(shapes_of(board, "segment")), len(shapes_of(board, "arc"))) == (4, 0)
    sides = {(s.props["SetStart"][0], s.props["SetEnd"][0]) for s in board.items}
    assert sides == {
        (mm_point(0, 0), mm_point(40, 0)),
        (mm_point(40, 0), mm_point(40, 24)),
        (mm_point(40, 24), mm_point(0, 24)),
        (mm_point(0, 24), mm_point(0, 0)),
    }


def test_the_outline_helpers_are_the_board_modules() -> None:
    """Placement draws with pcbkit.kicad.board, the module an outline hook is given."""
    assert place.kb is kb


# --- placing: what it checks before it touches a board -----------------------------


def test_without_a_netlist_it_says_to_run_sch(
    tmp_path: Path, fake_pcbnew: types.ModuleType
) -> None:
    write_file(tmp_path / "pcbkit.toml", TOML)
    with pytest.raises(click.ClickException) as raised:
        place.place_board(load_project(tmp_path))
    assert raised.value.message.endswith(
        "my_board.net not found: run `pcbkit sch` (or `pcbkit build`) first"
    )
    assert not (tmp_path / "kicad" / "my_board.kicad_pcb").exists()


def test_a_netlist_that_is_not_one_is_a_message(
    tmp_path: Path, fake_pcbnew: types.ModuleType
) -> None:
    write_file(tmp_path / "pcbkit.toml", TOML)
    write_file(tmp_path / "kicad" / "my_board.net", '(export (version "E"))')
    with pytest.raises(click.ClickException, match="not a KiCad netlist"):
        place.place_board(load_project(tmp_path))


def test_without_layout_py_it_says_what_a_project_needs(
    tmp_path: Path, fake_pcbnew: types.ModuleType
) -> None:
    write_file(tmp_path / "pcbkit.toml", TOML)
    write_file(tmp_path / "kicad" / "my_board.net", NETLIST)
    with pytest.raises(
        ProjectError, match=r"layout\.py not found: a board project needs"
    ):
        place.place_board(load_project(tmp_path))
