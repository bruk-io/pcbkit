"""Integration: the ``fab`` check group on a real board, run as `pcbkit check` runs it.

A small generic project is written to disk: a ``pcbkit.toml``, a ``specs.py``, the
netlist (served by a ``fake_nl`` plugin, so no schematic is needed) and a real 60 x 40
mm board built with pcbnew (tests/check_group_boards.py): two 0603 pull-ups, an IC with
a decoupling capacitor, a pin header with polarity marks and a slot, a power transistor
with a tab, three vias and some silkscreen text, and the stackup and rules KiCad saves
with a board. The real command line (``pcbkit.check.runner.command``) runs the group in
a child pytest and ``out/checks/results.json`` is read back.

The control project passes all eight checks of the group. Each check is then shown to
bite by planting one mistake in the control (a thin via ring, a small text, a part on a
part, a mark left off, a capacitor too far, a tab on the wrong net, a pull-up too large)
and watching exactly the check that guards it fail, with the message and the numbers
that say why. The numbers a check records are compared with the same quantity worked
out here from the plan: a ring is (diameter - drill) / 2, a distance is the distance
between the two pads that were placed, an I2C rise time is 0.8473 * R * C from the
parts and the tracks that were built.

These need KiCad's own Python (``import pcbnew``) to build the boards: see
tests/integration/test_kicad_core.py for how to make ``.venv-kicad``. No kicad-cli is
needed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from tests import check_group_boards as gb  # noqa: E402
from tests.check_group_run import (  # noqa: E402
    Lab,
    Result,
    replace_once,
    write_netlist,
    write_toml,
)

pytestmark = pytest.mark.kicad

MODULE = "pcbkit.check.builtin.test_fab"

# --- the plan: the limits and the model written into the specs ------------------------

# generic limits for the test, not any fab house's: the check reads whatever PCBWAY says
LIMITS = {
    "min_annular": 0.15,
    "min_drill": 0.3,
    "min_plated_slot": 0.5,
    "silk_min_height": 0.8,
    "silk_min_stroke": 0.15,
    "min_track_2oz": 0.2,
    "min_space_2oz": 0.2,
}
DECOUPLING_MAX_MM = 3.0
DRILL_MM = 1.0  # the lead of the header, on the datasheet
TR_MAX = 300e-9  # s, for 400 kHz
PIN_C = 10e-12  # F per device
TRACE_C_PER_MM = 0.1e-12
CABLE_M, CABLE_C_PER_M = 0.3, 100e-12
OFFBOARD = 1  # the display on the end of the cable
PAD_MM = 1.7  # the stock header's pads are 1.7 mm across

CONTROL = {
    "test_annular_rings_and_drills_meet_pcbway",
    "test_design_rules_cover_fab_limits",
    "test_silkscreen_text_is_printable",
    "test_assembly_drawing_text_does_not_overlap",
    "test_wire_pads_have_polarity_marks",
    "test_decoupling_caps_are_close[U1-8-caps0-3.0]",
    "test_power_footprints_match_datasheet",
    "test_i2c_rise_time[main]",
}

DECOUPLING = f'DECOUPLING = [("U1", 8, ("C1",), {DECOUPLING_MAX_MM!r})]\n'
EXEMPT = 'ASSEMBLY_TEXT_EXEMPT = ("J1",)\n'
POWER = (
    "POWER_FOOTPRINTS = [\n"
    '    dict(ref="Q1", tab_pad=2, tab_net="VBUS", smaller=[(1, 2), (3, 2)]),\n'
    f'    dict(ref="J1", drill_mm={{"1": {DRILL_MM!r}}}),\n'
    "]\n"
)
SPECS = (
    '"""Specs of the fab-group test board."""\n'
    "from __future__ import annotations\n\n"
    f"PCBWAY = {LIMITS!r}\n"
    'WIRE_PADS = {"J1": ("1", "2")}\n'
    f"{DECOUPLING}"
    f"{POWER}"
    'I2C_BUSES = {"main": ("SDA", "SCL", 400e3, [("DISPLAY", 0x3C, 10)])}\n'
    f"I2C_TR_MAX = {{400e3: {TR_MAX!r}}}\n"
    f"I2C_PIN_C = {PIN_C!r}\n"
    f"TRACE_C_PER_MM = {TRACE_C_PER_MM!r}\n"
    f"I2C_CABLE_LEN = {CABLE_M!r}\n"
    f"I2C_CABLE_C_PER_M = {CABLE_C_PER_M!r}\n"
    f"{EXEMPT}"
)


# --- projects: the control, and the control with one thing changed --------------------


@dataclass(frozen=True)
class Variant:
    """The control project with some changes, and nothing else changed.

    ``board`` are keyword arguments of ``check_group_boards.fab_board``; ``stackup`` is
    the keys of that table of pcbkit.toml; ``specs`` are edits of specs.py as (text to
    find, text to put there).
    """

    board: dict[str, Any] = field(default_factory=dict)
    stackup: dict[str, float] | None = None
    specs: tuple[tuple[str, str], ...] = ()


def build(root: Path, variant: Variant | None) -> gb.FabBoard:
    """Write the project of ``variant`` (the control when None) into ``root``."""
    variant = variant or Variant()
    board = gb.fab_board(root / "kicad", "board", **variant.board)
    write_toml(root, "board", ["fab"], variant.stackup)
    (root / "specs.py").write_text(SPECS, encoding="utf-8")
    for old, new in variant.specs:
        replace_once(root / "specs.py", old, new)
    write_netlist(root, board.toy)
    return board


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Lab:
    """Return the lab: the control and the plants are built and run on first use."""
    return Lab(tmp_path_factory.mktemp("fab_group"), build)


@pytest.fixture(scope="module")
def control(lab: Lab) -> Result:
    """Return the control project, run: every check of the group on a clean board."""
    return lab.result("control")


def numbers(result: Result, name: str) -> dict[str, Any]:
    """Return what the one check whose id contains ``name`` recorded."""
    return result.run.numbers(name)


# --- the control ----------------------------------------------------------------------


def test_the_control_project_passes_every_fab_check(control: Result) -> None:
    """Run the eight checks of the group on the clean board, and pass them all."""
    assert control.names() == CONTROL
    assert control.failed() == set(), (control.run.outcomes(), control.run.tail())
    assert control.run.done.returncode == 0, control.run.tail()
    assert all(entry["outcome"] == "passed" for entry in control.run.checks.values())
    assert {e["group"] for e in control.run.checks.values()} == {"fab"}
    assert all(key.startswith(MODULE) for key in control.run.checks)


def test_the_control_board_agrees_with_its_netlist(control: Result) -> None:
    """Put every pad of the board on the net its netlist names: one plan, two files."""
    gb.assert_matches_netlist(control.board.pcb, control.board.parts)


# --- test_annular_rings_and_drills_meet_pcbway ----------------------------------------

RINGS = "test_annular_rings_and_drills_meet_pcbway"
SLOT_RING = (PAD_MM - 1.2) / 2  # the header's pad is 1.7 mm across, the slot 1.2 long


def test_the_control_rings_drills_and_slot_are_inside_the_limits(
    control: Result,
) -> None:
    """Record the 0.2 mm ring of the 0.8 / 0.4 mm vias, the 0.4 mm drill, one slot."""
    fab = numbers(control, RINGS)["fab"]
    assert fab["min_annular_mm"] == pytest.approx((0.8 - 0.4) / 2, abs=0.0005)
    assert fab["at"] == "via"
    assert fab["min_drill_mm"] == pytest.approx(0.4)
    assert fab["plated_slots"] == [[0.6, "J1"]]
    assert (PAD_MM - 1.0) / 2 > fab["min_annular_mm"]  # the header's round pad
    assert SLOT_RING > fab["min_annular_mm"]


def test_a_via_with_a_thin_ring_fails_the_annular_limit(lab: Lab) -> None:
    """Fail vias of 0.6 mm over a 0.4 mm drill: a ring of 0.1 mm against 0.15."""
    result = lab.result("thin_ring", Variant(board={"via_mm": (0.6, 0.4)}))
    result.planted(RINGS)
    fab = numbers(result, RINGS)["fab"]
    assert fab["min_annular_mm"] == pytest.approx((0.6 - 0.4) / 2, abs=0.0005)
    assert fab["min_annular_mm"] < LIMITS["min_annular"]
    assert fab["at"] == "via"
    assert fab["min_drill_mm"] >= LIMITS["min_drill"]  # the drill is not the trouble


def test_a_via_with_a_small_drill_fails_the_drill_limit(lab: Lab) -> None:
    """Fail vias drilled 0.2 mm against 0.3 mm, though their ring is a roomy 0.3 mm."""
    result = lab.result("small_drill", Variant(board={"via_mm": (0.8, 0.2)}))
    result.planted(RINGS)
    fab = numbers(result, RINGS)["fab"]
    assert fab["min_drill_mm"] == pytest.approx(0.2)
    assert fab["min_drill_mm"] < LIMITS["min_drill"]
    assert fab["min_annular_mm"] >= LIMITS["min_annular"]  # the ring is not the trouble
    assert fab["min_annular_mm"] == pytest.approx(SLOT_RING, abs=0.0005)  # J1's slot
    assert fab["at"] == "J1.2"


def test_a_plated_slot_that_is_too_narrow_fails_the_slot_limit(lab: Lab) -> None:
    """Fail a slot 0.4 mm wide against 0.5 mm: ring and drill are inside limits."""
    result = lab.result("narrow_slot", Variant(board={"slot_mm": (0.4, 1.2)}))
    result.planted(RINGS)
    fab = numbers(result, RINGS)["fab"]
    assert fab["plated_slots"] == [[0.4, "J1"]]
    assert fab["plated_slots"][0][0] < LIMITS["min_plated_slot"]
    assert fab["min_annular_mm"] >= LIMITS["min_annular"]
    assert fab["min_drill_mm"] >= LIMITS["min_drill"]


# --- test_design_rules_cover_fab_limits -----------------------------------------------

RULES = "test_design_rules_cover_fab_limits"


def stackup_copper(result: Result) -> list[float]:
    """Return the copper thicknesses (mm) the saved board file's stackup says."""
    text = result.board.pcb.read_text(encoding="utf-8")
    found = re.findall(
        r'\(layer "[FB]\.Cu"\s*\(type "copper"\)\s*\(thickness ([\d.]+)\)', text
    )
    return [float(t) for t in found]


def test_a_stackup_that_disagrees_with_pcbkit_toml_fails_the_rules_check(
    lab: Lab, control: Result
) -> None:
    """Fail when the board says 0.035 mm of copper and [stackup] says 0.070."""
    assert stackup_copper(control) == [0.035, 0.035]
    result = lab.result("copper_toml_2oz", Variant(stackup={"copper_mm": 0.07}))
    result.planted(RULES)
    assert stackup_copper(result) == [0.035, 0.035]
    message = result.run.message(RULES)
    assert (
        "stackup must say 0.07 mm copper (pcbkit.toml [stackup] copper_mm)" in message
    )


def test_a_board_whose_stackup_says_two_ounces_passes_when_the_toml_does(
    lab: Lab,
) -> None:
    """Pass at 0.070 mm in both: the check reads the file's stackup, not a constant."""
    both = lab.result(
        "copper_both_2oz",
        Variant(board={"copper_mm": 0.07}, stackup={"copper_mm": 0.07}),
    )
    assert stackup_copper(both) == [0.07, 0.07]
    assert both.failed() == set(), both.run.outcomes()
    assert both.run.results["copper_mm"] == 0.07
    # and the board that says 0.070 fails a toml that says 0.035 (the default)
    other = lab.result("copper_board_2oz", Variant(board={"copper_mm": 0.07}))
    other.planted(RULES)
    assert "stackup must say 0.035 mm copper" in other.run.message(RULES)


def test_a_board_saved_with_no_stackup_fails_the_rules_check(lab: Lab) -> None:
    """Fail a board file that says nothing of its copper: no stackup to read."""
    result = lab.result("no_stackup", Variant(board={"stackup": False}))
    result.planted(RULES)
    assert stackup_copper(result) == []
    assert "stackup must say 0.035 mm copper" in result.run.message(RULES)


@pytest.mark.parametrize(
    ("name", "board"),
    [
        ("track_0.1", {"min_track_mm": 0.1}),
        ("track_0.19", {"min_track_mm": 0.19}),
        ("clearance_0.1", {"min_clearance_mm": 0.1}),
    ],
)
def test_a_rule_below_the_fab_limit_fails_the_rules_check(
    lab: Lab, name: str, board: dict[str, float]
) -> None:
    """Fail a minimum track or clearance rule under the 0.2 mm the fab house needs."""
    result = lab.result(f"rule_{name}", Variant(board=board))
    result.planted(RULES)
    (value,) = board.values()
    assert value < LIMITS["min_track_2oz"] == LIMITS["min_space_2oz"]
    assert f"assert {value:g} >= 0.2" in result.run.message(RULES)


# --- test_silkscreen_text_is_printable ------------------------------------------------

SILK = "test_silkscreen_text_is_printable"


def test_the_control_silkscreen_is_inside_the_limits(control: Result) -> None:
    """Pass: the reference texts and the board's text are 1 mm high, 0.15 thick."""
    assert numbers(control, SILK) == {"silk below limits": 0}
    assert 1.0 >= LIMITS["silk_min_height"]
    assert (
        1.0 * 0.15 >= LIMITS["silk_min_stroke"] - 1e-9
    )  # a stroke of 15 % of the height


@pytest.mark.parametrize(
    ("size", "bold", "short", "thin"),
    [(0.5, False, True, True), (0.8, False, False, True), (0.75, True, True, False)],
    ids=["short_and_thin", "thin_only", "short_only"],
)
def test_a_text_too_short_or_too_thin_fails_the_silkscreen_check(
    lab: Lab, size: float, bold: bool, short: bool, thin: bool
) -> None:
    """Fail text under 0.8 mm tall, text under 0.15 mm thick, and text that is both."""
    variant = Variant(board={"text_mm": size, "bold_text": bold})
    result = lab.result(f"text_{size}_{bold}", variant)
    result.planted(SILK)
    assert numbers(result, SILK) == {"silk below limits": 1}
    stroke = round(size * (0.2 if bold else 0.15), 3)  # the stroke add_text gives
    assert (size < LIMITS["silk_min_height"]) is short
    assert (stroke < LIMITS["silk_min_stroke"] - 1e-9) is thin
    message = result.run.message(SILK)
    assert f"[('board', 'FAB TEST', {size}, {stroke})]" in message


# --- test_assembly_drawing_text_does_not_overlap --------------------------------------

ASSEMBLY = "test_assembly_drawing_text_does_not_overlap"


def test_two_parts_on_top_of_each_other_fail_the_assembly_drawing_check(
    lab: Lab, control: Result
) -> None:
    """Fail R2 placed on R1: their references and their values print over each other."""
    assert numbers(control, ASSEMBLY)["fab texts checked"] >= len(control.board.parts)
    result = lab.result("overlap", Variant(board={"overlap": True}))
    result.planted(ASSEMBLY)
    message = result.run.message(ASSEMBLY)
    assert "overlapping text on the assembly drawing" in message
    # the pair is listed in whichever order KiCad keeps the two footprints
    for pair in (("R1:R1", "R2:R2"), ("R1:5.1k", "R2:5.1k")):
        assert any(f"('{a}', '{b}')" in message for a, b in (pair, pair[::-1]))


def test_a_designator_over_its_own_through_hole_pins_fails_unless_exempt(
    lab: Lab, control: Result
) -> None:
    """Fail J1's reference over J1's pins when the specs do not exempt it."""
    # the stock header prints its reference between its two pins, 1.0 mm drills 2.54 mm
    # apart: the text runs into both holes, so the control exempts J1 by name
    assert EXEMPT in SPECS
    result = lab.result("no_exemption", Variant(specs=((EXEMPT, ""),)))
    result.planted(ASSEMBLY)
    assert "text printed over through-hole pins: ['J1:J1']" in result.run.message(
        ASSEMBLY
    )
    assert control.run.outcome(ASSEMBLY) == "passed"


def test_an_exemption_covers_only_the_part_it_names(lab: Lab) -> None:
    """Fail J1's reference over its pins when the part the specs exempt is R2."""
    wrong = (EXEMPT, 'ASSEMBLY_TEXT_EXEMPT = ("R2",)\n')
    result = lab.result("exempt_the_wrong_part", Variant(specs=(wrong,)))
    result.planted(ASSEMBLY)
    assert "['J1:J1']" in result.run.message(ASSEMBLY)


# --- test_wire_pads_have_polarity_marks -----------------------------------------------

MARKS = "test_wire_pads_have_polarity_marks"


def test_a_wire_pad_with_no_marks_fails_the_polarity_check(
    lab: Lab, control: Result
) -> None:
    """Fail J1 with no "+" and no "-" on the silkscreen; pass it with both."""
    assert numbers(control, MARKS) == {"polarity marks": {"J1 +": True, "J1 -": True}}
    result = lab.result("no_marks", Variant(board={"marks": "none"}))
    result.planted(MARKS)
    assert numbers(result, MARKS) == {"polarity marks": {"J1 +": False, "J1 -": False}}
    assert "missing wire-pad polarity marks: ['J1 +', 'J1 -']" in result.run.message(
        MARKS
    )


def test_a_mark_nearer_the_other_pad_does_not_count(lab: Lab) -> None:
    """Fail the marks swapped: a "+" nearer pad 2 than pad 1 says nothing of pad 1."""
    result = lab.result("swapped_marks", Variant(board={"marks": "swapped"}))
    result.planted(MARKS)
    assert numbers(result, MARKS) == {"polarity marks": {"J1 +": False, "J1 -": False}}


def test_a_missing_mark_is_named_alone(lab: Lab) -> None:
    """Fail with only the "-" missing, and say so."""
    result = lab.result("plus_only", Variant(board={"marks": "plus_only"}))
    result.planted(MARKS)
    assert numbers(result, MARKS) == {"polarity marks": {"J1 +": True, "J1 -": False}}
    assert "missing wire-pad polarity marks: ['J1 -']" in result.run.message(MARKS)


# --- test_decoupling_caps_are_close ---------------------------------------------------

DECOUPLE = "test_decoupling_caps_are_close[U1-8-caps0-3.0]"


def test_a_decoupling_capacitor_too_far_from_its_pin_fails(
    lab: Lab, control: Result
) -> None:
    """Fail C1 8 mm from U1's pin 8 against a 3 mm limit; pass it at 2 mm."""
    near = numbers(control, DECOUPLE)["U1.8 nearest cap mm"]
    assert near == pytest.approx(
        math.dist(control.board.pin8, control.board.cap_pad), abs=0.01
    )
    assert near == pytest.approx(2.0, abs=0.01) and near <= DECOUPLING_MAX_MM
    result = lab.result("cap_far", Variant(board={"cap_mm": 8.0}))
    result.planted(DECOUPLE)
    far = numbers(result, DECOUPLE)["U1.8 nearest cap mm"]
    assert far == pytest.approx(
        math.dist(result.board.pin8, result.board.cap_pad), abs=0.01
    )
    assert far == pytest.approx(8.0, abs=0.01) and far > DECOUPLING_MAX_MM


# --- test_power_footprints_match_datasheet --------------------------------------------

POWER_CHECK = "test_power_footprints_match_datasheet"


def test_a_tab_on_the_wrong_net_fails_the_power_footprint_check(lab: Lab) -> None:
    """Fail Q1's tab, its largest pad, on GND when the datasheet's drain is VBUS."""
    result = lab.result("tab_on_gnd", Variant(board={"tab_net": "GND"}))
    result.planted(POWER_CHECK)
    assert "['Q1: the tab is on /GND, not VBUS']" in result.run.message(POWER_CHECK)


def test_a_datasheet_tab_that_is_not_the_largest_pad_fails(lab: Lab) -> None:
    """Fail when the datasheet says pad 1 is the tab and pad 2 is the largest."""
    edit = ("tab_pad=2", "tab_pad=1")
    result = lab.result("tab_pad_1", Variant(specs=(edit,)))
    result.planted(POWER_CHECK)
    assert (
        "Q1: the largest pad is 2, the datasheet's tab is pad 1"
        in result.run.message(POWER_CHECK)
    )


def test_a_pad_that_should_be_smaller_fails_when_it_is_not(lab: Lab) -> None:
    """Fail the datasheet's "pad 2 smaller than pad 1" where pad 2 is larger."""
    edit = ("smaller=[(1, 2), (3, 2)]", "smaller=[(2, 1)]")
    result = lab.result("smaller", Variant(specs=(edit,)))
    result.planted(POWER_CHECK)
    assert "Q1: pad 2 must be smaller than pad 1" in result.run.message(POWER_CHECK)


def test_a_hole_that_is_not_the_datasheets_drill_fails(lab: Lab) -> None:
    """Fail J1's 1.0 mm drill against a datasheet that says 1.2 mm."""
    edit = (f'"1": {DRILL_MM!r}', '"1": 1.2')
    result = lab.result("drill_1.2", Variant(specs=(edit,)))
    result.planted(POWER_CHECK)
    assert "J1 pad 1: drill 1.00 mm, datasheet 1.2 mm" in result.run.message(
        POWER_CHECK
    )


# --- test_i2c_rise_time ---------------------------------------------------------------

I2C = "test_i2c_rise_time[main]"
RISE_PER_RC = 0.8473  # 10 % to 90 %: ln(9) / 2.592 of the I2C standard's own constant
STUB_START = gb.STUB_AT
STUB = [STUB_START, *gb.serpentine((STUB_START[0] + 1.0, STUB_START[1]), 30.0, 0.5, 11)]


def rise(length_mm: float, pullup_ohm: float) -> tuple[float, float]:
    """Return the capacitance (F) and the rise time (s) of an I2C line.

    The line has U1 and the display on it, ``length_mm`` of track and the cable to the
    display: C = n * 10 pF + length * 0.1 pF/mm + 0.3 m * 100 pF/m, and tr = 0.8473 R C.
    """
    devices = 1 + OFFBOARD
    c = (
        devices * PIN_C
        + length_mm * TRACE_C_PER_MM
        + OFFBOARD * CABLE_M * CABLE_C_PER_M
    )
    return c, RISE_PER_RC * pullup_ohm * c


def assert_line(result: Result, line: str, length_mm: float, pullup_ohm: float) -> None:
    """Assert the check recorded the line's length, capacitance and rise time."""
    c, tr = rise(length_mm, pullup_ohm)
    recorded = numbers(result, I2C)[line]
    assert recorded["trace_mm"] == pytest.approx(length_mm, abs=0.06)
    assert recorded["c_pf"] == pytest.approx(c * 1e12, abs=0.06)
    assert recorded["rise_ns"] == pytest.approx(tr * 1e9, abs=1.0)


def test_the_control_i2c_lines_rise_inside_the_limit(control: Result) -> None:
    """Pass 5.1 kohm pull-ups on lines of 14.8 and 17.3 mm: about 220 ns of 300."""
    sda, scl = control.board.sda, control.board.scl
    assert_line(control, "SDA", gb.polyline_length(sda), 5100.0)
    assert_line(control, "SCL", gb.polyline_length(scl), 5100.0)
    assert rise(gb.polyline_length(sda), 5100.0)[1] < 0.8 * TR_MAX


def test_a_pull_up_too_large_fails_the_rise_time(lab: Lab) -> None:
    """Fail 10 kohm pull-ups: the same 51 pF rises in 440 ns, over the 300 ns limit."""
    result = lab.result("pullup_10k", Variant(board={"pullup": "10k"}))
    result.planted(I2C)
    length = gb.polyline_length(result.board.sda)
    c, tr = rise(length, 10e3)
    assert tr > TR_MAX
    assert_line(result, "SDA", length, 10e3)
    assert f"SDA rise {tr * 1e9:.0f} ns > 300 ns" in result.run.message(I2C)


def test_a_pull_up_too_large_on_scl_alone_fails_that_line(lab: Lab) -> None:
    """Fail 10 kohm on SCL with SDA's 5.1 kohm fine: each line is judged on its own."""
    result = lab.result("scl_10k", Variant(board={"scl_pullup": "10k"}))
    result.planted(I2C)
    sda, scl = result.board.sda, result.board.scl
    assert_line(result, "SDA", gb.polyline_length(sda), 5100.0)
    assert rise(gb.polyline_length(sda), 5100.0)[1] < TR_MAX
    assert_line(result, "SCL", gb.polyline_length(scl), 10e3)
    _, tr = rise(gb.polyline_length(scl), 10e3)
    assert f"SCL rise {tr * 1e9:.0f} ns > 300 ns" in result.run.message(I2C)


def test_a_long_trace_on_a_line_fails_the_rise_time(lab: Lab) -> None:
    """Fail a 335 mm serpentine stub on SDA: its 34 pF takes 5.1 kohm over 300 ns."""
    result = lab.result("long_stub", Variant(board={"stub": STUB}))
    result.planted(I2C)
    board = result.board
    length = gb.polyline_length(board.sda) + gb.polyline_length(
        [board.sda[1], *board.stub]
    )
    assert length > 350.0
    c, tr = rise(length, 5100.0)
    assert tr > TR_MAX > rise(gb.polyline_length(board.sda), 5100.0)[1]
    assert_line(result, "SDA", length, 5100.0)
    assert f"SDA rise {tr * 1e9:.0f} ns > 300 ns" in result.run.message(I2C)
