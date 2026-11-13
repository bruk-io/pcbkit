"""Integration: the ``power`` group's semiconductor-voltage check, as ``pcbkit check``.

The generic toy board (``tests/check_toy.py``) gets a low-side FET Q1 (a ``2N7002``)
switching a load from VIN with its gate on the LED's GPIO, a Schottky D2 across VIN,
and part numbers for those and the LED; its specs get ``SEMI_VRATED`` and stress
scenarios that drive the GPIO high and then low. The real command line runs in a child
pytest with ``fake_nl`` serving the netlist, so no KiCad is needed. The control passes;
each planted mistake turns the check red, with the message that says why.
"""

from __future__ import annotations

from pathlib import Path

from tests import check_toy
from tests.check_toy import SPECS, Toy
from tests.integration.test_check_plugin import Run, run

CHECK = "test_semiconductor_voltage_derated"
TABLE = """
STRESS_SCENARIOS = [
    dict(vin=5.0, drive={"LED_DRV": 3.3}),
    dict(vin=5.5, drive={"LED_DRV": 0.0}),
]
SEMI_VRATED = {
    "FET-1": {"vds": 60, "vgs": 20},
    "SCH-1": {"vrrm": 40},
    "LED-1": {"vrrm": 5},
}
"""


def board() -> Toy:
    """Return the toy with Q1 switching a load, D2 across VIN, and the LED's MPN."""
    toy = check_toy.good()
    value, lib, part, fields = toy.parts["D1"]
    toy.parts["D1"] = (value, lib, part, {**fields, "MPN": "LED-1"})
    check_toy.resistor(toy, "R7", "1k", "VIN", "LOAD")
    check_toy.resistor(toy, "R8", "100", "LED_DRV", "GATE")
    check_toy.resistor(toy, "R9", "100k", "GATE", "GND")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("GATE", "G"), "2": ("GND", "S"), "3": ("LOAD", "D")},
        MPN="FET-1",
    )
    toy.add(
        "D2",
        "SS14",
        "Device:D_Schottky",
        {"1": ("VIN", "K"), "2": ("GND", "A")},
        MPN="SCH-1",
    )
    return toy


def go(tmp_path: Path, name: str, toy: Toy, specs: str = SPECS + TABLE) -> Run:
    """Run only this module's check, with the ``power`` group on."""
    root = check_toy.write_project(tmp_path / name, toy, ["power"], specs=specs)
    return run(root, "test_semis")


def said(entry: dict[str, object]) -> str:
    """Return what a failed check said: its message's first line, without the type."""
    first = str(entry["message"]).splitlines()[0]
    prefix = "AssertionError: "
    assert first.startswith(prefix), first
    return first[len(prefix) :]


def test_the_control_passes_and_records_every_judged_part(tmp_path: Path) -> None:
    """Pass; record Q1, D2 and the LED with their worst cases and limits."""
    result = go(tmp_path, "control", board())
    assert result.done.returncode == 0, result.done.stdout
    entry = result.entry(CHECK)
    assert entry["outcome"] == "passed"
    assert entry["gate"] == "sch"
    assert entry["group"] == "power"
    parts = entry["numbers"]["semiconductor voltage"]
    assert sorted(parts) == ["D1", "D2", "Q1"]
    assert parts["Q1"] == {
        "kind": "fet",
        "mpn": "FET-1",
        "target": 0.8,
        "vds": {"v": 5.5, "rated": 60, "limit_v": 48.0, "scenario": 1},
        "vgs": {"v": 3.297, "rated": 20, "limit_v": 16.0, "scenario": 0},
    }
    assert parts["D2"]["vr"] == {
        "v": 5.5,
        "rated": 40,
        "limit_v": 32.0,
        "scenario": 1,
        "polarity": "reverse",
    }
    assert entry["numbers"]["fet gates"]["Q1"][0] == {
        "scenario": 0,
        "vgs": 3.297,
        "vth": 2.0,
        "on": True,
    }
    assert parts["D1"]["kind"] == "diode"


def test_a_5_v_rating_on_a_fet_that_blocks_5_5_v_fails(tmp_path: Path) -> None:
    """The plan's plant, at the toy's voltage: 5.5 V against 0.8 of 5 V."""
    specs = SPECS + TABLE.replace('"vds": 60', '"vds": 5')
    entry = go(tmp_path, "low-rating", board(), specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert said(entry) == (
        "Q1 |Vds| reaches 5.50 V in STRESS_SCENARIOS[1], over 0.8 of its 5 V rating "
        "(4.00 V)"
    )
    assert entry["numbers"]["semiconductor voltage"]["Q1"]["vds"]["limit_v"] == 4.0


def test_a_missing_rating_fails_by_name_and_records_the_rest(tmp_path: Path) -> None:
    """No row for the Schottky's MPN: named; every part still recorded."""
    specs = SPECS + TABLE.replace('    "SCH-1": {"vrrm": 40},\n', "")
    entry = go(tmp_path, "unrated", board(), specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert said(entry) == (
        "no voltage rating on file for D2 (SCH-1): add a SEMI_VRATED row"
    )
    parts = entry["numbers"]["semiconductor voltage"]
    assert sorted(parts) == ["D1", "D2", "Q1"]
    assert parts["D2"]["vr"]["rated"] is None


def test_a_diode_dc_does_not_model_fails_by_name(tmp_path: Path) -> None:
    """A ``Device:D`` the toy's scenario does not model cannot be judged."""
    toy = board()
    toy.add("D3", "D", "Device:D", {"1": ("+3V3", "K"), "2": ("GND", "A")}, MPN="SCH-1")
    entry = go(tmp_path, "unmodelled", toy).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert said(entry).startswith(
        "D3 (Device:D) is not modelled on pad 2 (A) on GND, pad 1 (K) on +3V3 in "
        "STRESS_SCENARIOS[0]: dc.build does not model this symbol"
    )


def test_a_gate_left_floating_in_a_scenario_fails(tmp_path: Path) -> None:
    """The GPIO free in scenario 1 and no gate pull-down: the gate and the LED float."""
    toy = board()
    del toy.parts["R9"], toy.pins["R9"]
    specs = SPECS + TABLE.replace(
        'dict(vin=5.5, drive={"LED_DRV": 0.0})', "dict(vin=5.5)"
    )
    entry = go(tmp_path, "free-gpio", toy, specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert said(entry) == (
        "D1 cannot be judged in STRESS_SCENARIOS[1]: pad 2 (A) on LED_A is floating "
        "there (its 0 V is the solver's leak); fix the net that drives it in that "
        "scenario, or give it a pull; Q1 cannot be judged in STRESS_SCENARIOS[1]: "
        "pad 1 (G) on GATE is floating there (its 0 V is the solver's leak); fix the "
        "net that drives it in that scenario, or give it a pull"
    )


def test_a_free_gpio_with_the_controllers_pin_on_it_cannot_be_judged(
    tmp_path: Path,
) -> None:
    """R9 holds the free GPIO low, but the MCU pin on it is not modelled: named."""
    specs = SPECS + TABLE.replace(
        'dict(vin=5.5, drive={"LED_DRV": 0.0})', "dict(vin=5.5)"
    )
    entry = go(tmp_path, "pulled", board(), specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert said(entry).startswith(
        "D1 cannot be judged in STRESS_SCENARIOS[1]: the DC model leaves out A1 pad 1 "
        "(Module:MCU) on LED_DRV, on the nets that set its pads' levels"
    )


def test_an_external_rail_the_stress_rows_do_not_fix_fails(tmp_path: Path) -> None:
    """F-B1: the reviewer's +24V_EXT on J5, divided to ground.

    Fixed in the scenario, with the load on J5's output modelled by J5's reference, Q5
    is judged and its gate fails at 24 V against 0.8 of 12 V.
    """
    toy = board()
    toy.add(
        "J5",
        "CONN",
        "Connector:Conn_01x03",
        {"1": ("+24V_EXT", "Pin_1"), "2": ("GND", "Pin_2"), "3": ("OUT", "Pin_3")},
    )
    check_toy.resistor(toy, "R20", "100k", "+24V_EXT", "SENSE24")
    check_toy.resistor(toy, "R21", "10k", "SENSE24", "GND")
    toy.add(
        "Q5",
        "AO3401A",
        "Transistor_FET:AO3401A",
        {"1": ("Q5G", "G"), "2": ("+24V_EXT", "S"), "3": ("OUT", "D")},
        MPN="AO3401A",
    )
    check_toy.resistor(toy, "R22", "100k", "+24V_EXT", "Q5G")
    toy.add(
        "Q6",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("LED_DRV", "G"), "2": ("GND", "S"), "3": ("Q5G", "D")},
        MPN="FET-1",
    )
    check_toy.resistor(toy, "R23", "10k", "OUT", "GND")
    table = TABLE.replace(
        '"LED-1": {"vrrm": 5},',
        '"LED-1": {"vrrm": 5},\n    "AO3401A": {"vds": 30, "vgs": 12},',
    )
    entry = go(tmp_path, "rail", toy, SPECS + table).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "Q5 cannot be judged in STRESS_SCENARIOS[0]: the DC model leaves out J5 "
        in (said(entry))
    )
    fixed = check_toy.CIRCUITS.replace(
        "    return circuit\n",
        "    circuit.fix('+24V_EXT', 24.0)\n"
        "    circuit.R('OUT', 'GND', 1000.0, 'J5.load')\n"
        "    return circuit\n",
        1,
    )
    root = check_toy.write_project(
        tmp_path / "rail-fixed", toy, ["power"], SPECS + table, fixed
    )
    entry = run(root, "test_semis").entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "Q5 |Vgs| reaches 24.00 V in STRESS_SCENARIOS[0], over 0.8 of its 12 V" in (
        said(entry)
    )


def test_a_missing_table_fails_by_name(tmp_path: Path) -> None:
    """With ``power`` on and no ``SEMI_VRATED``, the check fails naming it."""
    entry = go(tmp_path, "missing", board(), SPECS).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "specs.SEMI_VRATED is not defined" in entry["message"]


def test_a_stress_keyword_the_scenario_would_ignore_fails_by_name(
    tmp_path: Path,
) -> None:
    """``hot`` would land in **kw: the operating point is not what the row says."""
    circuits = check_toy.CIRCUITS.replace(
        "def scenario(nl, vin=5.0, drive=None):",
        "def scenario(nl, vin=5.0, drive=None, **kw):",
    )
    specs = SPECS + TABLE.replace(
        'dict(vin=5.5, drive={"LED_DRV": 0.0})',
        'dict(vin=5.5, drive={"LED_DRV": 0.0}, hot=True)',
    )
    root = check_toy.write_project(tmp_path / "kw", board(), ["power"], specs, circuits)
    entry = run(root, "test_semis").entry(CHECK)
    assert entry["outcome"] == "failed"
    assert said(entry) == (
        "STRESS_SCENARIOS[1]: circuits.scenario has no parameter named 'hot' (it "
        "would land in **kwargs, or nowhere, so the operating point solved may not be "
        "the one named): name it in scenario's signature"
    )
    assert entry["numbers"] == {"ignored keywords": {"1": ["hot"]}}


def test_an_ordinary_led_board_has_no_unphysical_diode(tmp_path: Path) -> None:
    """The control's diodes all conduct at their drop: nothing called unphysical."""
    entry = go(tmp_path, "phys", board()).entry(CHECK)
    assert entry["outcome"] == "passed"
