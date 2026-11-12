"""Unit tests for skills/review-spec-drift/scripts/listing.py.

The spec-drift review lists the design by script and compares the spec's mechanical
claims with that listing in code: a claim is written down first, then judged. These
tests list a small made-up netlist and compare claims against it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from fractions import Fraction
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


def netlist_text() -> str:
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
    return (
        '(export (version "E") (design (tool "Eeschema 10.0.6")) '
        f"(components {' '.join(comps)}) (nets {' '.join(nets)}))"
    )


def netlist() -> Netlist:
    """Return the test design as a Netlist."""
    return Netlist.from_text(netlist_text())


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


# --- what no claim names ------------------------------------------------------------


def test_the_parts_and_nets_no_claim_names_are_listed(found: dict[str, Any]) -> None:
    claims = {
        "part.J1.pads": 2,  # a key names J1
        "parts.connector": ["J1", "J10"],  # a value names J1 and J10
        "net.VIN": ["J1.1", "U2.1", "JP1.1"],  # a key names VIN; a value J1, U2, JP1
        "net.GND": ["J1.2"],
    }
    assert listing.unclaimed(claims, found) == {
        "parts": ["R2", "SW1", "TP1"],
        "nets": ["+5V", "EN"],
    }


def test_a_claim_naming_everything_leaves_nothing_unclaimed(
    found: dict[str, Any],
) -> None:
    claims = {
        f"part.{r}.pads": 1 for r in ("J1", "J10", "JP1", "TP1", "U2", "SW1", "R2")
    }
    claims.update({f"net.{n}": [] for n in ("VIN", "GND", "+5V", "EN")})
    assert listing.unclaimed(claims, found) == {"parts": [], "nets": []}


def test_a_net_named_only_as_a_value_counts_as_claimed(found: dict[str, Any]) -> None:
    claims = {"part.U2.value": "EN"}
    assert "EN" not in listing.unclaimed(claims, found)["nets"]


def test_the_connector_definition_is_the_one_the_docs_give() -> None:
    """PIN_NETS in docs/project-interface.md and the listing say one thing."""
    docs = (Path(listing.__file__).resolve().parents[3] / "docs").joinpath(
        "project-interface.md"
    )
    row = next(
        line
        for line in docs.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `PIN_NETS` |")
    )
    for phrase in (
        "symbol library starts `Connector`",
        "reference letters are `J`",
        "reference letters `TP`",
        "library starting `TestPoint`",
        "letters `JP`",
        "library starting `Jumper`",
    ):
        assert phrase in row, phrase
    assert listing.category("J12", "Lib") == "connector"
    assert listing.category("X1", "Connector_Generic") == "connector"
    assert listing.category("JP3", "Lib") == "jumper"
    assert listing.category("JP3", "Connector_Generic") == "jumper"
    assert listing.category("TP2", "Connector") == "test point"
    assert listing.category("X1", "TestPoint") == "test point"
    assert listing.category("X1", "Jumper") == "jumper"
    assert listing.category("JX1", "Lib") == "other"


def test_a_value_json_cannot_hold_fails_instead_of_becoming_a_string() -> None:
    assert listing.plain(3) == 3 and isinstance(listing.plain(3), int)
    assert listing.plain(Fraction(1, 4)) == 0.25
    with pytest.raises(TypeError, match="Netlist"):
        listing.plain(netlist())


# --- main, through the real entry point -------------------------------------------

FAKE_KICAD_CLI = """\
#!/bin/sh
if [ "$1" = "--version" ]; then echo "10.0.6"; exit 0; fi
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
cp "$NETLIST_FIXTURE" "$out"
"""
TOML = '[board]\nstem = "my_board"\ntitle = "My Board"\nrev = "A"\n'
TOML += 'fab_name = "My_Board_revA"\n'
DESIGN_PY = """\
from pcbkit.design import part

part("J1", "Connector_Generic:Conn_01x02", "In", "Lib:Fp", {"1": "VIN", "2": "GND"},
     block="Power")
part("R2", "Device:R", "10k", "Lib:Fp", {"1": "+5V", "2": "EN"}, dnp=True,
     block="Power")
"""
LAYOUT_PY = "W = 60.0\nH = 40.0\nHOLES = [(3, 3), (57, 37)]\n"


def make_project(root: Path, layout: str = LAYOUT_PY) -> Path:
    """Write a throwaway board project the script can run in."""
    (root / "kicad").mkdir(parents=True)
    (root / "kicad" / "my_board.kicad_sch").write_text("", encoding="utf-8")
    (root / "pcbkit.toml").write_text(TOML, encoding="utf-8")
    (root / "design.py").write_text(DESIGN_PY, encoding="utf-8")
    (root / "layout.py").write_text(layout, encoding="utf-8")
    return root


def run_main(
    root: Path, tmp_path: Path, claims: dict[str, Any] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run listing.py in ``root`` with a kicad-cli that writes the test netlist."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    cli = bin_dir / "kicad-cli"
    cli.write_text(FAKE_KICAD_CLI, encoding="utf-8")
    cli.chmod(0o755)
    (tmp_path / "board.net").write_text(netlist_text(), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["NETLIST_FIXTURE"] = str(tmp_path / "board.net")
    args = []
    if claims is not None:
        (tmp_path / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
        args = [str(tmp_path / "claims.json")]
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_main_lists_compares_and_names_what_no_claim_covers(tmp_path: Path) -> None:
    claims = {
        "outline.W": 60,
        "part.J1.pads": 3,
        "part.R2.dnp": True,
        "part.J9.pads": 1,
    }
    done = run_main(make_project(tmp_path / "my-board"), tmp_path, claims)
    assert done.returncode == 1, done.stdout + done.stderr  # J1 has 2 pads, J9 absent
    printed = json.loads(done.stdout)
    assert printed["facts"]["outline.H"] == 40.0
    assert printed["facts"]["holes"] == 2
    assert printed["facts"]["part.R2.dnp"] is True, (
        "design.py's dnp reached the listing"
    )
    assert {r["key"]: r["status"] for r in printed["claims"]} == {
        "outline.W": "matches",
        "part.J1.pads": "differs",
        "part.R2.dnp": "matches",
        "part.J9.pads": "not in design",
    }
    assert printed["unclaimed"] == {
        "parts": ["J10", "JP1", "SW1", "TP1", "U2"],
        "nets": ["+5V", "EN", "GND", "VIN"],
    }


def test_main_without_claims_prints_the_facts_and_no_unclaimed(tmp_path: Path) -> None:
    done = run_main(make_project(tmp_path / "my-board"), tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    printed = json.loads(done.stdout)
    assert printed["claims"] == [] and "unclaimed" not in printed


def test_main_fails_loudly_on_a_layout_value_json_cannot_hold(tmp_path: Path) -> None:
    odd = "from decimal import Decimal\nW = Decimal('60')\nH = 40.0\nHOLES = []\n"
    done = run_main(make_project(tmp_path / "my-board", odd), tmp_path)
    assert done.returncode == 2
    assert done.stdout == (
        "cannot print the listing: Decimal Decimal('60') is not a JSON value\n"
    )


def test_main_refuses_a_claims_file_that_is_not_a_table(tmp_path: Path) -> None:
    done = run_main(make_project(tmp_path / "my-board"), tmp_path, ["a"])  # type: ignore[arg-type]
    assert done.returncode == 2
    assert "is not a JSON table of fact key to claimed value" in done.stdout


def test_main_outside_a_project_or_without_a_schematic_exits_2(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    assert run_main(outside, tmp_path).stdout.startswith("no board project:")
    bare = make_project(tmp_path / "bare")
    (bare / "kicad" / "my_board.kicad_sch").unlink()
    done = run_main(bare, tmp_path)
    assert (done.returncode, "run pcbkit sch first" in done.stdout) == (2, True)
