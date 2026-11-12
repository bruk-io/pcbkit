"""Integration: the ``bringup`` group's exact bus scan, run as ``pcbkit check`` does.

The generic toy board (``tests/check_toy.py``) has one I2C bus with an on-board sensor
and an off-board display. Here the sensor's address is read from its address strap, so
a strap moved to the other rail changes the address the project reports. The real
command line runs in a child pytest with ``fake_nl`` serving the netlist, so no KiCad is
needed. The control passes; each planted mistake turns the check red, with the message
that says why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import check_toy
from tests.check_toy import CIRCUITS, SPECS, Toy
from tests.integration.test_check_plugin import Run, run

CHECK = "test_i2c_bus_answers_exactly_the_expected_addresses"
TABLE = "\nI2C_EXPECTED = {'main': [0x3C, 0x40]}\n"

# the sensor answers 0x40 with its address pin low, 0x41 with it high
STRAPPED = CIRCUITS.replace(
    'return [(nl.net("U1", 3), "SENSOR", 0x40)]',
    'return [(nl.net("U1", 3), "SENSOR", 0x41 if nl.net("U1", 5) == "+3V3" else 0x40)]',
)


def go(
    tmp_path: Path,
    name: str,
    toy: Toy | None = None,
    specs: str = SPECS + TABLE,
    circuits: str = STRAPPED,
) -> Run:
    """Run only this module's checks, with the ``bringup`` group on."""
    assert STRAPPED != CIRCUITS
    root = check_toy.write_project(
        tmp_path / name,
        toy or check_toy.good(),
        ["bringup"],
        specs=specs,
        circuits=circuits,
    )
    return run(root, "test_i2c_expected")


def test_the_control_passes_and_records_the_scan(tmp_path: Path) -> None:
    """Pass with the strap low (0x40) and the display at 0x3c; record all four lists."""
    result = go(tmp_path, "control")
    assert result.done.returncode == 0, result.done.stdout
    entry = result.entry(f"{CHECK}[main]")
    assert entry["outcome"] == "passed"
    assert entry["numbers"] == {
        "expected": ["0x3c", "0x40"],
        "found": ["0x3c", "0x40"],
        "missing": [],
        "extra": [],
        "devices": {"0x3c": ["DISPLAY"], "0x40": ["SENSOR"]},
    }
    assert (entry["gate"], entry["group"]) == ("sch", "bringup")
    assert len(result.checks) == 1


def test_an_address_strap_moved_to_the_other_rail_fails(tmp_path: Path) -> None:
    """Fail with 0x40 missing and 0x41 extra; the records still hold the scan."""
    toy = check_toy.good()
    toy.move_pin("U1", "5", "+3V3")
    entry = go(tmp_path, "strap", toy).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "bus main: expected 0x3c, 0x40; missing 0x40; extra 0x41 (SENSOR)"
        in (entry["message"])
    )
    assert entry["numbers"] == {
        "expected": ["0x3c", "0x40"],
        "found": ["0x3c", "0x41"],
        "missing": ["0x40"],
        "extra": ["0x41"],
        "devices": {"0x3c": ["DISPLAY"], "0x41": ["SENSOR"]},
    }


def test_a_bus_that_is_not_in_the_bus_table_fails_naming_it(tmp_path: Path) -> None:
    """Fail the unknown bus by name, and still pass the one that is there."""
    specs = SPECS + "\nI2C_EXPECTED = {'main': [0x3C, 0x40], 'aux': [0x50]}\n"
    result = go(tmp_path, "unknown", specs=specs)
    assert result.outcome(f"{CHECK}[main]") == "passed"
    entry = result.entry(f"{CHECK}[aux]")
    assert entry["outcome"] == "failed"
    assert (
        "bus aux is in specs.I2C_EXPECTED but not in specs.I2C_BUSES"
        in (entry["message"])
    )
    assert "the buses are ['main']" in entry["message"]


def test_an_expected_address_nothing_answers_to_fails(tmp_path: Path) -> None:
    """Fail when the spec expects an address (0x50) no device is at."""
    specs = SPECS + "\nI2C_EXPECTED = {'main': [0x3C, 0x40, 0x50]}\n"
    entry = go(tmp_path, "absent", specs=specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "missing 0x50" in entry["message"]
    assert entry["numbers"]["extra"] == []


def test_an_off_board_device_left_out_of_the_expectation_is_extra(
    tmp_path: Path,
) -> None:
    """Fail naming the display when the spec does not expect 0x3c."""
    specs = SPECS + "\nI2C_EXPECTED = {'main': [0x40]}\n"
    entry = go(tmp_path, "display", specs=specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "extra 0x3c (DISPLAY)" in entry["message"]


def test_a_missing_table_fails_by_name(tmp_path: Path) -> None:
    """Fail once as ``missing-spec``, naming the table and the file."""
    entry = go(tmp_path, "missing", specs=SPECS).entry(f"{CHECK}[missing-spec]")
    assert entry["outcome"] == "failed"
    assert "specs.I2C_EXPECTED is not defined in" in entry["message"]


def test_an_empty_table_says_no_bus_has_an_expectation(tmp_path: Path) -> None:
    """Skip, saying there is no row to run over, and exit 0."""
    result = go(tmp_path, "none", specs=SPECS + "\nI2C_EXPECTED = {}\n")
    assert result.done.returncode == 0, result.done.stdout
    entry = result.entry(CHECK)
    assert entry["outcome"] == "skipped"
    assert "empty parameter set" in entry["reason"]


def test_a_malformed_table_fails_by_name_with_its_path(tmp_path: Path) -> None:
    """Report the wrong address's path, with no traceback."""
    specs = SPECS + "\nI2C_EXPECTED = {'main': [0x100]}\n"
    entry = go(tmp_path, "malformed", specs=specs).entry(f"{CHECK}[bad-table]")
    assert entry["outcome"] == "failed"
    assert 'specs.I2C_EXPECTED["main"][0]: expected 7-bit address' in entry["message"]
    assert "Traceback" not in entry["message"]


def test_a_project_without_i2c_devices_fails_by_name(tmp_path: Path) -> None:
    """Fail naming ``circuits.i2c_devices`` when the project defines no such hook."""
    broken = STRAPPED.replace("def i2c_devices", "def i2c_devices_gone")
    root = check_toy.write_project(
        tmp_path / "nohook",
        check_toy.good(),
        ["bringup"],
        specs=SPECS + TABLE,
        circuits=broken,
    )
    entry = run(root, "test_i2c_expected").entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "circuits.i2c_devices is not defined in" in entry["message"]


@pytest.mark.parametrize("groups", [[], ["circuit", "ports", "power"]])
def test_a_board_that_has_not_switched_the_group_on_is_untouched(
    tmp_path: Path, groups: list[str]
) -> None:
    """Run no bus-scan check, and need no I2C_EXPECTED, unless ``bringup`` is on."""
    root = check_toy.write_project(tmp_path / "off", check_toy.good(), groups)
    result = run(root)
    assert not [k for k in result.checks if "test_i2c_expected" in k]
    assert "bringup" not in result.results["groups"]


def devices_returning(rows: str) -> str:
    """Return the toy's circuits with ``i2c_devices`` returning ``rows`` (Python)."""
    return CIRCUITS.replace(
        'return [(nl.net("U1", 3), "SENSOR", 0x40)]', f"return {rows}"
    )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ('[("SDA", "SENSOR", "0x40")]', "address '0x40' is not a whole number"),
        ("None", "circuits.i2c_devices returned None, expected a list of"),
        ('[("SDA", "SENSOR", 0x80)]', "address 0x80 is not a 7-bit address"),
        ('[("SDA", "SENSOR")]', "row 0 is ('SDA', 'SENSOR'), expected (sda net"),
    ],
)
def test_a_device_list_that_cannot_be_read_fails_naming_the_function(
    tmp_path: Path, rows: str, message: str
) -> None:
    """Fail with a message, not a TypeError, and record only what was expected."""
    result = go(tmp_path, "bad-rows", circuits=devices_returning(rows))
    entry = result.entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "circuits.i2c_devices" in entry["message"]
    assert message in entry["message"]
    assert "TypeError" not in entry["message"]
    assert entry["numbers"] == {"expected": ["0x3c", "0x40"]}


def test_two_devices_at_one_address_fail_here_without_the_circuit_group(
    tmp_path: Path,
) -> None:
    """Fail naming both devices, though ``circuit`` is off; record who answers."""
    rows = '[(nl.net("U1", 3), "SENSOR", 0x3C)]'
    result = go(
        tmp_path,
        "clash",
        circuits=devices_returning(rows),
        specs=SPECS + "\nI2C_EXPECTED = {'main': [0x3C]}\n",
    )
    entry = result.entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "bus main: address 0x3c answers for two devices: SENSOR, DISPLAY"
        in (entry["message"])
    )
    assert entry["numbers"]["devices"] == {"0x3c": ["SENSOR", "DISPLAY"]}
    assert entry["numbers"]["missing"] == [] and entry["numbers"]["extra"] == []


def test_an_address_listed_twice_in_the_expected_table_fails_by_name(
    tmp_path: Path,
) -> None:
    """Name the address, though the set of addresses matches."""
    specs = SPECS + "\nI2C_EXPECTED = {'main': [0x3C, 0x40, 0x40]}\n"
    entry = go(tmp_path, "twice", specs=specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "specs.I2C_EXPECTED['main'] lists 0x40 twice" in entry["message"]
    assert entry["numbers"]["missing"] == [] and entry["numbers"]["extra"] == []


def test_an_sda_net_written_with_a_slash_fails_saying_so(tmp_path: Path) -> None:
    """Say the net is not in the design, and that names carry no leading slash."""
    specs = SPECS.replace('("SDA", "SCL"', '("/SDA", "SCL"') + TABLE
    assert specs != SPECS + TABLE
    entry = go(tmp_path, "slash", specs=specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "SDA net /SDA is not in the design (net names carry no leading slash: "
        "write SDA)" in entry["message"]
    )
    assert entry["numbers"]["missing"] == ["0x40"]
