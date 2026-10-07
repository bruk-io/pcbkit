"""Unit tests for pcbkit.check.dc: the DC solver, on circuits with hand-worked answers.

Each expected value is derived in the test or in a comment beside it. The solver ties
every net to ground through 1 nS (``Circuit.leak``) so that floating nets solve to about
0 V; on kilo-ohm circuits that moves an answer by a few parts in 10^6, so values are
compared to within ``REL``, which is far tighter than any wrong stamp or sign would
leave them. ``build`` is tested on small netlists written out in the tests (generic
parts and nets), both for the elements it creates and by solving what it built.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check.dc import Circuit, build
from pcbkit.check.netlist import Netlist

# Relative tolerance: the solver's 1 nS leak to ground moves kilo-ohm circuits by
# a few parts in 10^6.
REL = 1e-4


def fet(
    kind: str,
    g: str | None,
    d: str | None,
    s: str | None,
    vth: float,
    ron: float,
    ref: str,
) -> dict[str, Any]:
    """Return a FET switch as ``Circuit.fets`` holds it."""
    return {"kind": kind, "g": g, "d": d, "s": s, "vth": vth, "ron": ron, "ref": ref}


def divider() -> Circuit:
    """Return 10 V over 1 kohm (R1) and 3 kohm (R2) in series, middle net MID."""
    c = Circuit()
    c.fix("VIN", 10.0).fix("GND", 0.0)
    c.R("VIN", "MID", 1000.0, "R1")
    c.R("MID", "GND", 3000.0, "R2")
    return c


def led_circuit(supply: float) -> Circuit:
    """Return ``supply`` volts, 330 ohm (R1), then a 2 V, 20 ohm LED (D1) to ground."""
    c = Circuit()
    c.fix("VCC", supply).fix("GND", 0.0)
    c.R("VCC", "A", 330.0, "R1")
    c.D("A", "GND", 2.0, 20.0, "D1")
    return c


def make_netlist(
    parts: list[tuple[str, str, str]], nets: dict[str, list[str]]
) -> Netlist:
    """Return a Netlist of ``parts`` ``(ref, "Lib:Symbol", value)`` joined by ``nets``.

    ``nets`` maps a net name to the pins on it, each written ``"R1.2"``.
    """
    comps = []
    for ref, kind, value in parts:
        lib, symbol = kind.split(":")
        comps.append(
            f'(comp (ref "{ref}") (value "{value}") '
            f'(libsource (lib "{lib}") (part "{symbol}")))'
        )
    net_items = []
    for code, (name, pins) in enumerate(nets.items(), start=1):
        nodes = " ".join(
            f'(node (ref "{ref}") (pin "{number}"))'
            for ref, number in (pin.split(".") for pin in pins)
        )
        net_items.append(f'(net (code "{code}") (name "/{name}") {nodes})')
    text = (
        '(export (version "E") (design (tool "Eeschema 10.0.6")) '
        f"(components {' '.join(comps)}) (nets {' '.join(net_items)}))"
    )
    return Netlist.from_text(text)


# --- linear circuits ----------------------------------------------------------------


def test_a_resistor_divider_settles_at_its_ratio() -> None:
    """Put MID at 10 V * 3k / (1k + 3k) = 7.5 V and leave the fixed nets as set."""
    sol = divider().solve()
    assert sol["MID"] == pytest.approx(7.5, rel=REL)
    assert sol["VIN"] == 10.0
    assert sol["GND"] == 0.0


def test_resistor_current_and_power_follow_ohms_law() -> None:
    """Carry 10 V / 4 kohm = 2.5 mA, so 6.25 mW in R1 and 18.75 mW in R2."""
    sol = divider().solve()
    assert sol.resistor_current("R1") == pytest.approx(2.5e-3, rel=REL)
    assert sol.resistor_current("R2") == pytest.approx(2.5e-3, rel=REL)
    assert sol.resistor_power() == pytest.approx(
        {"R1": 6.25e-3, "R2": 18.75e-3}, rel=REL
    )


def test_resistor_current_is_signed_from_the_first_net_to_the_second() -> None:
    """Report -2.5 mA for a resistor listed from MID to VIN: it flows the other way."""
    c = Circuit()
    c.fix("VIN", 10.0).fix("GND", 0.0)
    c.R("MID", "VIN", 1000.0, "R1")
    c.R("MID", "GND", 3000.0, "R2")
    sol = c.solve()
    assert sol.resistor_current("R1") == pytest.approx(-2.5e-3, rel=REL)
    assert sol.resistor_current("R2") == pytest.approx(2.5e-3, rel=REL)


def test_a_net_the_circuit_never_mentions_reads_zero_volts() -> None:
    """Return 0.0 for an unknown net rather than raise."""
    assert divider().solve()["NO_SUCH_NET"] == 0.0


@pytest.mark.parametrize(("amps", "volts"), [(1e-3, 8.25), (-1e-3, 6.75), (0.0, 7.5)])
def test_a_current_pushed_into_a_net_raises_it_and_one_drawn_out_lowers_it(
    amps: float, volts: float
) -> None:
    """Solve KCL at MID: V = (10 V / 1k + I) / (1/1k + 1/3k) = (0.01 + I) * 750."""
    c = divider()
    c.inject("MID", amps)
    assert c.solve()["MID"] == pytest.approx(volts, rel=REL)


def test_fix_holds_a_net_and_free_lets_it_go_and_both_chain() -> None:
    """Hold A at 5 V until it is freed, after which B (at 0 V) pulls it to 0 V."""
    c = Circuit()
    c.R("A", "B", 1000.0, "R1")
    assert c.fix("A", 5.0) is c
    assert c.fix("B", 0.0) is c
    assert c.solve()["A"] == 5.0
    assert c.free("A") is c
    assert c.solve()["A"] == pytest.approx(0.0, abs=1e-9)
    c.free("NEVER_FIXED")  # freeing a net that was not held is not an error


def test_a_resistor_with_an_open_end_or_both_ends_on_one_net_is_ignored() -> None:
    """Skip ``None`` and empty names, and a resistor from a net to itself."""
    c = Circuit()
    c.R("A", "A", 100.0, "R1")
    c.R(None, "B", 100.0, "R2")
    c.R("A", None, 100.0, "R3")
    c.R("", "B", 100.0, "R4")
    assert c.res == []
    c.R("A", "B", 100.0, "R5")
    assert c.res == [("A", "B", 100.0, "R5")]


def test_a_diode_with_an_open_end_is_ignored_and_ron_defaults_to_1_ohm() -> None:
    """Skip a diode with a missing anode or cathode, and default Ron to 1 ohm."""
    c = Circuit()
    c.D(None, "K", 2.0)
    c.D("A", "", 2.0)
    assert c.diodes == []
    c.D("A", "K", 2.0)
    assert c.diodes == [("A", "K", 2.0, 1.0, "")]


def test_nets_lists_every_net_once_sorted_and_never_none() -> None:
    """Gather nets from fixed values, resistors, diodes, FET pins and sources."""
    c = Circuit()
    c.fix("VCC", 5.0)
    c.R("VCC", "A", 100.0, "R1")
    c.D("A", "K", 2.0, 20.0, "D1")
    c.fets.append(fet("n", "G", "D", "S", 2.0, 5.0, "Q1"))
    c.fets.append(fet("n", None, "D", "S", 2.0, 5.0, "Q2"))  # an unconnected gate
    c.inject("I", 1e-3)
    assert c.nets() == ["A", "D", "G", "I", "K", "S", "VCC"]


def test_nets_that_nothing_drives_sit_at_the_current_into_them_over_the_leak() -> None:
    """Tie every net to ground through ``leak``: 1 mA into a 1 mS leak is 1 V."""
    c = Circuit()
    c.leak = 1e-3
    c.inject("X", 1e-3)
    assert c.solve()["X"] == pytest.approx(1.0, rel=1e-9)


def test_an_undriven_resistor_solves_to_zero_volts_not_a_singular_matrix() -> None:
    """Return about 0 V on both nets of an undriven resistor, thanks to the leak."""
    c = Circuit()
    c.R("A", "B", 1000.0, "R1")
    sol = c.solve()
    assert sol["A"] == pytest.approx(0.0, abs=1e-9)
    assert sol["B"] == pytest.approx(0.0, abs=1e-9)


# --- diodes -------------------------------------------------------------------------


def test_a_diode_above_its_forward_voltage_conducts_through_its_resistance() -> None:
    """Carry (5 V - 2 V) / (330 + 20 ohm) = 8.571 mA, with the anode at 2 V + 20 * I."""
    sol = led_circuit(5.0).solve()
    current = 3.0 / 350.0
    assert sol.diode_current("D1") == pytest.approx(current, rel=REL)
    assert sol.resistor_current("R1") == pytest.approx(current, rel=REL)
    assert sol["A"] == pytest.approx(2.0 + 20.0 * current, rel=REL)


def test_a_diode_below_its_forward_voltage_is_off_and_carries_exactly_nothing() -> None:
    """Leave the anode at the supply and return 0.0 A for the diode."""
    sol = led_circuit(1.5).solve()
    assert sol.diode_current("D1") == 0.0
    assert sol["A"] == pytest.approx(1.5, rel=REL)


def test_a_reverse_biased_diode_blocks() -> None:
    """Carry nothing when the cathode is the more positive side."""
    sol = led_circuit(-5.0).solve()
    assert sol.diode_current("D1") == 0.0
    assert sol["A"] == pytest.approx(-5.0, rel=REL)


def test_diode_current_of_a_diode_that_is_not_in_the_circuit_raises_key_error() -> None:
    """Name the missing diode in a KeyError."""
    sol = led_circuit(5.0).solve()
    with pytest.raises(KeyError, match="D9"):
        sol.diode_current("D9")


def test_solve_gives_up_after_max_iter_passes() -> None:
    """Need two passes for an LED: one to find it conducts, one to confirm it."""
    c = led_circuit(5.0)
    with pytest.raises(RuntimeError, match="did not converge"):
        c.solve(max_iter=1)
    assert c.solve(max_iter=2).diode_current("D1") == pytest.approx(
        3.0 / 350.0, rel=REL
    )


# --- FET switches -------------------------------------------------------------------


def low_side_switch(gate: float) -> Circuit:
    """Return 5 V, 100 ohm load (RL), then an N-FET (Q1: Vth 2 V, 5 ohm) to ground."""
    c = Circuit()
    c.fix("VCC", 5.0).fix("GND", 0.0).fix("G", gate)
    c.R("VCC", "D", 100.0, "RL")
    c.fets.append(fet("n", "G", "D", "GND", 2.0, 5.0, "Q1"))
    return c


@pytest.mark.parametrize(
    ("gate", "on"),
    [
        (0.0, False),
        (1.0, False),
        (1.999, False),
        (2.001, True),
        (3.3, True),
        (5.0, True),
    ],
)
def test_an_n_fet_switch_turns_on_when_its_gate_is_above_vth(
    gate: float, on: bool
) -> None:
    """Pull D to 5 V * 5 / (100 + 5) = 0.238 V when on, and leave it at 5 V when off."""
    sol = low_side_switch(gate).solve()
    assert sol.fet_on("Q1") is on
    assert sol.vgs("Q1") == gate
    assert sol["D"] == pytest.approx(5.0 * 5.0 / 105.0 if on else 5.0, rel=REL)


def high_side_switch(gate: float) -> Circuit:
    """Return a P-FET (Q1: Vth -2 V, 10 ohm) from VIN = 12 V to OUT, 90 ohm load."""
    c = Circuit()
    c.fix("VIN", 12.0).fix("GND", 0.0).fix("G", gate)
    c.R("OUT", "GND", 90.0, "RL")
    c.fets.append(fet("p", "G", "OUT", "VIN", -2.0, 10.0, "Q1"))
    return c


@pytest.mark.parametrize(
    ("gate", "on"),
    [
        (12.0, False),
        (11.0, False),
        (10.1, False),
        (9.9, True),
        (6.0, True),
        (0.0, True),
    ],
)
def test_a_p_fet_switch_turns_on_when_its_gate_is_below_the_source_by_more_than_vth(
    gate: float, on: bool
) -> None:
    """Drive OUT to 12 V * 90 / (90 + 10) = 10.8 V when on, and leave it at 0 V off."""
    sol = high_side_switch(gate).solve()
    assert sol.fet_on("Q1") is on
    assert sol.vgs("Q1") == pytest.approx(gate - 12.0, rel=1e-12, abs=1e-12)
    assert sol["OUT"] == pytest.approx(10.8 if on else 0.0, rel=REL, abs=1e-6)


@pytest.mark.parametrize(("gate", "on"), [(3.0, False), (3.6, True)])
def test_the_threshold_is_gate_to_source_not_gate_to_ground(
    gate: float, on: bool
) -> None:
    """Keep an N-FET with its source held at 1.5 V off until its gate is above 3.5 V."""
    c = Circuit()
    c.fix("VCC", 5.0).fix("S", 1.5).fix("G", gate)
    c.R("VCC", "D", 100.0, "RL")
    c.fets.append(fet("n", "G", "D", "S", 2.0, 5.0, "Q1"))
    sol = c.solve()
    assert sol.vgs("Q1") == pytest.approx(gate - 1.5, rel=1e-12)
    assert sol.fet_on("Q1") is on
    # on: (V - 5) / 100 + (V - 1.5) / 5 = 0, so V = (0.05 + 0.3) / 0.21 = 1.6667 V
    assert sol["D"] == pytest.approx(0.35 / 0.21 if on else 5.0, rel=REL)


def load_switch(enable: float) -> Circuit:
    """Return a P-FET load switch: gate pulled up by 10k, down by an N-FET.

    VIN = 12 V feeds Q1 (P-FET, 10 ohm); R1 pulls its gate G up to VIN; Q2 (N-FET,
    5 ohm, gate EN) pulls G down to ground; OUT drives a 90 ohm load.
    """
    c = Circuit()
    c.fix("VIN", 12.0).fix("GND", 0.0).fix("EN", enable)
    c.R("VIN", "G", 10000.0, "R1")
    c.R("OUT", "GND", 90.0, "RL")
    c.fets.append(fet("p", "G", "OUT", "VIN", -2.0, 10.0, "Q1"))
    c.fets.append(fet("n", "EN", "G", "GND", 2.0, 5.0, "Q2"))
    return c


def test_one_fet_can_switch_another_on() -> None:
    """Turn Q2 on with EN, which drags G low and so turns the P-FET Q1 on."""
    sol = load_switch(3.3).solve()
    assert sol.fet_on("Q2") is True
    assert sol.fet_on("Q1") is True
    pulled_down = 12.0 * 5.0 / (10000.0 + 5.0)  # the 10k pull-up over Q2's 5 ohm
    assert sol["G"] == pytest.approx(pulled_down, rel=REL)
    assert sol["OUT"] == pytest.approx(10.8, rel=REL)


def test_one_fet_off_leaves_the_pull_up_to_hold_the_other_off() -> None:
    """Leave G at VIN when Q2 is off, so Q1 is off and OUT stays at 0 V."""
    sol = load_switch(0.0).solve()
    assert sol.fet_on("Q2") is False
    assert sol.fet_on("Q1") is False
    assert sol["G"] == pytest.approx(12.0, rel=REL)
    assert sol["OUT"] == pytest.approx(0.0, abs=1e-6)


def test_a_circuit_with_no_consistent_switch_state_raises_runtime_error() -> None:
    """Flip a diode-connected N-FET on and off forever, and stop with an error.

    With gate and drain on one net X, pulled up by 1k: off, X is 5 V so the gate says
    on; on, X is 0.025 V so the gate says off. A switch has no state in between.
    """
    c = Circuit()
    c.fix("VCC", 5.0).fix("GND", 0.0)
    c.R("VCC", "X", 1000.0, "R1")
    c.fets.append(fet("n", "X", "X", "GND", 2.0, 5.0, "Q1"))
    with pytest.raises(RuntimeError, match="did not converge"):
        c.solve()


# --- floating -----------------------------------------------------------------------


def test_a_net_joined_to_a_driven_net_by_a_resistor_is_not_floating() -> None:
    """Follow resistors, and count a fixed net as driven, with or without a path."""
    c = divider()
    c.fix("ALONE", 3.0)  # held at 3 V and joined to nothing
    sol = c.solve()
    assert sol.floating("MID") is False
    assert sol.floating("VIN") is False
    assert sol.floating("ALONE") is False
    undriven = Circuit()
    undriven.R("A", "B", 1000.0, "R1")  # nothing in this circuit is driven
    assert undriven.solve().floating("A") is True


def test_a_net_behind_a_fet_floats_while_it_is_off_and_not_when_it_is_on() -> None:
    """Connect X to ground only through a FET, and follow it across two hops."""
    for gate, floating in ((0.0, True), (3.3, False)):
        c = Circuit()
        c.fix("GND", 0.0).fix("G", gate)
        c.R("X", "Y", 1000.0, "R1")
        c.fets.append(fet("n", "G", "Y", "GND", 2.0, 5.0, "Q1"))
        sol = c.solve()
        assert sol.fet_on("Q1") is not floating
        assert sol.floating("Y") is floating
        assert sol.floating("X") is floating


def test_a_net_behind_a_diode_floats_while_it_is_off_and_not_when_it_conducts() -> None:
    """Treat a forward-biased diode as a path and a diode below Vf as an open."""
    off = Circuit()
    off.fix("A", 1.0)
    off.D("A", "K", 2.0, 20.0, "D1")
    assert off.solve().floating("K") is True

    on = Circuit()
    on.fix("A", 5.0)
    on.D("A", "K", 2.0, 20.0, "D1")
    on.inject("K", -1e-3)  # a 1 mA sink makes it conduct: (5 - V - 2) / 20 = 1 mA
    sol = on.solve()
    assert sol.diode_current("D1") == pytest.approx(1e-3, rel=REL)
    assert sol["K"] == pytest.approx(5.0 - 2.0 - 20.0 * 1e-3, rel=REL)
    assert sol.floating("K") is False


# --- build, from a netlist ----------------------------------------------------------


def test_build_adds_each_plain_resistor_between_its_nets_with_its_value() -> None:
    """Join pin 1 to pin 2 and read the value (``2.2k`` is 2200 ohm)."""
    nl = make_netlist(
        [("R1", "Device:R", "1k"), ("R2", "Device:R", "2.2k")],
        {"VIN": ["R1.1"], "MID": ["R1.2", "R2.1"], "GND": ["R2.2"]},
    )
    c = build(nl)
    assert [(a, b, ref) for a, b, _, ref in c.res] == [
        ("VIN", "MID", "R1"),
        ("MID", "GND", "R2"),
    ]
    assert [r for _, _, r, _ in c.res] == pytest.approx([1000.0, 2200.0], rel=1e-12)
    sol = c.fix("VIN", 5.0).fix("GND", 0.0).solve()
    assert sol["MID"] == pytest.approx(5.0 * 2200.0 / 3200.0, rel=REL)  # 3.4375 V


def test_build_skips_a_resistor_with_an_unconnected_pin() -> None:
    """Leave out R1, whose pin 2 is on no net."""
    nl = make_netlist([("R1", "Device:R", "1k")], {"A": ["R1.1"]})
    assert build(nl).res == []


def test_build_ignores_capacitors_and_parts_it_does_not_know() -> None:
    """Treat a capacitor as open and leave an IC to the scenario."""
    nl = make_netlist(
        [("C1", "Device:C", "100n"), ("U1", "Test:IC3", "Reg3")],
        {"A": ["C1.1", "U1.1"], "B": ["C1.2", "U1.2"], "C": ["U1.3"]},
    )
    c = build(nl)
    assert (c.res, c.diodes, c.fets) == ([], [], [])


def test_build_splits_a_resistor_pack_into_four_named_pairs() -> None:
    """Name the four elements after the part: ``RN1.1`` to ``RN1.4``, 220 ohm each."""
    nl = make_netlist(
        [("RN1", "Device:R_Pack04", "4x220")],
        {f"P{k}": [f"RN1.{k}"] for k in range(1, 9)},
    )
    c = build(nl)
    assert [(a, b, ref) for a, b, _, ref in c.res] == [
        ("P1", "P8", "RN1.1"),
        ("P2", "P7", "RN1.2"),
        ("P3", "P6", "RN1.3"),
        ("P4", "P5", "RN1.4"),
    ]
    assert [r for _, _, r, _ in c.res] == [220.0] * 4
    # distinct voltages on every pin show which pin is paired with which
    for pin, volts in zip(range(1, 9), (1.0, 2.0, 3.0, 4.0, 0.5, 0.6, 0.7, 0.8)):
        c.fix(f"P{pin}", volts)
    sol = c.solve()
    assert sol.resistor_current("RN1.1") == pytest.approx((1.0 - 0.8) / 220.0, rel=1e-9)
    assert sol.resistor_current("RN1.2") == pytest.approx((2.0 - 0.7) / 220.0, rel=1e-9)
    assert sol.resistor_current("RN1.3") == pytest.approx((3.0 - 0.6) / 220.0, rel=1e-9)
    assert sol.resistor_current("RN1.4") == pytest.approx((4.0 - 0.5) / 220.0, rel=1e-9)


def test_build_models_a_shunt_as_the_element_and_two_sense_leads() -> None:
    """Add 10 mohm between pins 1 and 4 and 0.1 mohm leads from 1 to 2 and 4 to 3."""
    nl = make_netlist(
        [("R5", "Device:R_Shunt", "10m")],
        {f"S{k}": [f"R5.{k}"] for k in range(1, 5)},
    )
    c = build(nl)
    assert [(a, b, ref) for a, b, _, ref in c.res] == [
        ("S1", "S4", "R5"),
        ("S1", "S2", "R5.senseP"),
        ("S4", "S3", "R5.senseN"),
    ]
    assert [r for _, _, r, _ in c.res] == pytest.approx([0.01, 1e-4, 1e-4], rel=1e-12)
    # 2 A through the element reads 2 A * 10 mohm = 20 mV across the sense pins
    c.fix("S4", 0.0).inject("S1", 2.0)
    sol = c.solve()
    assert sol["S2"] - sol["S3"] == pytest.approx(0.02, rel=1e-6)
    assert sol["S1"] == pytest.approx(0.02, rel=1e-6)


@pytest.mark.parametrize(
    ("value", "vf"),
    [
        ("Green", 2.9),
        ("green", 2.9),
        ("BLUE", 3.0),
        ("White", 3.0),
        ("Red", 2.0),
        ("Yellow", 2.0),
        ("Amber", 2.0),  # a colour the table does not list falls back to 2.0 V
    ],
)
def test_build_gives_an_led_the_forward_voltage_of_its_colour(
    value: str, vf: float
) -> None:
    """Look up the colour in ``LED_VF`` ignoring case; anode pin 2, cathode pin 1."""
    nl = make_netlist([("D1", "Device:LED", value)], {"A": ["D1.2"], "K": ["D1.1"]})
    assert build(nl).diodes == [("A", "K", vf, 20.0, "D1")]


def test_build_wires_an_led_so_that_it_conducts_from_pin_2_to_pin_1() -> None:
    """Light a green LED from 5 V and 330 ohm: (5 - 2.9) / (330 + 20) = 6 mA."""
    nl = make_netlist(
        [("R1", "Device:R", "330"), ("D1", "Device:LED", "Green")],
        {"VCC": ["R1.1"], "A": ["R1.2", "D1.2"], "GND": ["D1.1"]},
    )
    sol = build(nl).fix("VCC", 5.0).fix("GND", 0.0).solve()
    assert sol.diode_current("D1") == pytest.approx(0.006, rel=REL)
    assert sol["A"] == pytest.approx(2.9 + 20.0 * 0.006, rel=REL)


def test_build_models_a_schottky_diode_from_pin_2_to_pin_1() -> None:
    """Pass (3.3 - 0.4) / (100 + 0.05) A forward, and nothing the other way."""
    nl = make_netlist(
        [("R1", "Device:R", "100"), ("D1", "Device:D_Schottky", "SS")],
        {"VCC": ["R1.1"], "A": ["R1.2", "D1.2"], "GND": ["D1.1"]},
    )
    assert build(nl).diodes == [("A", "GND", 0.40, 0.05, "D1")]
    forward = build(nl).fix("VCC", 3.3).fix("GND", 0.0).solve()
    assert forward.diode_current("D1") == pytest.approx(2.9 / 100.05, rel=REL)
    reverse = build(nl).fix("VCC", -3.3).fix("GND", 0.0).solve()
    assert reverse.diode_current("D1") == 0.0


def test_build_reads_the_2n7002_as_gate_pin_1_source_pin_2_drain_pin_3() -> None:
    """Make an N-FET switch of 5 ohm, with the threshold the caller passes."""
    nl = make_netlist(
        [("Q1", "Transistor_FET:2N7002", "NMOS")],
        {"G": ["Q1.1"], "S": ["Q1.2"], "D": ["Q1.3"]},
    )
    assert build(nl).fets == [fet("n", "G", "D", "S", 2.0, 5.0, "Q1")]
    assert build(nl, nfet_vth=1.2).fets == [fet("n", "G", "D", "S", 1.2, 5.0, "Q1")]


def test_build_switches_a_2n7002_by_its_gate_voltage() -> None:
    """Pull the drain to 0.238 V with 3.3 V on the gate; leave it at 5 V with 0 V."""
    nl = make_netlist(
        [("Q1", "Transistor_FET:2N7002", "NMOS"), ("R1", "Device:R", "100")],
        {"G": ["Q1.1"], "GND": ["Q1.2"], "D": ["Q1.3", "R1.2"], "VCC": ["R1.1"]},
    )
    on = build(nl).fix("VCC", 5.0).fix("GND", 0.0).fix("G", 3.3).solve()
    off = build(nl).fix("VCC", 5.0).fix("GND", 0.0).fix("G", 0.0).solve()
    assert on.fet_on("Q1") is True
    assert on["D"] == pytest.approx(5.0 * 5.0 / 105.0, rel=REL)
    assert off.fet_on("Q1") is False
    assert off["D"] == pytest.approx(5.0, rel=REL)


def test_build_reads_a_pmos_gds_as_gate_pin_1_drain_pin_2_source_pin_3() -> None:
    """Add a P-FET of 11 mohm with a body diode from its drain to its source."""
    nl = make_netlist(
        [("Q1", "Transistor_FET:Q_PMOS_GDS", "PMOS")],
        {"G": ["Q1.1"], "D": ["Q1.2"], "S": ["Q1.3"]},
    )
    c = build(nl)
    assert c.fets == [fet("p", "G", "D", "S", -2.0, 0.011, "Q1")]
    assert c.diodes == [("D", "S", 0.7, 0.05, "Q1.body")]
    assert build(nl, pfet_vth=-3.5).fets[0]["vth"] == -3.5


def test_build_reads_an_ao3401a_as_gate_pin_1_source_pin_2_drain_pin_3() -> None:
    """Add a P-FET of 60 mohm and -1.3 V with a body diode from drain to source."""
    nl = make_netlist(
        [("Q1", "Transistor_FET:AO3401A", "PMOS")],
        {"G": ["Q1.1"], "S": ["Q1.2"], "D": ["Q1.3"]},
    )
    c = build(nl)
    assert c.fets == [fet("p", "G", "D", "S", -1.3, 0.060, "Q1")]
    assert c.diodes == [("D", "S", 0.7, 0.05, "Q1.body")]


def pmos_load_switch_netlist() -> Netlist:
    """Return the load switch of ``load_switch`` as a netlist: Q1 P-FET, Q2 2N7002."""
    return make_netlist(
        [
            ("Q1", "Transistor_FET:Q_PMOS_GDS", "PMOS"),
            ("Q2", "Transistor_FET:2N7002", "NMOS"),
            ("R1", "Device:R", "10k"),
            ("RL", "Device:R", "90"),
        ],
        {
            "VIN": ["R1.1", "Q1.3"],
            "G": ["R1.2", "Q1.1", "Q2.3"],
            "OUT": ["Q1.2", "RL.1"],
            "EN": ["Q2.1"],
            "GND": ["RL.2", "Q2.2"],
        },
    )


@pytest.mark.parametrize(("enable", "on"), [(3.3, True), (0.0, False)])
def test_build_and_solve_a_p_fet_load_switch_driven_by_a_2n7002(
    enable: float, on: bool
) -> None:
    """Deliver 12 V * 90 / (90.011) when enabled and 0 V when not, body diode off."""
    c = build(pmos_load_switch_netlist()).fix("VIN", 12.0).fix("GND", 0.0)
    sol = c.fix("EN", enable).solve()
    assert sol.fet_on("Q2") is on
    assert sol.fet_on("Q1") is on
    assert sol["OUT"] == pytest.approx(
        12.0 * 90.0 / 90.011 if on else 0.0, rel=REL, abs=1e-6
    )
    assert sol.diode_current("Q1.body") == 0.0


def test_a_p_fet_that_is_off_still_conducts_backwards_through_its_body_diode() -> None:
    """Feed OUT from 5 V through 100 ohm with VIN at 0 V: (5 - 0.7) / (100 + 0.05) A."""
    nl = make_netlist(
        [
            ("Q1", "Transistor_FET:Q_PMOS_GDS", "PMOS"),
            ("R1", "Device:R", "10k"),
            ("RB", "Device:R", "100"),
        ],
        {
            "VIN": ["R1.1", "Q1.3"],
            "G": ["R1.2", "Q1.1"],
            "OUT": ["Q1.2", "RB.2"],
            "BAT": ["RB.1"],
        },
    )
    sol = build(nl).fix("VIN", 0.0).fix("BAT", 5.0).solve()
    assert sol.fet_on("Q1") is False
    assert sol.diode_current("Q1.body") == pytest.approx(4.3 / 100.05, rel=REL)
    assert sol["OUT"] == pytest.approx(0.7 + 0.05 * 4.3 / 100.05, rel=REL)
