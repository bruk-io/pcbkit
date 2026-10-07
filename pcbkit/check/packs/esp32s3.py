"""ESP32-S3 rules for a board that carries an ESP32-S3-DevKitC-1 in sockets.

The facts here are the chip's and the DevKit's, not any board's: which GPIOs belong to
the octal PSRAM, the on-board RGB LED, native USB and the UART bridge, which ones the
ADC can read while Wi-Fi runs, which are strapping pins and what each must see at
reset, what level a pin may take and how much current it may source (Espressif's
ESP32-S3 datasheet and the DevKitC-1 user guide, ESP-IDF's GPIO summary). They are the
defaults; a project can override any of them by defining the same name in its
``specs.py`` (``RESERVED``, ``UART0``, ``ADC1``, ``INPUT_ONLY``, ``STRAPS``,
``ESP32_VDD``, ``ESP32_VIH``, ``ESP32_VIL``, ``ESP32_VOH_MIN``, ``ESP32_PIN_ABS_MAX``,
``ESP32_GPIO_SOURCE_MAX``, ``ESP32_INTERNAL_PULL``).

What a board must say itself, because it is how the board was drawn:

* ``DEVKIT_REF``: the reference of the DevKit's part in the schematic.
* ``DEVKIT_GPIO``: its header pin number (as the board's own symbol numbers them) to the
  GPIO on that pin.

The checks of the pack are in ``pcbkit.check.builtin.test_esp32s3``; they are switched
on with ``esp32s3`` in ``[checks] groups``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pcbkit.check.dc import Circuit
from pcbkit.check.netlist import Netlist

DEFAULT_VDD = 3.3
# ADC2 (GPIO11-20) is unusable while Wi-Fi or ESP-NOW runs: analogue inputs go on ADC1.
DEFAULT_ADC1 = frozenset(range(1, 11))
# The S3 has no input-only pins; the check is kept for profiles that do.
DEFAULT_INPUT_ONLY: frozenset[int] = frozenset()
# GPIO35-37 octal PSRAM on N8R8 / N16R8 modules; GPIO38 the on-board RGB LED (v1.1);
# GPIO19/20 native USB (USB-JTAG / CDC); GPIO26-32 flash (not brought out).
DEFAULT_RESERVED = frozenset({35, 36, 37, 38, 19, 20})
# The USB-UART bridge on the DevKit's "UART" port.
DEFAULT_UART0 = frozenset({43, 44})
# Strapping pins and the level each must see at reset, with the internal pull that
# applies when nothing else drives the pin:
#   GPIO0 high = SPI boot; GPIO46 must be low for download mode (0/1 is invalid);
#   GPIO45 low = 3.3 V VDD_SPI (WROOM-1 flash is 3.3 V). GPIO3 is a strapping pin only
#   for the JTAG source and only once EFUSE_STRAP_JTAG_SEL is burned, which it is not
#   on a DevKit, so it is left out.
DEFAULT_STRAPS = {
    0: ("high", "pullup"),
    45: ("low", "pulldown"),
    46: ("low", "pulldown"),
}
DEFAULT_INTERNAL_PULL = 45e3  # about 45 k typical
DEFAULT_GPIO_SOURCE_MAX = 0.020  # keep per-pin source current at or under 20 mA


@dataclass(frozen=True)
class Chip:
    """The ESP32-S3 numbers the checks compare a design against (V, A, ohm)."""

    vdd: float
    vih: float  # datasheet: VIH min 0.75 x VDD
    vil: float  # datasheet: VIL max 0.25 x VDD
    voh_min: float  # datasheet: VOH min 0.8 x VDD
    pin_abs_max: float  # no pin is 5 V tolerant: VDD + 0.3 V
    gpio_source_max: float
    internal_pull: float
    adc1: frozenset[int]
    input_only: frozenset[int]
    reserved: frozenset[int]
    uart0: frozenset[int]
    straps: Mapping[int, tuple[str, str]]


def chip(specs: Any) -> Chip:
    """Return the chip's numbers: the pack's defaults, then the project's overrides."""

    def pick(name: str, default: Any) -> Any:
        return getattr(specs, name, default)

    vdd = pick("ESP32_VDD", DEFAULT_VDD)
    return Chip(
        vdd=vdd,
        vih=pick("ESP32_VIH", 0.75 * vdd),
        vil=pick("ESP32_VIL", 0.25 * vdd),
        voh_min=pick("ESP32_VOH_MIN", 0.8 * vdd),
        pin_abs_max=pick("ESP32_PIN_ABS_MAX", vdd + 0.3),
        gpio_source_max=pick("ESP32_GPIO_SOURCE_MAX", DEFAULT_GPIO_SOURCE_MAX),
        internal_pull=pick("ESP32_INTERNAL_PULL", DEFAULT_INTERNAL_PULL),
        adc1=frozenset(pick("ADC1", DEFAULT_ADC1)),
        input_only=frozenset(pick("INPUT_ONLY", DEFAULT_INPUT_ONLY)),
        reserved=frozenset(pick("RESERVED", DEFAULT_RESERVED)),
        uart0=frozenset(pick("UART0", DEFAULT_UART0)),
        straps=dict(pick("STRAPS", DEFAULT_STRAPS)),
    )


def gpio_nets(nl: Netlist, specs: Any) -> dict[int, str]:
    """Return ``{gpio number: net}`` for each wired DevKit pin that carries a GPIO.

    Reads ``specs.DEVKIT_REF`` and ``specs.DEVKIT_GPIO``; a pin whose net is KiCad's
    placeholder for an unconnected pin is left out.
    """
    ref, table = specs.DEVKIT_REF, specs.DEVKIT_GPIO
    out = {}
    for pin, gpio in table.items():
        n = nl.net(ref, pin)
        if n and not n.startswith("unconnected"):
            out[gpio] = n
    return out


def add_strap_pulls(circuit: Circuit, nl: Netlist, specs: Any) -> None:
    """Add the chip's internal reset-time pulls on its strapping pins to a circuit.

    A wired strapping pin gets a resistor (``ESP32_INTERNAL_PULL``) to the 3.3 V rail
    (``RAIL_3V3``, default "+3V3") or to ground (``GROUND_NET``, default "GND"); an
    unwired one is left to its internal pull alone.
    """
    ch = chip(specs)
    nets = gpio_nets(nl, specs)
    high = getattr(specs, "RAIL_3V3", "+3V3")
    low = getattr(specs, "GROUND_NET", "GND")
    for gpio, (_, pull) in ch.straps.items():
        n = nets.get(gpio)
        if n is None:
            continue  # unconnected strapping pin: the internal pull alone decides
        rail = high if pull == "pullup" else low
        circuit.R(n, rail, ch.internal_pull, f"esp_int_pull_io{gpio}")
