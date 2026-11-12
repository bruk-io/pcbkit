#!/usr/bin/env python3
"""List each BOM line's quantity to buy and whether its datasheet record is checked.

Two things in the sourcing review are decidable, so no conversation works them out:
the quantity of each line to buy, and which lines have no ``VERIFIED`` or ``CONFIRMED``
datasheet record (a line with any other status has an unchecked footprint). Run this
from the project folder (or anywhere inside it): it reads the one ``*_BOM.csv`` in
``out/fab/`` (or ``fab/``) and the records in ``parts/<part>.datasheet.json``, and
prints JSON:

    python3 "<plugin>/skills/review-sourcing/scripts/bomlines.py" --boards N [--spare S]

``--boards`` is the boards to assemble (not to make), ``--spare`` the extra parts per
line that the assembler's attrition allowance asks for, read from its page or quote
(0 if not given). Each line has ``item``, ``designators``, ``mpn``, ``per_board``,
``needed`` (``per_board * boards + spare``), and ``datasheet``: the record's status,
``NO RECORD`` when ``parts/`` has none for the part number, or ``NO MPN``. ``unchecked``
lists the items whose ``datasheet`` is not ``VERIFIED`` or ``CONFIRMED``.

Exit codes: 0 printed (an empty list is an answer), 1 no single BOM to read or a file
that is not a pcbkit BOM, 2 no board project or a bad argument. Standard library only;
any ``python3``, 3.9 included.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

MARKER = "pcbkit.toml"
RECORD_SUFFIX = ".datasheet.json"
CHECKED = ("VERIFIED", "CONFIRMED")
COLUMNS = ("Item", "Qty", "Designator", "Manufacturer Part Number")


def find_project(start: Path) -> Path | None:
    """Return the nearest folder at or above ``start`` that holds a pcbkit.toml."""
    for folder in (start, *start.parents):
        if (folder / MARKER).is_file():
            return folder
    return None


def record_name(mpn: str) -> str:
    """Return the record file name for ``mpn``, as pcbkit.datasheet_find spells it."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", mpn.strip()).strip("_.") or "part"
    return f"{slug}{RECORD_SUFFIX}"


def datasheet_status(parts: Path, mpn: str) -> str:
    """Return the status of ``mpn``'s record in ``parts``, or why there is none."""
    if not mpn.strip():
        return "NO MPN"
    path = parts / record_name(mpn)
    if not path.is_file():
        return "NO RECORD"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("mpn") != mpn:
            return "NO RECORD"
        return str(record["status"])
    except (OSError, ValueError, KeyError, AttributeError):
        return "UNREADABLE"


def lines(
    rows: list[dict[str, str]], parts: Path, boards: int, spare: int
) -> list[dict[str, object]]:
    """Return each BOM row with the quantity to buy and its datasheet status."""
    found: list[dict[str, object]] = []
    for row in rows:
        per_board = int(row["Qty"])
        mpn = row["Manufacturer Part Number"]
        found.append(
            {
                "item": row["Item"],
                "designators": row["Designator"],
                "mpn": mpn,
                "per_board": per_board,
                "needed": per_board * boards + spare,
                "datasheet": datasheet_status(parts, mpn),
            }
        )
    return found


def arguments(argv: list[str]) -> tuple[int, int] | None:
    """Return ``(boards, spare)`` from the arguments, or None if they are not valid."""
    values = {"--boards": None, "--spare": 0}
    it = iter(argv)
    for flag in it:
        value = next(it, "")
        if flag not in values or not value.isdigit():
            return None
        values[flag] = int(value)
    boards = values["--boards"]
    return None if boards is None else (boards, int(values["--spare"] or 0))


def main(argv: list[str]) -> int:
    """Print the BOM lines with their quantities and statuses; return the exit code."""
    parsed = arguments(argv)
    if parsed is None:
        print("usage: bomlines.py --boards N [--spare S]  (whole numbers)")
        return 2
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
    if rows and not all(column in rows[0] for column in COLUMNS):
        print(f"{boms[0].name} lacks one of {', '.join(COLUMNS)}: not a pcbkit BOM")
        return 1
    found = lines(rows, root / "parts", *parsed)
    unchecked = [row["item"] for row in found if row["datasheet"] not in CHECKED]
    print(json.dumps({"lines": found, "unchecked": unchecked}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
