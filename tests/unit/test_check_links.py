"""Unit tests for pcbkit.check.links: enable links, on a hand-built netlist."""

from __future__ import annotations

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


@pytest.mark.parametrize("value", ["10k", "1", "0.1", "DNP", "", "0k5x"])
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
