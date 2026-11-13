"""Unit tests for pcbkit.check.semis: semiconductor voltage, on hand-built netlists.

The board is a 12 V load switched by a low-side N-FET (Q1, a ``2N7002`` that
``dc.build`` models) whose gate a controller drives through R2: in scenario 0 the gate
is driven high (the FET conducts), in scenario 1 low (it blocks 12 V). Each test adds
parts and rows to that.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from pcbkit.check import semis, tables
from pcbkit.check.dc import Circuit, build
from pcbkit.check.netlist import Netlist
from tests import check_toy
from tests.check_toy import Toy

FET_ROW = {"vds": 60.0, "vgs": 20.0}
TABLE = {"FET-1": FET_ROW}


def netlist(toy: Toy) -> Netlist:
    """Return the netlist KiCad 10 would write for the toy."""
    return Netlist.from_text(check_toy.netlist_text(toy))


def board() -> Toy:
    """Return the load switch: R1 from +12V to LOAD, Q1 from LOAD to GND."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "1k", "+12V", "LOAD")
    check_toy.resistor(toy, "R2", "100", "GPIO", "GATE")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("GATE", "G"), "2": ("GND", "S"), "3": ("LOAD", "D")},
        MPN="FET-1",
    )
    return toy


Extra = Callable[[Circuit, int], None]


def attempts(
    toy: Toy, extra: Extra | None = None, gpio: tuple[float, ...] = (3.3, 0.0)
) -> list[tuple[int, Any, Exception | None]]:
    """Solve one scenario per GPIO level; ``extra(circuit, index)`` adds to each."""
    nl = netlist(toy)
    out: list[tuple[int, Any, Exception | None]] = []
    for index, level in enumerate(gpio):
        c = build(nl)
        c.fix("GND", 0.0).fix("+12V", 12.0).fix("+5V", 5.0).fix("GPIO", level)
        if extra is not None:
            extra(c, index)
        out.append((index, c.solve(), None))
    return out


def judge(
    toy: Toy, table: dict[str, Any] | None = None, extra: Extra | None = None
) -> semis.SemiReport:
    """Judge the toy over its two scenarios."""
    return semis.judge(
        netlist(toy), TABLE if table is None else table, attempts(toy, extra)
    )


# --- ordinary designs pass ---------------------------------------------------------


def test_a_fet_well_inside_its_rating_passes_and_records_its_worst_case() -> None:
    """Blocking 12 V against 0.8 of 60 V; the gate sees 3.3 V against 16 V."""
    report = judge(board())
    assert report.problems == []
    assert report.parts == {
        "Q1": {
            "kind": "fet",
            "mpn": "FET-1",
            "target": 0.8,
            "vds": {"v": 12.0, "rated": 60.0, "limit_v": 48.0, "scenario": 1},
            "vgs": {"v": 3.3, "rated": 20.0, "limit_v": 16.0, "scenario": 0},
        }
    }


def test_a_schottky_across_a_rail_passes() -> None:
    """A reverse-polarity clamp from GND to +5V: 5 V against 0.8 of 40 V."""
    toy = board()
    toy.add(
        "D2",
        "SS14",
        "Device:D_Schottky",
        {"1": ("+5V", "K"), "2": ("GND", "A")},
        MPN="SCH-1",
    )
    report = judge(toy, {**TABLE, "SCH-1": {"vrrm": 40.0}})
    assert report.problems == []
    assert report.parts["D2"]["vr"] == {
        "v": 5.0,
        "rated": 40.0,
        "limit_v": 32.0,
        "scenario": 0,
        "polarity": "reverse",
    }


def test_a_part_with_pins_in_its_row_is_judged_on_them() -> None:
    """Unnamed pins, read from the row; the scenario models the diode by its ref."""
    toy = board()
    toy.add(
        "D3", "X", "MyLib:Diode2", {"1": ("+5V", "~"), "2": ("GND", "~")}, MPN="D-X"
    )

    def model(c: Circuit, _index: int) -> None:
        c.D("GND", "+5V", 0.7, ref="D3")

    row = {"vrrm": 30.0, "pins": {"a": "2", "k": "1"}}
    report = judge(toy, {**TABLE, "D-X": row}, model)
    assert report.problems == []
    assert report.parts["D3"]["vr"]["v"] == 5.0


def test_a_tighter_derate_fails_what_the_default_passes() -> None:
    """12 V against 0.5 of 20 V fails; against 0.8 of 20 V it passes."""
    row = {"vds": 20.0, "vgs": 20.0}
    assert judge(board(), {"FET-1": row}).problems == []
    report = judge(board(), {"FET-1": {**row, "derate": 0.5}})
    assert report.problems == [
        "Q1 |Vds| reaches 12.00 V in STRESS_SCENARIOS[1], over 0.5 of its 20 V "
        "rating (10.00 V)"
    ]
    assert report.parts["Q1"]["target"] == 0.5


def test_a_fet_with_its_drain_on_two_pads_of_one_net_is_read() -> None:
    """A tab pad named D beside pad 3 is the same drain."""
    toy = board()
    toy.pins["Q1"]["4"] = ("LOAD", "D")
    assert judge(toy).problems == []


def test_ics_resistors_and_jumpers_are_not_judged() -> None:
    """A part with other pin names (VDD, IN), or A and B, is no semiconductor."""
    toy = board()
    toy.add("U1", "LDO", "Regulator:LDO", {"1": ("+12V", "IN"), "2": ("GND", "GND")})
    toy.add(
        "JP1",
        "Open",
        "Jumper:SolderJumper_2_Open",
        {"1": ("GPIO", "A"), "2": ("JP_OUT", "B")},
    )
    report = judge(toy)
    assert report.problems == []
    assert set(report.parts) == {"Q1"}


# --- the plan's planted mistakes -----------------------------------------------------


def test_a_5_v_rating_on_a_fet_that_blocks_12_v_fails() -> None:
    """The record says 12 V against a 4 V limit, and so does the message."""
    report = judge(board(), {"FET-1": {"vds": 5.0, "vgs": 20.0}})
    assert report.problems == [
        "Q1 |Vds| reaches 12.00 V in STRESS_SCENARIOS[1], over 0.8 of its 5 V rating "
        "(4.00 V)"
    ]
    assert report.parts["Q1"]["vds"]["limit_v"] == 4.0


def test_a_missing_rating_fails_and_still_records_the_voltages() -> None:
    """No row for the MPN: named, with the readings and a null rating."""
    report = judge(board(), {})
    assert report.problems == [
        "no voltage rating on file for Q1 (FET-1): add a SEMI_VRATED row"
    ]
    assert report.parts["Q1"]["vds"] == {
        "v": 12.0,
        "rated": None,
        "limit_v": None,
        "scenario": 1,
    }


def test_a_part_with_no_mpn_fails_as_unrated() -> None:
    """Say there is no MPN to look up."""
    toy = board()
    value, lib, part, _fields = toy.parts["Q1"]
    toy.parts["Q1"] = (value, lib, part, {})
    assert judge(toy).problems == ["no voltage rating on file for Q1 (no MPN)"]


# --- what the check cannot judge fails by name --------------------------------------


def test_a_diode_dc_does_not_model_fails_until_the_scenario_models_it() -> None:
    """``Device:D`` is not built: say so; modelled by its ref, it is judged."""
    toy = board()
    toy.add("D4", "D", "Device:D", {"1": ("+5V", "K"), "2": ("GND", "A")}, MPN="D-1")
    table = {**TABLE, "D-1": {"vrrm": 100.0}}
    report = judge(toy, table)
    assert report.problems == [
        "D4 (Device:D) is not modelled on pad 2 (A) on GND, pad 1 (K) on +5V in "
        "STRESS_SCENARIOS[0]: dc.build does not model this symbol (dc.build(nl, "
        "extended=True) models more), so the voltage across it is not the board's; "
        "add it in circuits.scenario with ref 'D4' (circuit.D(anode net, cathode net, "
        "vf, ref='D4') for a diode, a circuit.fets entry for a FET; a bidirectional "
        "part as an open element, circuit.R(net, net, 1e12, ref='D4.open'), never as "
        "circuit.D)"
    ]
    assert report.parts["D4"]["vr"]["v"] is None

    def model(c: Circuit, _index: int) -> None:
        c.D("GND", "+5V", 0.7, ref="D4")

    assert judge(toy, table, model).problems == []


def test_a_pin_on_a_floating_net_cannot_be_judged() -> None:
    """The gate's controller is free in scenario 1 and nothing pulls the gate."""

    def free(c: Circuit, index: int) -> None:
        if index == 1:
            c.free("GPIO")

    report = judge(board(), extra=free)
    assert report.problems == [
        "Q1 cannot be judged in STRESS_SCENARIOS[1]: pad 1 (G) on GATE is floating "
        "there (its 0 V is the solver's leak); fix the net that drives it in that "
        "scenario, or give it a pull"
    ]


def test_a_gate_pull_down_makes_the_free_controller_judgeable() -> None:
    """With 100k from GATE to GND the free gate is driven low."""
    toy = board()
    check_toy.resistor(toy, "R3", "100k", "GATE", "GND")

    def free(c: Circuit, index: int) -> None:
        if index == 1:
            c.free("GPIO")

    assert judge(toy, extra=free).problems == []


def test_a_scenario_that_does_not_solve_fails_by_its_index() -> None:
    """Name the row and the error; the other rows are still judged."""
    toy = board()
    tries = attempts(toy)[:1] + [(1, None, RuntimeError("DC solve did not converge"))]
    report = semis.judge(netlist(toy), TABLE, tries)
    assert report.unsolved == [1]
    assert report.problems == [
        "STRESS_SCENARIOS[1] could not be solved: RuntimeError: DC solve did not "
        "converge"
    ]
    assert report.parts["Q1"]["vgs"]["v"] == 3.3


def test_a_bipolar_transistor_fails_with_the_way_to_write_it() -> None:
    """B, C and E do not read as a FET or a diode; the hint gives the FET-row form."""
    toy = board()
    toy.add(
        "Q2",
        "BC847",
        "Device:Q_NPN_BCE",
        {"1": ("B2", "B"), "2": ("C2", "C"), "3": ("GND", "E")},
        MPN="BJT-1",
    )
    report = judge(toy, {**TABLE, "BJT-1": {"vds": 45.0, "vgs": 6.0}})
    assert report.problems == [
        "Q2 (Device:Q_NPN_BCE) cannot be judged: its pin names (B, C, E) do not read "
        'as one FET (G, D, S) or one diode (A, K); give its SEMI_VRATED row "pins" (a '
        'bipolar transistor is written as a FET row: "vds" its VCEO, "vgs" its VEBO, '
        '"pins" {"g": base pad, "d": collector pad, "s": emitter pad}), or declare it '
        "in SEMI_NOT_JUDGED with the reason"
    ]
    assert report.not_judged == {"Q2": report.problems[0]}


def test_a_bipolar_transistor_written_as_a_fet_row_and_modelled_is_judged() -> None:
    """The way out the hint gives: pins in the row and an element in the scenario."""
    toy = board()
    toy.add(
        "Q2",
        "BC847",
        "Device:Q_NPN_BCE",
        {"1": ("GATE", "B"), "2": ("LOAD", "C"), "3": ("GND", "E")},
        MPN="BJT-1",
    )
    row = {"vds": 45.0, "vgs": 6.0, "pins": {"g": 1, "d": 2, "s": 3}}

    def model(c: Circuit, _index: int) -> None:
        c.fets.append(
            {
                "kind": "n",
                "g": "GATE",
                "d": "LOAD",
                "s": "GND",
                "vth": 0.6,
                "ron": 1.0,
                "ref": "Q2",
            }
        )

    read = tables.read(
        type("S", (), {"SEMI_VRATED": {**TABLE, "BJT-1": row}}), "SEMI_VRATED"
    )
    report = judge(toy, read, model)
    assert report.problems == []
    assert report.parts["Q2"]["vds"]["v"] == 12.0


def test_a_dual_fet_with_one_element_in_its_row_cannot_be_judged() -> None:
    """A row describes one element; the second FET's pads are named."""
    toy = board()
    toy.add(
        "Q3",
        "DUAL",
        "MyLib:DualFET",
        {
            "1": ("GND", "S1"),
            "2": ("G1N", "G1"),
            "3": ("D1N", "D1"),
            "4": ("GND", "S2"),
            "5": ("G2N", "G2"),
            "6": ("D2N", "D2"),
        },
        MPN="DUAL-1",
    )
    row = {"vds": 30.0, "vgs": 12.0, "pins": {"g": "2", "d": "3", "s": "1"}}
    read = tables.read(
        type("S", (), {"SEMI_VRATED": {**TABLE, "DUAL-1": row}}), "SEMI_VRATED"
    )
    report = judge(toy, read)
    assert report.problems == [
        "Q3 (MyLib:DualFET) cannot be judged: its row's pins leave out pads 5, 6, on "
        'other nets: list every diode or FET of the part in its row\'s "elements"; '
        "correct the row, or declare it in SEMI_NOT_JUDGED with the reason"
    ]


def test_a_dual_fet_with_no_row_pins_is_found_by_its_names() -> None:
    """G1, D1, S1, G2... are one family's names: it is judged, and fails by name."""
    toy = board()
    toy.add(
        "Q3",
        "DUAL",
        "MyLib:DualFET",
        {"1": ("GND", "S1"), "2": ("G1N", "G1"), "3": ("D1N", "D1")},
    )
    report = judge(toy)
    assert (
        "Q3 (MyLib:DualFET) cannot be judged: its pin names (S1, G1, D1)"
        in (report.problems[0])
    )


def test_a_part_from_a_diode_library_with_no_pin_names_fails_by_name() -> None:
    """A dual diode whose symbol names no pins is still found, by its library."""
    toy = board()
    toy.add(
        "D5",
        "BAT54C",
        "Diode:BAT54C",
        {"1": ("GPIO", ""), "2": ("GATE", ""), "3": ("+5V", "")},
    )
    report = judge(toy)
    assert report.problems[0].startswith(
        "D5 (Diode:BAT54C) cannot be judged: its pin names ((no name)) do not read"
    )


def test_two_cathode_pads_on_different_nets_are_two_elements() -> None:
    """KiCad's series pair names both outer pads K: not one diode."""
    toy = board()
    toy.add(
        "D6",
        "BAV99",
        "Diode:BAV99",
        {"1": ("GPIO", "K"), "2": ("GND", "A"), "3": ("GATE", "K")},
    )
    report = judge(toy)
    assert report.problems[0].startswith(
        "D6 (Diode:BAV99) cannot be judged: its pads 1, 3 are all named K but are on "
        "different nets"
    )


def test_a_row_of_the_other_kind_fails() -> None:
    """A FET whose MPN has a diode row."""
    report = judge(board(), {"FET-1": {"vrrm": 60.0}})
    assert report.problems == [
        "Q1 (Transistor_FET:2N7002) cannot be judged: its pins read as a fet but its "
        "row is a diode row; write a FET row (vds, vgs) for it, or declare it in "
        "SEMI_NOT_JUDGED with the reason"
    ]


def test_row_pins_naming_a_pad_the_part_does_not_have_fail() -> None:
    """Pad 9 is on no net."""
    row = {"vds": 60.0, "vgs": 20.0, "pins": {"g": "9", "d": "3", "s": "2"}}
    report = judge(board(), {"FET-1": row})
    assert report.problems == [
        "Q1 (Transistor_FET:2N7002) cannot be judged: its row's pins name pads 9, "
        "which are on no net (its pads are 1, 2, 3); correct the row, or declare it "
        "in SEMI_NOT_JUDGED with the reason"
    ]


def test_a_part_modelled_in_the_circuit_is_judged_whatever_its_names() -> None:
    """An element with a part's ref brings the part in, so it cannot slip past."""
    toy = board()
    toy.add("X1", "?", "MyLib:Thing", {"1": ("+5V", "P"), "2": ("GND", "N")})

    def model(c: Circuit, _index: int) -> None:
        c.D("GND", "+5V", 0.7, ref="X1")

    report = judge(toy, extra=model)
    assert report.problems[0].startswith("X1 (MyLib:Thing) cannot be judged")


def test_the_shape_is_registered_here() -> None:
    """``SEMI_VRATED`` is this module's table, no longer a proposed one."""
    assert "SEMI_VRATED" in semis.TABLES
    assert "SEMI_VRATED" not in tables.PROPOSED


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ({"1": "G", "2": "D", "3": "S"}, True),
        ({"1": "GATE", "2": "drain", "3": "Source"}, True),
        ({"1": "A1", "2": "A2"}, True),
        ({"1": "B", "2": "C", "3": "E"}, True),
        ({"1": "A", "2": "B"}, False),
        ({"1": "A", "2": "K", "3": "C", "4": "E"}, False),
        ({"1": "IN", "2": "GND"}, False),
        ({"1": "~", "2": "~"}, False),
        ({"1": "K"}, False),
    ],
)
def test_named_like_a_semiconductor(names: dict[str, str], expected: bool) -> None:
    """One family's names, two pads or more."""
    toy = Toy()
    toy.add("X1", "?", "MyLib:Thing", {p: (f"N{p}", f) for p, f in names.items()})
    assert semis.named_like_a_semiconductor(netlist(toy), "X1") is expected


def test_no_stress_scenario_at_all_fails_rather_than_judging_nothing() -> None:
    """An empty ``STRESS_SCENARIOS`` with a FET on the board is no pass."""
    report = semis.judge(netlist(board()), TABLE, [])
    assert report.problems == [
        "no STRESS_SCENARIOS row was solved, so Q1 could not be judged: give the "
        "operating points that stress them hardest"
    ]
    assert report.parts["Q1"]["vds"]["v"] is None


def test_no_stress_scenario_and_no_semiconductor_passes() -> None:
    """Nothing to judge, nothing to solve: pass."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "1k", "A", "B")
    assert semis.judge(netlist(toy), {}, []).problems == []


# --- review findings ---------------------------------------------------------------


def external_rail() -> Toy:
    """F-B1: +24V_EXT on connector J5, sensed by a divider, switched by a P-FET."""
    toy = board()
    toy.add(
        "J5",
        "CONN",
        "Connector:Conn_01x03",
        {"1": ("+24V_EXT", "Pin_1"), "2": ("GND", "Pin_2"), "3": ("OUT", "Pin_3")},
    )
    check_toy.resistor(toy, "R20", "100k", "+24V_EXT", "SENSE24")
    check_toy.resistor(toy, "R21", "10k", "SENSE24", "GND")
    toy.add(
        "Q5",
        "AO3401A",
        "Transistor_FET:AO3401A",
        {"1": ("Q5G", "G"), "2": ("+24V_EXT", "S"), "3": ("OUT", "D")},
        MPN="AO3401A",
    )
    check_toy.resistor(toy, "R22", "100k", "+24V_EXT", "Q5G")
    check_toy.resistor(toy, "R23", "10k", "OUT", "GND")
    return toy


def test_a_source_the_model_leaves_out_on_a_judged_parts_nets_fails() -> None:
    """F-B1: the divider makes +24V_EXT "driven" at 0 V; J5 is named instead."""
    table = {**TABLE, "AO3401A": {"vds": 30.0, "vgs": 12.0}}
    report = judge(external_rail(), table)
    assert report.problems == [
        "Q5 cannot be judged in STRESS_SCENARIOS[0]: the DC model leaves out J5 pad 1 "
        "(Connector:Conn_01x03) on +24V_EXT, J5 pad 3 (Connector:Conn_01x03) on OUT, "
        "on the nets that set its pads' levels; fix those nets in the scenario, model "
        "what drives them with its reference, or, for a pad that draws and sources "
        "nothing at DC, list it in SEMI_HIGH_Z"
    ]


def test_fixing_the_external_rail_judges_the_fet_and_fails_its_gate() -> None:
    """With +24V_EXT fixed at 24 V the gate sees 24 V against 0.8 of 12 V."""
    table = {**TABLE, "AO3401A": {"vds": 30.0, "vgs": 12.0}}

    def rail(c: Circuit, _index: int) -> None:
        c.fix("+24V_EXT", 24.0)
        c.fix("OUT", 0.0)

    report = judge(external_rail(), table, rail)
    assert report.problems == []  # the gate is pulled to its source: Vgs 0
    assert report.parts["Q5"]["vds"]["v"] == 24.0


def test_row_pins_that_disagree_with_readable_names_fail() -> None:
    """F-M1: a 2N7002 named G, S, D whose row swaps g and d."""
    row = {"vds": 30.0, "vgs": 12.0, "pins": {"g": "3", "d": "1", "s": "2"}}
    report = judge(board(), {"FET-1": row})
    assert report.problems == [
        "Q1 (Transistor_FET:2N7002) cannot be judged: its row's pins disagree with its "
        "pin names: pad 3 is named D but the row says g; pad 1 is named G but the row "
        "says d; correct the row, or declare it in SEMI_NOT_JUDGED with the reason"
    ]


def test_row_pins_that_agree_with_readable_names_pass() -> None:
    """The same row, with the pads the names give."""
    row = {"vds": 60.0, "vgs": 20.0, "pins": {"g": "1", "d": "3", "s": "2"}}
    assert judge(board(), {"FET-1": row}).problems == []


def test_each_scenario_is_judged_as_it_is_solved() -> None:
    """F-m1: a project whose scenarios share one Circuit object."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "10k", "IN", "DRN")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("G", "G"), "2": ("GND", "S"), "3": ("DRN", "D")},
        MPN="FET-1",
    )
    check_toy.resistor(toy, "R2", "10k", "G", "GND")
    nl = netlist(toy)
    shared = build(nl)

    def attempts_shared() -> Any:
        shared.fix("GND", 0).fix("G", 0)  # scenario 0: IN undriven, DRN floats
        yield 0, shared.solve(), None
        shared.fix("IN", 40)  # scenario 1 changes the same object
        yield 1, shared.solve(), None

    report = semis.judge(nl, {"FET-1": {"vds": 60, "vgs": 20}}, attempts_shared())
    assert report.problems == [
        "Q1 cannot be judged in STRESS_SCENARIOS[0]: pad 3 (D) on DRN is floating "
        "there (its 0 V is the solver's leak); fix the net that drives it in that "
        "scenario, or give it a pull"
    ]
    assert report.parts["Q1"]["vds"]["v"] == 40.0


def test_a_diodes_forward_drop_is_not_held_to_its_reverse_rating() -> None:
    """F-m2: a white LED at 20 mA drops about 3.4 V; its 5 V VR row is not touched."""
    toy = board()
    toy.add("D8", "White", "Device:LED", {"1": ("GND", "K"), "2": ("LA", "A")}, MPN="W")
    check_toy.resistor(toy, "R8", "50", "+5V", "LA")
    report = judge(toy, {**TABLE, "W": {"vrrm": 5.0}})
    assert report.problems == []
    assert report.parts["D8"]["vr"]["v"] < 0


# --- elements, clamps, SEMI_NOT_JUDGED ----------------------------------------------


def dual_fet(toy: Toy) -> None:
    """Add a dual N-FET whose elements are modelled by the scenario."""
    toy.add(
        "Q3",
        "DUAL",
        "MyLib:DualFET",
        {
            "1": ("GND", "S1"),
            "2": ("GATE", "G1"),
            "3": ("LOAD", "D1"),
            "4": ("GND", "S2"),
            "5": ("GPIO", "G2"),
            "6": ("+5V", "D2"),
        },
        MPN="DUAL-1",
    )


def model_dual(c: Circuit, _index: int) -> None:
    """Model both FETs of Q3 as the scenario would."""
    c.fets.append(
        {"kind": "n", "g": "GATE", "d": "LOAD", "s": "GND", "vth": 2, "ron": 1,
         "ref": "Q3.a"}
    )  # fmt: skip
    c.fets.append(
        {"kind": "n", "g": "GPIO", "d": "+5V", "s": "GND", "vth": 2, "ron": 1,
         "ref": "Q3.b"}
    )  # fmt: skip


DUAL_ROW = {
    "vds": 30.0,
    "vgs": 12.0,
    "elements": [
        {"pins": {"g": "2", "d": "3", "s": "1"}},
        {"pins": {"g": "5", "d": "6", "s": "4"}},
    ],
}


def read(table: dict[str, Any]) -> Any:
    """Return the table as ``tables.read`` gives it to the check."""
    return tables.read(type("S", (), {"SEMI_VRATED": table}), "SEMI_VRATED")


def test_a_dual_part_is_judged_element_by_element() -> None:
    """Both FETs of Q3 against the shared rating; recorded per element."""
    toy = board()
    dual_fet(toy)
    report = judge(toy, read({**TABLE, "DUAL-1": DUAL_ROW}), model_dual)
    assert report.problems == []
    first, second = report.parts["Q3"]["elements"]
    assert first["vds"]["v"] == 12.0
    assert second["vds"]["v"] == 5.0


def test_one_element_over_its_rating_fails_naming_it() -> None:
    """A 10 V rating: element 1 blocks 12 V, element 2 only 5 V."""
    toy = board()
    dual_fet(toy)
    row = {**DUAL_ROW, "vds": 10.0}
    report = judge(toy, read({**TABLE, "DUAL-1": row}), model_dual)
    assert report.problems == [
        "Q3 element 1 |Vds| reaches 12.00 V in STRESS_SCENARIOS[1], over 0.8 of its "
        "10 V rating (8.00 V)"
    ]


def test_elements_that_leave_out_a_pad_on_another_net_fail() -> None:
    """The pads left out are judged against the union of the elements' pads."""
    toy = board()
    dual_fet(toy)
    row = {**DUAL_ROW, "elements": DUAL_ROW["elements"][:1]}
    report = judge(toy, read({**TABLE, "DUAL-1": row}), model_dual)
    assert report.problems[0].startswith(
        "Q3 (MyLib:DualFET) cannot be judged: its row's pins leave out pads 5, 6"
    )


def test_pins_and_elements_together_fail() -> None:
    """One or the other."""
    toy = board()
    dual_fet(toy)
    row = {**DUAL_ROW, "pins": {"g": "2", "d": "3", "s": "1"}}
    report = judge(toy, read({**TABLE, "DUAL-1": row}), model_dual)
    assert report.problems[0] == (
        'Q3 (MyLib:DualFET) cannot be judged: its row gives both "pins" and '
        '"elements": give one; correct the row, or declare it in SEMI_NOT_JUDGED with '
        "the reason"
    )


def test_a_dual_diode_with_a_common_cathode_is_judged_as_two_elements() -> None:
    """A BAT54C-style part: two anodes, one cathode."""
    toy = board()
    toy.add(
        "D5",
        "BAT54C",
        "Diode:BAT54C",
        {"1": ("GND", "~"), "2": ("+5V", "~"), "3": ("+12V", "~")},
        MPN="BAT54C",
    )
    row = {
        "vrrm": 30.0,
        "elements": [{"pins": {"a": "1", "k": "3"}}, {"pins": {"a": "2", "k": "3"}}],
    }

    def model(c: Circuit, _index: int) -> None:
        c.D("GND", "+12V", 0.4, ref="D5.a")
        c.D("+5V", "+12V", 0.4, ref="D5.b")

    report = judge(toy, read({**TABLE, "BAT54C": row}), model)
    assert report.problems == []
    assert [e["vr"]["v"] for e in report.parts["D5"]["elements"]] == [12.0, 7.0]


def tvs(toy: Toy) -> None:
    """Add a TVS from +12V (K) to GND (A), modelled forward only."""
    toy.add(
        "D9",
        "SMBJ12A",
        "Diode:SMBJ",
        {"1": ("+12V", "K"), "2": ("GND", "A")},
        MPN="T12",
    )


def model_tvs(c: Circuit, _index: int) -> None:
    """Model the TVS conducting only forward."""
    c.D("GND", "+12V", 0.7, ref="D9")


def test_a_tvs_at_its_standoff_passes_with_a_derate_of_one() -> None:
    """12 V on a 12 V standoff: a TVS is specified there."""
    toy = board()
    tvs(toy)
    report = judge(toy, read({**TABLE, "T12": {"vrwm": 12.0}}), model_tvs)
    assert report.problems == []
    assert report.parts["D9"]["kind"] == "tvs"
    assert report.parts["D9"]["target"] == 1.0
    assert report.parts["D9"]["vr"] == {
        "v": 12.0,
        "rated": 12.0,
        "limit_v": 12.0,
        "scenario": 0,
        "polarity": "either way",
    }


def test_a_tvs_over_its_standoff_fails() -> None:
    """12 V on a 10 V standoff."""
    toy = board()
    tvs(toy)
    report = judge(toy, read({**TABLE, "T12": {"vrwm": 10.0}}), model_tvs)
    assert report.problems == [
        "D9 |V(K) - V(A)| reaches 12.00 V in STRESS_SCENARIOS[0], over 1 of its 10 V "
        "rating (10.00 V)"
    ]


def test_a_tvs_that_is_not_modelled_fails_by_name() -> None:
    """A clamp kind still needs its element in the scenario."""
    toy = board()
    tvs(toy)
    report = judge(toy, read({**TABLE, "T12": {"vrwm": 12.0}}))
    assert report.problems[0].startswith("D9 (Diode:SMBJ) is not modelled")


def zener(toy: Toy) -> None:
    """Add a gate Zener from +12V (K) to ZG (A), ZG pulled to GND by 100k."""
    toy.add(
        "D10", "BZT", "Device:D_Zener", {"1": ("+12V", "K"), "2": ("ZG", "A")}, MPN="Z"
    )
    check_toy.resistor(toy, "R30", "100k", "ZG", "GND")


def test_a_zener_clamp_the_model_shows_over_its_vz_fails_and_says_why() -> None:
    """No breakdown in the model: 12 V across a 5.1 V Zener is not the board."""
    toy = board()
    zener(toy)

    def model(c: Circuit, _index: int) -> None:
        c.D("ZG", "+12V", 0.7, ref="D10")

    report = judge(toy, read({**TABLE, "Z": {"vz": 5.1}}), model)
    assert report.problems == [
        "D10 is a clamp (vz 5.1 V) but the model shows 12.00 V across it in "
        "STRESS_SCENARIOS[0]: the DC model has no breakdown, so this solve is not the "
        "board's; fix the clamped net at its clamped voltage in that scenario, or "
        "model the breakdown as a diode from cathode to anode with vf 5.1 V (ref "
        "'D10.breakdown')"
    ]
    assert report.parts["D10"]["kind"] == "clamp"


def test_a_zener_clamp_whose_breakdown_is_modelled_passes_and_is_recorded() -> None:
    """With the breakdown as a reverse diode at vz, the model shows about vz."""
    toy = board()
    zener(toy)

    def model(c: Circuit, _index: int) -> None:
        c.D("ZG", "+12V", 0.7, ref="D10")
        c.D("+12V", "ZG", 5.1, ref="D10.breakdown")

    report = judge(toy, read({**TABLE, "Z": {"vz": 5.1}}), model)
    assert report.problems == []
    entry = report.parts["D10"]
    assert entry["kind"] == "clamp"
    assert entry["vr"]["v"] == pytest.approx(5.1, abs=0.01)
    assert entry["vr"]["vz"] == 5.1


def test_semi_not_judged_skips_a_declared_part_and_records_its_reason() -> None:
    """A bridge rectifier the check cannot read: declared, skipped, recorded."""
    toy = board()
    toy.add(
        "D11",
        "BRIDGE",
        "Diode_Bridge:Bridge",
        {"1": ("AC1", "~"), "2": ("+12V", "+"), "3": ("AC2", "~"), "4": ("GND", "-")},
    )
    assert judge(toy).problems[0].startswith("D11 (Diode_Bridge:Bridge) cannot be")
    report = semis.judge(
        netlist(toy), TABLE, attempts(toy), {"D11": "a bridge: judged by hand"}
    )
    assert report.problems == []
    assert report.declared == {"D11": "a bridge: judged by hand"}
    assert "D11" not in report.parts


def test_semi_not_judged_naming_a_part_not_in_the_design_fails() -> None:
    """A stale row fails by name."""
    report = semis.judge(netlist(board()), TABLE, attempts(board()), {"D99": "x"})
    assert report.problems == ["SEMI_NOT_JUDGED names D99, which is not in the design"]


def test_a_declared_part_with_a_rating_row_is_still_judged() -> None:
    """Q1 has a row, so declaring it changes nothing: it is judged and fails."""
    report = semis.judge(
        netlist(board()),
        {"FET-1": {"vds": 5.0, "vgs": 20.0}},
        attempts(board()),
        {"Q1": "said to be out of reach"},
    )
    assert report.problems[0].startswith("Q1 |Vds| reaches 12.00 V")
    assert report.declared == {}


def test_a_fet_with_a_floating_gate_on_a_judged_parts_nets_stops_the_verdict() -> None:
    """D2's cathode net is pulled up and pulled down by Q7, whose gate floats."""
    toy = board()
    check_toy.resistor(toy, "R9", "1k", "+12V", "NODE")
    toy.add(
        "D2",
        "SS14",
        "Device:D_Schottky",
        {"1": ("NODE", "K"), "2": ("GND", "A")},
        MPN="SCH-1",
    )
    toy.add(
        "Q7",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("QG", "G"), "2": ("GND", "S"), "3": ("NODE", "D")},
        MPN="FET-1",
    )
    report = judge(toy, {**TABLE, "SCH-1": {"vrrm": 40.0}})
    assert (
        "D2 cannot be judged in STRESS_SCENARIOS[0]: the gate of Q7 (QG) is floating, "
        "so whether Q7 conducts is the solver's guess (the leak holds the gate at 0 "
        "V); pull the gate to a net the scenario fixes, or fix it in the scenario"
    ) in report.problems


def test_a_part_the_scenario_models_only_from_a_later_row_is_named() -> None:
    """X1 has a row, so it is judged from scenario 0, where nothing models it."""
    toy = board()
    toy.add("X1", "?", "MyLib:Thing", {"1": ("+5V", "P"), "2": ("GND", "N")}, MPN="X")

    def late(c: Circuit, index: int) -> None:
        if index == 1:
            c.D("GND", "+5V", 0.7, ref="X1")

    row = {"vrrm": 30.0, "pins": {"a": "2", "k": "1"}}
    report = judge(toy, read({**TABLE, "X": row}), late)
    assert len(report.problems) == 1
    assert report.problems[0].startswith(
        "X1 (MyLib:Thing) is not modelled on pad 2 (A) on GND, pad 1 (K) on +5V in "
        "STRESS_SCENARIOS[0]"
    )


def test_a_part_found_only_through_a_later_scenarios_element_cannot_be_read() -> None:
    """Without a row its pins (P, N) do not read as a part: it fails by name."""
    toy = board()
    toy.add("X1", "?", "MyLib:Thing", {"1": ("+5V", "P"), "2": ("GND", "N")})

    def late(c: Circuit, index: int) -> None:
        if index == 1:
            c.D("GND", "+5V", 0.7, ref="X1")

    report = judge(toy, extra=late)
    assert report.problems == [
        "X1 (MyLib:Thing) cannot be judged: its pin names (P, N) do not read as one "
        'FET (G, D, S) or one diode (A, K); give its SEMI_VRATED row "pins", or '
        "declare it in SEMI_NOT_JUDGED with the reason"
    ]


def test_row_pins_that_disagree_with_the_circuits_diode_fail() -> None:
    """Unnamed pins, anode and cathode swapped in the row: the circuit disagrees."""
    toy = board()
    toy.add(
        "D3", "X", "MyLib:Diode2", {"1": ("+5V", "~"), "2": ("GND", "~")}, MPN="D-X"
    )

    def model(c: Circuit, _index: int) -> None:
        c.D("GND", "+5V", 0.7, ref="D3")

    row = {"vrrm": 4.0, "pins": {"a": "1", "k": "2"}}
    report = judge(toy, {**TABLE, "D-X": row}, model)
    assert report.problems == [
        "D3 cannot be judged in STRESS_SCENARIOS[0]: its row's element 1 (anode pad 1 "
        "on +5V, cathode pad 2 on GND) is not how the circuit models it: its diode "
        "conducts from GND to +5V; correct the row's pins"
    ]


def test_a_declared_forward_only_zener_on_a_judged_parts_nets_is_named() -> None:
    """SEMI_NOT_JUDGED hides a Zener from judging, not from the nets it can clamp."""
    toy = board()
    check_toy.resistor(toy, "R9", "10k", "+12V", "NODE")
    check_toy.resistor(toy, "R10", "10k", "NODE", "GND")
    toy.add("D10", "BZT", "Device:D_Zener", {"1": ("NODE", "K"), "2": ("GND", "A")})
    toy.add(
        "D2",
        "SS14",
        "Device:D_Schottky",
        {"1": ("NODE", "K"), "2": ("GND", "A")},
        MPN="SCH-1",
    )
    nl = netlist(toy)
    tries = []
    for index, level in enumerate((3.3, 0.0)):
        c = build(nl, extended=True)
        c.fix("GND", 0.0).fix("+12V", 12.0).fix("+5V", 5.0).fix("GPIO", level)
        tries.append((index, c.solve(), None))
    report = semis.judge(
        nl, {**TABLE, "SCH-1": {"vrrm": 40.0}}, tries, {"D10": "a clamp"}
    )
    assert (
        "D2 cannot be judged in STRESS_SCENARIOS[0]: D10 is modelled forward only (no "
        "breakdown) and has 6.00 V across it in reverse, so whether it conducts is not "
        "known; if it breaks down (a Zener or TVS), model its breakdown "
        "(circuit.D(cathode net, anode net, vz, ref='D10.breakdown')) or fix the net "
        "it clamps; if it blocks, a SEMI_VRATED row of kind vrrm says it blocks up to "
        "its VRRM"
    ) in report.problems


def test_row_pins_that_disagree_with_the_circuits_fet_fail() -> None:
    """Unnamed pins; the row's gate is the pad the scenario models as the drain."""
    toy = board()
    toy.add(
        "Q8",
        "X",
        "MyLib:FETX",
        {"1": ("G8", "~"), "2": ("GND", "~"), "3": ("D8", "~")},
        MPN="F-X",
    )
    check_toy.resistor(toy, "R8", "1k", "+5V", "D8")
    check_toy.resistor(toy, "R18", "1k", "GPIO", "G8")

    def model(c: Circuit, _index: int) -> None:
        c.fets.append(
            {"kind": "n", "g": "G8", "d": "D8", "s": "GND", "vth": 2, "ron": 1,
             "ref": "Q8"}
        )  # fmt: skip

    good = {"vds": 30.0, "vgs": 20.0, "pins": {"g": "1", "d": "3", "s": "2"}}
    assert judge(toy, read({**TABLE, "F-X": good}), model).problems == []
    bad = {**good, "pins": {"g": "3", "d": "1", "s": "2"}}
    assert judge(toy, read({**TABLE, "F-X": bad}), model).problems == [
        "Q8 cannot be judged in STRESS_SCENARIOS[0]: its row's element 1 (gate pad 3 "
        "on D8) is not how the circuit models it: no FET of it there has that gate, "
        "drain and source; correct the row's pins"
    ]


# --- second review ------------------------------------------------------------------


def bidirectional_tvs(a: str, k: str, row: dict[str, Any]) -> semis.SemiReport:
    """Judge the reviewer's p1: a Device:D_TVS (A1, A2) across +24V, on ``row``."""
    toy = Toy()
    toy.add(
        "D1",
        "TVS",
        "Device:D_TVS",
        {"1": ("+24V", "A1"), "2": ("GND", "A2")},
        MPN="TVS15CA",
    )
    check_toy.resistor(toy, "R1", "10k", "+24V", "GND")
    nl = netlist(toy)
    c = build(nl, extended=True).fix("GND", 0).fix("+24V", 24)
    return semis.judge(
        nl, read({"TVS15CA": {**row, "pins": {"a": a, "k": k}}}), [(0, c.solve(), None)]
    )


def test_a_bidirectional_tvs_is_held_to_its_standoff_either_way_round() -> None:
    """p1: the pad order of a bidirectional part is arbitrary; both orders fail."""
    for a, k in (("1", "2"), ("2", "1")):
        report = bidirectional_tvs(a, k, {"vrwm": 15.0})
        assert report.problems == [
            "D1 |V(K) - V(A)| reaches 24.00 V in STRESS_SCENARIOS[0], over 1 of its "
            "15 V rating (15.00 V)"
        ]
        assert report.parts["D1"]["vr"]["polarity"] == "either way"


def test_the_earlier_board_2_row_a_vrrm_tvs_with_a_on_the_rail_fails() -> None:
    """The first reviewer's USB TVS row (vrrm 5.5, a on the rail) read -V and passed."""
    report = bidirectional_tvs("1", "2", {"vrrm": 5.5})
    assert report.problems == [
        "D1 |V(K) - V(A)| reaches 24.00 V in STRESS_SCENARIOS[0], over 0.8 of its "
        "5.5 V rating (4.40 V)"
    ]


def test_a_diode_is_held_to_its_rating_either_way_when_polarity_is_unknown() -> None:
    """Unnamed pins and no diode element in the circuit: the absolute voltage counts."""
    toy = board()
    toy.add("D7", "TVS", "MyLib:TVS", {"1": ("+5V", "~"), "2": ("GND", "~")}, MPN="T")

    def model(c: Circuit, _index: int) -> None:
        c.R("+5V", "GND", 1e12, ref="D7.open")

    row = {"vrrm": 5.0, "pins": {"a": "1", "k": "2"}}
    report = judge(toy, read({**TABLE, "T": row}), model)
    assert report.parts["D7"]["vr"]["v"] == 5.0
    assert report.problems == [
        "D7 |V(K) - V(A)| reaches 5.00 V in STRESS_SCENARIOS[0], over 0.8 of its 5 V "
        "rating (4.00 V)"
    ]


def test_unnamed_pins_confirmed_by_the_circuits_diode_are_judged_on_reverse_only() -> (
    None
):
    """The scenario's own diode says which pad is the anode: a forward drop is fine."""
    toy = board()
    toy.add("D7", "D", "MyLib:D", {"1": ("LA", "~"), "2": ("GND", "~")}, MPN="T")
    check_toy.resistor(toy, "R8", "50", "+5V", "LA")

    def model(c: Circuit, _index: int) -> None:
        c.D("LA", "GND", 3.0, 20.0, ref="D7")

    row = {"vrrm": 3.0, "pins": {"a": "1", "k": "2"}}
    report = judge(toy, read({**TABLE, "T": row}), model)
    assert report.problems == []
    assert report.parts["D7"]["vr"]["polarity"] == "reverse"
    assert report.parts["D7"]["vr"]["v"] < 0


def test_semi_high_z_pads_are_exempt_from_the_left_out_rule() -> None:
    """An idle controller pin the designer states draws and sources nothing."""
    toy = board()
    toy.add("U9", "MCU", "MCU:MCU", {"1": ("LOAD", "PA0"), "2": ("+3V3", "VDD")})
    nl = netlist(toy)
    report = semis.judge(nl, TABLE, attempts(toy))
    assert "the DC model leaves out U9 pad 1 (MCU:MCU) on LOAD" in report.problems[0]
    report = semis.judge(nl, TABLE, attempts(toy), None, [("U9", "1")])
    assert report.problems == []
    assert report.high_z == ["U9 pad 1"]


def test_semi_high_z_naming_a_part_or_pad_not_in_the_design_fails() -> None:
    """Each is refused by name."""
    report = semis.judge(
        netlist(board()), TABLE, attempts(board()), None, [("U9", "1"), ("Q1", "9")]
    )
    assert report.problems == [
        "SEMI_HIGH_Z names U9 pad 1: U9 is not in the design",
        "SEMI_HIGH_Z names Q1 pad 9: Q1 has no pad 9 on a net",
    ]


def test_the_not_modelled_message_drops_the_extended_advice_when_it_is_on() -> None:
    """With extended on, say what it models instead of advising it."""
    toy = board()
    toy.add("D4", "D", "MyLib:Diode", {"1": ("+5V", "K"), "2": ("GND", "A")}, MPN="D-1")
    nl = netlist(toy)
    c = build(nl, extended=True)
    c.fix("GND", 0.0).fix("+12V", 12.0).fix("+5V", 5.0).fix("GPIO", 3.3)
    report = semis.judge(nl, {**TABLE, "D-1": {"vrrm": 100.0}}, [(0, c.solve(), None)])
    (problem,) = report.problems
    assert "build with dc.build(nl, extended=True)" not in problem
    assert "dc.build(extended=True) does not model this symbol" in problem


def test_the_hint_for_a_part_with_several_elements_names_elements() -> None:
    """A dual FET found by its names: elements and SEMI_NOT_JUDGED are the way out."""
    toy = board()
    toy.add(
        "Q3",
        "DUAL",
        "MyLib:DualFET",
        {"1": ("GND", "S1"), "2": ("G1N", "G1"), "3": ("D1N", "D1")},
    )
    (problem,) = judge(toy).problems
    assert problem.endswith(
        'list each diode or FET of it in its SEMI_VRATED row\'s "elements", or declare '
        "it in SEMI_NOT_JUDGED with the reason"
    )


def test_the_hint_for_a_row_of_the_wrong_kind_says_which_kind() -> None:
    """A FET given a clamp row."""
    (problem,) = judge(board(), {"FET-1": {"vz": 5.0}}).problems
    assert problem.endswith(
        "write a FET row (vds, vgs) for it, or declare it in SEMI_NOT_JUDGED with the "
        "reason"
    )


def test_f_records_the_fets_the_verdict_leaned_on() -> None:
    """Q1's own state, per scenario: Vgs, threshold, on or off."""
    report = judge(board())
    assert report.fet_gates == {
        "Q1": [
            {"scenario": 0, "vgs": 3.3, "vth": 2.0, "on": True},
            {"scenario": 1, "vgs": 0.0, "vth": 2.0, "on": False},
        ]
    }


def one_way_part(names: tuple[str, str], element: str) -> semis.SemiReport:
    """Judge a part across +5V, a on the rail, held by the R element ``element``."""
    toy = board()
    toy.add(
        "D7",
        "X",
        "MyLib:Part",
        {"1": ("+5V", names[0]), "2": ("GND", names[1])},
        MPN="T",
    )

    def model(c: Circuit, _index: int) -> None:
        c.R("+5V", "GND", 1e12, ref=element)

    row = {"vrrm": 5.0, "pins": {"a": "1", "k": "2"}}
    return judge(toy, read({**TABLE, "T": row}), model)


def test_a_part_held_as_open_is_judged_either_way_even_with_a_and_k_names() -> None:
    """``<ref>.open`` says nothing conducts: the names alone do not settle polarity."""
    report = one_way_part(("A", "K"), "D7.open")
    assert report.parts["D7"]["vr"] == {
        "v": 5.0,
        "rated": 5.0,
        "limit_v": 4.0,
        "scenario": 0,
        "polarity": "either way",
    }


def test_a_part_whose_polarity_nothing_confirms_is_judged_either_way() -> None:
    """Unnamed pins, held by a plain element with no diode: absolute voltage."""
    report = one_way_part(("~", "~"), "D7")
    assert report.parts["D7"]["vr"]["polarity"] == "either way"
    assert report.parts["D7"]["vr"]["v"] == 5.0


def test_a_and_k_names_alone_confirm_polarity() -> None:
    """Named A and K, held by a plain element: reverse only, so -5 V passes."""
    report = one_way_part(("A", "K"), "D7")
    assert report.parts["D7"]["vr"]["polarity"] == "reverse"
    assert report.parts["D7"]["vr"]["v"] == -5.0


def test_a_vrrm_row_lets_a_blocking_library_diode_sit_on_a_judged_parts_nets() -> None:
    """F: a Diode-library part of unknown kind, stated to block to 100 V, is fine."""
    toy = board()
    check_toy.resistor(toy, "R9", "10k", "+12V", "NODE")
    check_toy.resistor(toy, "R10", "10k", "NODE", "GND")
    toy.add(
        "D10",
        "1N4148",
        "Diode:1N4148",
        {"1": ("NODE", "K"), "2": ("GND", "A")},
        MPN="1N4148",
    )
    toy.add(
        "D2",
        "SS14",
        "Device:D_Schottky",
        {"1": ("NODE", "K"), "2": ("GND", "A")},
        MPN="SCH-1",
    )
    nl = netlist(toy)
    tries = []
    for index, level in enumerate((3.3, 0.0)):
        c = build(nl, extended=True)
        c.fix("GND", 0.0).fix("+12V", 12.0).fix("+5V", 5.0).fix("GPIO", level)
        tries.append((index, c.solve(), None))
    table = {**TABLE, "SCH-1": {"vrrm": 40.0}, "1N4148": {"vrrm": 100.0}}
    assert semis.judge(nl, table, tries).problems == []
    table["1N4148"] = {"vz": 5.1}
    report = semis.judge(nl, table, list(tries))
    assert any("D2 cannot be judged" in p and "D10 is modelled forward" in p
               for p in report.problems)  # fmt: skip


# --- narrow review ------------------------------------------------------------------


def bidir_esd(
    names: tuple[str, str], row: dict[str, Any], model: str
) -> semis.SemiReport:
    """Judge the reviewer's p_pol: a custom bidirectional ESD part on VBUS (6 V)."""
    toy = Toy()
    toy.add(
        "D1",
        "ESD",
        "MyLib:ESD_BIDIR",
        {"1": ("VBUS", names[0]), "2": ("GND", names[1])},
        MPN="ESD-5",
    )
    check_toy.resistor(toy, "R1", "10k", "VBUS", "GND")
    nl = netlist(toy)
    c = build(nl, extended=True).fix("GND", 0).fix("VBUS", 6.0)
    if model == "diode":
        c.D("VBUS", "GND", 0.7, ref="D1")
    else:
        c.R("VBUS", "GND", 1e12, ref="D1.open")
    return semis.judge(nl, read({"ESD-5": row}), [(0, c.solve(), None)])


ESD_ROW = {"vrrm": 5.5, "pins": {"a": "1", "k": "2"}}


def test_a_hand_diode_never_confirms_polarity_for_pins_named_a1_and_a2() -> None:
    """p_pol, with VBUS fed through 10 ohm so the hand diode is physical."""
    toy = Toy()
    toy.add(
        "D1",
        "ESD",
        "MyLib:ESD_BIDIR",
        {"1": ("VBUS", "A1"), "2": ("GND", "A2")},
        MPN="ESD-5",
    )
    check_toy.resistor(toy, "R1", "10", "+6V", "VBUS")
    nl = netlist(toy)
    c = build(nl, extended=True).fix("GND", 0).fix("+6V", 6.0)
    c.D("VBUS", "GND", 0.7, ref="D1")
    report = semis.judge(nl, read({"ESD-5": ESD_ROW}), [(0, c.solve(), None)])
    assert report.parts["D1"]["vr"]["polarity"] == "either way"


def test_a_part_with_no_reading_records_no_polarity() -> None:
    """Stopped as unphysical: no voltage, so no claim about its polarity."""
    report = bidir_esd(("1", "2"), ESD_ROW, "diode")
    assert report.parts["D1"]["vr"]["v"] is None
    assert report.parts["D1"]["vr"]["polarity"] is None


@pytest.mark.parametrize("names", [("A1", "A2"), ("~", "~"), ("1", "2")])
def test_a_diode_element_conducting_far_past_its_drop_is_unphysical(
    names: tuple[str, str],
) -> None:
    """A diode across two fixed nets shows 6 V forward: the model is not the board."""
    report = bidir_esd(names, ESD_ROW, "diode")
    assert (
        "D1 cannot be judged in STRESS_SCENARIOS[0]: its diode element D1 conducts "
        "with 6.00 V across it, far more than its 0.7 V forward drop, which only a "
        "diode between two fixed nets can show, so the model is not the board; model "
        "a bidirectional part as an open element (circuit.R(net, net, 1e12, "
        "ref='D1.open')), never as circuit.D"
    ) in report.problems


@pytest.mark.parametrize("names", [("A1", "A2"), ("~", "~"), ("1", "2")])
def test_a_bidirectional_part_modelled_open_is_judged_either_way(
    names: tuple[str, str],
) -> None:
    """The way out: open, judged at 6 V against 0.8 of 5.5 V."""
    report = bidir_esd(names, ESD_ROW, "open")
    assert report.problems == [
        "D1 |V(K) - V(A)| reaches 6.00 V in STRESS_SCENARIOS[0], over 0.8 of its "
        "5.5 V rating (4.40 V)"
    ]


def test_an_ordinary_forward_diode_is_not_unphysical() -> None:
    """The LED through its resistor conducts at its drop: no complaint."""
    assert judge(board(), {**TABLE}).problems == []


def test_the_not_modelled_message_says_how_to_model_a_bidirectional_part() -> None:
    """Advise an open element, never circuit.D, for a bidirectional part."""
    toy = board()
    toy.add("D4", "D", "MyLib:Diode", {"1": ("+5V", "K"), "2": ("GND", "A")}, MPN="D-1")
    (problem,) = judge(toy, {**TABLE, "D-1": {"vrrm": 100.0}}).problems
    assert problem.endswith(
        "a circuit.fets entry for a FET; a bidirectional part as an open element, "
        "circuit.R(net, net, 1e12, ref='D4.open'), never as circuit.D)"
    )


@pytest.mark.parametrize("symbol", ["Device:D_Zener", "Device:D_TVS"])
def test_a_vrrm_row_on_a_device_zener_or_tvs_fails_by_name(symbol: str) -> None:
    """Its symbol says it breaks down: ask for vz or vrwm."""
    toy = board()
    toy.add("D9", "Z", symbol, {"1": ("+5V", "K"), "2": ("GND", "A")}, MPN="Z")
    nl = netlist(toy)
    tries = []
    for index, level in enumerate((3.3, 0.0)):
        c = build(nl, extended=True)
        c.fix("GND", 0.0).fix("+12V", 12.0).fix("+5V", 5.0).fix("GPIO", level)
        tries.append((index, c.solve(), None))
    report = semis.judge(nl, {**TABLE, "Z": {"vrrm": 100.0}}, tries)
    assert report.problems == [
        f"D9 ({symbol}) cannot be judged: its symbol says it breaks down, but its row "
        "is a vrrm row; write a vz row (a Zener used as a clamp) or a vrwm row (a "
        "TVS), or declare it in SEMI_NOT_JUDGED with the reason"
    ]
    assert "D9" not in semis.blocking_ratings(nl, {"Z": {"vrrm": 100.0}})


def test_an_unlisted_pad_of_a_part_with_a_high_z_pad_is_still_named() -> None:
    """SEMI_HIGH_Z exempts the pads it names, not their whole part."""
    toy = board()
    toy.add(
        "U9",
        "MCU",
        "MCU:MCU",
        {"1": ("LOAD", "PA0"), "2": ("NODE9", "PA1"), "3": ("+3V3", "VDD")},
    )
    check_toy.resistor(toy, "R9", "10k", "NODE9", "LOAD")
    report = semis.judge(netlist(toy), TABLE, attempts(toy), None, [("U9", "1")])
    assert "the DC model leaves out U9 pad 2 (MCU:MCU) on NODE9" in report.problems[0]


def test_an_unlisted_pad_of_a_partly_modelled_part_with_a_high_z_pad_is_named() -> None:
    """U9 modelled on LOAD; pad 2 high-Z; its pad 3, on a free net, is still named."""
    toy = board()
    toy.add(
        "U9",
        "MCU",
        "MCU:MCU",
        {"1": ("LOAD", "PA0"), "2": ("NODE9", "PA1"), "3": ("NODE10", "PA2")},
    )

    def model(c: Circuit, _index: int) -> None:
        c.R("LOAD", "GND", 1e7, ref="U9")

    nl = netlist(toy)
    report = semis.judge(nl, TABLE, attempts(toy, model), None, [("U9", "2")])
    assert "the DC model leaves out U9 pad 3 (MCU:MCU) on NODE10" in report.problems[0]
    assert "U9 pad 2" not in report.problems[0]
