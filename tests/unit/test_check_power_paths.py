"""Unit tests for pcbkit.check.power_paths, on hand-built netlists.

The first half is the ordinary power trees the checks must pass with the table a
designer would write; the second half is the wrong designs they must catch, starting
with the plan's planted mistakes. Every design is generic.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import power_paths as pp
from pcbkit.check import tables
from pcbkit.check.netlist import Netlist
from tests import check_toy
from tests.check_toy import Toy


def netlist(toy: Toy) -> Netlist:
    """Return the netlist KiCad 10 would write for the toy."""
    return Netlist.from_text(check_toy.netlist_text(toy))


def regulator(toy: Toy, ref: str, vin: str, vout: str) -> None:
    """Add a three-pin regulator: pad 1 in, pad 2 ground, pad 3 out."""
    toy.add(
        ref,
        "LDO",
        "Regulator:LDO",
        {"1": (vin, "IN"), "2": ("GND", "GND"), "3": (vout, "OUT")},
    )


def schottky(toy: Toy, ref: str, anode: str, cathode: str) -> None:
    """Add a two-pad Schottky diode, pins named A and K."""
    toy.add(ref, "SS14", "Device:D_Schottky", {"1": (cathode, "K"), "2": (anode, "A")})


def pfet(toy: Toy, ref: str, gate: str, source: str, drain: str) -> None:
    """Add a P-FET whose pins are named G, S and D (pads 1, 2, 3)."""
    toy.add(
        ref,
        "AO3401A",
        "Transistor_FET:AO3401A",
        {"1": (gate, "G"), "2": (source, "S"), "3": (drain, "D")},
    )


def two_pad(toy: Toy, ref: str, kind: str, value: str, a: str, b: str) -> None:
    """Add a two-pad part of ``kind`` ("Device:FerriteBead") between two nets."""
    toy.add(ref, value, kind, {"1": (a, "~"), "2": (b, "~")})


def ic(toy: Toy, ref: str, pads: dict[str, str]) -> None:
    """Add an IC with ``{pad: net}``, its pins named after their nets."""
    toy.add(ref, "IC", "Interface:IC", {p: (n, f"P{p}") for p, n in pads.items()})


def table(
    nets: list[str],
    paths: list[tuple[str, str, list[str]]],
    parts: dict[str, list[tuple[Any, Any]]] | None = None,
    max_ohm: float | None = None,
) -> dict[str, Any]:
    """Return a ``POWER_PATHS`` table, read through its shape as the check reads it."""
    raw: dict[str, Any] = {"nets": nets, "paths": paths}
    if parts is not None:
        raw["parts"] = parts
    if max_ohm is not None:
        raw["max_ohm"] = max_ohm
    out, errors = tables.convert("POWER_PATHS", raw, tables.registry()["POWER_PATHS"])
    assert errors == []
    return out


def run(
    toy: Toy,
    power: dict[str, Any],
    supplies: list[str] | None = None,
    ground: str | None = "GND",
) -> dict[str, list[str]]:
    """Run all three checks; return their problems keyed ``table``, rows, and so on."""
    nl = netlist(toy)
    if supplies is None:
        supplies = [*power["nets"], *([ground] if ground in nl.nets else [])]
    model = pp.build_model(nl, power, ground)
    out = {"table": pp.table_problems(nl, power, supplies, ground)}
    for row in pp.read_rows(power):
        out[pp.path_id(row)] = pp.check_path(nl, model, power, row).problems
    out["unlisted"] = pp.check_unlisted(nl, model, power).problems
    out["modelled"] = pp.check_modelled(nl, model, power).problems
    return out


def red(found: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return only the checks that failed."""
    return {k: v for k, v in found.items() if v}


# --------------------------------------------------------------- values


@pytest.mark.parametrize(
    ("value", "ohms"),
    [
        ("10k", 1e4),
        ("4k7", 4700.0),
        ("10K", 1e4),
        ("4K7", 4700.0),
        ("4R7", 4.7),
        ("R47", 0.47),
        ("0R", 0.0),
        ("0E", 0.0),
        ("0", 0.0),
        ("10kohm", 1e4),
        ("100K_0402", 1e5),
        ("1M", 1e6),
        ("1m", 1e-3),
        ("0.1", 0.1),
        ("4x10k", 1e4),
        ("10k 1%", 1e4),
    ],
)
def test_resistor_values_read_in_every_usual_spelling(value: str, ohms: float) -> None:
    """Read the usual ways of writing a resistance."""
    assert pp.read_ohms(value) == pytest.approx(ohms)


@pytest.mark.parametrize("value", ["", "x", "R", "DNP", "abc", "1.5k5"])
def test_values_that_are_no_resistance_read_as_none(value: str) -> None:
    """Refuse text that is not a resistance, so the caller can count it as 0 ohm."""
    assert pp.read_ohms(value) is None


# --------------------------------------------------- ordinary designs that pass


def test_a_regulator_declared_in_parts_passes_with_one_row() -> None:
    """VIN to +5V through U3, declared input pad to output pad."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "+5V")
    power = table(["VIN", "+5V"], [("VIN", "+5V", ["U3"])], {"U3": [(1, 3)]})
    assert red(run(toy, power)) == {}


def test_a_diode_or_of_two_sources_passes_and_neither_source_feeds_the_other() -> None:
    """VBUS and VBAT each into VIN through a Schottky; no path between the sources."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "VIN")
    schottky(toy, "D2", "VBAT", "VIN")
    power = table(
        ["VBUS", "VBAT", "VIN"],
        [("VBUS", "VIN", ["D1"]), ("VBAT", "VIN", ["D2"])],
    )
    assert red(run(toy, power)) == {}
    nl = netlist(toy)
    report = pp.check_unlisted(nl, pp.build_model(nl, power, "GND"), power)
    assert sorted(report.numbers["power paths"]) == ["VBAT->VIN", "VBUS->VIN"]


def reverse_protection() -> Toy:
    """Return a battery connector, a P-FET in its positive line, and a regulator."""
    toy = Toy()
    toy.add(
        "J2",
        "BAT",
        "Connector:Conn_01x02",
        {"1": ("VBAT_IN", "Pin_1"), "2": ("GND", "Pin_2")},
    )
    pfet(toy, "Q1", "GND", "VBAT", "VBAT_IN")
    regulator(toy, "U3", "VBAT", "+3V3")
    return toy


def test_a_reverse_protection_fet_on_the_connector_side_needs_no_row() -> None:
    """Leave the connector side unjudged: the FET reaches no second judged net."""
    power = table(["VBAT", "+3V3"], [("VBAT", "+3V3", ["U3"])], {"U3": [(1, 3)]})
    assert red(run(reverse_protection(), power)) == {}


def test_a_reverse_protection_fet_between_two_judged_rails_needs_a_row_each_way() -> (
    None
):
    """A FET that is on lets current back: both directions are rows."""
    toy = reverse_protection()
    one_way = table(
        ["VBAT_IN", "VBAT", "+3V3"],
        [("VBAT_IN", "VBAT", ["Q1"]), ("VBAT", "+3V3", ["U3"])],
        {"U3": [(1, 3)], "J2": []},
    )
    found = red(run(toy, one_way))
    assert list(found) == ["unlisted"]
    assert found["unlisted"] == [
        "VBAT -> VBAT_IN is joined at 0 ohm (VBAT - Q1 - VBAT_IN) and no row of "
        'POWER_PATHS["paths"] declares it, so current can flow from VBAT into '
        'VBAT_IN: remove the path, or if it is meant add ("VBAT", "VBAT_IN", '
        '["Q1"]) (Q1 is a FET: its channel counts as conducting both ways, whatever '
        "its gate does)"
    ]
    both = table(
        ["VBAT_IN", "VBAT", "+3V3"],
        [
            ("VBAT_IN", "VBAT", ["Q1"]),
            ("VBAT", "VBAT_IN", ["Q1"]),
            ("VBAT", "+3V3", ["U3"]),
        ],
        {"U3": [(1, 3)], "J2": []},
    )
    assert red(run(toy, both)) == {}


def test_a_load_switch_declared_one_way_passes() -> None:
    """An IC load switch from +5V to +5V_SW, with its enable from a GPIO."""
    toy = Toy()
    ic(toy, "U4", {"1": "+5V", "2": "GND", "3": "EN_SW", "4": "+5V_SW"})
    check_toy.resistor(toy, "R1", "100k", "EN_SW", "GND")
    power = table(["+5V", "+5V_SW"], [("+5V", "+5V_SW", ["U4"])], {"U4": [("1", "4")]})
    assert red(run(toy, power)) == {}


@pytest.mark.parametrize(
    ("ref", "kind", "value"),
    [
        ("FB1", "Device:FerriteBead", "600R@100MHz"),
        ("F1", "Device:Fuse", "1A"),
        ("L1", "Device:L", "10u"),
        ("R1", "Device:R", "0.1"),
    ],
)
def test_a_series_part_between_a_rail_and_its_copy_passes_with_a_row_each_way(
    ref: str, kind: str, value: str
) -> None:
    """A ferrite, fuse, inductor or sense resistor conducts both ways."""
    toy = Toy()
    two_pad(toy, ref, kind, value, "+3V3", "+3V3A")
    power = table(
        ["+3V3", "+3V3A"], [("+3V3", "+3V3A", [ref]), ("+3V3A", "+3V3", [ref])]
    )
    assert red(run(toy, power)) == {}


def test_a_sense_resistor_on_the_way_to_an_unjudged_net_needs_no_row() -> None:
    """A 0.1 ohm shunt to a load net that is not a judged rail is not a pair."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "+5V")
    check_toy.resistor(toy, "R9", "0.1", "+5V", "LOAD")
    toy.add(
        "J3", "LOAD", "Connector:Conn_01x02", {"1": ("LOAD", "1"), "2": ("GND", "2")}
    )
    power = table(["VIN", "+5V"], [("VIN", "+5V", ["U3"])], {"U3": [(1, 3)]})
    assert red(run(toy, power)) == {}


def test_pull_ups_leds_and_a_level_shifter_are_not_power_paths() -> None:
    """Record the 9.4k route through two pull-ups, and pass."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "4.7k", "+3V3", "SDA_A")
    toy.add(
        "Q5",
        "BSS138",
        "Transistor_FET:BSS138",
        {"1": ("+3V3", "G"), "2": ("SDA_A", "S"), "3": ("SDA_B", "D")},
    )
    check_toy.resistor(toy, "R2", "4.7k", "SDA_B", "+5V")
    check_toy.resistor(toy, "R3", "1k", "+5V", "LED_A")
    toy.add("D3", "Red", "Device:LED", {"1": ("GND", "K"), "2": ("LED_A", "A")})
    power = table(["+3V3", "+5V"], [])
    assert red(run(toy, power)) == {}
    nl = netlist(toy)
    found = pp.check_unlisted(nl, pp.build_model(nl, power, "GND"), power)
    record = found.numbers["power paths"]["+3V3->+5V"]
    assert record["ohms"] == pytest.approx(9400.0)
    assert record["path"] == ["+3V3", "R1", "SDA_A", "Q5", "SDA_B", "R2", "+5V"]
    assert record["power path"] is False
    assert record["listed"] is False


def test_a_capacitor_between_two_rails_is_open_and_never_named() -> None:
    """A two-pad capacitor across +5V and +3V3 is neither a path nor unmodelled."""
    toy = Toy()
    two_pad(toy, "C4", "Device:C", "100n", "+5V", "+3V3")
    assert red(run(toy, table(["+5V", "+3V3"], []))) == {}


def test_an_ic_powered_from_two_rails_passes_once_declared_as_passing_nothing() -> None:
    """A level shifter on VCCA and VCCB is declared ``[]``."""
    toy = Toy()
    ic(toy, "U4", {"1": "+3V3", "2": "+5V", "3": "GND"})
    assert red(run(toy, table(["+3V3", "+5V"], [], {"U4": []}))) == {}


def test_a_buck_converter_is_declared_to_its_switch_node() -> None:
    """VIN through U2 to SW, then through L1 to +5V: one row names U2."""
    toy = Toy()
    ic(toy, "U2", {"1": "VIN", "2": "GND", "3": "SW", "4": "FB"})
    two_pad(toy, "L1", "Device:L", "10u", "SW", "+5V")
    schottky(toy, "D1", "GND", "SW")
    check_toy.resistor(toy, "R1", "100k", "+5V", "FB")
    check_toy.resistor(toy, "R2", "22k", "FB", "GND")
    power = table(["VIN", "+5V"], [("VIN", "+5V", ["U2"])], {"U2": [(1, 3)]})
    assert red(run(toy, power)) == {}


def test_paths_compose_through_a_judged_rail() -> None:
    """VBUS to +5V and +5V to +3V3 need no VBUS to +3V3 row."""
    toy = Toy()
    regulator(toy, "U1", "VBUS", "+5V")
    regulator(toy, "U2", "+5V", "+3V3")
    power = table(
        ["VBUS", "+5V", "+3V3"],
        [("VBUS", "+5V", ["U1"]), ("+5V", "+3V3", ["U2"])],
        {"U1": [(1, 3)], "U2": [(1, 3)]},
    )
    assert red(run(toy, power)) == {}


def test_a_path_through_two_series_parts_names_both_or_either() -> None:
    """A fuse then a ferrite to the filtered rail; the row may name either part."""
    toy = Toy()
    two_pad(toy, "F1", "Device:Fuse", "1A", "VIN", "VIN_F")
    two_pad(toy, "FB1", "Device:FerriteBead", "600R", "VIN_F", "VIN_FILT")
    rows = [("VIN", "VIN_FILT", ["F1"]), ("VIN_FILT", "VIN", ["F1", "FB1"])]
    assert red(run(toy, table(["VIN", "VIN_FILT"], rows))) == {}


def test_the_walk_never_crosses_the_ground_net() -> None:
    """A short to ground and a clamp from ground into a rail join no two rails."""
    toy = Toy()
    pfet(toy, "Q1", "EN", "+3V3", "GND")
    schottky(toy, "D1", "GND", "+5V")
    assert red(run(toy, table(["+3V3", "+5V"], []))) == {}


def test_a_three_pad_feedthrough_capacitor_is_walked() -> None:
    """Its through pads are one conductor: +5V and +5V_F are joined both ways."""
    toy = Toy()
    toy.add(
        "C5",
        "NFM",
        "Device:C_Feedthrough",
        {"1": ("+5V", "~"), "2": ("GND", "~"), "3": ("+5V_F", "~")},
    )
    rows = [("+5V", "+5V_F", ["C5"]), ("+5V_F", "+5V", ["C5"])]
    assert red(run(toy, table(["+5V", "+5V_F"], rows))) == {}


def test_at_max_ohm_exactly_a_route_is_a_power_path() -> None:
    """A 10 ohm route at the default threshold counts; 10.5 does not."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "10", "+5V", "+3V3")
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert len(found["unlisted"]) == 2
    toy.set_value("R1", "10.5")
    assert red(run(toy, table(["+5V", "+3V3"], []))) == {}


# ------------------------------------------------------ wrong designs it catches


def test_plan_1_a_pfet_whose_drain_lands_on_another_rail_is_an_unlisted_path() -> None:
    """A P-FET across +3V3 and VBUS joins them both ways."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "+5V")
    pfet(toy, "Q1", "GND", "+3V3", "VBUS")
    power = table(["VBUS", "+5V", "+3V3"], [("VBUS", "+5V", ["D1"])])
    found = red(run(toy, power))
    assert list(found) == ["unlisted"]
    assert [line.split(" is joined")[0] for line in found["unlisted"]] == [
        "VBUS -> +3V3",
        "+3V3 -> VBUS",
    ]
    assert (
        "(Q1 is a FET: its channel counts as conducting both ways"
        in (found["unlisted"][0])
    )


def test_plan_1_as_written_swapping_drain_and_source_changes_nothing() -> None:
    """The plan's own FET rule (channel both ways) cannot see a D/S swap."""
    toy = reverse_protection()
    swapped = reverse_protection()
    pfet(swapped, "Q1", "GND", "VBAT_IN", "VBAT")
    power = table(
        ["VBAT_IN", "VBAT", "+3V3"],
        [("VBAT_IN", "VBAT", ["Q1"]), ("VBAT", "VBAT_IN", ["Q1"])]
        + [("VBAT", "+3V3", ["U3"])],
        {"U3": [(1, 3)], "J2": []},
    )
    assert run(toy, power) == run(swapped, power)


def test_plan_2_a_regulator_output_moved_to_another_net_is_not_found() -> None:
    """Name the row, the part, its pads and what it is declared to pass."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "VOUT_X")
    two_pad(toy, "C2", "Device:C", "10u", "+5V", "GND")
    power = table(["VIN", "+5V"], [("VIN", "+5V", ["U3"])], {"U3": [(1, 3)]})
    found = red(run(toy, power))
    assert list(found) == ["VIN->+5V"]
    assert found["VIN->+5V"] == [
        "declared path VIN->+5V through U3 not found: current cannot flow from VIN "
        "to +5V (U3: pads 1 on VIN, 2 on GND, 3 on VOUT_X; declared to pass 1 -> 3)"
    ]


def test_plan_3_an_undeclared_ic_across_two_rails_is_not_modelled() -> None:
    """Fail by name with both nets and the two ways out; nothing else goes red."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "+5V")
    ic(toy, "U5", {"1": "+5V", "2": "GND", "3": "+3V3"})
    power = table(["VIN", "+5V", "+3V3"], [("VIN", "+5V", ["U3"])], {"U3": [(1, 3)]})
    found = red(run(toy, power))
    assert list(found) == ["modelled"]
    assert found["modelled"] == [
        "U5 (Interface:IC) reaches 2 judged nets (+5V: pad 1 on +5V; +3V3: pad 3 on "
        "+3V3) and an IC or module (reference letters U, IC or A) is not walked, so "
        'a power path through it is not seen: declare what it passes in POWER_PATHS["'
        'parts"] (in pad to out pad, such as {"U5": [("1", "3")]}), or {"U5": []} '
        "if it passes no power"
    ]


def test_a_diode_from_the_rail_back_to_a_source_is_a_back_feed() -> None:
    """A second diode from VIN into VBUS lets VIN feed VBUS."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "VIN")
    schottky(toy, "D5", "VIN", "VBUS")
    found = red(run(toy, table(["VBUS", "VIN"], [("VBUS", "VIN", ["D1"])])))
    assert list(found) == ["unlisted"]
    assert found["unlisted"][0].startswith(
        "VIN -> VBUS is joined at 0 ohm (VIN - D5 - VBUS)"
    )


def test_a_bypass_beside_a_declared_regulator_fails_the_row() -> None:
    """A 0 ohm option from VIN to +5V: the row names U3 only."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "+5V")
    check_toy.resistor(toy, "R9", "0", "VIN", "+5V")
    power = table(["VIN", "+5V"], [("VIN", "+5V", ["U3"])], {"U3": [(1, 3)]})
    found = red(run(toy, power))
    assert list(found) == ["VIN->+5V", "unlisted"]
    assert found["VIN->+5V"] == [
        "VIN->+5V also runs through parts the row does not name: VIN - R9 - +5V (0 "
        'ohm): name them in the row (("VIN", "+5V", ["U3", "R9"])) if the path is '
        "meant, or remove the path"
    ]
    assert found["unlisted"][0].startswith("+5V -> VIN is joined at 0 ohm")


def test_a_not_fitted_option_counts_as_fitted_and_says_so() -> None:
    """``DNP`` alone is 0 ohm; the message says the netlist cannot tell."""
    toy = Toy()
    check_toy.resistor(toy, "R9", "DNP", "+5V", "+3V3")
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert (
        "(R9 is marked not fitted (DNP) and counts as fitted at 0 ohm"
        in (found["unlisted"][0])
    )
    toy.set_value("R9", "4.7 DNP")
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert (
        "(R9 is marked not fitted (4.7 DNP) and counts as fitted"
        in (found["unlisted"][0])
    )


def test_an_unreadable_resistor_value_counts_as_zero_ohm() -> None:
    """A value of ``x`` is a path, and the message says why."""
    toy = Toy()
    check_toy.resistor(toy, "R9", "x", "+5V", "+3V3")
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert (
        "(R9's value 'x' does not read as ohms, so it counts as 0 ohm)"
        in (found["unlisted"][0])
    )


def test_two_resistors_in_parallel_are_combined() -> None:
    """Two 15 ohm resistors are 7.5 ohm: a power path, though each alone is not."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "15", "+5V", "+3V3")
    check_toy.resistor(toy, "R2", "15", "+5V", "+3V3")
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert found["unlisted"][0].startswith("+5V -> +3V3 is joined at 7.5 ohm")
    assert (
        "(routes in parallel join the nets at 7.5 ohm, though this path alone is "
        "15 ohm)" in (found["unlisted"][0])
    )


def test_a_near_miss_passes_and_is_recorded() -> None:
    """A 12 ohm resistor between rails is recorded with its resistance."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "12", "+5V", "+3V3")
    power = table(["+5V", "+3V3"], [])
    assert red(run(toy, power)) == {}
    nl = netlist(toy)
    pairs = pp.check_unlisted(nl, pp.build_model(nl, power, "GND"), power).numbers
    assert pairs["power paths"]["+5V->+3V3"]["ohms"] == pytest.approx(12.0)
    assert pairs["power paths"]["+5V->+3V3"]["power path"] is False


def relay(toy: Toy) -> None:
    """Add a relay: coil from +5V to a driver net, contact from +12V to +12V_OUT."""
    toy.add(
        "K1",
        "RELAY",
        "Relay:SPST",
        {
            "1": ("+5V", "~"),
            "2": ("COIL", "~"),
            "3": ("+12V", "~"),
            "4": ("+12V_OUT", "~"),
        },
    )


def test_a_part_whose_pinout_is_not_known_is_joined_on_all_pads_and_says_so() -> None:
    """A relay joins its coil to its contact until its real conduction is declared."""
    toy = Toy()
    relay(toy)
    nets = ["+5V", "+12V", "+12V_OUT"]
    rows = [("+12V", "+12V_OUT", ["K1"]), ("+12V_OUT", "+12V", ["K1"])]
    found = red(run(toy, table(nets, rows)))
    assert list(found) == ["unlisted"]
    assert (
        "(K1 has four pads and is joined on all of them because its pinout is not "
        'known: declare what it passes in POWER_PATHS["parts"])'
    ) in found["unlisted"][0]
    declared = table(nets, rows, {"K1": [(3, 4), (4, 3)]})
    assert red(run(toy, declared)) == {}


def test_an_ic_whose_output_reaches_a_rail_through_a_ferrite_is_not_modelled() -> None:
    """The regulator's own output net is not judged; the rail past FB1 is."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "VOUT")
    two_pad(toy, "FB1", "Device:FerriteBead", "600R", "VOUT", "+3V3")
    found = red(run(toy, table(["VIN", "+3V3"], [])))
    assert list(found) == ["modelled"]
    assert (
        "+3V3: pad 3 on VOUT, joined to it (VOUT - FB1 - +3V3, 0 ohm)"
        in (found["modelled"][0])
    )


def test_a_connector_across_two_rails_must_be_declared() -> None:
    """A header that brings out two rails passes nothing on the board, if declared."""
    toy = Toy()
    toy.add(
        "J4",
        "HDR",
        "Connector:Conn_01x03",
        {"1": ("+5V", "Pin_1"), "2": ("+3V3", "Pin_2"), "3": ("GND", "Pin_3")},
    )
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert list(found) == ["modelled"]
    assert (
        "a connector is not walked: it joins nothing on the board"
        in (found["modelled"][0])
    )
    assert red(run(toy, table(["+5V", "+3V3"], [], {"J4": []}))) == {}


def test_a_bipolar_transistor_across_two_rails_is_joined_on_all_pads() -> None:
    """A PNP whose emitter and collector are on two rails is a path both ways."""
    toy = Toy()
    toy.add(
        "Q2",
        "PNP",
        "Transistor_BJT:PNP",
        {"1": ("BASE", "B"), "2": ("+5V", "E"), "3": ("+3V3", "C")},
    )
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert list(found) == ["unlisted"]
    assert len(found["unlisted"]) == 2


def test_a_dual_diode_is_joined_on_all_pads() -> None:
    """A series pair across two rails: its pin names cannot say how it is wired."""
    toy = Toy()
    toy.add(
        "D7",
        "BAV99",
        "Diode:BAV99",
        {"1": ("+5V", "K"), "2": ("+3V3", "A"), "3": ("SIG", "K")},
    )
    found = red(run(toy, table(["+5V", "+3V3"], [])))
    assert len(found["unlisted"]) == 2


def test_a_named_part_that_is_on_no_power_path_fails_the_row() -> None:
    """D2 is named but sits elsewhere: the row is wrong."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "VIN")
    schottky(toy, "D2", "VBAT", "SIG")
    found = red(run(toy, table(["VBUS", "VIN"], [("VBUS", "VIN", ["D1", "D2"])])))
    assert found == {
        "VBUS->VIN": [
            "D2 is named in VBUS->VIN but no power path from VBUS to VIN runs "
            "through it (the path is VBUS - D1 - VIN)"
        ]
    }


def test_a_path_above_max_ohm_is_not_found_and_says_what_was() -> None:
    """Name the least resistance and the path when it is over the threshold."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "47", "VIN", "+5V")
    found = red(run(toy, table(["VIN", "+5V"], [("VIN", "+5V", ["R1"])])))
    assert found["VIN->+5V"] == [
        "declared path VIN->+5V through R1 not found: the least resistance from VIN "
        "to +5V is 47 ohm (VIN - R1 - +5V), above max_ohm 10 (R1: pads 1 on VIN, 2 "
        "on +5V)"
    ]
    assert red(run(toy, table(["VIN", "+5V"], [("VIN", "+5V", ["R1"])], None, 50)))[
        "unlisted"
    ]


@pytest.mark.parametrize(
    ("row", "says"),
    [
        (("VIN", "+9V", ["D1"]), '+9V is not in POWER_PATHS["nets"]'),
        (("VIN", "VIN", ["D1"]), "the row runs from VIN to itself"),
        (("VBUS", "VIN", []), "the row VBUS->VIN names no part"),
        (("VBUS", "VIN", ["D9"]), "D9 is not in the design"),
    ],
)
def test_a_wrong_row_fails_by_name(row: tuple[str, str, list[str]], says: str) -> None:
    """Say what is wrong with the row, and judge nothing else in it."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "VIN")
    rows = [row, ("VBUS", "VIN", ["D1"])] if row[:2] != ("VBUS", "VIN") else [row]
    found = run(toy, table(["VBUS", "VIN"], rows))
    assert says in "; ".join(found[pp.path_id(row)])


def test_a_row_listed_twice_fails() -> None:
    """Both copies of a duplicate row fail."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "VIN")
    power = table(["VBUS", "VIN"], [("VBUS", "VIN", ["D1"]), ("VBUS", "VIN", ["D1"])])
    nl = netlist(toy)
    model = pp.build_model(nl, power, "GND")
    for row in pp.read_rows(power):
        assert pp.check_path(nl, model, power, row).problems == [
            'VBUS->VIN is listed twice in POWER_PATHS["paths"]'
        ]


# ------------------------------------------------------------------- the table


def test_judged_nets_must_be_exactly_the_supplies_other_than_ground() -> None:
    """A judged net not supplied, and a supply not judged, each fail by name."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "VIN")
    two_pad(toy, "FB1", "Device:FerriteBead", "x", "VIN", "VREF")
    two_pad(toy, "C1", "Device:C", "1u", "VIN", "GND")
    power = table(["VBUS", "VIN"], [("VBUS", "VIN", ["D1"])])
    found = run(toy, power, supplies=["GND", "VIN", "VREF"])
    assert found["table"] == [
        'POWER_PATHS["nets"]: VBUS is not in specs.SUPPLY_NETS: every judged rail is '
        "a supply, so add it there (the enable-link walk must stop at it too)",
        'specs.SUPPLY_NETS: VREF is not in POWER_PATHS["nets"]: every supply other '
        "than the ground net (GND) is judged, so add it there (a second ground, such "
        "as AGND, too: set specs.GROUND_NET if it is the ground, else an IC across it "
        'and a rail is declared {"U1": []})',
    ]


def test_the_ground_net_is_never_judged() -> None:
    """Listing the ground net in nets fails."""
    toy = Toy()
    schottky(toy, "D1", "GND", "VIN")
    found = run(toy, table(["GND", "VIN"], []), supplies=["GND", "VIN"])
    assert found["table"] == [
        'POWER_PATHS["nets"]: GND is the ground net, the return of every path and '
        "not a rail: leave it out (it is never judged and never crossed)"
    ]


def test_a_net_not_in_the_design_and_a_slash_are_named() -> None:
    """Say the net is missing, and that a leading slash is not written."""
    toy = Toy()
    schottky(toy, "D1", "VBUS", "VIN")
    found = run(toy, table(["/VIN", "VBUS"], []), supplies=["GND", "/VIN", "VBUS"])
    assert (
        'POWER_PATHS["nets"]: /VIN is not in the design (net names carry no leading '
        "slash: write VIN)"
    ) in found["table"]


def test_a_declared_part_must_be_in_the_design_with_its_pads() -> None:
    """Name a part that is not there and a pad the part does not have."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "+5V")
    power = table(["VIN", "+5V"], [], {"U3": [(1, 9)], "U8": []})
    found = run(toy, power)
    assert found["table"] == [
        'POWER_PATHS["parts"]: pad 9 is not a pad of U3 on a net (its pads are 1, 2, '
        "3)",
        'POWER_PATHS["parts"]: U8 is not in the design',
    ]


def test_a_declared_part_overrides_every_built_in_rule() -> None:
    """An ideal-diode FET declared one way passes power one way only."""
    toy = Toy()
    pfet(toy, "Q1", "GATE", "VIN", "VBUS")
    power = table(["VBUS", "VIN"], [("VBUS", "VIN", ["Q1"])], {"Q1": [(3, 2)]})
    assert red(run(toy, power)) == {}


def test_a_declared_part_is_trusted_and_records_what_it_passes() -> None:
    """Record the declared arcs with their nets."""
    toy = Toy()
    regulator(toy, "U3", "VIN", "+5V")
    power = table(["VIN", "+5V"], [("VIN", "+5V", ["U3"])], {"U3": [(1, 3)]})
    nl = netlist(toy)
    report = pp.check_modelled(nl, pp.build_model(nl, power, "GND"), power)
    assert report.numbers == {
        "not modelled": {},
        "declared parts": {"U3": ["1 -> 3 (VIN -> +5V)"]},
    }


def test_no_ground_net_means_nothing_stops_the_walk_but_the_rails() -> None:
    """Without a ground, the return net is walked like a signal."""
    toy = Toy()
    pfet(toy, "Q1", "EN", "+3V3", "0V")
    schottky(toy, "D1", "0V", "+5V")
    found = red(run(toy, table(["+3V3", "+5V"], []), ground=None))
    assert list(found) == ["unlisted"]
    assert (
        "(0V is walked as a signal: a rail goes in specs.SUPPLY_NETS"
        in (found["unlisted"][0])
    )
