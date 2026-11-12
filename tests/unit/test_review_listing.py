"""Unit tests for skills/review-spec-drift/scripts/listing.py.

The spec-drift review lists the design by script and compares the spec's mechanical
claims with that listing in code: a claim is written down first, then judged. These
tests list a small made-up netlist and compare claims against it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pcbkit.check.netlist import Netlist

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "review-spec-drift"
    / "scripts"
    / "listing.py"
)


def load() -> ModuleType:
    """Import the script as a module, by its path."""
    spec = importlib.util.spec_from_file_location("review_listing", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


listing = load()

# (ref, lib, symbol, footprint); nets as "REF.pad".
PARTS = [
    ("J1", "Connector_Generic", "Conn_01x02", "TerminalBlock:TB_1x02"),
    ("J10", "Connector_Generic", "Conn_01x04", "Connector:Header_1x04"),
    ("JP1", "Jumper", "SolderJumper_2_Open", "Jumper:SolderJumper"),
    ("TP1", "Connector", "TestPoint", "TestPoint:Pad_1mm"),
    ("U2", "Regulator_Switching", "Buck", "Package_TO_SOT_SMD:SOT-23-6"),
    ("SW1", "Switch", "SW_Push", "Button:SW_6mm"),
    ("R2", "Device", "R", "Resistor_SMD:R_0603"),
]
NETS = {
    "VIN": ["J1.1", "U2.1", "JP1.1"],
    "GND": ["J1.2", "U2.2", "J10.1", "SW1.2"],
    "+5V": ["U2.3", "J10.2", "TP1.1", "R2.1"],
    "EN": ["U2.4", "SW1.1", "R2.2", "JP1.2"],
}


def netlist() -> Netlist:
    """Return the test design as kicad-cli writes a netlist."""
    comps = [
        f'(comp (ref "{ref}") (value "{symbol}") (footprint "{fp}") '
        f'(fields (field (name "MPN") "MPN-{ref}")) '
        f'(libsource (lib "{lib}") (part "{symbol}")))'
        for ref, lib, symbol, fp in PARTS
    ]
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


@pytest.fixture
def found() -> dict[str, Any]:
    """Return the facts of the test design."""
    return listing.facts(
        netlist(),
        {"R2"},
        {"W": 60.0, "H": 40.0, "HOLES": [(3, 3), (57, 37)]},
        {"layers": 2, "copper_mm": 0.035, "thickness_mm": 1.6},
    )


def test_parts_are_sorted_into_categories(found: dict[str, Any]) -> None:
    """A jumper or test point is not a connector, though JP and TP lead with J or T."""
    assert found["parts.connector"] == ["J1", "J10"]
    assert found["parts.jumper"] == ["JP1"]
    assert found["parts.test point"] == ["TP1"]
    assert found["parts.ic"] == ["U2"]
    assert found["parts.switch"] == ["SW1"]
    assert found["parts.other"] == ["R2"]


def test_each_part_has_its_facts(found: dict[str, Any]) -> None:
    assert found["part.J1.pads"] == 2
    assert found["part.J1.footprint"] == "TerminalBlock:TB_1x02"
    assert found["part.J1.mpn"] == "MPN-J1"
    assert found["part.R2.dnp"] is True
    assert found["part.J1.dnp"] is False


def test_a_net_lists_its_pads_in_natural_order(found: dict[str, Any]) -> None:
    assert found["net.EN"] == ["JP1.2", "R2.2", "SW1.1", "U2.4"]
    assert found["net.GND"] == ["J1.2", "J10.1", "SW1.2", "U2.2"]


def test_the_outline_holes_and_stackup_are_facts(found: dict[str, Any]) -> None:
    assert (found["outline.W"], found["outline.H"], found["holes"]) == (60.0, 40.0, 2)
    assert found["stackup.layers"] == 2


def test_claims_are_judged_against_the_facts(found: dict[str, Any]) -> None:
    claims = {
        "outline.W": 60,
        "outline.H": 45.0,
        "part.J1.pads": 2,
        "parts.connector": ["J10", "J1"],
        "net.EN": ["U2.4", "SW1.1"],
        "part.J9.pads": 2,
        "part.R2.dnp": False,
    }
    rows = {row["key"]: row for row in listing.compare(claims, found)}
    assert rows["outline.W"]["status"] == "matches"
    assert rows["outline.H"] == {
        "key": "outline.H",
        "claimed": 45.0,
        "design": 40.0,
        "status": "differs",
    }
    assert rows["part.J1.pads"]["status"] == "matches"
    assert rows["parts.connector"]["status"] == "matches"
    assert rows["net.EN"]["status"] == "differs", "a pad the spec never named"
    assert rows["part.J9.pads"] == {
        "key": "part.J9.pads",
        "claimed": 2,
        "status": "not in design",
    }
    assert rows["part.R2.dnp"]["status"] == "differs", "False is not 0, nor True"


def test_a_claimed_number_is_not_matched_by_a_bool() -> None:
    assert not listing.same(1, True)
    assert listing.same(0.035, 0.0350000001)
