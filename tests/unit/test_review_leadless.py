"""Unit tests for skills/review-sourcing/scripts/leadless.py.

The quote counts BGA, QFP and QFN by name; the sourcing review lists the other leadless
packages from the BOM with a shipped pattern, not one picked at review time.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "review-sourcing"
    / "scripts"
    / "leadless.py"
)
HEADER = [
    "Item",
    "Qty",
    "Designator",
    "Manufacturer",
    "Manufacturer Part Number",
    "Value",
    "Description",
    "Package / Footprint",
    "Type",
]
PACKAGES = {
    "U1": "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
    "U2": "Package_SON:WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm",
    "U3": "my_lib:DFN-8-1EP_3x3mm_P0.5mm_EP1.66x2.38mm",
    # The quote matches the whole footprint id: KiCad's library name has QFN in it,
    # so the quote counts this DFN already.
    "U7": "Package_DFN_QFN:DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm",
    "U4": "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y",
    "U5": "Package_CSP:WLCSP-9_1.4x1.4mm_P0.4mm",
    "U6": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "R1": "Resistor_SMD:R_0603_1608Metric",
}


def write_project(root: Path) -> Path:
    """Write a project with a BOM of one line per package above."""
    (root / "out" / "fab").mkdir(parents=True)
    (root / "pcbkit.toml").write_text("[board]\n", encoding="utf-8")
    with open(root / "out" / "fab" / "B_BOM.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for item, (ref, package) in enumerate(PACKAGES.items(), start=1):
            writer.writerow([item, 1, ref, "M", f"P{item}", "v", "d", package, "SMD"])
    return root


def run(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the script inside ``root``."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=root, capture_output=True, text=True
    )


def test_it_lists_the_leadless_lines_the_quote_does_not_count(tmp_path: Path) -> None:
    done = run(write_project(tmp_path / "my-board"))
    assert done.returncode == 0, done.stdout
    found = {row["designators"]: row["family"] for row in json.loads(done.stdout)}
    assert found == {"U2": "SON", "U3": "DFN", "U4": "LGA", "U5": "CSP"}


def test_without_a_single_bom_it_says_so(tmp_path: Path) -> None:
    root = write_project(tmp_path / "my-board")
    (root / "out" / "fab" / "B_BOM.csv").unlink()
    done = run(root)
    assert done.returncode == 1
    assert done.stdout.startswith("0 files ending _BOM.csv in fab/")


@pytest.mark.parametrize("where", [".", "out"])
def test_outside_or_inside_a_project(tmp_path: Path, where: str) -> None:
    if where == ".":
        assert run(tmp_path).returncode == 2
    else:
        assert run(write_project(tmp_path / "b") / where).returncode == 0
