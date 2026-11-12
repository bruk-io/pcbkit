"""Unit tests for skills/review-derating/scripts/stress.py.

The derating review reads every part's worst stress and margin from this script, never
from arithmetic in a conversation. The tests solve a small made-up circuit whose
answers are worked out by hand here: 12 V and 24 V across a capacitor, a divider, a
FET and a reverse-biased diode.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from pcbkit.check.dc import Circuit, build
from pcbkit.check.netlist import Netlist

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "review-derating"
    / "scripts"
    / "stress.py"
)


def load() -> ModuleType:
    """Import the script as a module, by its path."""
    spec = importlib.util.spec_from_file_location("review_stress", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stress = load()

# (ref, "Lib:Symbol", value, MPN); nets as "REF.pad".
PARTS = [
    ("C1", "Device:C", "10u", "CAP-25V"),
    ("R1", "Device:R", "1k", "RES-1K"),
    ("R2", "Device:R", "1k", "RES-1K"),
    ("Q1", "Transistor_FET:2N7002", "2N7002", "FET-60V"),
    ("D1", "Device:D_Schottky", "SS14", "DIODE-40V"),
    ("U1", "Device:Load", "load", "IC-1"),
]
NETS = {
    "VIN": ["C1.1", "R1.1", "D1.1"],
    "GND": ["C1.2", "R2.2", "Q1.2", "D1.2"],
    "OUT": ["R1.2", "R2.1", "Q1.3", "U1.1"],
    "GATE": ["Q1.1"],
}


def netlist(parts: list[tuple[str, str, str, str]] = PARTS) -> Netlist:
    """Return the test circuit as kicad-cli writes a netlist, MPN fields included."""
    comps = []
    for ref, kind, value, mpn in parts:
        lib, symbol = kind.split(":")
        comps.append(
            f'(comp (ref "{ref}") (value "{value}") '
            f'(fields (field (name "MPN") "{mpn}")) '
            f'(libsource (lib "{lib}") (part "{symbol}")))'
        )
    nets = []
    for code, (name, pins) in enumerate(NETS.items(), start=1):
        nodes = " ".join(
            f'(node (ref "{r}") (pin "{p}"))' for r, p in (x.split(".") for x in pins)
        )
        nets.append(f'(net (code "{code}") (name "/{name}") {nodes})')
    return Netlist.from_text(
        '(export (version "E") (design (tool "Eeschema 10.0.6")) '
        f"(components {' '.join(comps)}) (nets {' '.join(nets)}))"
    )


def scenario(nl: Netlist, vin: float) -> Circuit:
    """Return the circuit at input ``vin``, the FET's gate held low."""
    return build(nl).fix("VIN", vin).fix("GND", 0.0).fix("GATE", 0.0)


CIRCUITS = SimpleNamespace(scenario=scenario)


def specs(**changes: Any) -> SimpleNamespace:
    """Return specs for the circuit at 12 V and 24 V, with ``changes`` applied."""
    table: dict[str, Any] = {
        "STRESS_SCENARIOS": [{"vin": 12.0}, {"vin": 24.0}],
        "CAP_VRATED": {"CAP-25V": 25.0},
        "RESISTOR_POWER_W": {"default": 0.25},
        "SEMI_VRATED": {
            "FET-60V": {"vds": 60.0, "vgs": 20.0},
            "DIODE-40V": {"vrrm": 40.0},
        },
        "SUPPLY_SCENARIO": {"vin": 10.0},
        "SUPPLY_PINS": [("U1", 1, (4.5, 11.0))],
        "SUPPLY_LIMITS": [("input (V)", 24.0, "<=", 30.0)],
    }
    table.update(changes)
    return SimpleNamespace(**table)


@pytest.fixture
def result() -> dict[str, Any]:
    """Return the script's table for the circuit with the default specs."""
    return stress.stress(netlist(), specs(), CIRCUITS)


def test_a_capacitor_is_judged_at_its_worst_scenario(result: dict[str, Any]) -> None:
    """24 V on a 25 V part: 96 %, over the 80 % target, at scenario 1."""
    assert result["capacitors"]["C1"] == {
        "worst": 24.0,
        "scenario": 1,
        "mpn": "CAP-25V",
        "rating": 25.0,
        "ratio": 0.96,
        "margin": 0.04,
        "target": 0.8,
        "verdict": "FAIL",
    }


def test_a_resistor_is_judged_against_half_its_rating(result: dict[str, Any]) -> None:
    """12 V over 1 kohm is 0.144 W: 58 % of 0.25 W, over the 50 % target."""
    r1 = result["resistors"]["R1"]
    assert (r1["worst"], r1["scenario"], r1["rating"]) == (0.144, 1, 0.25)
    assert (r1["ratio"], r1["margin"], r1["verdict"]) == (0.576, 0.424, "FAIL")


def test_semiconductors_get_vds_vgs_and_reverse_voltage(result: dict[str, Any]) -> None:
    semis = result["semiconductors"]
    assert semis["Q1 vds"]["worst"] == pytest.approx(12.0, rel=1e-3)
    assert semis["Q1 vds"]["verdict"] == "OK"
    assert semis["Q1 vgs"]["worst"] == 0.0
    assert semis["D1 vrrm"]["worst"] == 24.0
    assert (semis["D1 vrrm"]["ratio"], semis["D1 vrrm"]["verdict"]) == (0.6, "OK")
    assert not [k for k in semis if "." in k], "a body diode is not a part"


def test_a_part_with_no_rating_on_file_says_so() -> None:
    result = stress.stress(netlist(), specs(CAP_VRATED={}), CIRCUITS)
    assert result["capacitors"]["C1"]["verdict"] == "NO RATING"
    assert result["capacitors"]["C1"]["margin"] is None


def test_a_supply_pin_is_read_at_every_scenario_and_the_supply_one(
    result: dict[str, Any],
) -> None:
    """Half the input: 6 V, 12 V and 5 V; the 24 V corner is over its 11 V maximum."""
    (row,) = result["supply_pins"]
    assert row["v"] == pytest.approx({"0": 6.0, "1": 12.0, "supply": 5.0}, rel=1e-3)
    assert row["ok"] is False


def test_supply_limits_are_evaluated_with_their_margin() -> None:
    def from_netlist(nl: Netlist) -> tuple[str, float, str, float]:
        return ("parts on OUT", float(len(nl.refs_on("OUT"))), "<", 4.0)

    rows = specs(SUPPLY_LIMITS=[("input (V)", 24.0, "<=", 30.0), from_netlist])
    result = stress.stress(netlist(), rows, CIRCUITS)
    assert result["supply_limits"] == [
        {
            "what": "input (V)",
            "value": 24.0,
            "op": "<=",
            "limit": 30.0,
            "ok": True,
            "margin": 0.2,
        },
        {
            "what": "parts on OUT",
            "value": 4.0,
            "op": "<",
            "limit": 4.0,
            "ok": False,
            "margin": 0.0,
        },
    ]


def test_a_reverse_biased_polarised_capacitor_is_marked() -> None:
    nl = netlist([("C1", "Device:C_Polarized", "10u", "CAP-25V"), *PARTS[1:]])
    flipped = SimpleNamespace(
        scenario=lambda nl, vin: build(nl).fix("VIN", -vin).fix("GND", 0.0)
    )
    result = stress.stress(nl, specs(), flipped)
    assert result["capacitors"]["C1"]["verdict"] == "REVERSED"
    assert result["capacitors"]["C1"]["reverse"] == -24.0
