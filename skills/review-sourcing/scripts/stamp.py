#!/usr/bin/env python3
"""Record which BOM and centroid the sourcing review judged, for the order to verify.

``review-sourcing`` judges the BOM and the centroid in ``out/fab/`` (or ``fab/``, the
older place). Run this from the project folder (or anywhere inside it) once its
``sourcing.md`` is written: it writes ``sourcing.json`` beside ``pcbkit.toml`` with the
sha256 of each of those two files. The order skill's ``preflight.py`` hashes the files
again and refuses an order whose files are not the ones the review judged.

``sourcing.json`` is not a source of the board (not Python, not in ``footprints/`` or
``golden/``), so writing it makes nothing stale.

Exit codes: 0 the stamp is written, 1 something it needs is missing (said on one line),
2 no board project was found. Standard library only; any ``python3``, 3.9 included.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

MARKER = "pcbkit.toml"
STAMP = "sourcing.json"
REVIEW = "sourcing.md"
# The two files the review judges, by the suffix finalize gives them.
JUDGED = ("_BOM.csv", "_centroid.csv")
FORMAT = 1


def find_project(start: Path) -> Path | None:
    """Return the nearest folder at or above ``start`` that holds a pcbkit.toml."""
    for folder in (start, *start.parents):
        if (folder / MARKER).is_file():
            return folder
    return None


def fab_folder(root: Path) -> Path:
    """Return ``out/fab``, or ``fab`` where only the older place exists."""
    return root / "out" / "fab" if (root / "out" / "fab").is_dir() else root / "fab"


def sha256(path: Path) -> str:
    """Return the sha256 of a file's bytes, as hex."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def judged_files(root: Path) -> tuple[list[Path], list[str]]:
    """Return the BOM and centroid to stamp, and a line per one that is not there."""
    folder = fab_folder(root)
    found: list[Path] = []
    problems: list[str] = []
    for suffix in JUDGED:
        files = sorted(folder.glob(f"*{suffix}")) if folder.is_dir() else []
        if len(files) != 1:
            problems.append(
                f"MISSING: {folder.relative_to(root).as_posix()}/ has {len(files)} "
                f"files ending {suffix}, and the stamp needs exactly one: run "
                "pcbkit finalize, then review the files it writes"
            )
        else:
            found.append(files[0])
    return found, problems


def stamp(root: Path) -> list[str]:
    """Write the project's sourcing.json; return a line per reason it could not."""
    problems = []
    if not (root / REVIEW).is_file():
        problems.append(
            f"MISSING: {REVIEW}: write the sourcing review first, then stamp the "
            "files it judged"
        )
    files, missing = judged_files(root)
    problems += missing
    if problems:
        return problems
    data = {
        "format": FORMAT,
        "review": REVIEW,
        "files": {path.relative_to(root).as_posix(): sha256(path) for path in files},
    }
    (root / STAMP).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return []


def main() -> int:
    """Write the stamp for the project here; return the exit code."""
    root = find_project(Path.cwd().resolve())
    if root is None:
        print("no pcbkit.toml here or above: run this inside a board project")
        return 2
    problems = stamp(root)
    for line in problems:
        print(line)
    if problems:
        return 1
    files = json.loads((root / STAMP).read_text(encoding="utf-8"))["files"]
    print(f"OK: {STAMP} records {', '.join(sorted(files))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
