"""The exact I2C bus scan: the addresses that answer are the ones expected.

Switched on by ``bringup`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic and on ``circuits.i2c_devices``. What the project gives:

* ``specs.I2C_EXPECTED``: ``{bus: [7-bit address]}``, the addresses a scan of the bus
  must show, for buses that are keys of ``specs.I2C_BUSES``. ``{}`` says no bus has an
  expectation.
* ``specs.I2C_BUSES`` (as in the ``fab`` group) and ``circuits.i2c_devices(nl)``: the
  devices on each bus. A device that answers an all-call or general-call address must
  be reported at that address as well, or the scan will not match. Two devices at one
  address, an address listed twice in ``I2C_EXPECTED`` and an SDA net that is not in the
  netlist (net names carry no leading slash) fail by name.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import i2c, tables
from pcbkit.check.netlist import Netlist
from pcbkit.check.plugin import ProjectModule, spec_params


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the check from the buses of ``I2C_EXPECTED``."""
    spec_params(
        metafunc, "bus", lambda specs: sorted(tables.read(specs, "I2C_EXPECTED"))
    )


def test_i2c_bus_answers_exactly_the_expected_addresses(
    nl: Netlist,
    record: Any,
    specs: ProjectModule,
    circuits: ProjectModule,
    bus: str,
) -> None:
    """Match the addresses found on a bus with the ones the spec expects.

    Records ``expected``, ``found``, ``missing`` and ``extra`` as lists of ``0x3c``
    text and ``devices`` (``{"0x3c": [device names]}``), before any failure is raised
    (only ``expected`` when a device list cannot be read). Fails too for a device list
    that is not rows of ``(sda net, name, address)`` with an address from 0 to 0x7F, two
    devices at one address, an address listed twice in ``I2C_EXPECTED`` and an SDA net
    that is not in the design.
    """
    expected = tables.read(specs, "I2C_EXPECTED")[bus]
    buses = tables.read(specs, "I2C_BUSES")
    assert bus in buses, (
        f"bus {bus} is in specs.I2C_EXPECTED but not in specs.I2C_BUSES "
        f"(the buses are {sorted(buses)})"
    )
    sda, _scl, _hz, off_board = buses[bus]
    problems = [
        f"specs.I2C_EXPECTED[{bus!r}] lists 0x{a:02x} twice"
        for a in i2c.expected_duplicates(expected)
    ]
    sda_missing = i2c.sda_problem(sda, nl.nets)
    if sda_missing is not None:
        problems.append(sda_missing)
    on_board, unreadable = i2c.device_rows(
        circuits.i2c_devices(nl), "circuits.i2c_devices"
    )
    off_rows, off_unreadable = i2c.device_rows(
        [(sda, name, addr) for name, addr, _ in off_board],
        f"specs.I2C_BUSES[{bus!r}]",
    )
    record("expected", i2c.hexes(set(expected)))
    # a list that cannot be read is not compared: nothing else would be right
    assert not (unreadable or off_unreadable), f"bus {bus}: " + "; ".join(
        problems + unreadable + off_unreadable
    )
    found = i2c.found_on_bus(on_board, sda, [(n, a) for _, n, a in off_rows])
    report = i2c.compare(expected, found)
    record("found", i2c.hexes(report.found))
    record("missing", i2c.hexes(report.missing))
    record("extra", i2c.hexes(report.extra))
    record("devices", i2c.devices_record(found))
    problems += i2c.clash_problems(found)
    scan = report.problem()
    if scan is not None:
        problems.append(scan)
    assert not problems, f"bus {bus}: " + "; ".join(problems)
