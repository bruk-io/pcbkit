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


def test_a_regulator_behind_its_own_zero_ohm_link_passes_and_names_the_link() -> None:
    """Find R7 for U2 and R8 for U3, whichever way round the link is wired."""
    report = links.check_enable_net(netlist(board()), "EN_ALL", ROWS)
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


def test_a_part_from_a_jumper_library_is_a_link() -> None:
    """Take a solder jumper whatever its value, and nothing else but a 0 ohm R."""
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
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert report.problems == [
        "U2 pad 3 is wired straight to EN_ALL: no link of its own",
        "U3 pad 3 is wired straight to EN_ALL: no link of its own",
    ]
    assert report.links == {"U2": None, "U3": None}


def test_a_link_replaced_by_a_resistor_fails_naming_the_part_and_its_value() -> None:
    """Say R7 (10k) joins the nets but is not a link."""
    toy = board()
    toy.set_value("R7", "10k")
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert report.problems == [
        "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL "
        "(R7 (10k) joins them but is not a link)"
    ]
    assert report.links == {"U2": None, "U3": "R8"}


def test_nothing_between_the_nets_fails_without_naming_a_part() -> None:
    """Say no link joins them when no part does."""
    toy = board()
    toy.move_pin("R8", "1", "SOMEWHERE")
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert report.problems == [
        "U3 pad 3: no 0 ohm resistor or jumper joins EN_U3 to EN_ALL"
    ]


def test_two_regulators_behind_one_link_share_it_and_fail() -> None:
    """Name both regulators and the link they share."""
    toy = board()
    toy.move_pin("U3", "3", "EN_U2")
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert report.problems == [
        "U2 and U3 share the link R7: each regulator needs its own"
    ]
    assert report.links == {"U2": "R7", "U3": "R7"}


def test_two_links_in_parallel_do_not_isolate_the_regulator() -> None:
    """Fail naming both links."""
    toy = board()
    check_toy.resistor(toy, "R9", "0", "EN_U2", "EN_ALL")
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert report.problems == [
        "U2 pad 3: 2 links (R7, R9) join EN_U2 to EN_ALL, so removing one does not "
        "isolate it"
    ]


def test_a_part_that_is_not_in_the_design_a_pad_on_no_net_and_a_missing_net() -> None:
    """Name each of the three, and stop short of a second complaint about one row."""
    toy = board()
    toy.move_pin("U3", "3", None)
    report = links.check_enable_net(
        netlist(toy), "EN_ALL", [("U2", "3"), ("U3", "3"), ("U9", "3")]
    )
    assert report.problems == ["U3 pad 3 is on no net", "U9 is not in the design"]
    report = links.check_enable_net(netlist(board()), "NO_NET", ROWS)
    assert report.problems[0] == "net NO_NET is not in the design"


def test_a_regulator_listed_on_two_pads_is_keyed_by_its_pad() -> None:
    """Key the two rows of U2 apart, so neither hides the other."""
    toy = board()
    toy.pins["U2"]["4"] = ("EN_U2", "EN2")
    report = links.check_enable_net(netlist(toy), "EN_ALL", [("U2", "3"), ("U2", "4")])
    assert list(report.links) == ["U2 pad 3", "U2 pad 4"]
    assert "U2 and U2 share the link R7" in report.problems[0]


def test_the_enable_links_shape_is_this_modules_and_reads_ints_as_pad_text() -> None:
    """Register the table here, not in PROPOSED, and normalise pads to text."""
    assert "ENABLE_LINKS" not in tables.PROPOSED
    shape = links.TABLES["ENABLE_LINKS"]
    assert tables.registry()["ENABLE_LINKS"] is shape
    out, errors = tables.convert("ENABLE_LINKS", {"EN": [["U3", 3]]}, shape)
    assert errors == []
    assert out == {"EN": [("U3", "3")]}
    found = tables.validate("ENABLE_LINKS", {"EN": [("U3",)]}, shape)
    assert found == [
        'specs.ENABLE_LINKS["EN"][0]: expected (regulator ref, enable pad), '
        "got ('U3',)"
    ]


def with_part(toy: Toy, ref: str, value: str, a: str, b: str, kind: str) -> Toy:
    """Add a two-pin part of ``kind`` ("Device:R_Small") between two nets."""
    toy.add(ref, value, kind, {"1": (a, "~"), "2": (b, "~")})
    return toy


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
        "net EN_ALL (EN_U2 - R9 - MID - R10 - EN_ALL)",
    ),
]


@pytest.mark.parametrize(("plant", "message"), BYPASSES)
def test_a_second_path_around_the_link_fails_naming_the_path(
    plant: Callable[[Toy], None], message: str
) -> None:
    """Fail each of the four bypasses, though a link is found for each regulator."""
    toy = board()
    plant(toy)
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert message in report.problems
    assert report.links["U2"] == "R7"


def test_every_regulator_with_a_bypass_is_named() -> None:
    """Name U3 as well as U2 when a bridge joins their enables."""
    toy = board()
    bypass_enables_with_zero_ohms(toy)
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert [p.split(":")[0] for p in report.problems] == ["U2 pad 3", "U3 pad 3"]


def add_pull(toy: Toy) -> None:
    """Pull each enable net down and up: R20/R21 to GND, R22/R23 to rails."""
    check_toy.resistor(toy, "R20", "100k", "EN_U2", "GND")
    check_toy.resistor(toy, "R21", "100k", "EN_U3", "GND")
    check_toy.resistor(toy, "R22", "100k", "EN_U2", "VIN")
    check_toy.resistor(toy, "R23", "100k", "EN_U3", "+5V")
    check_toy.resistor(toy, "R24", "100k", "EN_ALL", "+3V3")


def test_pull_downs_and_pull_ups_on_the_enable_nets_still_pass() -> None:
    """Do not walk through ground or a supply net: two pulls to one rail are fine."""
    toy = board()
    add_pull(toy)
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert report.problems == []


def test_the_walk_does_not_cross_a_ground_pin_net_whatever_it_is_called() -> None:
    """Treat a net a ground pin is on as ground, as a pull-down's net is."""
    toy = board()
    toy.add("U9", "x", "Regulator:LDO", {"1": ("RETURN", "GND")})
    check_toy.resistor(toy, "R20", "1k", "EN_U2", "RETURN")
    check_toy.resistor(toy, "R21", "1k", "EN_U3", "RETURN")
    assert links.check_enable_net(netlist(toy), "EN_ALL", ROWS).problems == []


def test_a_capacitor_between_the_enable_nets_does_not_connect_them() -> None:
    """Let a capacitor (an open at DC) sit between two enables."""
    toy = board()
    with_part(toy, "C5", "100n", "EN_U2", "EN_U3", "Device:C")
    assert links.check_enable_net(netlist(toy), "EN_ALL", ROWS).problems == []


@pytest.mark.parametrize(
    "kind", ["Device:D", "Device:L", "Device:FerriteBead", "Device:Fuse", "Switch:SW"]
)
def test_any_other_two_pin_part_but_a_capacitor_conducts_for_the_walk(
    kind: str,
) -> None:
    """Fail a diode, inductor, bead, fuse or switch bridging the enables."""
    toy = board()
    with_part(toy, "X5", "x", "EN_U2", "EN_U3", kind)
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert any("EN_U2 - X5 - EN_U3" in p for p in report.problems), report.problems


def test_a_jumper_part_with_three_pads_conducts_between_all_of_them() -> None:
    """Walk through a 3-pad solder jumper from EN_U2 to the shared net."""
    toy = board()
    toy.add(
        "JP9",
        "x",
        "Jumper:SolderJumper_3_Open",
        {"1": ("EN_U2", "A"), "2": ("X", "C"), "3": ("EN_ALL", "B")},
    )
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert any(
        "EN_U2 - JP9 - X - JP9 - EN_ALL" in p or "- JP9 -" in p for p in report.problems
    )


def test_a_resistor_pack_element_between_the_enables_is_a_bypass() -> None:
    """Pair pad 1 with pad 8 and 2 with 7 in an R_Pack04, as KiCad draws it."""
    toy = board()
    toy.add(
        "RN1",
        "10k",
        "Device:R_Pack04",
        {
            "1": ("EN_U2", "R1.1"),
            "8": ("EN_U3", "R1.2"),
            "2": ("GND", "R2.1"),
            "7": ("GND", "R2.2"),
        },
    )
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert any("EN_U2 - RN1 - EN_U3" in p for p in report.problems)


def test_pack_elements_on_other_pairs_do_not_connect_the_enables() -> None:
    """Pull each enable down with its own element of a pack: no bypass."""
    toy = board()
    toy.add(
        "RN1",
        "10k",
        "Device:R_Pack04",
        {
            "1": ("EN_U2", "R1.1"),
            "8": ("GND", "R1.2"),
            "2": ("EN_U3", "R2.1"),
            "7": ("GND", "R2.2"),
        },
    )
    assert links.check_enable_net(netlist(toy), "EN_ALL", ROWS).problems == []


def test_a_network_pack_joins_every_pad_to_its_common_pad() -> None:
    """Take pad 1 of an R_Network as the common: two enables on it are bridged."""
    toy = board()
    toy.add(
        "RN1",
        "10k",
        "Device:R_Network04",
        {"1": ("GND", "COM"), "2": ("EN_U2", "R1"), "3": ("EN_U3", "R2")},
    )
    assert links.check_enable_net(netlist(toy), "EN_ALL", ROWS).problems == []
    toy.move_pin("RN1", "1", "COMMON")
    toy.move_pin("RN1", "2", "EN_U2")
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert any("EN_U2 - RN1 - COMMON - RN1 - EN_U3" in p for p in report.problems)


def test_a_bypass_beside_a_regulator_with_no_link_is_not_walked() -> None:
    """Report only that there is no link when there is nothing to remove."""
    toy = board()
    toy.set_value("R7", "10k")
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_U3")
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert not any("removing" in p and p.startswith("U2") for p in report.problems)


# ------------------------------------------------------------ links that are right
@pytest.mark.parametrize("kind", ["Device:R_Small", "Device:R_US", "Device:R_Small_US"])
def test_a_zero_ohm_small_or_us_resistor_is_a_link(kind: str) -> None:
    """Accept any two-pin Device:R symbol, not only Device:R."""
    toy = board()
    del toy.parts["R7"], toy.pins["R7"]
    with_part(toy, "R7", "0", "EN_U2", "EN_ALL", kind)
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
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
    ],
)
def test_the_ways_to_write_a_link_are_all_links(value: str) -> None:
    """Read every spelling of zero, and a DNP link, as a link."""
    toy = board()
    toy.set_value("R7", value)
    assert links.is_link(netlist(toy), "R7")


@pytest.mark.parametrize("value", ["4R7", "0R5", "R5", "E", "R", "", "  ", "10k DNP"])
def test_other_values_are_not_links_and_none_raises(value: str) -> None:
    """Refuse a value that is not zero, empty and blank text included."""
    toy = board()
    toy.set_value("R7", value)
    assert not links.is_link(netlist(toy), "R7")


def test_a_dnp_link_passes_the_check() -> None:
    """Count a link marked DNP as a link: the footprint is there to fit."""
    toy = board()
    toy.set_value("R7", "DNP")
    report = links.check_enable_net(netlist(toy), "EN_ALL", ROWS)
    assert report.problems == []
    assert report.links["U2"] == "R7"


def test_the_same_pad_listed_twice_is_a_duplicate_row() -> None:
    """Say the row is a duplicate and not that U2 shares a link with itself."""
    report = links.check_enable_net(
        netlist(board()), "EN_ALL", [("U2", "3"), ("U2", "3"), ("U3", "3")]
    )
    assert report.problems == ["U2 pad 3 is listed twice"]
    assert report.links == {"U2": "R7", "U3": "R8"}
