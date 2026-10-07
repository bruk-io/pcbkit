#!/usr/bin/env python3
"""Say whether a board project's fab files and checks are current, before an order.

An order is paid for, and a file made before the last change to the board would be paid
for too. Run this from the project folder (or anywhere inside it). It answers from the
files alone: it needs no pcbkit, no KiCad and no network, only a ``python3``.

It reports a problem when

* the Gerber zip, the BOM or the centroid is missing from ``out/fab/`` (or ``fab/``, the
  older place), or older than any source of the board: ``pcbkit.toml``, every Python
  module of the project except the ones that only the checks read, ``footprints/`` and
  ``golden/``;
* ``out/checks/results.json`` is missing, older than any of those sources or than the
  checks' own inputs (``specs.py``, ``circuits.py``, ``checks/``), or records a failed
  check or a non-zero exit. A ``pcbkit check -k ...`` run replaces the results with its
  subset, so run the checks in full before an order.

Exit codes: 0 every file is current and the checks passed, 1 at least one problem
(each is printed, with what to run), 2 no board project was found.

Standard library only; it runs under whichever ``python3`` is on PATH.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MARKER = "pcbkit.toml"
# Python modules that only the checks read: changing one cannot change a fab file.
# mutants.py is not an input of a check run either, only of `pcbkit mutants`.
CHECK_ONLY = ("specs.py", "circuits.py", "mutants.py")
CHECK_INPUTS = ("specs.py", "circuits.py")
# Folders that are never sources of the board: generated, or the user's own notes.
SKIP_DIRS = ("checks", "archive", "kicad", "out", "fab", "__pycache__")
# The three files an order needs, by the suffix finalize gives them.
FAB_FILES = ("_gerbers.zip", "_BOM.csv", "_centroid.csv")


def find_project(start: Path) -> Path | None:
    """Return the nearest folder at or above ``start`` that holds a pcbkit.toml."""
    for folder in (start, *start.parents):
        if (folder / MARKER).is_file():
            return folder
    return None


def source_files(root: Path) -> list[Path]:
    """Return every file whose change can change the board or the files made from it."""
    found = [root / MARKER]
    for folder, names, files in os.walk(root):
        here = Path(folder)
        names[:] = [n for n in names if not n.startswith(".") and n not in SKIP_DIRS]
        relative = here.relative_to(root).parts
        if relative[:1] in (("footprints",), ("golden",)):
            found += [here / name for name in files]  # every file counts there
            continue
        for name in files:
            check_only = not relative and name in CHECK_ONLY
            if name.endswith(".py") and not check_only:
                found.append(here / name)
    return [p for p in found if p.is_file()]


def check_inputs(root: Path) -> list[Path]:
    """Return what a check run reads beyond the board's sources."""
    found = [root / name for name in CHECK_INPUTS]
    found += sorted((root / "checks").rglob("*.py"))
    return [p for p in found if p.is_file()]


def newest(paths: list[Path]) -> Path:
    """Return the path with the latest modification time."""
    return max(paths, key=lambda p: p.stat().st_mtime)


def fab_folder(root: Path) -> Path:
    """Return ``out/fab``, or ``fab`` where only the older place exists."""
    return root / "out" / "fab" if (root / "out" / "fab").is_dir() else root / "fab"


def problems(root: Path) -> list[str]:
    """Return one line per reason the order files or the checks are not current."""
    found = []
    latest = newest(source_files(root))
    when = latest.stat().st_mtime
    where = latest.relative_to(root)
    latest_check = newest([latest, *check_inputs(root)])
    when_checks = latest_check.stat().st_mtime
    where_checks = latest_check.relative_to(root)
    folder = fab_folder(root)
    for suffix in FAB_FILES:
        files = sorted(folder.glob(f"*{suffix}")) if folder.is_dir() else []
        if len(files) != 1:
            found.append(
                f"MISSING: {folder.relative_to(root)}/ has {len(files)} files "
                f"ending {suffix}, and an order needs exactly one: run pcbkit finalize"
            )
        elif files[0].stat().st_mtime < when:
            found.append(
                f"STALE: {files[0].relative_to(root)} is older than {where}: "
                "run pcbkit build, route, promote and finalize"
            )
    results = root / "out" / "checks" / "results.json"
    if not results.is_file():
        found.append("MISSING: out/checks/results.json: run pcbkit check")
        return found
    if results.stat().st_mtime < when_checks:
        found.append(
            f"STALE: the checks were run before {where_checks} changed: "
            "run pcbkit check"
        )
    try:
        data = json.loads(results.read_text(encoding="utf-8"))
        bad = data["counts"].get("failed", 0) + data["counts"].get("error", 0)
        exit_status = data["exit_status"]
    except (ValueError, KeyError, TypeError, AttributeError):
        found.append("UNREADABLE: out/checks/results.json: run pcbkit check again")
        return found
    if bad or exit_status != 0:
        found.append(
            f"FAILED: the last pcbkit check run did not pass ({bad} failed or "
            f"errored checks, exit status {exit_status}): read "
            "out/checks/results.json and fix the board"
        )
    return found


def main() -> int:
    """Print what is wrong with the project's order files; return the exit code."""
    root = find_project(Path.cwd().resolve())
    if root is None:
        print("no pcbkit.toml here or above: run this inside a board project")
        return 2
    found = problems(root)
    for line in found:
        print(line)
    if not found:
        print(f"OK: the fab files and checks of {root.name} are newer than its sources")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
