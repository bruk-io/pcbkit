#!/usr/bin/env python3
"""List the BOM lines in leadless packages that ``pcbkit quote`` does not count.

``pcbkit quote`` counts BGA, QFP and QFN parts by those letters anywhere in the
footprint id, library included (so a DFN from KiCad's ``Package_DFN_QFN`` is counted).
Dual-row and land-grid leadless packages (DFN, SON, LGA, wafer-level and chip-scale)
also need X-ray or care at assembly and are not in that count. Run this from the
project folder (or anywhere inside it): it reads the one ``*_BOM.csv`` in ``out/fab/``
(or ``fab/``) and prints JSON, one object per BOM line whose ``Package / Footprint``
matches ``LEADLESS`` and not the quote's own pattern: ``item``, ``designators``,
``package`` and ``family`` (the letters that matched).

Exit codes: 0 printed (an empty list is an answer), 1 no single BOM to read or a file
that is not a pcbkit BOM, 2 no board project. Standard library only; any ``python3``.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

MARKER = "pcbkit.toml"
# The families the quote leaves out, as footprint names spell them (WSON, VSON and
# TSON contain SON; UDFN and TDFN contain DFN; WLCSP contains CSP).
LEADLESS = re.compile(r"DFN|SON|LGA|LLP|CSP|WLP|MLP|MLF", re.IGNORECASE)
# What the quote already counts (pcbkit/fab/pcbway.py FINE_PITCH).
COUNTED = re.compile(r"BGA|QFP|QFN", re.IGNORECASE)
PACKAGE = "Package / Footprint"


def find_project(start: Path) -> Path | None:
    """Return the nearest folder at or above ``start`` that holds a pcbkit.toml."""
    for folder in (start, *start.parents):
        if (folder / MARKER).is_file():
            return folder
    return None


def leadless(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return the BOM rows in a leadless package the quote does not count."""
    found = []
    for row in rows:
        package = row[PACKAGE]
        family = LEADLESS.search(package)
        if family and not COUNTED.search(package):
            found.append(
                {
                    "item": row["Item"],
                    "designators": row["Designator"],
                    "package": package,
                    "family": family.group(0).upper(),
                }
            )
    return found


def main() -> int:
    """Print the leadless lines of the project's BOM; return the exit code."""
    root = find_project(Path.cwd().resolve())
    if root is None:
        print("no pcbkit.toml here or above: run this inside a board project")
        return 2
    folder = root / "out" / "fab" if (root / "out" / "fab").is_dir() else root / "fab"
    boms = sorted(folder.glob("*_BOM.csv")) if folder.is_dir() else []
    if len(boms) != 1:
        print(
            f"{len(boms)} files ending _BOM.csv in {folder.name}/: run pcbkit finalize"
        )
        return 1
    with open(boms[0], newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if rows and PACKAGE not in rows[0]:
        print(f"{boms[0].name} has no {PACKAGE!r} column: it is not a pcbkit BOM")
        return 1
    print(json.dumps(leadless(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
