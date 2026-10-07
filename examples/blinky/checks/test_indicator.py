"""Blinky's own checks: the LED's current, and the resistor that sets it.

pcbkit runs every test_*.py in this folder after the built-in checks. The fixtures `nl`
(the netlist KiCad exports from the schematic), `specs` (specs.py) and `record` (numbers
for the report) come from pcbkit's check plugin. The checks read the netlist, never
the board, so a mistake in design.py is caught the moment the schematic is rebuilt.
"""

from __future__ import annotations

from typing import Any

from pcbkit.check import dc
from pcbkit.check.netlist import Netlist, parse_value
from pcbkit.check.plugin import ProjectModule


def solve(nl: Netlist, specs: ProjectModule, vin: float, vf: float) -> dc.Solution:
    """Return the DC operating point with VIN at ``vin`` and every LED dropping ``vf``.

    Built here from the netlist and specs.py rather than with ``dc.build``, which gives
    an LED a forward voltage by colour (2.9 V for green) and 20 ohm, where this LED's
    datasheet says 2.2 V typical and 2.5 V at most. Resistors take their values from
    the netlist. An LED is a fixed forward voltage plus 1 ohm, which the solver wants
    above zero.
    """
    circuit = dc.Circuit()
    circuit.fix(specs.SUPPLY_NET, vin).fix(specs.GROUND_NET, 0.0)
    for ref in nl.by_kind("Device:R"):
        circuit.R(
            nl.net(ref, 1), nl.net(ref, 2), parse_value(nl.parts[ref]["value"]), ref
        )
    for ref in nl.by_kind("Device:LED"):
        # KiCad's LED symbol: pin 1 is the cathode, pin 2 the anode
        circuit.D(nl.net(ref, 2), nl.net(ref, 1), vf, 1.0, ref)
    return circuit.solve()


def test_led_current_window(nl: Netlist, specs: ProjectModule, record: Any) -> None:
    """Light every LED at the lowest supply and keep it under its limit at the highest.

    The dimmest case is the lowest VIN with the highest forward voltage, the brightest
    the highest VIN with the lowest forward voltage. A reversed LED, or a supply that
    cannot reach the forward voltage, carries no current and fails the low end.
    """
    low, high = specs.SUPPLY_V
    cases = {"dimmest": (low, specs.LED_VF_MAX), "brightest": (high, specs.LED_VF_MIN)}
    leds = nl.by_kind("Device:LED")
    assert leds, "the design has no LED"
    found: dict[str, float] = {}
    for name, (vin, vf) in cases.items():
        solved = solve(nl, specs, vin, vf)
        for ref in leds:
            found[f"{ref} {name}"] = solved.diode_current(ref)
    record("LED mA", {key: round(amps * 1e3, 2) for key, amps in found.items()})
    bad = {
        key: round(amps * 1e3, 2)
        for key, amps in found.items()
        if not specs.LED_I_MIN <= amps <= specs.LED_I_MAX
    }
    assert not bad, (
        f"LED current (mA) outside {specs.LED_I_MIN * 1e3:g} to "
        f"{specs.LED_I_MAX * 1e3:g} mA: {bad}"
    )


def test_resistor_power_margin(nl: Netlist, specs: ProjectModule, record: Any) -> None:
    """Keep every resistor under half its power rating, at the brightest case."""
    solved = solve(nl, specs, specs.SUPPLY_V[1], specs.LED_VF_MIN)
    rating = dict(specs.RESISTOR_POWER_W)
    power = solved.resistor_power()
    record("resistor mW", {ref: round(watts * 1e3, 1) for ref, watts in power.items()})
    bad = {
        ref: round(watts * 1e3, 1)
        for ref, watts in power.items()
        if watts > 0.5 * rating.get(ref, rating["default"])
    }
    assert not bad, f"over half the power rating (mW): {bad}"
