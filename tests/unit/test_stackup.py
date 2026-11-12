"""Unit tests for pcbkit.stackup: the layer model built from a [stackup] table."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbkit import place
from pcbkit.project import InnerLayer, Project, StackupConfig, parse_config
from pcbkit.route import pre
from pcbkit.stackup import MIN_CORE_MM, build_stackup, core_mm

BOARD = {
    "stem": "my_board",
    "title": "My Board",
    "rev": "A",
    "fab_name": "My_Board_revA",
}

# A 1.6 mm board with 0.2 mm prepreg and 1 oz (0.035 mm) copper on every layer.
FOUR = StackupConfig(
    layers=4,
    thickness_mm=1.6,
    copper_mm=0.035,
    inner_copper_mm=0.035,
    prepreg_mm=0.2,
    inner=(InnerLayer("plane", "GND"), InnerLayer("split")),
)
TWO = StackupConfig(layers=2, thickness_mm=1.6, copper_mm=0.035)


def test_the_four_layer_order_names_and_roles() -> None:
    """Order the layers top to bottom with KiCad's names, roles and types."""
    stack = build_stackup(FOUR)
    assert [layer.name for layer in stack.layers] == ["top", "in1", "in2", "bottom"]
    assert [layer.kicad_name for layer in stack.layers] == [
        "F.Cu",
        "In1.Cu",
        "In2.Cu",
        "B.Cu",
    ]
    assert [layer.role for layer in stack.layers] == [
        "signal",
        "plane",
        "split",
        "signal",
    ]
    assert [layer.net for layer in stack.layers] == [None, "GND", None, None]
    assert [layer.kicad_type for layer in stack.layers] == [
        "signal",
        "power",
        "power",
        "signal",
    ]


def test_the_core_makes_copper_and_dielectric_add_up_to_the_thickness() -> None:
    """Add the layers and the gaps to exactly 1.6 mm, with a 1.06 mm core."""
    stack = build_stackup(FOUR)
    assert core_mm(FOUR) == 1.06
    assert stack.gaps_mm == (0.2, 1.06, 0.2)
    total = sum(layer.thickness_mm for layer in stack.layers) + sum(stack.gaps_mm)
    assert total == pytest.approx(1.6, abs=1e-9)


def test_the_z_positions_are_the_layer_centres_below_the_top_face() -> None:
    """Put each layer's centre at the sum of what lies above it plus half of itself."""
    stack = build_stackup(FOUR)
    assert [layer.z_mm for layer in stack.layers] == pytest.approx(
        [0.0175, 0.2525, 1.3475, 1.5825], abs=1e-9
    )
    assert stack.layer("in2").thickness_mm == 0.035


def test_the_top_layer_sits_one_prepreg_above_its_reference() -> None:
    """Take the first inner layer as the top's reference, 0.2 mm away."""
    stack = build_stackup(FOUR)
    assert stack.reference_layer("top") == "in1"
    assert stack.height_to_reference("top") == 0.2


def test_the_bottom_layer_refers_to_the_second_inner_layer() -> None:
    """Mirror the top: the bottom's reference is in2, 0.2 mm away."""
    stack = build_stackup(FOUR)
    assert stack.reference_layer("bottom") == "in2"
    assert stack.height_to_reference("bottom") == 0.2


def test_an_inner_layer_refers_to_the_nearer_outer_layer() -> None:
    """Pick the closer neighbour for an inner layer: 0.2 mm beats the 1.06 mm core."""
    stack = build_stackup(FOUR)
    assert (stack.reference_layer("in1"), stack.height_to_reference("in1")) == (
        "top",
        0.2,
    )
    assert (stack.reference_layer("in2"), stack.height_to_reference("in2")) == (
        "bottom",
        0.2,
    )


def test_span_runs_from_the_outer_face_of_one_layer_to_the_other() -> None:
    """Measure a via's barrel: top to bottom is the board, in1 to in2 spans the core."""
    stack = build_stackup(FOUR)
    assert stack.span_mm("top", "bottom") == pytest.approx(1.6, abs=1e-9)
    assert stack.span_mm("bottom", "top") == pytest.approx(1.6, abs=1e-9)
    assert stack.span_mm("top", "in1") == pytest.approx(0.27, abs=1e-9)
    assert stack.span_mm("in1", "in2") == pytest.approx(1.13, abs=1e-9)
    assert stack.span_mm("in1", "in1") == pytest.approx(0.035, abs=1e-9)


def test_an_unknown_layer_name_is_refused() -> None:
    """Raise ValueError naming the layers the board does have."""
    with pytest.raises(ValueError, match="no layer 'in1' on this board"):
        build_stackup(TWO).layer("in1")
    with pytest.raises(ValueError, match="top, in1, in2, bottom"):
        build_stackup(FOUR).reference_layer("in3")


def test_a_two_layer_board_has_one_core_between_its_coppers() -> None:
    """Model two layers as top and bottom with the rest of the thickness between."""
    stack = build_stackup(TWO)
    assert [layer.name for layer in stack.layers] == ["top", "bottom"]
    assert stack.gaps_mm == (1.53,)
    assert stack.reference_layer("top") == "bottom"
    assert stack.height_to_reference("top") == 1.53
    assert stack.span_mm("top", "bottom") == pytest.approx(1.6, abs=1e-9)


def test_inner_layers_take_their_copper_and_roles_from_the_config() -> None:
    """Use inner_copper_mm for in1 and in2, and carry each role and net across."""
    config = StackupConfig(
        4, 1.6, 0.07, 0.0175, 0.1, (InnerLayer("signal"), InnerLayer("plane", "VCC"))
    )
    stack = build_stackup(config)
    assert [layer.thickness_mm for layer in stack.layers] == [
        0.07,
        0.0175,
        0.0175,
        0.07,
    ]
    assert [(layer.role, layer.net) for layer in stack.layers[1:3]] == [
        ("signal", None),
        ("plane", "VCC"),
    ]
    assert core_mm(config) == pytest.approx(1.6 - 0.14 - 0.035 - 0.2, abs=1e-9)


def test_a_core_under_the_minimum_is_refused() -> None:
    """Raise ValueError when the config leaves less than MIN_CORE_MM."""
    thin = StackupConfig(4, 0.6, 0.035, 0.035, 0.2, FOUR.inner)
    assert core_mm(thin) < MIN_CORE_MM
    with pytest.raises(ValueError, match="under the 0.1 mm minimum"):
        build_stackup(thin)


def test_the_model_is_frozen() -> None:
    """Refuse to change a built stackup."""
    stack = build_stackup(FOUR)
    with pytest.raises(AttributeError):
        stack.thickness_mm = 2.0  # type: ignore[misc]


# --- the interim guard on the build stages --------------------------------------------


def four_layer_project(tmp_path: Path) -> Project:
    """Return a four-layer project that exists only as a config."""
    data = {
        "board": BOARD,
        "stackup": {
            "layers": 4,
            "inner": [{"role": "plane", "net": "GND"}, {"role": "split"}],
        },
    }
    return Project(root=tmp_path / "board", config=parse_config(data))


def test_place_board_stops_a_four_layer_project(tmp_path: Path) -> None:
    """Refuse before pcbnew or any file is touched."""
    with pytest.raises(Exception, match="four-layer boards are not finished yet"):
        place.place_board(four_layer_project(tmp_path))
    assert not (tmp_path / "board").exists()


def test_pre_stops_a_four_layer_project(tmp_path: Path) -> None:
    """Refuse before pcbnew or any file is touched."""
    with pytest.raises(Exception, match="four-layer boards are not finished yet"):
        pre.pre(four_layer_project(tmp_path))
    assert not (tmp_path / "board").exists()
