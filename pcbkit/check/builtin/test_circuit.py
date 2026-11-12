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
  watts}``; ``CAP_VRATED``: capacitor MPN to rated volts; optional ``CAP_DERATE``:
  ``{mpn: fraction}``, a share of the rating tighter than 0.8 (at most 0.8) for a part
  whose class needs it.
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
from typing import Any

import pytest

from pcbkit.check import tables
from pcbkit.check.netlist import Netlist, natkey, parse_value
from pcbkit.check.plugin import ProjectModule, spec_params
from pcbkit.check.stress import CAP_DERATE_DEFAULT, stress_attempts, stress_solutions

# Symbol pin names that mean "no name": the checks skip them.
GENERIC_ALIASES: dict[str, str | None] = {"~": None, "": None}


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the pinout and I2C checks from the project's tables."""
    spec_params(metafunc, "ref", lambda specs: sorted(specs.PINOUT))
    spec_params(metafunc, "bus", lambda specs: sorted(specs.I2C_BUSES))


def _plain(value: Any) -> Any:
    """Return a numpy scalar as the Python number it holds, anything else as it is.

    ``results.json`` is written with ``default=str``, which would turn a numpy bool or
    a 32-bit float into text (``"False"``, truthy to a reader).
    """
    item = getattr(value, "item", None)
    return item() if callable(item) and type(value).__module__ == "numpy" else value


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
def _unique(table: dict[str, Any], key: str) -> str:
    """Return ``key``, or ``key #2`` and so on when a row already took it."""
    if key not in table:
        return key
    n = 2
    while f"{key} #{n}" in table:
        n += 1
    return f"{key} #{n}"


def test_resistor_power_derated(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Keep every resistor under half its power rating at the worst operating point.

    Records ``hottest resistor mW`` (the four hottest) and ``resistor power``: for each
    judged resistor, ``{"mW", "limit_mW", "scenario"}``, where ``scenario`` is the index
    into ``STRESS_SCENARIOS`` of its worst case.
    """
    worst: dict[str, tuple[float, int]] = {}
    for index, solved in stress_solutions(nl, specs, circuits):
        for ref, p in solved.resistor_power().items():
            if ref not in worst or p > worst[ref][0]:
                worst[ref] = (p, index)
    rating = dict(specs.RESISTOR_POWER_W)
    default = rating["default"]
    # the parts of multi-element parts (names with a dot) and elements a scenario adds
    # by name (lower case) are not judged on their own
    judged = [r for r in worst if not r[0].islower() and "." not in r]
    bad = {
        r: round(worst[r][0] * 1e3, 1)
        for r in worst
        if r in judged and worst[r][0] > 0.5 * rating.get(r, default)
    }
    top = sorted(worst.items(), key=lambda kv: -kv[1][0])[:4]
    record("hottest resistor mW", {r: round(p * 1e3, 2) for r, (p, _) in top})
    record(
        "resistor power",
        {
            r: {
                "mW": _plain(round(worst[r][0] * 1e3, 2)),
                "limit_mW": _plain(round(0.5 * rating.get(r, default) * 1e3, 2)),
                "scenario": worst[r][1],
            }
            for r in sorted(judged, key=natkey)
        },
    )
    assert not bad, f"over 50% of rating (mW): {bad}"


def test_capacitor_voltage_derated(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Keep capacitors under 80 % of their rated voltage, polarised ones forward.

    ``CAP_DERATE`` may set a tighter share for a part number. Records ``cap stress (V,
    rated)`` (the four most stressed, only when every capacitor has a rating and none is
    reverse biased) and
    ``capacitor voltage``: for each capacitor, ``{"v", "rated", "target", "limit_v",
    "mpn", "scenario"}``, before any failure is raised. ``rated`` is null when the table
    has no row, ``target`` is the share of the rating allowed, ``limit_v`` is ``target``
    times ``rated`` (null when unrated) and ``scenario`` the index into
    ``STRESS_SCENARIOS`` of the worst case. When a reverse-biased capacitor has been
    found, a later scenario that cannot be solved is left out of the worst cases and its
    index is recorded as ``unsolved scenarios``.

    A failure is raised in the order it always was: a reverse-biased polarised
    capacitor, then a missing rating, then one over its target.
    """
    derate = tables.read(specs, "CAP_DERATE") if "CAP_DERATE" in specs else {}
    worst: dict[str, tuple[float, int]] = {}
    reverse: tuple[str, float] | None = None  # the first one found, as the old assert
    unsolved: list[int] = []
    caps = nl.by_kind("Device:C", "Device:C_Polarized")
    for index, solved, error in stress_attempts(nl, specs, circuits):
        if error is not None:
            # The check used to stop at the first reverse-biased capacitor, so a later
            # scenario that cannot be solved never changed what it said; it still does
            # not, but the record names it. Before one is found the error is raised.
            if reverse is None:
                raise error
            unsolved.append(index)
            continue
        try:
            volts = {
                ref: solved[nl.net(ref, 1)] - solved[nl.net(ref, 2)] for ref in caps
            }
        except Exception:  # noqa: BLE001 - a scenario that reads no voltage is unsolved
            if reverse is None:
                raise
            unsolved.append(index)
            continue
        for ref, v in volts.items():
            if (
                reverse is None
                and nl.kind(ref) == "Device:C_Polarized"
                and not v >= -0.3
            ):
                reverse = (ref, v)
            if ref not in worst or abs(v) > worst[ref][0]:
                worst[ref] = (abs(v), index)
    rated_by_mpn = specs.CAP_VRATED
    mpns = {r: nl.parts[r]["fields"].get("MPN", "") for r in worst}
    rated = {r: rated_by_mpn.get(mpns[r]) for r in worst}
    target = {r: derate.get(mpns[r], CAP_DERATE_DEFAULT) for r in worst}
    if reverse is None and all(rated.values()):
        # recorded first, as before the per-part table was added, and only when the
        # check got this far
        top = sorted(worst, key=lambda r: -worst[r][0])[:4]
        record(
            "cap stress (V, rated)",
            {r: (round(worst[r][0], 2), rated[r]) for r in top},
        )
    record(
        "capacitor voltage",
        {
            r: {
                "v": _plain(round(worst[r][0], 2)),
                "rated": _plain(rated[r]) or None,
                "target": _plain(target[r]),
                "limit_v": _plain(round(target[r] * rated[r], 4)) if rated[r] else None,
                "mpn": mpns[r],
                "scenario": worst[r][1],
            }
            for r in sorted(worst, key=natkey)
        },
    )
    if unsolved:
        record("unsolved scenarios", unsolved)
    if reverse is not None:
        ref, v = reverse
        assert v >= -0.3, f"{ref} reverse biased ({v:.2f} V)"
    for ref in worst:
        assert rated[ref], f"no voltage rating on file for {ref} ({mpns[ref]})"
    bad = {
        ref: (round(v, 2), rated[ref])
        for ref, (v, _) in worst.items()
        if v > target[ref] * rated[ref]
    }
    unrated = sorted(m for m in derate if m not in rated_by_mpn)
    assert not unrated, f"CAP_DERATE names {unrated}, which have no row in CAP_VRATED"
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
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Hold each supply pin in its datasheet range and the design inside its limits.

    Records ``supply pins`` (``{"U1 pad 1": {"v", "min", "max"}}``) and ``supply
    limits`` (``{what: {"value", "op", "limit", "ok"}}``, a row with an operator that is
    not ``<`` or ``<=`` included, as not ok) before any failure is raised. Numbers are
    recorded as Python numbers, a numpy scalar as the value it holds.
    """
    solved = circuits.scenario(nl, **specs.SUPPLY_SCENARIO).solve()
    problems = []
    pins: dict[str, Any] = {}
    for ref, pin, (low, high) in specs.SUPPLY_PINS:
        v = solved[nl.net(ref, pin)]
        pins[_unique(pins, f"{ref} pad {pin}")] = {
            "v": _plain(round(v, 3)),
            "min": _plain(low),
            "max": _plain(high),
        }
        if not low <= v <= high:
            problems.append(
                f"{ref} pin {pin} sees {v:.2f} V, allowed {low} to {high} V"
            )
    limits: dict[str, Any] = {}
    bad_operator = None
    for entry in specs.SUPPLY_LIMITS:
        what, value, op, limit = entry(nl) if callable(entry) else entry
        valid = op in ("<", "<=")
        if not valid:
            bad_operator = bad_operator or (
                f"SUPPLY_LIMITS: {what!r}: operator {op!r} is not < or <="
            )
        # a row with a bad operator is recorded too, as not ok
        ok = valid and bool(value < limit if op == "<" else value <= limit)
        limits[_unique(limits, what)] = {
            "value": _plain(value),
            "op": op,
            "limit": _plain(limit),
            "ok": ok,
        }
        if valid and not ok:
            problems.append(f"{what}: {value:g} {op} {limit:g} is false")
    record("supply pins", pins)
    record("supply limits", limits)
    if bad_operator is not None:
        raise ValueError(bad_operator)
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
