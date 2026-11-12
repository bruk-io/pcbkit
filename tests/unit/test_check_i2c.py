"""Unit tests for pcbkit.check.i2c: the exact bus scan, on plain tuples."""

from __future__ import annotations

import pytest

from pcbkit.check import i2c, tables

ON_BOARD = [("SDA", "SENSOR", 0x40), ("SDA2", "OTHER", 0x50)]
OFF_BOARD = [("DISPLAY", 0x3C)]


def test_found_is_the_on_board_devices_of_this_bus_plus_the_off_board_ones() -> None:
    """Leave out a device on another bus's SDA net."""
    found = i2c.found_on_bus(ON_BOARD, "SDA", OFF_BOARD)
    assert found == {0x40: ["SENSOR"], 0x3C: ["DISPLAY"]}


def test_two_devices_at_one_address_are_listed_under_it() -> None:
    """Keep both names, as one scan would show one address."""
    found = i2c.found_on_bus(
        [("SDA", "A", 0x40), ("SDA", "B", 0x40)], "SDA", [("C", 0x40)]
    )
    assert found == {0x40: ["A", "B", "C"]}


def test_hexes_are_two_digit_lowest_first() -> None:
    """Write 0x3c, 0x40 and 0x05 as the tools do, sorted."""
    assert i2c.hexes([0x40, 0x05, 0x3C]) == ["0x05", "0x3c", "0x40"]
    assert i2c.hexes([]) == []


def test_matching_sets_have_no_problem() -> None:
    """Pass when the expected and found addresses are equal, in any order."""
    found = i2c.found_on_bus(ON_BOARD, "SDA", OFF_BOARD)
    report = i2c.compare([0x40, 0x3C], found)
    assert report.problem() is None
    assert (report.missing, report.extra) == ([], [])
    assert (report.expected, report.found) == ([0x3C, 0x40], [0x3C, 0x40])


def test_a_strap_moved_gives_a_missing_and_an_extra_address() -> None:
    """Name 0x40 as missing and 0x41 as extra, with the device that answers there."""
    found = i2c.found_on_bus([("SDA", "SENSOR", 0x41)], "SDA", OFF_BOARD)
    report = i2c.compare([0x3C, 0x40], found)
    assert report.missing == [0x40]
    assert report.extra == [0x41]
    assert report.problem() == "expected 0x3c, 0x40; missing 0x40; extra 0x41 (SENSOR)"


def test_an_extra_address_only_and_a_missing_address_only() -> None:
    """Report one side alone without a stray separator."""
    found = i2c.found_on_bus([], "SDA", [("A", 0x10), ("B", 0x11)])
    assert i2c.compare([0x10], found).problem() == "expected 0x10; extra 0x11 (B)"
    assert i2c.compare([0x10, 0x11, 0x12], found).problem() == (
        "expected 0x10, 0x11, 0x12; missing 0x12"
    )


def test_a_general_call_address_the_project_reports_counts_as_found() -> None:
    """Take 0x70 as found when the project lists a device there, expected or not."""
    found = i2c.found_on_bus([("SDA", "MUX all-call", 0x70)], "SDA", [])
    assert i2c.compare([0x70], found).problem() is None
    assert (
        i2c.compare([], found).problem()
        == "expected no address; extra 0x70 (MUX all-call)"
    )


def test_duplicates_in_the_expected_list_are_one_address() -> None:
    """Treat the expected list as a set."""
    found = i2c.found_on_bus([], "SDA", [("A", 0x10)])
    assert i2c.compare([0x10, 0x10], found).problem() is None


def test_the_i2c_expected_shape_is_this_modules_and_bounds_the_address() -> None:
    """Register the table here, not in PROPOSED; refuse 0x80 and a non-list."""
    shape = i2c.TABLES["I2C_EXPECTED"]
    assert "I2C_EXPECTED" not in tables.PROPOSED
    assert tables.registry()["I2C_EXPECTED"] is shape
    assert tables.validate("I2C_EXPECTED", {"main": [0x3C, 0x7F, 0]}, shape) == []
    assert tables.validate("I2C_EXPECTED", {"main": [0x80]}, shape) == [
        'specs.I2C_EXPECTED["main"][0]: expected 7-bit address from 0 to 127, got 128'
    ]


@pytest.mark.parametrize("value", [-1, 1.5, "0x3c", True])
def test_the_shape_refuses_an_address_that_is_not_a_whole_number_in_range(
    value: object,
) -> None:
    """Refuse a negative, a float, text and a bool."""
    found = tables.validate(
        "I2C_EXPECTED", {"main": [value]}, i2c.TABLES["I2C_EXPECTED"]
    )
    assert len(found) == 1
    assert found[0].startswith('specs.I2C_EXPECTED["main"][0]: expected 7-bit address')


# ------------------------------------------------------------ devices that are wrong
@pytest.mark.parametrize(
    ("rows", "problem"),
    [
        (None, "circuits.i2c_devices returned None, expected a list of "),
        (5, "circuits.i2c_devices returned int, expected a list of "),
        ("abc", "circuits.i2c_devices returned str, expected a list of "),
        (
            [("SDA", "A")],
            "circuits.i2c_devices row 0 is ('SDA', 'A'), expected (sda net, name, "
            "address)",
        ),
        (
            [("SDA", "A", 0x40), "SDA"],
            "circuits.i2c_devices row 1 is 'SDA', expected (sda net, name, address)",
        ),
        (
            [("SDA", "A", "0x40")],
            "circuits.i2c_devices row 0 (A): address '0x40' is not a whole number: "
            "write 0x40, not '0x40'",
        ),
        (
            [("SDA", "A", 64.0)],
            "circuits.i2c_devices row 0 (A): address 64.0 is not a whole number",
        ),
        (
            [("SDA", "A", True)],
            "circuits.i2c_devices row 0 (A): address True is not a whole number",
        ),
        (
            [("SDA", "A", "0x3c")],
            "circuits.i2c_devices row 0 (A): address '0x3c' is not a whole number: "
            "write 0x3c, not '0x3c'",
        ),
        (
            [("SDA", "A", "sensor")],
            "circuits.i2c_devices row 0 (A): address 'sensor' is not a whole number",
        ),
        (
            [("SDA", "A", 0x80)],
            "circuits.i2c_devices row 0 (A): address 0x80 is not a 7-bit address",
        ),
        (
            [("SDA", "A", -1)],
            "circuits.i2c_devices row 0 (A): address -1 is not a 7-bit address",
        ),
    ],
)
def test_a_device_list_that_is_not_rows_of_net_name_address_is_named(
    rows: object, problem: str
) -> None:
    """Say what circuits.i2c_devices gave, for each way it can be wrong."""
    clean, found = i2c.device_rows(rows, "circuits.i2c_devices")
    if "row" in problem:
        assert found[0] == problem
    else:
        assert found[0].startswith(problem)
    assert clean == []


def test_good_rows_pass_and_the_first_problem_per_row_only() -> None:
    """Return the rows as tuples and no problem, for the shapes a project writes."""
    clean, found = i2c.device_rows(
        [["SDA", "A", 0x40], ("SDA", "B", 0x7F), ("SDA", "C", 0)], "x"
    )
    assert found == []
    assert clean == [("SDA", "A", 0x40), ("SDA", "B", 0x7F), ("SDA", "C", 0)]


def test_clashes_are_the_addresses_with_two_devices_in_found_order() -> None:
    """Name both devices at an address; a device alone is no clash."""
    found = i2c.found_on_bus(
        [("SDA", "A", 0x40), ("SDA", "B", 0x40), ("SDA", "D", 0x41)],
        "SDA",
        [("C", 0x3C), ("E", 0x41)],
    )
    assert i2c.clashes(found) == {0x40: ["A", "B"], 0x41: ["D", "E"]}
    assert i2c.clash_problems(found) == [
        "address 0x40 answers for two devices: A, B",
        "address 0x41 answers for two devices: D, E",
    ]


def test_two_devices_reported_at_the_all_call_address_are_a_clash() -> None:
    """Fail at 0x00 when two devices each answer the general call, as the docs say."""
    found = i2c.found_on_bus(
        [("SDA", "A", 0x40), ("SDA", "A", 0x00), ("SDA", "B", 0x41), ("SDA", "B", 0)],
        "SDA",
        [],
    )
    assert i2c.clash_problems(found) == ["address 0x00 answers for two devices: A, B"]


def test_an_expected_address_listed_twice_is_named() -> None:
    """Name the repeated address, once however often it repeats."""
    assert i2c.expected_duplicates([0x40, 0x3C, 0x40, 0x40]) == [0x40]
    assert i2c.expected_duplicates([0x40, 0x3C]) == []


def test_names_per_address_are_keyed_by_the_hex_address() -> None:
    """Give ``{"0x3c": ["DISPLAY"]}`` for the record, lowest first."""
    found = i2c.found_on_bus(ON_BOARD, "SDA", OFF_BOARD)
    assert i2c.devices_record(found) == {"0x3c": ["DISPLAY"], "0x40": ["SENSOR"]}
    assert list(i2c.devices_record(found)) == ["0x3c", "0x40"]


def test_an_sda_net_that_is_not_in_the_design_says_names_carry_no_slash() -> None:
    """Say the net is missing, and why when the only difference is a slash."""
    nets = {"SDA": []}
    assert i2c.sda_problem("SDA", nets) is None
    assert i2c.sda_problem("/SDA", nets) == (
        "SDA net /SDA is not in the design (net names carry no leading slash: "
        "write SDA)"
    )
    assert i2c.sda_problem("SDX", nets) == "SDA net SDX is not in the design"
