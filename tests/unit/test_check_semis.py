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
        {"1": ("GPIO", "A"), "2": ("GATE", "B")},
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
        "STRESS_SCENARIOS[0]: dc.build does not model this symbol, so the voltage "
        "across it is not the board's; add it in circuits.scenario with ref 'D4' "
        "(circuit.D(anode net, cathode net, vf, ref='D4') for a diode, a "
        "circuit.fets entry for a FET)"
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
        {"1": ("GATE", "B"), "2": ("LOAD", "C"), "3": ("GND", "E")},
        MPN="BJT-1",
    )
    report = judge(toy, {**TABLE, "BJT-1": {"vds": 45.0, "vgs": 6.0}})
    assert report.problems == [
        "Q2 (Device:Q_NPN_BCE) cannot be judged: its pin names (B, C, E) do not read "
        'as one FET (G, D, S) or one diode (A, K); give its SEMI_VRATED row "pins" (a '
        'bipolar transistor is written as a FET row: "vds" its VCEO, "vgs" its VEBO, '
        '"pins" {"g": base pad, "d": collector pad, "s": emitter pad})'
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
            "2": ("GATE", "G1"),
            "3": ("LOAD", "D1"),
            "4": ("GND", "S2"),
            "5": ("GPIO", "G2"),
            "6": ("+5V", "D2"),
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
        "other nets: a row describes one element, so a part with a second diode or "
        "FET cannot be judged"
    ]


def test_a_dual_fet_with_no_row_pins_is_found_by_its_names() -> None:
    """G1, D1, S1, G2... are one family's names: it is judged, and fails by name."""
    toy = board()
    toy.add(
        "Q3",
        "DUAL",
        "MyLib:DualFET",
        {"1": ("GND", "S1"), "2": ("GATE", "G1"), "3": ("LOAD", "D1")},
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
        'row is a diode row; give its SEMI_VRATED row "pins"'
    ]


def test_row_pins_naming_a_pad_the_part_does_not_have_fail() -> None:
    """Pad 9 is on no net."""
    row = {"vds": 60.0, "vgs": 20.0, "pins": {"g": "9", "d": "3", "s": "2"}}
    report = judge(board(), {"FET-1": row})
    assert report.problems == [
        "Q1 (Transistor_FET:2N7002) cannot be judged: its row's pins name pads 9, "
        "which are on no net (its pads are 1, 2, 3)"
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


def test_a_diode_is_held_to_its_rating_either_way() -> None:
    """A bidirectional part written with its pins one way round sees -5 V: 5 V."""
    toy = board()
    toy.add("D7", "TVS", "MyLib:TVS", {"1": ("+5V", "~"), "2": ("GND", "~")}, MPN="T")

    def model(c: Circuit, _index: int) -> None:
        c.D("GND", "+5V", 0.7, ref="D7")

    row = {"vrrm": 5.0, "pins": {"a": "1", "k": "2"}}
    report = judge(toy, {**TABLE, "T": row}, model)
    assert report.parts["D7"]["vr"]["v"] == 5.0
    assert report.problems == [
        "D7 |V(K) - V(A)| reaches 5.00 V in STRESS_SCENARIOS[0], over 0.8 of its 5 V "
        "rating (4.00 V)"
    ]


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
