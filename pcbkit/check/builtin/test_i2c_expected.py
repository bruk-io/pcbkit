"""The exact I2C bus scan: the addresses that answer are the ones expected.

Switched on by ``bringup`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic and on ``circuits.i2c_devices``. What the project gives:

* ``specs.I2C_EXPECTED``: ``{bus: [7-bit address]}``, the addresses a scan of the bus
  must show, for buses that are keys of ``specs.I2C_BUSES``. ``{}`` says no bus has an
  expectation.
* ``specs.I2C_BUSES`` (as in the ``fab`` group) and ``circuits.i2c_devices(nl)``: the
  devices on each bus. A device that answers an all-call or general-call address must
  be reported at that address as well, or the scan will not match.
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
    text, before any failure is raised.
    """
    expected = tables.read(specs, "I2C_EXPECTED")[bus]
    buses = tables.read(specs, "I2C_BUSES")
    assert bus in buses, (
        f"bus {bus} is in specs.I2C_EXPECTED but not in specs.I2C_BUSES "
        f"(the buses are {sorted(buses)})"
    )
    sda, _scl, _hz, off_board = buses[bus]
    found = i2c.found_on_bus(
        circuits.i2c_devices(nl), sda, [(name, addr) for name, addr, _ in off_board]
    )
    report = i2c.compare(expected, found)
    record("expected", i2c.hexes(report.expected))
    record("found", i2c.hexes(report.found))
    record("missing", i2c.hexes(report.missing))
    record("extra", i2c.hexes(report.extra))
    problem = report.problem()
    assert problem is None, f"bus {bus}: {problem}"
