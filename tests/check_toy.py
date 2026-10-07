"""A toy board project for the check tests: a netlist, specs and circuits, all generic.

The board is an MCU module (A1) with an LED on one GPIO, an I2C sensor (U1) with its
pull-ups, a boot-strap pull-up, a divider that brings a 5 V rail down to an analogue
input, and a decoupling capacitor. Everything the built-in ``circuit`` and ``esp32s3``
checks read is here, and ``good()`` passes all of them. A test makes a mistake by
changing the ``Toy`` (a resistor value, a pin's net) or the specs text, then runs the
checks and sees the one that should catch it fail.

``netlist_text`` writes the toy as KiCad 10 writes a netlist, so ``Netlist.from_text``
reads it as it reads the real thing. ``write_project`` puts a project on disk: a
``pcbkit.toml``, ``specs.py``, ``circuits.py``, the netlist as ``toy.net`` and
``fake_nl.py``, a pytest plugin that serves that netlist as the ``nl`` fixture so no
KiCad is needed (load it with ``-p fake_nl`` after ``-p pcbkit.check.plugin``: a later
plugin's fixture wins).
"""

from __future__ import annotations

import copy
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

TOML = """\
[board]
stem = "toy"
title = "Toy Board"
rev = "A"
fab_name = "Toy_revA"

[checks]
groups = {groups}
"""

# KiCad's own placeholder for a pin with no net is "unconnected-(...)"; a pin the
# schematic does not use at all is simply not listed.


@dataclass
class Toy:
    """The parts of the toy board and the net and function of each of their pins."""

    parts: dict[str, tuple[str, str, str, dict[str, str]]] = field(default_factory=dict)
    pins: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)

    def add(
        self,
        ref: str,
        value: str,
        lib_part: str,
        pins: dict[str, tuple[str, str]],
        **fields: str,
    ) -> None:
        """Add a part: ``lib_part`` is "Device:R"; ``pins``: pad to (net, function)."""
        lib, _, part = lib_part.partition(":")
        self.parts[ref] = (value, lib, part, fields)
        self.pins[ref] = dict(pins)

    def set_value(self, ref: str, value: str) -> None:
        """Change a part's value, as editing the schematic would."""
        _, lib, part, fields = self.parts[ref]
        self.parts[ref] = (value, lib, part, fields)

    def move_pin(self, ref: str, pad: str, net: str | None) -> None:
        """Move one pad to another net, or take it off every net (None)."""
        func = self.pins[ref][pad][1]
        if net is None:
            del self.pins[ref][pad]
        else:
            self.pins[ref][pad] = (net, func)

    def copy(self) -> Toy:
        """Return an independent copy."""
        return copy.deepcopy(self)


def resistor(toy: Toy, ref: str, value: str, a: str, b: str) -> None:
    """Add a resistor between two nets."""
    toy.add(ref, value, "Device:R", {"1": (a, "~"), "2": (b, "~")}, MPN=f"RES-{value}")


def good() -> Toy:
    """Return the toy board with nothing wrong with it."""
    toy = Toy()
    # The MCU module: header pins 1..6 carry GPIO4, 5, 6, 1, 0 and 35, as in
    # specs.DEVKIT_GPIO.
    toy.add(
        "A1",
        "MCU module",
        "Module:MCU",
        {
            "1": ("LED_DRV", "IO4"),
            "2": ("SDA", "IO5"),
            "3": ("SCL", "IO6"),
            "4": ("SENSE", "IO1"),
            "5": ("BOOT", "IO0"),
            "7": ("+3V3", "3V3"),
            "8": ("GND", "GND"),
        },
        MPN="MODULE-1",
    )
    resistor(toy, "R1", "330", "LED_DRV", "LED_A")
    toy.add("D1", "Red", "Device:LED", {"1": ("GND", "K"), "2": ("LED_A", "A")})
    # I2C: the sensor, its address pin strapped low, one pull-up per line.
    toy.add(
        "U1",
        "SENSOR",
        "Sensor:S1",
        {
            "1": ("+3V3", "VDD"),
            "2": ("GND", "GND"),
            "3": ("SDA", "SDA"),
            "4": ("SCL", "SCL"),
            "5": ("GND", "ADDR"),
        },
        MPN="SENSOR-1",
    )
    resistor(toy, "R2", "4.7k", "SDA", "+3V3")
    resistor(toy, "R3", "4.7k", "SCL", "+3V3")
    # a 5 V rail divided to the analogue input, and the boot pull-up
    resistor(toy, "R4", "100k", "VIN", "SENSE")
    resistor(toy, "R5", "47k", "SENSE", "GND")
    resistor(toy, "R6", "10k", "BOOT", "+3V3")
    toy.add(
        "C1",
        "10u",
        "Device:C",
        {"1": ("+3V3", "~"), "2": ("GND", "~")},
        MPN="CAP-10U",
    )
    return toy


def netlist_text(toy: Toy) -> str:
    """Return the toy as a KiCad 10 netlist file."""
    comps = []
    for ref, (value, lib, part, fields) in toy.parts.items():
        field_text = "".join(
            f'\n        (field (name "{k}") "{v}")' for k, v in fields.items()
        )
        comps.append(
            f"""    (comp
      (ref "{ref}")
      (value "{value}")
      (footprint "Pkg:{part}")
      (fields{field_text}
      )
      (libsource (lib "{lib}") (part "{part}") (description ""))
    )"""
        )
    by_net: dict[str, list[tuple[str, str, str]]] = {}
    for ref, pads in toy.pins.items():
        for pad, (net, func) in pads.items():
            by_net.setdefault(net, []).append((ref, pad, func))
    nets = []
    for code, (net, nodes) in enumerate(sorted(by_net.items()), start=1):
        node_text = "".join(
            f'\n      (node (ref "{ref}") (pin "{pad}") (pinfunction "{func}_{pad}")'
            ' (pintype "passive"))'
            for ref, pad, func in nodes
        )
        nets.append(f'    (net (code "{code}") (name "/{net}"){node_text}\n    )')
    return (
        '(export (version "E")\n  (design (source "toy.kicad_sch") '
        '(date "2026-01-01T00:00:00") (tool "Eeschema 10.0.6"))\n  (components\n'
        + "\n".join(comps)
        + "\n  )\n  (nets\n"
        + "\n".join(nets)
        + "\n  )\n)\n"
    )


SPECS = '''\
"""Specs of the toy board."""
from __future__ import annotations

import itertools

# --- esp32s3 pack
DEVKIT_REF = "A1"
DEVKIT_GPIO = {1: 4, 2: 5, 3: 6, 4: 1, 5: 0, 6: 35}
ANALOG_NETS = ("SENSE",)
STRAP_SCENARIO = {}
GPIO_SOURCE_SCENARIO = {}
GPIO_SCENARIOS = {
    f"{vin}-{led}": dict(vin=vin, drive={"LED_DRV": 3.3} if led else {})
    for vin, led in itertools.product((5.0, 5.5), (False, True))
}
I2C_BUSES = {"main": ("SDA", "SCL", 400e3, [("DISPLAY", 0x3C, 10)])}

# --- circuit group
PINOUT = {"U1": {"1": "VDD", "2": "GND", "3": "SDA", "4": "SCL"}}
PIN_ALIASES = {}
STRESS_SCENARIOS = [dict(vin=5.0, drive={"LED_DRV": 3.3}), dict(vin=5.5)]
RESISTOR_POWER_W = {"default": 0.100}
CAP_VRATED = {"CAP-10U": 10.0}
LED_REFS = ("D1",)
LED_SCENARIO = dict(vin=5.0, drive={"LED_DRV": 3.3})
LED_I_MIN = 0.3e-3
LED_I_MAX = 0.020
SUPPLY_SCENARIO = dict(vin=5.0)
SUPPLY_PINS = [("U1", 1, (2.7, 5.5))]
SUPPLY_LIMITS = [("the rail is under the regulator's input limit", 5.5, "<=", 6.0)]
I2C_IOL = {400e3: 3e-3}
'''

CIRCUITS = '''\
"""Operating points of the toy board."""
from __future__ import annotations

from pcbkit.check.dc import build


def scenario(nl, vin=5.0, drive=None):
    """Return the toy's circuit: 3.3 V and ground held, VIN set, extra nets driven."""
    circuit = build(nl)
    circuit.fix("GND", 0.0).fix("+3V3", 3.3).fix("VIN", vin)
    for net, volts in (drive or {}).items():
        circuit.fix(net, volts)
    return circuit


def i2c_devices(nl):
    """Return (SDA net, name, address) of the on-board I2C devices."""
    return [(nl.net("U1", 3), "SENSOR", 0x40)]
'''

FAKE_NL = '''\
"""Serve the toy netlist as the nl fixture, so a run needs no KiCad."""
from pathlib import Path

import pytest

from pcbkit.check.netlist import Netlist


@pytest.fixture(scope="session")
def nl():
    return Netlist.from_file(Path(__file__).with_name("toy.net"))
'''


def write_project(
    root: Path,
    toy: Toy,
    groups: list[str],
    specs: str = SPECS,
    circuits: str = CIRCUITS,
) -> Path:
    """Write the toy project into ``root`` and return it.

    ``groups`` is the ``[checks] groups`` list. ``specs`` and ``circuits`` default to
    the toy's own; a test passes a changed text to plant a mistake in the specs.
    """
    root.mkdir(parents=True, exist_ok=True)
    quoted = "[" + ", ".join(f'"{g}"' for g in groups) + "]"
    (root / "pcbkit.toml").write_text(TOML.format(groups=quoted), encoding="utf-8")
    (root / "specs.py").write_text(specs, encoding="utf-8")
    (root / "circuits.py").write_text(circuits, encoding="utf-8")
    (root / "toy.net").write_text(netlist_text(toy), encoding="utf-8")
    (root / "fake_nl.py").write_text(textwrap.dedent(FAKE_NL), encoding="utf-8")
    return root
