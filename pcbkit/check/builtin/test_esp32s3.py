"""ESP32-S3 DevKitC checks: pin use, strapping levels, GPIO limits.

Switched on by ``esp32s3`` in ``[checks] groups``. The chip's facts are in
``pcbkit.check.packs.esp32s3`` (the project's ``specs.py`` may override them). What the
project must give:

* ``specs.DEVKIT_REF`` and ``specs.DEVKIT_GPIO``: the DevKit's reference and its header
  pin to GPIO table.
* ``specs.ANALOG_NETS``: the nets that carry analogue inputs.
* ``specs.STRAP_SCENARIO``, ``specs.GPIO_SOURCE_SCENARIO``: keyword arguments for the
  project's ``circuits.scenario(nl, **kw)`` that describe the operating conditions to
  judge the strapping pins and the pin source currents in; ``specs.GPIO_SCENARIOS``:
  ``{id: keywords}`` for the grid of conditions a pin must never exceed its absolute
  maximum in.
* ``circuits.scenario``: builds an unsolved ``pcbkit.check.dc.Circuit``.

Every check reads the netlist KiCad exports from the schematic.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check.netlist import Netlist
from pcbkit.check.packs import esp32s3
from pcbkit.check.plugin import ProjectModule, spec_params


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the over-voltage check from the project's scenario grid."""
    spec_params(
        metafunc,
        "case",
        lambda specs: list(specs.GPIO_SCENARIOS.items()),
        ids=lambda item: str(item[0]),
    )


def test_esp32_reserved_and_uart0_pins_unconnected(
    nl: Netlist, specs: ProjectModule
) -> None:
    """Keep PSRAM, the RGB LED, native USB and UART0 pins free of the board's nets."""
    chip = esp32s3.chip(specs)
    nets = esp32s3.gpio_nets(nl, specs)
    used = sorted((chip.reserved | chip.uart0) & set(nets))
    assert not used, f"GPIO {used} must stay free (PSRAM / RGB LED / USB / serial)"


def test_analog_inputs_are_on_adc1(nl: Netlist, specs: ProjectModule) -> None:
    """Put analogue inputs on ADC1: ADC2 is unusable while Wi-Fi or ESP-NOW runs."""
    chip = esp32s3.chip(specs)
    by_net = {n: g for g, n in esp32s3.gpio_nets(nl, specs).items()}
    for net in specs.ANALOG_NETS:
        gpio = by_net.get(net)
        assert gpio is not None, f"{net} is not on a DevKit GPIO"
        assert gpio in chip.adc1, f"{net} is on GPIO{gpio}, which is not an ADC1 pin"


def test_esp32_input_only_pins_have_a_defined_level(
    nl: Netlist, specs: ProjectModule
) -> None:
    """Give every input-only pin a pull; the S3 has none, so this is a no-op there."""
    chip = esp32s3.chip(specs)
    nets = esp32s3.gpio_nets(nl, specs)
    for gpio in chip.input_only & set(nets):
        refs = nl.refs_on(nets[gpio]) - {specs.DEVKIT_REF}
        assert any(r.startswith("R") for r in refs), (
            f"GPIO{gpio} ({nets[gpio]}) floats: add a pull"
        )


def test_strapping_pins_at_reset(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Read every strapping pin at the level it needs at reset, pulls counted."""
    chip = esp32s3.chip(specs)
    nets = esp32s3.gpio_nets(nl, specs)
    circuit = circuits.scenario(nl, **specs.STRAP_SCENARIO)
    esp32s3.add_strap_pulls(circuit, nl, specs)
    solved = circuit.solve()
    rows: dict[str, Any] = {}
    for gpio, (need, _) in chip.straps.items():
        if gpio not in nets:
            rows[f"GPIO{gpio}"] = "unconnected (internal pull)"
            continue
        v = solved[nets[gpio]]
        rows[f"GPIO{gpio}"] = round(v, 2)
        if need == "high":
            assert v >= chip.vih, f"GPIO{gpio} must read high at reset, sees {v:.2f} V"
        else:
            assert v <= chip.vil, f"GPIO{gpio} must read low at reset, sees {v:.2f} V"
    record("strap levels", rows)


def test_no_esp32_gpio_above_abs_max(
    nl: Netlist, specs: ProjectModule, circuits: ProjectModule, case: Any
) -> None:
    """Keep every GPIO under VDD + 0.3 V in each operating condition of the grid."""
    _, keywords = case
    chip = esp32s3.chip(specs)
    nets = esp32s3.gpio_nets(nl, specs)
    solved = circuits.scenario(nl, **keywords).solve()
    over = {
        g: round(solved[n], 2) for g, n in nets.items() if solved[n] > chip.pin_abs_max
    }
    assert not over, f"GPIO above {chip.pin_abs_max:.1f} V: {over}"


def test_esp32_gpio_source_current(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Keep the current any GPIO sources into the board under its limit."""
    chip = esp32s3.chip(specs)
    nets = esp32s3.gpio_nets(nl, specs)
    open_drain = getattr(specs, "OPEN_DRAIN_NETS", None)
    if open_drain is None:
        buses = getattr(specs, "I2C_BUSES", {})
        open_drain = [n for sda, scl, *_ in buses.values() for n in (sda, scl)]
    worst: dict[str, float] = {}
    for gpio, net in nets.items():
        if gpio in chip.input_only or net in open_drain:
            continue
        circuit = circuits.scenario(
            nl, **specs.GPIO_SOURCE_SCENARIO, drive={net: chip.vdd}
        )
        solved = circuit.solve()
        amps = sum(
            (solved[net] - solved[b if a == net else a]) / r
            for a, b, r, _ in circuit.res
            if net in (a, b)
        )
        amps += sum(
            solved.diode_current(ref) for a, _, _, _, ref in circuit.diodes if a == net
        )
        worst[f"GPIO{gpio}"] = amps
    top = sorted(worst.items(), key=lambda kv: -kv[1])[:3]
    record("GPIO source mA (top 3)", {k: round(v * 1e3, 2) for k, v in top})
    assert max(worst.values()) <= chip.gpio_source_max
