"""Unit tests for pcbkit.check.faults: fault-state pins, on hand-built netlists.

Each board is a regulator U2 whose enable is pad 3 on net EN, with whatever the test
puts around it; the circuit is ``dc.build`` with the nets the test fixes, as a
project's ``circuits.scenario`` would make it.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import faults, tables
from pcbkit.check.dc import Circuit, build
from pcbkit.check.netlist import Netlist
from tests import check_toy
from tests.check_toy import Toy

OFF = (0.0, 0.4)  # an enable's "off" window
ON = (2.0, 5.5)  # an enable's "on" window
RAILS = {"GND": 0.0, "+3V3": 3.3, "VIN": 5.0}


def netlist(toy: Toy) -> Netlist:
    """Return the netlist KiCad 10 would write for the toy."""
    return Netlist.from_text(check_toy.netlist_text(toy))


def regulator(toy: Toy, ref: str = "U2", enable: str = "EN") -> None:
    """Add a regulator: input pad 1, ground pad 2, enable pad 3."""
    toy.add(
        ref,
        "LDO",
        "Regulator:LDO",
        {"1": ("VIN", "IN"), "2": ("GND", "GND"), "3": (enable, "EN")},
    )


def board() -> Toy:
    """Return the regulator with a 100k pull-down on its enable."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "100k", "EN", "GND")
    return toy


def circuit(nl: Netlist, fixed: dict[str, float] | None = None) -> Circuit:
    """Return ``dc.build`` with the rails (or ``fixed``) held."""
    c = build(nl)
    for net, v in (RAILS if fixed is None else fixed).items():
        c.fix(net, v)
    return c


def judge(
    toy: Toy,
    pins: list[tuple[str, str, tuple[float, float]]] | None = None,
    fixed: dict[str, float] | None = None,
    extra: Any = None,
) -> faults.FaultReport:
    """Build, solve and judge the toy; ``extra(circuit)`` adds to the circuit."""
    nl = netlist(toy)
    c = circuit(nl, fixed)
    if extra is not None:
        extra(c)
    solved = c.solve()
    return faults.judge(
        nl,
        c,
        solved,
        [("U2", "3", OFF)] if pins is None else pins,
        faults.low_leak_solution(c),
    )


# --- ordinary designs pass ---------------------------------------------------------


def test_a_pull_down_to_a_fixed_ground_is_driven_low() -> None:
    """Pass, recording 0 V and ``driven``."""
    report = judge(board())
    assert report.problems == []
    assert report.pins == {"U2 pad 3": [0.0, 0.0, 0.4, "driven"]}
    assert report.not_modelled == {}


def test_a_pull_up_to_a_fixed_rail_is_driven_high() -> None:
    """Pass in the on window; fail in the off window, with the level."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "100k", "EN", "+3V3")
    assert judge(toy, [("U2", "3", ON)]).problems == []
    assert judge(toy).problems == ["U2 pad 3 (EN) is at 3.300 V, outside 0 to 0.4 V"]


def test_a_divider_is_judged_at_its_midpoint() -> None:
    """VIN 5 V over 100k and 47k: 1.599 V, inside (1.2, 2.0), outside the off window."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "100k", "VIN", "EN")
    check_toy.resistor(toy, "R2", "47k", "EN", "GND")
    report = judge(toy, [("U2", "3", (1.2, 2.0))])
    assert report.problems == []
    assert report.pins["U2 pad 3"][0] == pytest.approx(5 * 47 / 147, abs=1e-3)


def test_an_rc_on_the_enable_passes_and_the_capacitor_is_not_a_left_out_part() -> None:
    """A Device capacitor is open at DC, so it is no reason to refuse the verdict."""
    toy = board()
    toy.add("C1", "100n", "Device:C", {"1": ("EN", "~"), "2": ("GND", "~")})
    toy.add("C2", "1u", "Device:C_Small", {"1": ("EN", "~"), "2": ("GND", "~")})
    report = judge(toy)
    assert report.problems == []
    assert report.not_modelled == {}


def test_a_pin_driven_through_a_series_resistor_from_a_fixed_net_passes() -> None:
    """The controller's pin is on a net the scenario fixes, so it is not looked at."""
    toy = Toy()
    regulator(toy)
    toy.add("U1", "MCU", "MCU:MCU", {"1": ("GPIO", "IO1"), "2": ("GND", "GND")})
    check_toy.resistor(toy, "R1", "1k", "GPIO", "EN")
    report = judge(toy, [("U2", "3", ON)], fixed={**RAILS, "GPIO": 3.3})
    assert report.problems == []
    assert report.pins["U2 pad 3"][3] == "driven"


def test_a_fet_pulling_the_enable_down_is_a_drive_when_it_conducts() -> None:
    """A 2N7002 whose gate is pulled up holds EN low; its pull-up is modelled."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "100k", "EN", "+3V3")
    check_toy.resistor(toy, "R2", "10k", "GATE", "+3V3")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("GATE", "G"), "2": ("GND", "S"), "3": ("EN", "D")},
    )
    report = judge(toy)
    assert report.problems == []
    assert report.pins["U2 pad 3"][0] < 0.4


# --- floating ---------------------------------------------------------------------


def test_a_removed_pull_down_fails_as_floating_although_0_v_is_in_the_window() -> None:
    """The plant that matters: the leak reads 0 V, inside (0, 0.4); floating fails."""
    toy = board()
    del toy.parts["R1"], toy.pins["R1"]
    report = judge(toy)
    assert report.pins == {"U2 pad 3": [0.0, 0.0, 0.4, "floating"]}
    assert report.problems == [
        "U2 pad 3 (EN) is floating: nothing the DC model holds joins EN to a net the "
        "scenario fixes, so its 0 V reading is the solver's leak, not a level "
        "(pull it to a net the scenario fixes)"
    ]


def test_a_pin_left_to_an_ic_alone_is_floating_and_names_the_ic() -> None:
    """Only a controller's pin on EN, and the scenario does not fix EN: floating."""
    toy = board()
    del toy.parts["R1"], toy.pins["R1"]
    toy.add("U1", "MCU", "MCU:MCU", {"1": ("EN", "IO1"), "2": ("GND", "GND")})
    report = judge(toy)
    assert report.pins["U2 pad 3"][3] == "floating"
    assert report.not_modelled == {"U2 pad 3": ["U1 pad 1 (MCU:MCU) on EN"]}
    assert (
        "the parts on its nets the model leaves out: U1 pad 1 (MCU:MCU) on EN"
        in (report.problems[0])
    )


def test_a_pin_left_to_a_diode_dc_does_not_model_is_floating() -> None:
    """A ``Device:D`` from VIN to EN is not in the model: EN floats, the diode named."""
    toy = board()
    del toy.parts["R1"], toy.pins["R1"]
    toy.add("D2", "D", "Device:D", {"1": ("EN", "K"), "2": ("VIN", "A")})
    report = judge(toy)
    assert report.pins["U2 pad 3"][3] == "floating"
    assert "D2 pad 1 (Device:D) on EN" in report.problems[0]


def test_a_pin_left_to_a_capacitor_alone_is_floating() -> None:
    """A capacitor holds nothing at DC."""
    toy = board()
    del toy.parts["R1"], toy.pins["R1"]
    toy.add("C1", "100n", "Device:C", {"1": ("EN", "~"), "2": ("GND", "~")})
    report = judge(toy)
    assert report.pins["U2 pad 3"][3] == "floating"


def test_a_pin_behind_a_reverse_biased_diode_is_floating() -> None:
    """A Schottky from EN to GND conducts nothing when EN would sit at 0 V or above."""
    toy = board()
    del toy.parts["R1"], toy.pins["R1"]
    toy.add("D2", "BAT", "Device:D_Schottky", {"1": ("EN", "K"), "2": ("GND", "A")})
    assert judge(toy).pins["U2 pad 3"][3] == "floating"


def test_a_pin_pulled_to_a_rail_the_scenario_leaves_free_is_floating() -> None:
    """A pull-down to a ground the scenario does not fix holds nothing."""
    report = judge(board(), fixed={"+3V3": 3.3, "VIN": 5.0})
    assert report.pins["U2 pad 3"][3] == "floating"


def test_a_pin_wired_to_nothing_says_so() -> None:
    """KiCad's ``unconnected-(...)`` net is floating, and the message says why."""
    toy = Toy()
    regulator(toy, enable="unconnected-(U2-EN-Pad3)")
    report = judge(toy)
    assert report.problems == [
        "U2 pad 3 (unconnected-(U2-EN-Pad3)) is floating: it is wired to nothing "
        "(pull it to a net the scenario fixes)"
    ]


def test_a_level_held_only_by_the_leak_fails() -> None:
    """A 10 Gohm pull-up reads 0.3 V against the 1 nS leak: in the window, but false."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "10000M", "EN", "+3V3")
    report = judge(toy)
    assert report.pins["U2 pad 3"][0] == pytest.approx(0.3, abs=1e-3)
    assert len(report.problems) == 1
    assert report.problems[0].startswith(
        "U2 pad 3 (EN) reads 0.300 V only because of the solver's 1 nS leak"
    )


def test_a_ten_megohm_pull_does_not_trip_the_leak_test() -> None:
    """10 Mohm moves by about 33 mV with the leak: under the 50 mV the test allows."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "10M", "EN", "+3V3")
    assert judge(toy, [("U2", "3", ON)]).problems == []


# --- parts the model leaves out ----------------------------------------------------


def test_a_diode_dc_does_not_model_beside_a_pull_down_cannot_be_judged() -> None:
    """A ``Device:D`` from VIN into EN would lift it; the solve cannot see that."""
    toy = board()
    toy.add("D2", "D", "Device:D", {"1": ("EN", "K"), "2": ("VIN", "A")})
    report = judge(toy)
    assert report.pins["U2 pad 3"] == [0.0, 0.0, 0.4, "driven"]
    assert report.not_modelled == {"U2 pad 3": ["D2 pad 1 (Device:D) on EN"]}
    assert report.problems == [
        "U2 pad 3 (EN) cannot be judged: the DC model leaves out D2 pad 1 (Device:D) "
        "on EN. Model each in circuits.scenario (an element whose ref is the part's "
        "reference), fix the net it drives, or, for a pad that is high-impedance in "
        "this condition, list it in this condition's pins (listing a pad claims it "
        "draws no current and drives nothing). A Zener or TVS modelled by hand forward "
        "only must also be added to circuit.forward_only"
    ]


def test_a_part_modelled_in_the_scenario_by_its_reference_is_no_longer_left_out() -> (
    None
):
    """The way out the message gives: add the diode with ``ref="D2"``."""
    toy = board()
    toy.add("D2", "D", "Device:D", {"1": ("EN", "K"), "2": ("SIG", "A")})

    def model(c: Circuit) -> None:
        c.D("SIG", "EN", 0.7, ref="D2")
        c.fix("SIG", 0.0)

    assert judge(toy, extra=model).problems == []


def test_a_part_modelled_on_one_net_is_still_left_out_on_another() -> None:
    """A scenario that models D2 from SIG to EN says nothing of D2's pad on VIN."""
    toy = board()
    toy.add(
        "D2",
        "BAV",
        "Diode:Dual",
        {"1": ("EN", "K"), "2": ("SIG", "A"), "3": ("VIN", "A")},
    )

    def model(c: Circuit) -> None:
        c.D("SIG", "EN", 0.7, ref="D2.a")
        c.fix("SIG", 0.0)

    report = judge(toy, fixed={"GND": 0.0, "+3V3": 3.3}, extra=model)
    assert report.not_modelled == {"U2 pad 3": ["D2 pad 3 (Diode:Dual) on VIN"]}


def test_element_nets_are_the_nets_each_parts_elements_touch() -> None:
    """Every terminal of every element, by part."""
    c = Circuit()
    c.R("A", "B", 1.0, "RN1.1")
    c.R("C", "D", 1.0, "RN1.2")
    c.D("E", "F", 0.7, ref="D1")
    c.fets.append(
        {"kind": "n", "g": "G", "d": "DR", "s": "S", "vth": 2, "ron": 1, "ref": "Q1"}
    )
    assert faults.element_nets(c) == {
        "RN1": {"A", "B", "C", "D"},
        "D1": {"E", "F"},
        "Q1": {"G", "DR", "S"},
    }


def test_a_controller_pin_on_the_enable_is_left_out_until_it_is_listed() -> None:
    """An absent controller's pin: name it, then pass once it is a listed pin."""
    toy = board()
    toy.add("U1", "MCU", "MCU:MCU", {"1": ("EN", "IO1"), "2": ("GND", "GND")})
    report = judge(toy)
    assert report.not_modelled == {"U2 pad 3": ["U1 pad 1 (MCU:MCU) on EN"]}
    assert len(report.problems) == 1
    listed = judge(toy, [("U2", "3", OFF), ("U1", "1", (-0.3, 0.4))])
    assert listed.problems == []
    assert set(listed.pins) == {"U2 pad 3", "U1 pad 1"}


def test_a_left_out_part_beyond_a_resistor_is_found_on_the_level_nets() -> None:
    """A controller pin on GPIO, a free net joined to EN by 1k: named, with its net."""
    toy = board()
    toy.add("U1", "MCU", "MCU:MCU", {"1": ("GPIO", "IO1"), "2": ("GND", "GND")})
    check_toy.resistor(toy, "R2", "1k", "GPIO", "EN")
    report = judge(toy)
    assert report.not_modelled == {"U2 pad 3": ["U1 pad 1 (MCU:MCU) on GPIO"]}


def test_the_walk_stops_at_fixed_nets() -> None:
    """Parts on GND and +3V3 are not looked at: the scenario fixes those levels."""
    toy = board()
    toy.add("U1", "MCU", "MCU:MCU", {"1": ("+3V3", "VDD"), "2": ("GND", "GND")})
    toy.add("D2", "D", "Device:D", {"1": ("+3V3", "K"), "2": ("GND", "A")})
    assert judge(toy).problems == []


def test_another_pad_of_the_judged_part_on_a_free_net_is_left_out() -> None:
    """Pull-up to VIN with VIN free: U2's own input pad sets the level too."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "100k", "EN", "VIN")
    check_toy.resistor(toy, "R2", "100k", "EN", "GND")
    report = judge(toy, fixed={"GND": 0.0})
    assert report.not_modelled == {"U2 pad 3": ["U2 pad 1 (Regulator:LDO) on VIN"]}


def test_a_two_pad_part_with_reference_letters_c_is_open_at_dc_from_any_library() -> (
    None
):
    """The rule is the reference letters ``C`` and two pads, whatever the library."""
    toy = board()
    toy.add("C1", "100n", "MyLib:Cap", {"1": ("EN", "~"), "2": ("GND", "~")})
    toy.add("C2", "100n", "MyLib:C", {"1": ("EN", "~"), "2": ("GND", "~")})
    assert judge(toy).not_modelled == {}


def test_a_device_capacitor_symbol_without_reference_letters_c_is_left_out() -> None:
    """``Device:C`` referenced ``X1``, a three-pad ``C3`` and a ``CF1``: not exempt."""
    toy = board()
    toy.add("X1", "100n", "Device:C", {"1": ("EN", "~"), "2": ("GND", "~")})
    toy.add(
        "C3",
        "1u",
        "Device:C_Feedthrough",
        {"1": ("EN", "~"), "2": ("GND", "~"), "3": ("EN_OUT", "~")},
    )
    toy.add("CF1", "1u", "MyLib:C", {"1": ("EN", "~"), "2": ("GND", "~")})
    assert judge(toy).not_modelled == {
        "U2 pad 3": [
            "X1 pad 1 (Device:C) on EN",
            "C3 pad 1 (Device:C_Feedthrough) on EN",
            "CF1 pad 1 (MyLib:C) on EN",
        ]
    }


def test_a_pin_the_scenario_fixes_is_recorded_as_fixed() -> None:
    """A-m1: not "driven": the scenario says so."""
    report = judge(board(), fixed={**RAILS, "EN": 0.0})
    assert report.pins == {"U2 pad 3": [0.0, 0.0, 0.4, "fixed"]}
    assert report.problems == []


def test_a_part_with_both_pads_on_the_pins_net_carries_no_current() -> None:
    """A-m2: a resistor shorted to EN cannot move its level: not named."""
    toy = board()
    check_toy.resistor(toy, "R10", "10k", "EN", "EN")
    toy.add("FB1", "BEAD", "MyLib:Bead", {"1": ("EN", "~"), "2": ("EN", "~")})
    report = judge(toy)
    assert report.not_modelled == {}
    assert report.problems == []


def test_a_zero_ohm_link_on_the_enable_solves() -> None:
    """A-m3: an ordinary 0 ohm enable link used to raise ZeroDivisionError."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "0", "EN_SHARED", "EN")
    check_toy.resistor(toy, "R2", "100k", "EN_SHARED", "GND")
    report = judge(toy)
    assert report.problems == []
    assert report.pins["U2 pad 3"][3] == "driven"


def test_the_left_out_message_says_listing_claims_high_impedance() -> None:
    """A-m4: listing a pad is a claim, and the message says so."""
    toy = board()
    toy.add("U1", "MCU", "MCU:MCU", {"1": ("EN", "IO1"), "2": ("GND", "GND")})
    (problem,) = judge(toy).problems
    assert problem.endswith(
        "or, for a pad that is high-impedance in this condition, list it in this "
        "condition's pins (listing a pad claims it draws no current and drives "
        "nothing). A Zener or TVS modelled by hand forward only must also be added to "
        "circuit.forward_only"
    )


def fet_board(gate_pull: bool) -> Toy:
    """A-B1: the reviewer's load switch, its P-FET's gate pull-up fitted or not."""
    toy = Toy()
    toy.add(
        "U4",
        "LSW",
        "Power:LOADSW",
        {"1": ("VIN", "VIN"), "2": ("GND", "GND"), "3": ("NEN", "~{EN}")},
    )
    check_toy.resistor(toy, "R11", "100k", "NEN", "GND")
    toy.add(
        "Q4",
        "AO3401A",
        "Transistor_FET:AO3401A",
        {"1": ("Q4G", "G"), "2": ("+3V3", "S"), "3": ("NEN", "D")},
    )
    toy.add(
        "Q3",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("Q3G", "G"), "2": ("GND", "S"), "3": ("Q4G", "D")},
    )
    check_toy.resistor(toy, "R13", "100k", "Q3G", "GND")
    if gate_pull:
        check_toy.resistor(toy, "R12", "100k", "+3V3", "Q4G")
    return toy


def test_a_p_fet_whose_gate_floats_cannot_decide_the_pin() -> None:
    """A-B1: Q4's gate reads 0 V from the leak, so Q4 counted as on; now it fails."""
    report = judge(fet_board(gate_pull=False), [("U4", "3", (2.0, 3.6))])
    assert report.problems == [
        "U4 pad 3 (NEN): the gate of Q4 (Q4G) is floating, so whether Q4 conducts is "
        "the solver's guess (the leak holds the gate at 0 V); pull the gate to a net "
        "the scenario fixes, or fix it in the scenario"
    ]


def test_with_its_gate_pull_up_the_p_fet_is_off_and_the_pin_is_judged() -> None:
    """The pull-up fitted: Q4 off, NEN pulled down, outside the (2, 3.6) window."""
    report = judge(fet_board(gate_pull=True), [("U4", "3", (2.0, 3.6))])
    assert report.problems == ["U4 pad 3 (NEN) is at 0.000 V, outside 2 to 3.6 V"]


def test_an_n_fet_whose_gate_floats_cannot_decide_the_pin() -> None:
    """The mirror case: an N-FET read as off because its gate floats."""
    toy = board()
    check_toy.resistor(toy, "R1", "100k", "EN", "+3V3")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("QG", "G"), "2": ("GND", "S"), "3": ("EN", "D")},
    )
    report = judge(toy, [("U2", "3", ON)])
    assert report.problems == [
        "U2 pad 3 (EN): the gate of Q1 (QG) is floating, so whether Q1 conducts is "
        "the solver's guess (the leak holds the gate at 0 V); pull the gate to a net "
        "the scenario fixes, or fix it in the scenario"
    ]


def test_a_gate_held_only_through_a_gigaohm_cannot_decide_the_pin() -> None:
    """The gate's level moves with the leak: the same failure, said so."""
    toy = fet_board(gate_pull=False)
    check_toy.resistor(toy, "R12", "10000M", "+3V3", "Q4G")
    (problem,) = judge(toy, [("U4", "3", (2.0, 3.6))]).problems
    assert (
        problem.startswith("U4 pad 3 (NEN): the gate of Q4 (Q4G) reads ")
        and "only because of the solver's leak" in problem
    )


def test_ignored_scenario_keywords_are_named() -> None:
    """A-M1: a keyword that lands only in **kwargs, or nowhere, is named."""

    def scenario(nl: Any, vin: float = 5.0, drive: Any = None, **kw: Any) -> None:
        return None

    def strict(nl: Any, vin: float = 5.0) -> None:
        return None

    def positional(nl: Any, vin: float, /) -> None:
        return None

    assert faults.ignored_keywords(scenario, {"vin": 5, "controller": "absent"}) == [
        "controller"
    ]
    assert faults.ignored_keywords(strict, {"vin": 5, "x": 1}) == ["x"]
    assert faults.ignored_keywords(strict, {"vin": 5}) == []
    assert faults.ignored_keywords(positional, {"vin": 5}) == ["vin"]


def test_a_resistor_symbol_dc_does_not_model_is_left_out() -> None:
    """``Device:R_Small`` is not built, so EN floats and the part is named."""
    toy = Toy()
    regulator(toy)
    toy.add("R1", "100k", "Device:R_Small", {"1": ("EN", "~"), "2": ("GND", "~")})
    report = judge(toy)
    assert report.pins["U2 pad 3"][3] == "floating"
    assert "R1 pad 1 (Device:R_Small) on EN" in report.problems[0]


# --- rows that name the wrong thing -------------------------------------------------


def test_a_pad_not_in_the_netlist_fails_naming_the_pads_there_are() -> None:
    """``no net``, with no voltage."""
    report = judge(board(), [("U2", "7", OFF)])
    assert report.pins == {"U2 pad 7": [None, 0.0, 0.4, "no net"]}
    assert report.problems == [
        "U2 has no pad 7 on a net in the netlist (its pads are 1, 2, 3)"
    ]


def test_a_part_not_in_the_design_fails_by_name() -> None:
    """``no net`` for a reference the netlist does not have."""
    report = judge(board(), [("U9", "3", OFF)])
    assert report.problems == ["U9 is not in the design"]
    assert report.pins == {"U9 pad 3": [None, 0.0, 0.4, "no net"]}


def test_a_pin_listed_twice_fails() -> None:
    """Say so, and judge it once."""
    report = judge(board(), [("U2", "3", OFF), ("U2", "3", ON)])
    assert report.problems == ["U2 pad 3 is listed twice"]


def test_level_nets_go_through_every_element_whatever_its_state() -> None:
    """Through a resistor and an off FET's terminals, not past a fixed net."""
    c = Circuit()
    c.R("EN", "MID", 1e3, "R1")
    c.fets.append(
        {"kind": "n", "g": "G", "d": "MID", "s": "GND", "vth": 2, "ron": 1, "ref": "Q"}
    )
    c.R("GND", "FAR", 1e3, "R2")
    c.fix("GND", 0.0)
    assert faults.level_nets(c, "EN") == ["EN", "G", "MID"]
    assert faults.level_nets(c, "GND") == []


def test_element_refs_are_the_parts_not_their_elements() -> None:
    """``RN1.1`` and ``Q1.body`` belong to RN1 and Q1."""
    c = Circuit()
    c.R("A", "B", 1.0, "RN1.1")
    c.D("A", "B", 0.7, ref="Q1.body")
    c.R("A", "B", 1.0, "")
    assert faults.element_refs(c) == {"RN1", "Q1"}


def test_the_shape_is_registered_here_and_reads_pads_as_text() -> None:
    """``FAULT_STATES`` is this module's table, no longer a proposed one."""
    assert "FAULT_STATES" in faults.TABLES
    assert "FAULT_STATES" not in tables.PROPOSED

    class Specs:
        FAULT_STATES = {"c": {"scenario": {}, "pins": [("U2", 3, (0, 0.4))]}}

    assert tables.read(Specs, "FAULT_STATES") == {
        "c": {"scenario": {}, "pins": [("U2", "3", (0, 0.4))]}
    }


def test_a_condition_with_no_pins_fails_rather_than_judging_nothing() -> None:
    """``"pins": []`` is a mistake, not a pass."""
    assert judge(board(), []).problems == [
        "it lists no pins, so nothing is judged: list the pins whose level the "
        "condition must hold, or remove the condition"
    ]


def test_a_partly_modelled_parts_pad_on_a_fixed_net_is_not_named() -> None:
    """Current into a net the scenario fixes moves no level: only free pads count."""
    toy = board()
    toy.add(
        "D2",
        "BAV",
        "Diode:Dual",
        {"1": ("EN", "K"), "2": ("SIG", "A"), "3": ("VIN", "A")},
    )

    def model(c: Circuit) -> None:
        c.D("SIG", "EN", 0.7, ref="D2.a")
        c.fix("SIG", 0.0)

    assert judge(toy, extra=model).not_modelled == {}


def zener_into_enable() -> Toy:
    """Return the board with a 12 V Zener from +24V (K) into EN (A)."""
    toy = board()
    toy.add("D2", "BZT", "Device:D_Zener", {"1": ("+24V", "K"), "2": ("EN", "A")})
    return toy


def test_a_forward_only_zener_reverse_biased_on_the_pins_nets_cannot_be_judged() -> (
    None
):
    """extended holds the Zener forward only: 24 V reverse could break it down."""
    nl = netlist(zener_into_enable())
    c = build(nl, extended=True)
    for net, v in {**RAILS, "+24V": 24.0}.items():
        c.fix(net, v)
    report = faults.judge(nl, c, c.solve(), [("U2", "3", OFF)])
    assert report.problems == [
        "U2 pad 3 (EN): D2 is modelled forward only (no breakdown) and has 24.00 V "
        "across it in reverse, so whether it conducts is not known; if it breaks down "
        "(a Zener or TVS), model its breakdown (circuit.D(cathode net, anode net, vz, "
        "ref='D2.breakdown')) or fix the net it clamps; if it blocks, a SEMI_VRATED "
        "row of kind vrrm says it blocks up to its VRRM"
    ]


def test_with_its_breakdown_modelled_the_zener_lifts_the_enable() -> None:
    """The way out: 24 - 12 = 12 V into 100k: EN is on, outside the off window."""
    nl = netlist(zener_into_enable())
    c = build(nl, extended=True)
    for net, v in {**RAILS, "+24V": 24.0}.items():
        c.fix(net, v)
    c.D("+24V", "EN", 12.0, ref="D2.breakdown")
    (problem,) = faults.judge(nl, c, c.solve(), [("U2", "3", OFF)]).problems
    assert problem == "U2 pad 3 (EN) is at 12.000 V, outside 0 to 0.4 V"


def test_a_forward_only_zener_with_little_reverse_voltage_is_fine() -> None:
    """Under 1 V reverse no Zener or TVS breaks down."""
    nl = netlist(zener_into_enable())
    c = build(nl, extended=True)
    for net, v in {**RAILS, "+24V": 0.5}.items():
        c.fix(net, v)
    assert faults.judge(nl, c, c.solve(), [("U2", "3", OFF)]).problems == []


def test_a_resistor_marked_not_fitted_alone_is_absent_not_left_out() -> None:
    """``DNP`` alone: dc leaves it out and so does the check."""
    toy = board()
    check_toy.resistor(toy, "R2", "DNP", "EN", "+3V3")
    assert judge(toy).problems == []


def test_a_resistor_with_a_value_and_a_not_fitted_mark_cannot_be_judged() -> None:
    """``100k DNP`` is modelled as fitted, but the board may not carry it."""
    toy = board()
    toy.set_value("R1", "100k DNP")
    report = judge(toy)
    assert report.not_modelled == {
        "U2 pad 3": ["R1 pad 1 (Device:R) on EN: its value '100k DNP' also marks it "
                     "not fitted; write the value alone or the mark alone"]
    }  # fmt: skip


# --- second review ------------------------------------------------------------------


def zener_from_library(lib: str) -> Toy:
    """Return the reviewer's p4: a 5.1 V Zener from +12V (K) into EN (A)."""
    toy = Toy()
    toy.add("U1", "REG", "Regulator:REG", {"1": ("EN", "EN"), "2": ("GND", "GND")})
    toy.add("D1", "BZX84C5V1", lib, {"1": ("+12V", "K"), "2": ("EN", "A")})
    check_toy.resistor(toy, "R1", "100k", "EN", "GND")
    return toy


def judge_zener(
    toy: Toy, hand: bool = False, ratings: dict[str, float] | None = None
) -> faults.FaultReport:
    """Judge the Zener board with extended, the part modelled by hand if asked."""
    nl = netlist(toy)
    c = build(nl, extended=not hand)
    c.fix("GND", 0.0).fix("+12V", 12.0)
    if hand:
        c.D("EN", "+12V", 0.7, ref="D1")
        c.forward_only.add("D1")
    return faults.judge(nl, c, c.solve(), [("U1", "1", OFF)], None, ratings)


def test_a_diode_library_zener_into_a_pin_cannot_pass_with_extended() -> None:
    """``extended`` models it forward and does not know its kind: it fails by name."""
    (problem,) = judge_zener(zener_from_library("Diode:BZX84Cxx")).problems
    assert problem.startswith(
        "U1 pad 1 (EN): D1 is modelled forward only (no breakdown) and has 12.00 V "
        "across it in reverse"
    )
    assert "a SEMI_VRATED row of kind vrrm says it blocks" in problem


def test_a_vrrm_row_says_the_part_blocks_up_to_its_rating() -> None:
    """A blocking diode rated 100 V with 12 V across it: no breakdown asked for."""
    toy = zener_from_library("Diode:BZX84Cxx")
    assert judge_zener(toy, ratings={"D1": 100.0}).problems == []
    (problem,) = judge_zener(toy, ratings={"D1": 10.0}).problems
    assert "D1 is modelled forward only" in problem


def test_a_hand_modelled_zener_added_to_forward_only_cannot_pass() -> None:
    """Without extended the designer adds it to ``circuit.forward_only``, as told.

    The same part modelled by hand with ``extended`` on is refused as a double model.
    """
    (problem,) = judge_zener(zener_from_library("Diode:BZX84Cxx"), hand=True).problems
    assert "D1 is modelled forward only" in problem


def test_the_left_out_message_says_to_list_a_hand_modelled_zener() -> None:
    """The advice includes ``circuit.forward_only``."""
    toy = board()
    toy.add("U1", "MCU", "MCU:MCU", {"1": ("EN", "IO1"), "2": ("GND", "GND")})
    (problem,) = judge(toy).problems
    assert (
        "A Zener or TVS modelled by hand forward only must also be added to "
        "circuit.forward_only" in problem
    )


def test_a_gate_pin_does_not_walk_to_the_drain_side() -> None:
    """Judging a FET's gate net: an IC on its drain net cannot move the gate level."""
    toy = Toy()
    toy.add("U1", "DRV", "Driver:X", {"1": ("GATE", "OUT"), "2": ("GND", "GND")})
    check_toy.resistor(toy, "R1", "100k", "GATE", "GND")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("GATE", "G"), "2": ("GND", "S"), "3": ("LOAD", "D")},
    )
    toy.add("U2", "IC", "Load:IC", {"1": ("LOAD", "VDD"), "2": ("GND", "GND")})
    nl = netlist(toy)
    c = build(nl).fix("GND", 0.0)
    report = faults.judge(nl, c, c.solve(), [("U1", "1", OFF)])
    assert report.problems == []
    assert faults.level_nets(c, "GATE") == ["GATE"]


def test_a_pin_on_the_drain_net_still_needs_the_gate_judged() -> None:
    """From the drain the walk reaches the gate, so a floating gate still fails."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "100k", "EN", "+3V3")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("QG", "G"), "2": ("GND", "S"), "3": ("EN", "D")},
    )
    toy.add("U5", "DRV", "Driver:X", {"1": ("QG", "OUT"), "2": ("GND", "GND")})
    report = judge(toy, [("U2", "3", ON)])
    assert report.not_modelled == {"U2 pad 3": ["U5 pad 1 (Driver:X) on QG"]}


def test_the_fets_the_verdict_leaned_on_are_recorded() -> None:
    """Vgs, the threshold used and the state, for each FET on the pin's nets."""
    toy = Toy()
    regulator(toy)
    check_toy.resistor(toy, "R1", "100k", "EN", "+3V3")
    check_toy.resistor(toy, "R2", "100k", "+3V3", "QG")
    check_toy.resistor(toy, "R3", "100k", "QG", "GND")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("QG", "G"), "2": ("GND", "S"), "3": ("EN", "D")},
    )
    report = judge(toy, [("U2", "3", ON)])
    assert report.fet_gates == {
        "U2 pad 3": {"Q1": {"vgs": 1.65, "vth": 2.0, "on": False}}
    }
    assert report.problems == []


def test_a_listed_pad_of_a_partly_modelled_part_is_not_named() -> None:
    """A relay's coil modelled, its contact pad listed as high-impedance."""
    toy = board()
    toy.add(
        "K1",
        "RELAY",
        "Relay:X",
        {"1": ("EN", "~"), "2": ("GND", "~"), "3": ("COM", "~")},
    )

    def coil(c: Circuit) -> None:
        c.R("EN", "GND", 70.0, ref="K1")

    assert judge(toy, extra=coil).not_modelled == {
        "U2 pad 3": ["K1 pad 3 (Relay:X) on COM"]
    }
    listed = judge(toy, [("U2", "3", OFF), ("K1", "3", (-1.0, 1.0))], extra=coil)
    assert listed.not_modelled == {}
