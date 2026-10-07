"""Schematic-level checks: datasheet pinouts, component stress, LEDs, supplies, I2C.

Switched on by ``circuit`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic, and on operating points the project describes. What the project
gives, in ``specs.py``:

* ``PINOUT``: ``{ref: {pad: function}}``, the datasheet's name for each pad; optional
  ``PIN_ALIASES`` (the symbol's spelling to the datasheet's: ``{"IN": "VIN"}``, ``None``
  for a pin that is unnamed in the symbol) and ``ABSENT_REFS`` (``{ref: reason}`` for a
  part this revision does not carry: its pinout check skips with the reason). A row for
  a part that is neither in the design nor in ``ABSENT_REFS`` compares nothing and
  passes, with a warning.
* ``STRESS_SCENARIOS``: keyword arguments for ``circuits.scenario`` for the operating
  conditions that stress parts hardest; ``RESISTOR_POWER_W``: ``{"default": watts, ref:
  watts}``; ``CAP_VRATED``: capacitor MPN to rated volts.
* ``LED_REFS``, ``LED_SCENARIO``, ``LED_I_MIN``, ``LED_I_MAX``: the LEDs (a name the
  scenario adds, such as ``"panel_led"``, stands for an off-board one), the conditions
  and the current window in amps.
* ``SUPPLY_SCENARIO``, ``SUPPLY_PINS`` (rows ``(ref, pin, (min V, max V))``) and
  ``SUPPLY_LIMITS`` (rows ``(what, value, "<=" or "<", limit)``, or a function of the
  netlist that returns such a row): the rails the parts see and the limits the design's
  ranges must fit inside.
* ``I2C_BUSES`` ``{bus: (sda net, scl net, speed Hz, off-board devices)}`` with
  ``I2C_IOL`` (sink current per speed); optional ``I2C_PULLUP_NET`` ("+3V3") and
  ``I2C_VDD`` (3.3).

and, in ``circuits.py``, ``scenario(nl, **kw)`` returning an unsolved
``pcbkit.check.dc.Circuit`` and ``i2c_devices(nl)`` returning ``(sda net, name,
address)`` for each on-board I2C device.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator
from typing import Any

import pytest

from pcbkit.check.dc import Solution
from pcbkit.check.netlist import Netlist, parse_value
from pcbkit.check.plugin import ProjectModule, spec_params

# Symbol pin names that mean "no name": the checks skip them.
GENERIC_ALIASES: dict[str, str | None] = {"~": None, "": None}


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the pinout and I2C checks from the project's tables."""
    spec_params(metafunc, "ref", lambda specs: sorted(specs.PINOUT))
    spec_params(metafunc, "bus", lambda specs: sorted(specs.I2C_BUSES))


def _function_name(name: str, aliases: dict[str, str | None]) -> str | None:
    """Return a symbol's pin name as the datasheet spells it, or None for no name."""
    name = aliases.get(name, name)
    if name is not None:
        overbar = re.fullmatch(r"~\{(.+)\}", name)  # ~{OE} is written OE
        if overbar:
            name = overbar.group(1)
    return name


# ------------------------------------------------------------------ pinouts
def test_symbol_pin_functions_match_datasheet(
    nl: Netlist, specs: ProjectModule, ref: str
) -> None:
    """Give each pad the function the datasheet gives it, as the symbol names it."""
    if ref not in nl.parts:
        reason = dict(getattr(specs, "ABSENT_REFS", {})).get(ref)
        if reason is not None:
            pytest.skip(reason)
        # Nothing to compare, and the old check passed here: keep that, but say so.
        warnings.warn(
            f"{ref} is in specs.PINOUT but not in the design, so nothing was compared: "
            "remove the row, or list the part in ABSENT_REFS with the reason",
            stacklevel=1,
        )
        return
    aliases = {**GENERIC_ALIASES, **dict(getattr(specs, "PIN_ALIASES", {}))}
    want = specs.PINOUT[ref]
    got = {
        pin: func
        for (r, pin), _ in nl.pin_net.items()
        if r == ref
        for rr, pp, func in nl.nets[nl.pin_net[(r, pin)]]
        if rr == r and pp == pin
    }
    bad = []
    for pin, name in want.items():
        found = _function_name(got.get(str(pin), ""), aliases)
        if found is None:
            continue  # an unnamed symbol pin: connectivity checks cover it
        if found.upper() != name.upper():
            bad.append((pin, name, found))
    assert not bad, f"pin, datasheet, symbol: {bad}"


# ------------------------------------------------------------------ component stress
def _stress_scenarios(
    nl: Netlist, specs: ProjectModule, circuits: ProjectModule
) -> Iterator[Solution]:
    """Yield the solved operating points that stress parts hardest."""
    for keywords in specs.STRESS_SCENARIOS:
        yield circuits.scenario(nl, **keywords).solve()


def test_resistor_power_derated(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Keep every resistor under half its power rating at the worst operating point."""
    worst: dict[str, float] = {}
    for solved in _stress_scenarios(nl, specs, circuits):
        for ref, p in solved.resistor_power().items():
            worst[ref] = max(worst.get(ref, 0), p)
    rating = dict(specs.RESISTOR_POWER_W)
    default = rating["default"]
    # the parts of multi-element parts (names with a dot) and elements a scenario adds
    # by name (lower case) are not judged on their own
    bad = {
        r: round(p * 1e3, 1)
        for r, p in worst.items()
        if not r[0].islower() and "." not in r and p > 0.5 * rating.get(r, default)
    }
    top = sorted(worst.items(), key=lambda kv: -kv[1])[:4]
    record("hottest resistor mW", {r: round(p * 1e3, 2) for r, p in top})
    assert not bad, f"over 50% of rating (mW): {bad}"


def test_capacitor_voltage_derated(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Keep capacitors under 80 % of their rated voltage, polarised ones forward."""
    worst: dict[str, float] = {}
    caps = nl.by_kind("Device:C", "Device:C_Polarized")
    for solved in _stress_scenarios(nl, specs, circuits):
        for ref in caps:
            v = solved[nl.net(ref, 1)] - solved[nl.net(ref, 2)]
            if nl.kind(ref) == "Device:C_Polarized":
                assert v >= -0.3, f"{ref} reverse biased ({v:.2f} V)"
            worst[ref] = max(worst.get(ref, 0), abs(v))
    rated_by_mpn = specs.CAP_VRATED
    bad = {}
    for ref, v in worst.items():
        mpn = nl.parts[ref]["fields"].get("MPN", "")
        rated = rated_by_mpn.get(mpn)
        assert rated, f"no voltage rating on file for {ref} ({mpn})"
        if v > 0.8 * rated:
            bad[ref] = (round(v, 2), rated)
    top = sorted(worst, key=lambda r: -worst[r])[:4]
    record(
        "cap stress (V, rated)",
        {
            r: (round(worst[r], 2), rated_by_mpn[nl.parts[r]["fields"]["MPN"]])
            for r in top
        },
    )
    assert not bad, bad


def test_led_currents(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Run each LED at a current that lights it without harming it or its driver."""
    solved = circuits.scenario(nl, **specs.LED_SCENARIO).solve()
    got = {r: solved.diode_current(r) for r in specs.LED_REFS}
    record("LED mA", {k: round(v * 1e3, 2) for k, v in got.items()})
    for ref, amps in got.items():
        assert specs.LED_I_MIN <= amps <= specs.LED_I_MAX, (
            f"{ref} at {amps * 1e3:.2f} mA"
        )


def test_supply_pins_in_range(
    nl: Netlist, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Hold each supply pin in its datasheet range and the design inside its limits."""
    solved = circuits.scenario(nl, **specs.SUPPLY_SCENARIO).solve()
    problems = []
    for ref, pin, (low, high) in specs.SUPPLY_PINS:
        v = solved[nl.net(ref, pin)]
        if not low <= v <= high:
            problems.append(
                f"{ref} pin {pin} sees {v:.2f} V, allowed {low} to {high} V"
            )
    for entry in specs.SUPPLY_LIMITS:
        what, value, op, limit = entry(nl) if callable(entry) else entry
        if op not in ("<", "<="):
            raise ValueError(f"SUPPLY_LIMITS: {what!r}: operator {op!r} is not < or <=")
        if not (value < limit if op == "<" else value <= limit):
            problems.append(f"{what}: {value:g} {op} {limit:g} is false")
    assert not problems, problems


# ------------------------------------------------------------------ I2C
def test_i2c_addresses_unique_per_bus(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Give every device on an I2C bus its own address, general-call ones included."""
    devices = circuits.i2c_devices(nl)
    table: dict[str, dict[str, str]] = {}
    for bus, (sda, _scl, _hz, offboard) in specs.I2C_BUSES.items():
        devs = [(name, addr) for net, name, addr in devices if net == sda]
        devs += [(n, a) for n, a, _ in offboard]
        addrs = [a for _, a in devs]
        table[bus] = {n: hex(a) for n, a in devs}
        assert len(addrs) == len(set(addrs)), (
            f"address clash on bus {bus}: {table[bus]}"
        )
    record("i2c", table)


def test_i2c_single_pullup_and_sink_current(
    nl: Netlist, bus: str, specs: ProjectModule
) -> None:
    """Pull each I2C line up with one resistor the bus speed's sink current allows."""
    sda, scl, hz, _ = specs.I2C_BUSES[bus]
    rail = getattr(specs, "I2C_PULLUP_NET", "+3V3")
    vdd = getattr(specs, "I2C_VDD", 3.3)
    for line in (sda, scl):
        pulls = [
            r
            for r in nl.by_kind("Device:R")
            if {nl.net(r, 1), nl.net(r, 2)} == {line, rail}
        ]
        assert len(pulls) == 1, f"{line} pull-ups: {pulls}"
        rmin = (vdd - 0.4) / specs.I2C_IOL[hz]
        assert parse_value(nl.parts[pulls[0]]["value"]) >= rmin
