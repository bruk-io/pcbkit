"""Unit tests for pcbkit.check.links: enable links, on a hand-built netlist."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from pcbkit.check import links, tables
from pcbkit.check.netlist import Netlist
from tests import check_toy
from tests.check_toy import Toy


def netlist(toy: Toy) -> Netlist:
    """Return the netlist KiCad 10 would write for the toy."""
    return Netlist.from_text(check_toy.netlist_text(toy))


def regulator(toy: Toy, ref: str, enable_net: str) -> None:
    """Add a regulator whose enable pin is pad 3."""
    toy.add(
        ref,
        "LDO",
        "Regulator:LDO",
        {"1": ("VIN", "IN"), "2": ("GND", "GND"), "3": (enable_net, "EN")},
    )


def board() -> Toy:
    """Return two regulators, each behind its own 0 ohm link to EN_ALL."""
    toy = Toy()
    regulator(toy, "U2", "EN_U2")
    regulator(toy, "U3", "EN_U3")
    check_toy.resistor(toy, "R7", "0", "EN_U2", "EN_ALL")
    check_toy.resistor(toy, "R8", "0R", "EN_ALL", "EN_U3")
    return toy


ROWS = [("U2", "3"), ("U3", "3")]
SUPPLIES = ["GND", "VIN"]


def check(
    toy: Toy,
    table: dict[str, list[tuple[str, str]]] | None = None,
    net: str = "EN_ALL",
    supplies: list[str] | None = None,
) -> links.EnableReport:
    """Check shared net ``net`` of the table (default: EN_ALL with U2 and U3)."""
    return links.check_enable_net(
        netlist(toy),
        net,
        {"EN_ALL": ROWS} if table is None else table,
        SUPPLIES if supplies is None else supplies,
    )


def add_part(
    toy: Toy, ref: str, kind: str, pads: dict[str, str], value: str = "x"
) -> None:
    """Add a part of ``kind`` ("Device:R_Pack04") with ``{pad: net}``."""
    toy.add(ref, value, kind, {pad: (net, "~") for pad, net in pads.items()})


def with_part(toy: Toy, ref: str, value: str, a: str, b: str, kind: str) -> Toy:
    """Add a two-pin part of ``kind`` ("Device:R_Small") between two nets."""
    toy.add(ref, value, kind, {"1": (a, "~"), "2": (b, "~")})
    return toy


def replace(toy: Toy, ref: str) -> None:
    """Take part ``ref`` off the board."""
    del toy.parts[ref], toy.pins[ref]


def test_a_regulator_behind_its_own_zero_ohm_link_passes_and_names_the_link() -> None:
    """Find R7 for U2 and R8 for U3, whichever way round the link is wired."""
    report = check(board())
    assert report.problems == []
    assert report.links == {"U2": "R7", "U3": "R8"}


@pytest.mark.parametrize("value", ["0", "0R", "0Ω", "0.0"])
def test_a_zero_ohm_resistor_is_a_link_however_it_is_written(value: str) -> None:
    """Read 0, 0R, 0Ω and 0.0 as a link."""
    toy = board()
    toy.set_value("R7", value)
    assert links.is_link(netlist(toy), "R7")


@pytest.mark.parametrize("value", ["10k", "1", "0.1", "", "0k5x"])
def test_any_other_resistor_value_is_not_a_link(value: str) -> None:
    """Refuse a resistor that is not 0, and a value that cannot be read."""
    toy = board()
    toy.set_value("R7", value)
    assert not links.is_link(netlist(toy), "R7")


def test_a_two_pad_part_from_a_jumper_library_is_a_link() -> None:
    """Take a two-pad solder jumper whatever its value, and nothing else but a 0 R."""
    toy = board()
    toy.add(
        "JP1",
        "Open",
        "Jumper:SolderJumper_2_Open",
        {"1": ("EN_U2", "A"), "2": ("EN_ALL", "B")},
    )
    nl = netlist(toy)
    assert links.is_link(nl, "JP1")
    toy.add("C9", "0", "Device:C", {"1": ("EN_U2", "~"), "2": ("EN_ALL", "~")})
    assert not links.is_link(netlist(toy), "C9")


def test_a_three_pad_jumper_is_not_a_link() -> None:
    """Refuse a jumper with three pads as a link: the walk still goes through it."""
    toy = board()
    toy.add(
        "JP2",
        "Bridged",
        "Jumper:SolderJumper_3_Bridged12",
        {"1": ("EN_U2", "A"), "2": ("EN_ALL", "C"), "3": ("GND", "B")},
    )
    assert not links.is_link(netlist(toy), "JP2")


def test_the_library_rule_for_jumpers_is_a_prefix() -> None:
    """Leave a library that merely contains the word out."""
    toy = board()
    toy.add("X1", "x", "My_Jumper_Lib:J", {"1": ("EN_U2", "A"), "2": ("EN_ALL", "B")})
    assert not links.is_link(netlist(toy), "X1")


def test_joiners_are_two_pin_parts_between_the_two_nets_only() -> None:
    """Skip a three-pin part, and a part with only one pin on each net's neighbour."""
    toy = board()
    toy.add(
        "JP2",
        "Bridged",
        "Jumper:SolderJumper_3_Bridged12",
        {"1": ("EN_U2", "A"), "2": ("EN_ALL", "C"), "3": ("GND", "B")},
    )
    nl = netlist(toy)
    assert links.joiners(nl, "EN_U2", "EN_ALL") == ["R7"]
    assert links.joiners(nl, "EN_ALL", "EN_U2") == ["R7"]
    assert links.joiners(nl, "EN_U2", "EN_U3") == []
    assert links.joiners(nl, "EN_U2", "NOPE") == []


def test_both_enables_wired_straight_to_the_shared_net_fail_naming_each() -> None:
    """Name each regulator, and say it has no link of its own."""
    toy = board()
    toy.move_pin("U2", "3", "EN_ALL")
    toy.move_pin("U3", "3", "EN_ALL")
    report = check(toy)
    assert report.problems == [
        "U2 pad 3 is wired straight to EN_ALL: no link of its own",
        "U3 pad 3 is wired straight to EN_ALL: no link of its own",
    ]
    assert report.links == {"U2": None, "U3": None}


def test_a_link_replaced_by_a_resistor_fails_naming_the_part_and_its_value() -> None:
    """Say R7 (10k) joins the nets but is not a link."""
    toy = board()
    toy.set_value("R7", "10k")
    report = check(toy)
    assert report.problems == [
        "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL "
        "(R7 (10k) joins them but is not a link: its value is not 0 ohm or a "
        "not-fitted mark)"
    ]
    assert report.links == {"U2": None, "U3": "R8"}


def test_nothing_between_the_nets_fails_without_naming_a_part() -> None:
    """Say no link joins them when no part does."""
    toy = board()
    toy.move_pin("R8", "1", "SOMEWHERE")
    report = check(toy)
    assert report.problems == [
        "U3 pad 3: no 0 ohm resistor or jumper joins EN_U3 to EN_ALL"
    ]


def test_two_regulators_behind_one_link_share_it_and_fail() -> None:
    """Name both regulators and the link they share."""
    toy = board()
    toy.move_pin("U3", "3", "EN_U2")
    report = check(toy)
    assert report.problems == [
        "U2 and U3 share the link R7: each regulator needs its own"
    ]
    assert report.links == {"U2": "R7", "U3": "R7"}


def test_two_links_in_parallel_do_not_isolate_the_regulator() -> None:
    """Fail naming the link taken out and the other one that still joins the nets."""
    toy = board()
    check_toy.resistor(toy, "R9", "0", "EN_U2", "EN_ALL")
    report = check(toy)
    assert report.problems == [
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the shared "
        "net EN_ALL (EN_U2 - R9 - EN_ALL)"
    ]


def test_a_part_that_is_not_in_the_design_a_pad_on_no_net_and_a_missing_net() -> None:
    """Name each of the three, and stop short of a second complaint about one row."""
    toy = board()
    toy.move_pin("U3", "3", None)
    rows = [("U2", "3"), ("U3", "3"), ("U9", "3")]
    report = check(toy, {"EN_ALL": rows})
    assert report.problems == ["U3 pad 3 is on no net", "U9 is not in the design"]
    report = check(board(), {"NO_NET": ROWS}, net="NO_NET")
    assert report.problems[0] == "net NO_NET is not in the design"


def test_a_regulator_listed_on_two_pads_is_keyed_by_its_pad() -> None:
    """Key the two rows of U2 apart, so neither hides the other."""
    toy = board()
    toy.pins["U2"]["4"] = ("EN_U2", "EN2")
    report = check(toy, {"EN_ALL": [("U2", "3"), ("U2", "4")]})
    assert list(report.links) == ["U2 pad 3", "U2 pad 4"]
    assert report.problems == [
        "U2 pad 3 and U2 pad 4 share the link R7: each regulator needs its own"
    ]


def test_the_tables_are_this_modules_and_read_ints_as_pad_text() -> None:
    """Register both tables here, not in PROPOSED, and normalise pads to text."""
    for name in ("ENABLE_LINKS", "SUPPLY_NETS"):
        assert name not in tables.PROPOSED
        assert tables.registry()[name] is links.TABLES[name]
    shape = links.TABLES["ENABLE_LINKS"]
    out, errors = tables.convert("ENABLE_LINKS", {"EN": [["U3", 3]]}, shape)
    assert errors == []
    assert out == {"EN": [("U3", "3")]}
    found = tables.validate("ENABLE_LINKS", {"EN": [("U3",)]}, shape)
    assert found == [
        'specs.ENABLE_LINKS["EN"][0]: expected (regulator ref, enable pad), '
        "got ('U3',)"
    ]
    supplies = links.TABLES["SUPPLY_NETS"]
    assert tables.describe(supplies) == "[supply or ground net]"
    assert tables.validate("SUPPLY_NETS", ["GND", 3], supplies) == [
        "specs.SUPPLY_NETS[1]: expected supply or ground net, got 3"
    ]


# ------------------------------------------------------------ the isolation walk
def bypass_enables_with_zero_ohms(toy: Toy) -> None:
    """Short the two enable nets together downstream of their links."""
    check_toy.resistor(toy, "R9", "0", "EN_U2", "EN_U3")


def bypass_enables_with_ten_k(toy: Toy) -> None:
    """Bridge the two enable nets with 10k."""
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_U3")


def bypass_link_in_parallel(toy: Toy) -> None:
    """Put a 10k in parallel with the link."""
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_ALL")


def bypass_through_two_parts(toy: Toy) -> None:
    """Join EN_U2 to EN_ALL through R9 and R10 with a middle net."""
    check_toy.resistor(toy, "R9", "0", "EN_U2", "MID")
    check_toy.resistor(toy, "R10", "0", "MID", "EN_ALL")


BYPASSES = [
    (
        bypass_enables_with_zero_ohms,
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the enable "
        "net EN_U3 of U3 (EN_U2 - R9 - EN_U3)",
    ),
    (
        bypass_enables_with_ten_k,
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the enable "
        "net EN_U3 of U3 (EN_U2 - R9 - EN_U3)",
    ),
    (
        bypass_link_in_parallel,
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the shared "
        "net EN_ALL (EN_U2 - R9 - EN_ALL)",
    ),
    (
        bypass_through_two_parts,
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the shared "
        "net EN_ALL (EN_U2 - R9 - MID - R10 - EN_ALL) (if MID is a supply rail, "
        "declare it in specs.SUPPLY_NETS; if it is a signal, this path is real)",
    ),
]


@pytest.mark.parametrize(("plant", "message"), BYPASSES)
def test_a_second_path_around_the_link_fails_naming_the_path(
    plant: Callable[[Toy], None], message: str
) -> None:
    """Fail each of the four bypasses, though a link is found for each regulator."""
    toy = board()
    plant(toy)
    report = check(toy)
    assert message in report.problems
    assert report.links["U2"] == "R7"


def test_every_regulator_with_a_bypass_is_named() -> None:
    """Name U3 as well as U2 when a bridge joins their enables."""
    toy = board()
    bypass_enables_with_zero_ohms(toy)
    report = check(toy)
    assert [p.split(":")[0] for p in report.problems] == ["U2 pad 3", "U3 pad 3"]


# B1: the supply nets are the declared ones, whatever the others are called
@pytest.mark.parametrize(
    "middle", ["VBUS_EN", "VCC_EN", "VDD_EN", "VSYS_ON", "VIN_CTRL", "+EN", "GND_SW"]
)
def test_enables_bridged_through_a_net_named_like_a_supply_fail(middle: str) -> None:
    """Walk across a net that is not in SUPPLY_NETS, whatever its name."""
    toy = board()
    check_toy.resistor(toy, "R9", "0", "EN_U2", middle)
    check_toy.resistor(toy, "R10", "0", middle, "EN_U3")
    report = check(toy)
    assert (
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the enable "
        f"net EN_U3 of U3 (EN_U2 - R9 - {middle} - R10 - EN_U3) (if {middle} is a "
        "supply rail, declare it in specs.SUPPLY_NETS; if it is a signal, this path "
        "is real)"
    ) in report.problems


def test_a_bypass_parallel_to_the_link_through_a_supply_like_name_fails() -> None:
    """Fail EN_U2 - R9 - VCC_EN_BUS - R10 - EN_ALL beside R7."""
    toy = board()
    check_toy.resistor(toy, "R9", "0", "EN_U2", "VCC_EN_BUS")
    check_toy.resistor(toy, "R10", "0", "VCC_EN_BUS", "EN_ALL")
    report = check(toy)
    assert any(
        "(EN_U2 - R9 - VCC_EN_BUS - R10 - EN_ALL)" in p for p in report.problems
    ), report.problems


def test_a_pin_named_like_a_supply_does_not_make_its_net_one() -> None:
    """Walk across a net with a VIN pin on it when the net is not declared."""
    toy = board()
    check_toy.resistor(toy, "R9", "0", "EN_U2", "SEQ")
    check_toy.resistor(toy, "R10", "0", "SEQ", "EN_ALL")
    toy.add("U9", "X", "Sensor:X", {"1": ("SEQ", "VIN"), "2": ("GND", "GND")})
    assert any("EN_U2 - R9 - SEQ - R10 - EN_ALL" in p for p in check(toy).problems)


def test_a_declared_supply_with_any_name_is_not_walked() -> None:
    """Stop at a net in SUPPLY_NETS even when it is named like a signal."""
    toy = board()
    check_toy.resistor(toy, "R9", "100k", "EN_U2", "RAIL_A")
    check_toy.resistor(toy, "R10", "100k", "EN_U3", "RAIL_A")
    assert check(toy, supplies=["GND", "VIN", "RAIL_A"]).problems == []


# B2: the targets are the whole table's
def test_a_bridge_to_a_regulator_of_another_row_fails_both_rows() -> None:
    """Fail EN_ALL's U2 and EN_B's U4 when R9 joins their enables."""
    toy = board()
    regulator(toy, "U4", "EN_U4")
    regulator(toy, "U5", "EN_U5")
    check_toy.resistor(toy, "R11", "0", "EN_U4", "EN_B")
    check_toy.resistor(toy, "R12", "0", "EN_U5", "EN_B")
    check_toy.resistor(toy, "R9", "0", "EN_U2", "EN_U4")
    table = {"EN_ALL": ROWS, "EN_B": [("U4", "3"), ("U5", "3")]}
    report = check(toy, table)
    assert (
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the enable "
        "net EN_U4 of U4 (EN_U2 - R9 - EN_U4)"
    ) in report.problems
    report = check(toy, table, net="EN_B")
    assert (
        "U4 pad 3: removing R11 does not isolate it: EN_U4 still reaches the enable "
        "net EN_U2 of U2 (EN_U4 - R9 - EN_U2)"
    ) in report.problems


def test_a_bridge_to_another_rows_shared_net_fails() -> None:
    """Fail U2 when a 10k joins its enable to the shared net of another row."""
    toy = board()
    regulator(toy, "U4", "EN_U4")
    check_toy.resistor(toy, "R11", "0", "EN_U4", "EN_B")
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_B")
    report = check(toy, {"EN_ALL": ROWS, "EN_B": [("U4", "3")]})
    assert (
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the shared "
        "net EN_B (EN_U2 - R9 - EN_B)"
    ) in report.problems


def test_two_rows_each_right_pass_together() -> None:
    """Pass two shared nets whose regulators are each behind their own link."""
    toy = board()
    regulator(toy, "U4", "EN_U4")
    regulator(toy, "U5", "EN_U5")
    check_toy.resistor(toy, "R11", "0", "EN_U4", "EN_B")
    check_toy.resistor(toy, "R12", "0", "EN_U5", "EN_B")
    table = {"EN_ALL": ROWS, "EN_B": [("U4", "3"), ("U5", "3")]}
    assert check(toy, table).problems == []
    assert check(toy, table, net="EN_B").problems == []


# S1: a pull-up to a supply that is declared passes, whatever its name
@pytest.mark.parametrize("rail", ["3V3", "3.3V", "P3V3", "V3V3", "1V8_LDO"])
def test_pull_ups_to_a_declared_supply_pass(rail: str) -> None:
    """Pass EN_U2 - R20 - rail - R21 - EN_U3 when the rail is in SUPPLY_NETS."""
    toy = board()
    check_toy.resistor(toy, "R20", "100k", "EN_U2", rail)
    check_toy.resistor(toy, "R21", "100k", "EN_U3", rail)
    assert check(toy, supplies=["GND", "VIN", rail]).problems == []


def test_pull_ups_to_an_undeclared_supply_fail_with_a_way_out() -> None:
    """Say how to declare the rail when the path runs through one."""
    toy = board()
    check_toy.resistor(toy, "R20", "100k", "EN_U2", "3V3")
    check_toy.resistor(toy, "R21", "100k", "EN_U3", "3V3")
    report = check(toy)
    assert (
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the enable "
        "net EN_U3 of U3 (EN_U2 - R20 - 3V3 - R21 - EN_U3) (if 3V3 is a supply "
        "rail, declare it in specs.SUPPLY_NETS; if it is a signal, this path is real)"
    ) in report.problems


def test_the_hint_names_every_net_between_and_none_that_is_an_enable() -> None:
    """List the two nets of a longer path, and never an enable net."""
    toy = board()
    check_toy.resistor(toy, "R20", "1k", "EN_U2", "A")
    check_toy.resistor(toy, "R21", "1k", "A", "B")
    check_toy.resistor(toy, "R22", "1k", "B", "EN_ALL")
    report = check(toy)
    assert any(
        p.endswith(
            "(if any of A, B is a supply rail, declare it in specs.SUPPLY_NETS; if "
            "they are signals, this path is real)"
        )
        for p in report.problems
    ), report.problems
    assert not any("if EN_" in p for p in check(board()).problems)


def test_ground_net_counts_as_a_supply_without_being_listed() -> None:
    """Take specs.GROUND_NET as ground, so pull-downs to it pass."""
    toy = board()
    check_toy.resistor(toy, "R20", "100k", "EN_U2", "GND")
    check_toy.resistor(toy, "R21", "100k", "EN_U3", "GND")
    assert check(toy, supplies=["VIN"]).problems != []
    nl = netlist(toy)
    report = links.check_enable_net(nl, "EN_ALL", {"EN_ALL": ROWS}, ["VIN"], "GND")
    assert report.problems == []


def test_a_supply_net_that_is_not_in_the_design_fails_by_name() -> None:
    """Name the net, and say so when it is a leading-slash spelling."""
    report = check(board(), supplies=["GND", "VIN", "VDD_TYPO", "/GND"])
    assert report.problems == [
        "specs.SUPPLY_NETS: VDD_TYPO is not in the design",
        "specs.SUPPLY_NETS: /GND is not in the design (net names carry no leading "
        "slash: write GND)",
    ]


def test_an_enable_on_a_supply_net_fails() -> None:
    """Fail a regulator whose enable pad is on a declared supply."""
    toy = board()
    toy.move_pin("U2", "3", "VIN")
    report = check(toy)
    assert report.problems == [
        "U2 pad 3 is on VIN, a net in specs.SUPPLY_NETS: no link can switch it"
    ]


def test_a_regulator_whose_own_enable_is_named_like_a_rail_still_fails_a_bypass() -> (
    None
):
    """Walk from VIN_EN2 and find EN_U2 - R9 - EN_ALL2 - R10 - EN_ALL."""
    toy = board()
    toy.move_pin("U2", "3", "VIN_EN2")
    replace(toy, "R7")
    check_toy.resistor(toy, "R7", "0", "VIN_EN2", "EN_ALL")
    check_toy.resistor(toy, "R9", "0", "VIN_EN2", "EN_ALL2")
    check_toy.resistor(toy, "R10", "0", "EN_ALL2", "EN_ALL")
    assert any(
        "VIN_EN2 - R9 - EN_ALL2 - R10 - EN_ALL" in p for p in check(toy).problems
    )


def test_a_shared_net_named_like_a_rail_is_still_a_target() -> None:
    """Fail a 10k in parallel with the link to +EN."""
    toy = Toy()
    regulator(toy, "U2", "EN_U2")
    regulator(toy, "U3", "EN_U3")
    check_toy.resistor(toy, "R7", "0", "EN_U2", "+EN")
    check_toy.resistor(toy, "R8", "0", "EN_U3", "+EN")
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "+EN")
    report = check(toy, {"+EN": ROWS}, net="+EN")
    assert any("(EN_U2 - R9 - +EN)" in p for p in report.problems), report.problems


# S2: the link may sit anywhere on the path
def test_a_link_then_a_series_resistor_and_a_delay_capacitor_passes() -> None:
    """Pass EN_ALL - R7 (0) - EN_U2D - R9 (10k) - EN_U2 with C9 to ground."""
    toy = board()
    replace(toy, "R7")
    check_toy.resistor(toy, "R7", "0", "EN_ALL", "EN_U2D")
    check_toy.resistor(toy, "R9", "10k", "EN_U2D", "EN_U2")
    with_part(toy, "C9", "1u", "EN_U2", "GND", "Device:C")
    report = check(toy)
    assert report.problems == []
    assert report.links == {"U2": "R7", "U3": "R8"}


def test_a_series_resistor_then_the_link_passes() -> None:
    """Pass EN_U2 - R9 (10k) - EN_U2D - R7 (0) - EN_ALL."""
    toy = board()
    replace(toy, "R7")
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_U2D")
    check_toy.resistor(toy, "R7", "0", "EN_U2D", "EN_ALL")
    report = check(toy)
    assert report.problems == []
    assert report.links["U2"] == "R7"


def test_a_resistor_then_a_link_with_a_bypass_around_both_fails() -> None:
    """Fail when a 10k joins EN_U2 to EN_ALL around the series pair."""
    toy = board()
    replace(toy, "R7")
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_U2D")
    check_toy.resistor(toy, "R7", "0", "EN_U2D", "EN_ALL")
    check_toy.resistor(toy, "R10", "10k", "EN_U2", "EN_ALL")
    report = check(toy)
    assert report.problems != []
    assert report.problems[0].startswith("U2 pad 3: ")


def test_two_links_in_series_are_not_one_link() -> None:
    """Fail a regulator with two removable links in a row, naming both."""
    toy = board()
    replace(toy, "R7")
    check_toy.resistor(toy, "R7", "0", "EN_U2", "EN_U2D")
    check_toy.resistor(toy, "R9", "0", "EN_U2D", "EN_ALL")
    report = check(toy)
    assert report.problems == [
        "U2 pad 3: R7 and R9 each isolate it when removed: a regulator needs "
        "exactly one link"
    ]
    assert report.links["U2"] is None


# S3: resistor packs by their real pinout
def sip(toy: Toy, kind: str, pads: dict[str, str]) -> None:
    """Add RN1 of ``kind`` with every other of its eight pads on its own net."""
    full = {str(n): f"A{n}" for n in range(1, 9)}
    full.update(pads)
    add_part(toy, "RN1", kind, full, "4x10k")


def test_a_sip_pack_element_across_the_link_is_a_bypass() -> None:
    """Pair pads 1 and 2 of an R_Pack04_SIP, as KiCad's symbol does."""
    toy = board()
    sip(toy, "Device:R_Pack04_SIP", {"1": "EN_U2", "2": "EN_ALL"})
    assert any("(EN_U2 - RN1 - EN_ALL)" in p for p in check(toy).problems)


def test_sip_pads_one_and_eight_are_not_one_element() -> None:
    """Pass a SIP whose pads 1 and 8 are on EN_U2 and EN_ALL: they are not joined."""
    toy = board()
    sip(toy, "Device:R_Pack04_SIP", {"1": "EN_U2", "8": "EN_ALL"})
    assert check(toy).problems == []


@pytest.mark.parametrize("kind", ["Device:R_Pack04", "Device:R_Pack04_Split"])
def test_a_pack_pairs_pad_n_with_pad_n_plus_one_minus_n(kind: str) -> None:
    """Fail pads 1 and 8 across the link; pass pads 1 and 2."""
    toy = board()
    sip(toy, kind, {"1": "EN_U2", "8": "EN_ALL"})
    assert any("(EN_U2 - RN1 - EN_ALL)" in p for p in check(toy).problems)
    toy = board()
    sip(toy, kind, {"1": "EN_U2", "2": "EN_ALL"})
    assert check(toy).problems == []


def test_a_pack_with_pads_left_off_every_net_is_paired_by_its_name() -> None:
    """Pair R_Pack04 by its four elements, not by the highest pad on a net."""
    toy = board()
    add_part(toy, "RN1", "Device:R_Pack04", {"1": "EN_U2", "2": "EN_U3"}, "4x10k")
    assert check(toy).problems == []


@pytest.mark.parametrize(
    "kind",
    ["Device:R_Network_Dividers_x02_SIP", "Device:R_Pack04_Odd", "Device:R_Array"],
)
def test_a_pack_whose_pinout_is_not_known_joins_all_its_pads(kind: str) -> None:
    """Join every pad of a pack the check does not know, and name it in the path."""
    toy = board()
    add_part(toy, "RN1", kind, {"1": "X1", "2": "EN_U2", "3": "EN_U3", "4": "X4"})
    report = check(toy)
    assert any("(EN_U2 - RN1 - EN_U3)" in p for p in report.problems), report.problems


def test_a_pack_element_is_not_a_link_even_at_zero_ohms() -> None:
    """Refuse a 0 ohm pack as a link."""
    toy = board()
    replace(toy, "R7")
    add_part(toy, "RN1", "Device:R_Pack04", {"1": "EN_U2", "8": "EN_ALL"}, "0")
    report = check(toy)
    assert report.problems == [
        "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL "
        "(RN1 (0) joins them but is not a link: it is a resistor pack, network or "
        "multi-pad resistor)"
    ]


# S4: a straight tie to a supply on the regulator's side of the link
@pytest.mark.parametrize(
    ("value", "kind", "supply"),
    [
        ("0", "Device:R", "VIN"),
        ("0R", "Device:R_Small", "GND"),
        ("Closed", "Jumper:SolderJumper_2_Bridged", "VIN"),
    ],
)
def test_a_zero_ohm_or_jumper_tie_to_a_supply_fails(
    value: str, kind: str, supply: str
) -> None:
    """Fail, naming the tie, the net and the link it makes useless."""
    toy = board()
    with_part(toy, "R9", value, "EN_U2", supply, kind)
    report = check(toy)
    assert report.problems == [
        f"U2 pad 3: R9 ({value}) ties EN_U2 to {supply}, a net in "
        "specs.SUPPLY_NETS, so the link R7 controls nothing"
    ]
    assert report.links["U2"] == "R7"


def test_a_tie_from_a_net_on_the_regulators_side_of_the_link_fails() -> None:
    """Fail a 0 ohm from EN_U2D, between the link and a series resistor, to GND."""
    toy = board()
    replace(toy, "R7")
    check_toy.resistor(toy, "R7", "0", "EN_ALL", "EN_U2D")
    check_toy.resistor(toy, "R9", "10k", "EN_U2D", "EN_U2")
    check_toy.resistor(toy, "R10", "0", "EN_U2D", "GND")
    assert check(toy).problems == [
        "U2 pad 3: R10 (0) ties EN_U2D to GND, a net in specs.SUPPLY_NETS, so the "
        "link R7 controls nothing"
    ]


@pytest.mark.parametrize("value", ["100k", "DNP", "10k DNP"])
def test_a_pull_or_an_unfitted_tie_to_a_supply_passes(value: str) -> None:
    """Pass a 100k pull-up and a not-fitted strap to VIN."""
    toy = board()
    check_toy.resistor(toy, "R9", value, "EN_U2", "VIN")
    assert check(toy).problems == []


def test_a_tie_on_the_shared_side_of_the_link_is_not_this_regulators() -> None:
    """Leave a 0 ohm from the shared net to VIN alone: it is not past a link."""
    toy = board()
    check_toy.resistor(toy, "R9", "0", "EN_ALL", "VIN")
    assert check(toy).problems == []


# S5: three-pad jumpers
def test_a_three_pad_jumper_as_the_link_says_a_link_has_two_pads() -> None:
    """Fail JP1 (EN_U2 / EN_ALL / GND) and say why it is not a link."""
    toy = board()
    replace(toy, "R7")
    toy.add(
        "JP1",
        "J",
        "Jumper:SolderJumper_3_Bridged12",
        {"1": ("EN_U2", "A"), "2": ("EN_ALL", "C"), "3": ("GND", "B")},
    )
    assert check(toy).problems == [
        "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL "
        "(EN_U2 - JP1 - EN_ALL joins them, with no link on it; JP1 has three pads: "
        "a link must have two)"
    ]


def test_a_jumper_part_with_three_pads_conducts_between_all_of_them() -> None:
    """Walk through a 3-pad solder jumper from EN_U2 to the shared net."""
    toy = board()
    toy.add(
        "JP9",
        "x",
        "Jumper:SolderJumper_3_Open",
        {"1": ("EN_U2", "A"), "2": ("X", "C"), "3": ("EN_ALL", "B")},
    )
    report = check(toy)
    assert any("(EN_U2 - JP9 - EN_ALL)" in p for p in report.problems)


# L1: diodes
def diode(toy: Toy, ref: str, anode: str, cathode: str) -> None:
    """Add a Device:D with its pins named A and K, as KiCad names them."""
    toy.add(ref, "D", "Device:D", {"1": (cathode, "K"), "2": (anode, "A")})


def test_a_diode_or_of_two_enables_does_not_join_them() -> None:
    """Pass two diodes from EN_U2 and EN_U3 into one net: neither drives the other."""
    toy = board()
    diode(toy, "D9", "EN_U2", "ANY_ON")
    diode(toy, "D10", "EN_U3", "ANY_ON")
    assert check(toy).problems == []


def test_a_diode_from_one_enable_to_another_fails_the_one_that_drives() -> None:
    """Fail U2 when D9 runs from EN_U2 to EN_U3: enabling U2 enables U3."""
    toy = board()
    diode(toy, "D9", "EN_U2", "EN_U3")
    report = check(toy)
    assert any(
        p.startswith("U2 pad 3: removing R7") and "(EN_U2 - D9 - EN_U3)" in p
        for p in report.problems
    ), report.problems


@pytest.mark.parametrize(
    ("anode", "cathode"), [("EN_ALL", "EN_U2"), ("EN_U2", "EN_ALL")]
)
def test_a_diode_beside_the_link_fails_whichever_way_it_points(
    anode: str, cathode: str
) -> None:
    """Fail a diode in parallel with R7: it carries one way or the other."""
    toy = board()
    diode(toy, "D9", anode, cathode)
    assert any("(EN_U2 - D9 - EN_ALL)" in p for p in check(toy).problems)


def test_a_diode_whose_pins_are_not_named_a_and_k_is_walked_both_ways() -> None:
    """Fail a two-pin diode from a custom library whose pins are 1 and 2."""
    toy = board()
    with_part(toy, "D9", "D", "EN_U2", "ANY_ON", "Device:D")
    with_part(toy, "D10", "D", "EN_U3", "ANY_ON", "Device:D")
    assert check(toy).problems != []


# --------------------------------------------------------------- the walk itself
def add_pull(toy: Toy) -> None:
    """Pull each enable net down and up: R20/R21 to GND, R22/R23 to rails."""
    check_toy.resistor(toy, "R20", "100k", "EN_U2", "GND")
    check_toy.resistor(toy, "R21", "100k", "EN_U3", "GND")
    check_toy.resistor(toy, "R22", "100k", "EN_U2", "VIN")
    check_toy.resistor(toy, "R23", "100k", "EN_U3", "+5V")
    check_toy.resistor(toy, "R24", "100k", "EN_ALL", "+3V3")


def test_pull_downs_and_pull_ups_on_the_enable_nets_still_pass() -> None:
    """Do not walk through a declared supply: two pulls to one rail are fine."""
    toy = board()
    add_pull(toy)
    assert check(toy, supplies=["GND", "VIN", "+5V", "+3V3"]).problems == []


def test_a_capacitor_between_the_enable_nets_does_not_connect_them() -> None:
    """Let a capacitor (an open at DC) sit between two enables."""
    toy = board()
    for n, kind in enumerate(["Device:C", "Device:C_Small", "Device:CP_Small"]):
        with_part(toy, f"C{n + 5}", "100n", "EN_U2", "EN_U3", kind)
    assert check(toy).problems == []


@pytest.mark.parametrize(
    "kind",
    [
        "Device:L",
        "Device:FerriteBead",
        "Device:Fuse",
        "Switch:SW",
        "Custom:Weird2",
    ],
)
def test_any_other_two_pin_part_but_a_capacitor_conducts_for_the_walk(
    kind: str,
) -> None:
    """Fail an inductor, bead, fuse, switch or header bridging the enables."""
    toy = board()
    with_part(toy, "X5", "x", "EN_U2", "EN_U3", kind)
    report = check(toy)
    assert any("EN_U2 - X5 - EN_U3" in p for p in report.problems), report.problems


def test_a_resistor_pack_element_between_the_enables_is_a_bypass() -> None:
    """Pair pad 1 with pad 8 and 2 with 7 in an R_Pack04, as KiCad draws it."""
    toy = board()
    add_part(
        toy,
        "RN1",
        "Device:R_Pack04",
        {"1": "EN_U2", "8": "EN_U3", "2": "GND", "7": "GND"},
        "10k",
    )
    assert any("EN_U2 - RN1 - EN_U3" in p for p in check(toy).problems)


def test_pack_elements_on_other_pairs_do_not_connect_the_enables() -> None:
    """Pull each enable down with its own element of a pack: no bypass."""
    toy = board()
    add_part(
        toy,
        "RN1",
        "Device:R_Pack04",
        {"1": "EN_U2", "8": "GND", "2": "EN_U3", "7": "GND"},
        "10k",
    )
    assert check(toy).problems == []


def test_a_network_pack_joins_every_pad_to_its_common_pad() -> None:
    """Take pad 1 of an R_Network as the common: two enables on it are bridged."""
    toy = board()
    add_part(
        toy,
        "RN1",
        "Device:R_Network04",
        {"1": "GND", "2": "EN_U2", "3": "EN_U3"},
        "10k",
    )
    assert check(toy).problems == []
    toy.move_pin("RN1", "1", "COMMON")
    report = check(toy)
    assert any("EN_U2 - RN1 - COMMON - RN1 - EN_U3" in p for p in report.problems)


def test_a_path_through_an_active_part_is_not_walked() -> None:
    """Leave a transistor between EN_U2 and EN_ALL alone: a documented limit."""
    toy = board()
    toy.add(
        "Q1",
        "Q",
        "Device:Q_NMOS_GSD",
        {"1": ("G1", "G"), "2": ("EN_U2", "S"), "3": ("EN_U3", "D")},
    )
    assert check(toy).problems == []


def test_a_bypass_beside_a_regulator_with_no_link_is_not_walked() -> None:
    """Report only that there is no link when there is nothing to remove."""
    toy = board()
    toy.set_value("R7", "10k")
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_U3")
    report = check(toy)
    assert not any("removing" in p and p.startswith("U2") for p in report.problems)


# ------------------------------------------------------------ links that are right
@pytest.mark.parametrize("kind", ["Device:R_Small", "Device:R_US", "Device:R_Small_US"])
def test_a_zero_ohm_small_or_us_resistor_is_a_link(kind: str) -> None:
    """Accept any two-pin Device:R symbol, not only Device:R."""
    toy = board()
    replace(toy, "R7")
    with_part(toy, "R7", "0", "EN_U2", "EN_ALL", kind)
    report = check(toy)
    assert report.problems == []
    assert report.links["U2"] == "R7"


@pytest.mark.parametrize(
    "kind", ["Device:R_Pack04", "Device:R_Network04", "Device:R_Potentiometer"]
)
def test_a_pack_or_potentiometer_is_never_a_link(kind: str) -> None:
    """Refuse the multi-pin resistor symbols as a link."""
    toy = board()
    toy.parts["R7"] = ("0", "Device", kind.partition(":")[2], {})
    assert not links.is_link(netlist(toy), "R7")


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "0R",
        "0R0",
        "0E",
        "R0",
        "0 ohm",
        "0Ω",
        "0.0",
        "0E0",
        "0r",
        "DNP",
        "dnp",
        "DNF",
        "NF",
        "np",
        "NC",
    ],
)
def test_the_ways_to_write_a_link_are_all_links(value: str) -> None:
    """Read every spelling of zero, and a not-fitted link, as a link."""
    toy = board()
    toy.set_value("R7", value)
    assert links.is_link(netlist(toy), "R7")


@pytest.mark.parametrize(
    "value", ["4R7", "0R5", "R5", "E", "R", "", "  ", "10k DNP", "NCP", "10k NF"]
)
def test_other_values_are_not_links_and_none_raises(value: str) -> None:
    """Refuse a value that is not zero, empty and blank text included."""
    toy = board()
    toy.set_value("R7", value)
    assert not links.is_link(netlist(toy), "R7")


def test_a_dnp_link_passes_the_check() -> None:
    """Count a link marked DNP as a link: the footprint is there to fit."""
    toy = board()
    toy.set_value("R7", "DNP")
    report = check(toy)
    assert report.problems == []
    assert report.links["U2"] == "R7"


def test_a_resistor_with_a_value_and_a_dnp_mark_is_walked_as_a_path() -> None:
    """Fail a 10k DNP bridge: fitted at some builds, it joins the enables."""
    toy = board()
    check_toy.resistor(toy, "R9", "10k DNP", "EN_U2", "EN_U3")
    assert any("(EN_U2 - R9 - EN_U3)" in p for p in check(toy).problems)


def test_the_same_pad_listed_twice_is_a_duplicate_row() -> None:
    """Say the row is a duplicate and not that U2 shares a link with itself."""
    report = check(board(), {"EN_ALL": [("U2", "3"), ("U2", "3"), ("U3", "3")]})
    assert report.problems == ["U2 pad 3 is listed twice"]
    assert report.links == {"U2": "R7", "U3": "R8"}


# ------------------------------------------- third review: walk everything by default
def multi(
    toy: Toy, ref: str, kind: str, pads: dict[str, object], value: str = "x"
) -> None:
    """Add ``ref`` with ``{pad: net or (net, pin name)}``."""
    toy.add(
        ref,
        value,
        kind,
        {
            pad: (net, "~") if isinstance(net, str) else net  # type: ignore[misc]
            for pad, net in pads.items()
        },
    )


@pytest.mark.parametrize(
    ("ref", "kind", "pads"),
    [
        (
            "RN1",
            "Custom_Resistors:CAY16",
            {"1": "EN_U2", "8": "EN_U3", "2": "a", "7": "b", "3": "c", "6": "d"},
        ),
        (
            "SW1",
            "Switch:SW_DIP_x02",
            {"1": "EN_U2", "2": "S3", "3": "S4", "4": "EN_U3"},
        ),
        ("SW1", "Switch:SW_SPDT", {"1": "EN_U2", "2": "EN_U3", "3": "GND"}),
        (
            "L9",
            "Device:L_Coupled_1423",
            {"1": "EN_U2", "4": "EN_U3", "2": "X2", "3": "X3"},
        ),
        ("C9", "Device:C_Feedthrough", {"1": "EN_U2", "2": "EN_U3", "3": "GND"}),
        ("R9", "Device:R_Shunt", {"1": "EN_U2", "2": "EN_U3", "3": "S3", "4": "S4"}),
        ("X9", "Mystery:Part3", {"1": "EN_U2", "2": "M", "3": "EN_U3"}),
    ],
)
def test_a_passive_part_with_three_or_more_pads_is_walked_through_every_pad(
    ref: str, kind: str, pads: dict[str, object]
) -> None:
    """Fail a bridge through any part of any library that is not on the closed list."""
    toy = board()
    multi(toy, ref, kind, pads)
    report = check(toy)
    assert any(f"(EN_U2 - {ref} - EN_U3)" in p for p in report.problems), (
        report.problems
    )


@pytest.mark.parametrize(
    ("ref", "kind"),
    [
        ("U9", "Logic:74LVC1G08"),
        ("IC4", "Custom:Sequencer"),
        ("Q1", "Device:Q_NMOS_GSD"),
        ("J5", "Connector_Generic:Conn_02x02_Odd_Even"),
        ("P3", "Connector:Conn_01x02"),
        ("TP1", "Custom:Probe"),
        ("X2", "Connector:TestPoint_2Pole"),
        ("C9", "Custom:Cap"),
    ],
)
def test_the_parts_on_the_closed_list_are_not_walked(ref: str, kind: str) -> None:
    """Leave ICs, transistors, connectors, test points and two-pad capacitors alone."""
    toy = board()
    pads: dict[str, object] = {"1": "EN_U2", "2": "EN_U3"}
    if not ref.startswith("C"):
        pads["3"] = "GND"
    multi(toy, ref, kind, pads)
    assert check(toy).problems == []


def bat54c(toy: Toy, a1: str, a2: str, k: str) -> None:
    """Add a common-cathode dual diode, its pins named A1, A2 and K as KiCad does."""
    toy.add(
        "D9",
        "BAT54C",
        "Diode:BAT54C",
        {"1": (a1, "A1"), "2": (a2, "A2"), "3": (k, "K")},
    )


def test_a_dual_diode_from_another_enable_into_this_one_fails() -> None:
    """Fail when EN_U3 drives EN_U2 through one half of a BAT54C."""
    toy = board()
    bat54c(toy, "PG_U1", "EN_U3", "EN_U2")
    report = check(toy)
    assert any("(EN_U2 - D9 - EN_U3)" in p for p in report.problems), report.problems


def test_a_dual_diode_or_after_the_link_passes() -> None:
    """Pass EN_ALL - R7 - X, with X and PG_U1 ORed into EN_U2 by a BAT54C."""
    toy = board()
    replace(toy, "R7")
    check_toy.resistor(toy, "R7", "0", "EN_ALL", "X")
    bat54c(toy, "X", "PG_U1", "EN_U2")
    report = check(toy)
    assert report.problems == []
    assert report.links["U2"] == "R7"


def test_a_series_dual_diode_beside_the_link_fails() -> None:
    """Fail a Device dual diode from EN_ALL to EN_U2, pins keyed by pad, not name."""
    toy = board()
    toy.add(
        "D9",
        "D",
        "Device:D_Dual_Series_AKC",
        {"1": ("EN_ALL", "A"), "2": ("X", "K"), "3": ("EN_U2", "K")},
    )
    assert check(toy).problems != []


def test_diode_pins_are_keyed_by_pad_so_repeated_names_do_not_collapse() -> None:
    """Give each pad named A an arc to each pad named K."""
    toy = Toy()
    toy.add(
        "D9",
        "D",
        "Device:D_Dual",
        {"1": ("N1", "A"), "2": ("N2", "K"), "3": ("N3", "A"), "4": ("N4", "K")},
    )
    found = sorted(links.arcs(netlist(toy), "D9"))
    assert found == [("1", "2"), ("1", "4"), ("3", "2"), ("3", "4")]


# should-fix 2: open jumpers are not fitted for the tie rule
@pytest.mark.parametrize("value", ["Open", "DNP"])
def test_an_open_jumper_strap_to_a_supply_is_not_a_tie(value: str) -> None:
    """Pass a SolderJumper_2_Open from EN_U2 to GND: it is not fitted."""
    toy = board()
    toy.add(
        "JP3",
        value,
        "Jumper:SolderJumper_2_Open",
        {"1": ("EN_U2", "A"), "2": ("GND", "B")},
    )
    assert check(toy).problems == []


# should-fix 3: a link is a zero-ohm R from any library
@pytest.mark.parametrize("kind", ["MyLib:R_0402", "easyeda2kicad:0402WGF0000TCE"])
def test_a_zero_ohm_resistor_from_any_library_is_a_link(kind: str) -> None:
    """Take a two-pad part with reference R and value 0 from a custom library."""
    toy = board()
    replace(toy, "R7")
    with_part(toy, "R7", "0Ω", "EN_U2", "EN_ALL", kind)
    report = check(toy)
    assert report.problems == []
    assert report.links["U2"] == "R7"


def test_a_zero_ohm_part_that_is_not_a_resistor_says_why_it_is_not_a_link() -> None:
    """Name the reference rule when a 0 ohm ferrite FB1 joins the nets."""
    toy = board()
    replace(toy, "R7")
    with_part(toy, "FB1", "0", "EN_U2", "EN_ALL", "Device:FerriteBead")
    assert check(toy).problems == [
        "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL (FB1 (0) joins "
        "them but is not a link: a link is a resistor (reference R) or a two-pad "
        "jumper)"
    ]


# should-fix 4: tie gaps
def test_a_bridged_three_pad_jumper_strapping_the_enable_to_a_supply_is_a_tie() -> None:
    """Fail JP2 (Bridged12) with pad 1 on VIN and pad 2 on EN_U2."""
    toy = board()
    toy.add(
        "JP2",
        "J",
        "Jumper:SolderJumper_3_Bridged12",
        {"1": ("VIN", "A"), "2": ("EN_U2", "C"), "3": ("GND", "B")},
    )
    assert check(toy).problems == [
        "U2 pad 3: JP2 (J) ties EN_U2 to VIN, a net in specs.SUPPLY_NETS, so the "
        "link R7 controls nothing"
    ]


def test_a_three_pad_jumper_whose_bridged_pads_miss_the_supply_is_not_a_tie() -> None:
    """Pass Bridged12 with pad 3 on GND: pads 2 and 3 are not bridged."""
    toy = board()
    toy.add(
        "JP2",
        "J",
        "Jumper:SolderJumper_3_Bridged12",
        {"1": ("X", "A"), "2": ("EN_U2", "C"), "3": ("GND", "B")},
    )
    assert check(toy).problems == []


def test_a_zero_ohm_pack_element_tying_the_enable_to_a_supply_is_a_tie() -> None:
    """Fail RN2 (R_Pack02, 0) with pad 1 on EN_U2 and pad 4 on VIN."""
    toy = board()
    add_part(
        toy,
        "RN2",
        "Device:R_Pack02",
        {"1": "EN_U2", "4": "VIN", "2": "q", "3": "r"},
        "0",
    )
    assert check(toy).problems == [
        "U2 pad 3: RN2 (0) ties EN_U2 to VIN, a net in specs.SUPPLY_NETS, so the "
        "link R7 controls nothing"
    ]


# Low: a 0 ohm option in series with a pull-up is a pull-up, not a tie
def test_a_pull_up_with_a_zero_ohm_option_in_series_passes() -> None:
    """Pass EN_U2 - R30 (100k) - PU - R31 (0) - VIN."""
    toy = board()
    check_toy.resistor(toy, "R30", "100k", "EN_U2", "PU")
    check_toy.resistor(toy, "R31", "0", "PU", "VIN")
    assert check(toy).problems == []


def test_a_tie_through_two_zero_ohm_parts_off_the_enable_still_fails() -> None:
    """Fail EN_U2 - R30 (0) - X - R31 (0) - GND: the enable is held at ground."""
    toy = board()
    check_toy.resistor(toy, "R30", "0", "EN_U2", "X")
    check_toy.resistor(toy, "R31", "0", "X", "GND")
    assert check(toy).problems == [
        "U2 pad 3: R31 (0) ties X to GND, a net in specs.SUPPLY_NETS, so the link R7 "
        "controls nothing"
    ]


# Low: a three-pad jumper with a pad left off the netlist is still three pads
def test_a_three_pad_jumper_with_a_pad_off_the_netlist_is_not_a_link() -> None:
    """Read the pad count from the symbol name, not from the pads on a net."""
    toy = board()
    toy.add(
        "JP1",
        "J",
        "Jumper:SolderJumper_3_Open",
        {"1": ("EN_U2", "A"), "2": ("EN_ALL", "C")},
    )
    assert not links.is_link(netlist(toy), "JP1")


# should-fix 5: the search is bounded
def grid(toy: Toy, size: int, value: str) -> None:
    """Add a ``size`` by ``size`` mesh of resistors of ``value`` on nets N<i>_<j>."""
    for i in range(size):
        for j in range(size):
            if i + 1 < size:
                check_toy.resistor(
                    toy, f"RG{i}_{j}a", value, f"N{i}_{j}", f"N{i + 1}_{j}"
                )
            if j + 1 < size:
                check_toy.resistor(
                    toy, f"RG{i}_{j}b", value, f"N{i}_{j}", f"N{i}_{j + 1}"
                )


def test_a_dense_zero_ohm_mesh_with_ten_regulators_is_searched_in_bounded_steps() -> (
    None
):
    """Count steps, not seconds: each regulator costs a few walks of the mesh."""
    toy = Toy()
    grid(toy, 30, "0")
    rows = []
    for r in range(10):
        regulator(toy, f"U{r}", f"EN_U{r}")
        check_toy.resistor(toy, f"R{900 + r}", "0", f"EN_U{r}", "EN_ALL")
        check_toy.resistor(toy, f"R{950 + r}", "10k", f"EN_U{r}", f"N{r}_{r}")
        rows.append((f"U{r}", "3"))
    nl = netlist(toy)
    report = links.check_enable_net(nl, "EN_ALL", {"EN_ALL": rows}, SUPPLIES)
    assert len(report.problems) == 10
    edges = sum(len(nl.functions(r)) for r in nl.parts)
    assert report.steps < 10 * 2 * edges, report.steps


def test_a_big_board_with_an_undeclared_rail_is_searched_in_bounded_steps() -> None:
    """Search 3,000 pull-ups to an undeclared 3V3 and 40 regulators in bounded steps."""
    toy = Toy()
    for i in range(3000):
        check_toy.resistor(toy, f"RS{i}", "10k", f"S{i}", "3V3")
    rows = []
    for r in range(40):
        regulator(toy, f"U{r}", f"EN_U{r}")
        check_toy.resistor(toy, f"R{900 + r}", "0", f"EN_U{r}", "EN_ALL")
        check_toy.resistor(toy, f"R{980 + r}", "100k", f"EN_U{r}", "3V3")
        rows.append((f"U{r}", "3"))
    nl = netlist(toy)
    report = links.check_enable_net(nl, "EN_ALL", {"EN_ALL": rows}, SUPPLIES)
    assert len(report.problems) == 40
    edges = sum(len(nl.functions(r)) for r in nl.parts)
    assert report.steps < 40 * 2 * edges, report.steps
    declared = links.check_enable_net(
        nl, "EN_ALL", {"EN_ALL": rows}, [*SUPPLIES, "3V3"]
    )
    assert declared.problems == []


def test_a_zero_ohm_pack_element_as_the_only_link_says_a_link_has_two_pads() -> None:
    """Say why RN1 is not a link when an element of it is the only way through."""
    toy = board()
    replace(toy, "R7")
    sip(toy, "Device:R_Pack04", {"1": "EN_U2", "8": "EN_ALL"})
    toy.set_value("RN1", "4x0")
    assert check(toy).problems == [
        "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL (EN_U2 - RN1 - "
        "EN_ALL joins them, with no link on it; RN1 has eight pads: a link must have "
        "two)"
    ]
