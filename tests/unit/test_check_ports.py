"""Unit tests for pcbkit.check.ports: the port map, on a hand-built netlist."""

from __future__ import annotations

import pytest

from pcbkit.check import ports, tables
from pcbkit.check.netlist import Netlist
from tests import check_toy
from tests.check_toy import Toy


def netlist(toy: Toy) -> Netlist:
    """Return the netlist KiCad 10 would write for the toy."""
    return Netlist.from_text(check_toy.netlist_text(toy))


def board() -> Toy:
    """Return a board with a power header, a signal connector and a controller."""
    toy = Toy()
    toy.add(
        "J1",
        "Power",
        "Connector_Generic:Conn_01x03",
        {
            "1": ("VIN", "Pin_1"),
            "2": ("GND", "Pin_2"),
            "3": ("unconnected-(J1-Pad3)", "Pin_3"),
        },
    )
    toy.add(
        "P2",
        "Signals",
        "Connector:Screw_Terminal_01x02",
        {"1": ("SDA", "Pin_1"), "2": ("SCL", "Pin_2")},
    )
    toy.add(
        "JP1",
        "Jumper",
        "Jumper:SolderJumper_2_Open",
        {"1": ("SDA", "~"), "2": ("SCL", "~")},
    )
    toy.add("TP1", "TP", "Connector:TestPoint", {"1": ("SDA", "1")})
    toy.add(
        "U1",
        "Controller",
        "MCU:Chip",
        {"1": ("SDA", "SDA"), "2": ("SCL", "SCL"), "3": ("GND", "GND")},
    )
    check_toy.resistor(toy, "R1", "1k", "VIN", "SDA")
    return toy


def test_a_connector_is_from_a_connector_library_or_has_exactly_the_letter_j() -> None:
    """Take J1 (a J), P2 (the Connector library); not JP1 (a jumper), R1 or U1."""
    nl = netlist(board())
    assert ports.connectors(nl) == ["J1", "P2"]
    assert ports.is_connector(nl, "P2")
    assert not ports.is_connector(nl, "JP1")
    assert not ports.is_connector(nl, "U1")
    assert not ports.is_connector(nl, "R1")


@pytest.mark.parametrize(
    ("ref", "lib_part", "expected"),
    [
        ("J1", "Anything:Thing", True),
        ("J12", "Anything:Thing", True),
        ("P1", "Connector:Conn_01x02", True),
        ("X1", "Connector_Generic:Conn_01x02", True),
        ("JP1", "Jumper:SolderJumper_2_Open", False),
        ("JP2", "Device:R", False),
        ("J2", "Jumper:SolderJumper_2_Open", False),
        ("TP1", "Connector:TestPoint", False),
        ("TP2", "Connector:Conn_01x01", False),
        ("J3", "Connector:TestPoint", False),
        ("J4", "TestPoint:TestPoint_Pad_D1.0mm", False),
        ("J5", "Connector:TestPoint_Probe", False),
        ("W1", "Connector:TestPoint_Loop", False),
        ("JACK1", "Anything:Thing", False),
        ("CN1", "Custom_Lib:Socket", False),
        ("P3", "Custom_Lib:Socket", False),
        ("X2", "Custom_Lib:Socket", False),
    ],
)
def test_the_connector_rule_leaves_out_test_points_and_jumpers(
    ref: str, lib_part: str, expected: bool
) -> None:
    """Take a library starting Connector or the letter J, never a test point or jumper.

    A connector from a custom library with reference P3, CN1 or X2 is not found: it
    must be listed by hand to be checked.
    """
    toy = Toy()
    toy.add(ref, "x", lib_part, {"1": ("A", "1")})
    assert ports.is_connector(netlist(toy), ref) is expected


def test_the_library_rule_is_a_prefix_not_a_substring() -> None:
    """Leave a library that merely contains the word out."""
    toy = Toy()
    toy.add("X1", "x", "My_Connector_Lib:Thing", {"1": ("A", "1")})
    toy.add("X2", "x", "Connector_Generic:Conn", {"1": ("A", "1")})
    assert ports.connectors(netlist(toy)) == ["X2"]


def test_found_nets_maps_every_listed_pad_and_unwired_ones_to_none() -> None:
    """Read an ``unconnected-(...)`` net as None, in natural pad order."""
    nl = netlist(board())
    assert ports.found_nets(nl, "J1") == {"1": "VIN", "2": "GND", "3": None}
    assert list(ports.found_nets(nl, "J1")) == ["1", "2", "3"]
    assert ports.found_nets(nl, "NOPE") == {}


def test_found_nets_orders_pads_naturally() -> None:
    """List pad 2 before pad 10."""
    toy = Toy()
    toy.add(
        "J1",
        "x",
        "Connector:C",
        {"10": ("A", "p"), "2": ("B", "p"), "1": ("C", "p")},
    )
    assert list(ports.found_nets(netlist(toy), "J1")) == ["1", "2", "10"]


def test_a_row_that_matches_has_nothing_wrong_and_nothing_unlisted() -> None:
    """Accept the port map as the design has it."""
    nl = netlist(board())
    row = {"1": "VIN", "2": "GND", "3": None}
    report = ports.compare(nl, "J1", row)
    assert report.wrong == []
    assert report.unlisted == {}
    assert report.found == row


def test_two_swapped_nets_are_each_named_with_expected_and_found() -> None:
    """Name both pads of a swap, in pad order."""
    nl = netlist(board())
    report = ports.compare(nl, "J1", {"1": "GND", "2": "VIN", "3": None})
    assert report.wrong == [
        "pad 1: expected GND, found VIN",
        "pad 2: expected VIN, found GND",
    ]


def test_a_pad_the_row_says_is_wired_but_is_not_is_named_unconnected() -> None:
    """Say ``found unconnected`` for an unwired pad, ``expected unconnected`` too."""
    nl = netlist(board())
    report = ports.compare(nl, "J1", {"1": "VIN", "2": "GND", "3": "SDA"})
    assert report.wrong == ["pad 3: expected SDA, found unconnected"]
    report = ports.compare(nl, "J1", {"1": "VIN", "2": None, "3": None})
    assert report.wrong == ["pad 2: expected unconnected, found GND"]


@pytest.mark.parametrize("net", [None, "X"])
def test_a_listed_pad_the_part_does_not_have_fails_as_not_a_pad(
    net: str | None,
) -> None:
    """Say ``not a pad of J1`` whether the row gives the pad a net or None."""
    nl = netlist(board())
    report = ports.compare(nl, "J1", {"1": "VIN", "2": "GND", "9": net})
    assert report.wrong == ["pad 9: not a pad of J1"]
    assert report.not_pads == ["9"]
    assert "9" not in report.found  # None in found means unwired, never "no such pad"


def test_an_unwired_pad_is_found_as_none_and_is_not_a_missing_pad() -> None:
    """Keep None in found for a pad the part has and leaves unwired."""
    nl = netlist(board())
    report = ports.compare(nl, "J1", {"3": None})
    assert report.found == {"3": None}
    assert report.not_pads == []


def test_a_net_written_with_a_leading_slash_says_names_carry_none() -> None:
    """Explain "/VIN" when VIN is what the netlist has, and only then."""
    nl = netlist(board())
    report = ports.compare(nl, "J1", {"1": "/VIN", "2": "GND", "3": None})
    assert report.wrong == [
        "pad 1: expected /VIN, found VIN (net names carry no leading slash)"
    ]
    report = ports.compare(nl, "J1", {"1": "/GND", "2": "GND", "3": None})
    assert report.wrong == ["pad 1: expected /GND, found VIN"]


def test_a_wired_connector_pad_the_row_leaves_out_is_unlisted() -> None:
    """Find pad 2 of J1, wired to GND, when the row stops at pad 1."""
    nl = netlist(board())
    report = ports.compare(nl, "J1", {"1": "VIN"})
    assert report.wrong == []
    assert report.unlisted == {"2": "GND"}  # pad 3 is unwired, so it need not be listed


def test_a_part_that_is_not_a_connector_needs_only_its_listed_pads() -> None:
    """Leave the controller's other pads alone."""
    nl = netlist(board())
    report = ports.compare(nl, "U1", {"1": "SDA"})
    assert report.wrong == []
    assert report.unlisted == {}


def test_a_part_missing_from_the_design_has_no_pad_of_the_row() -> None:
    """Compare against nothing: each pad of the row is not a pad."""
    nl = netlist(board())
    report = ports.compare(nl, "J9", {"1": "VIN"})
    assert report.wrong == ["pad 1: not a pad of J9"]
    assert report.found == {}
    assert report.not_pads == ["1"]


def test_unlisted_connectors_are_the_ones_that_are_not_keys() -> None:
    """Name each connector the map leaves out, in natural order."""
    nl = netlist(board())
    assert ports.unlisted_connectors(nl, {"J1": {}, "U1": {}}) == ["P2"]
    assert ports.unlisted_connectors(nl, {"J1": {}, "P2": {}}) == []


def test_a_design_with_no_connector_needs_an_empty_map_only() -> None:
    """Pass ``{}`` for a board with no connector."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "1k", "A", "B")
    assert ports.unlisted_connectors(netlist(toy), {}) == []


def test_the_port_map_as_found_covers_every_connector() -> None:
    """Return each connector with all its pads, unwired ones as None."""
    found = ports.port_map_found(netlist(board()))
    assert list(found) == ["J1", "P2"]
    assert found["J1"] == {"1": "VIN", "2": "GND", "3": None}
    assert found["P2"] == {"1": "SDA", "2": "SCL"}


def test_the_pin_nets_shape_is_this_modules_and_reads_ints_as_pad_text() -> None:
    """Register the table here, not in PROPOSED, and normalise pads to text."""
    assert "PIN_NETS" not in tables.PROPOSED
    assert tables.registry()["PIN_NETS"] is ports.TABLES["PIN_NETS"]
    table = {"J1": {1: "VIN", 2: None}}
    assert tables.validate("PIN_NETS", table, ports.TABLES["PIN_NETS"]) == []
    out, _ = tables.convert("PIN_NETS", table, ports.TABLES["PIN_NETS"])
    assert out == {"J1": {"1": "VIN", "2": None}}


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"J1": {1: 5}}, 'specs.PIN_NETS["J1"][1]: expected net or None, got 5'),
        ({"J 1": {}}, "specs.PIN_NETS[\"J 1\"]: expected ref, got 'J 1'"),
        ({"J1": []}, 'specs.PIN_NETS["J1"]: expected {pad: net or None}, got []'),
    ],
)
def test_the_pin_nets_shape_refuses_a_wrong_row_with_its_path(
    table: object, message: str
) -> None:
    """Refuse a non-net, a ref with a space and a row that is not a dict."""
    assert message in tables.validate("PIN_NETS", table, ports.TABLES["PIN_NETS"])
