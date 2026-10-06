"""The centroid file: where each part sits, in the frame an assembler expects.

The origin is the board's bottom-left corner, y points up, units are millimetres, and
only the top side is listed. kicad-cli's position export gives each footprint's anchor.
For a surface-mount part whose anchor is not at the middle of its body (more than
0.1 mm off the centre of its F.Fab outline) the assembler wants the package centre, so
``body_centres`` finds it on the board and ``centroid_rows`` swaps it in. Everything
else, including every number kicad-cli printed, passes through as text.

``centroid_rows`` and ``write_centroid`` are pure; ``body_centres`` and
``export_centroid`` need pcbnew.
"""

from __future__ import annotations

import csv
import tempfile
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pcbkit.kicad import board as kb
from pcbkit.kicad import cli

HEADER = (
    "Designator",
    "Mid X (mm)",
    "Mid Y (mm)",
    "Layer",
    "Rotation",
    "Value",
    "Footprint",
)

# A body centre closer than this to the footprint's anchor is the anchor.
BODY_TOLERANCE_MM = 0.1


def body_centres(board: Any, height_mm: float) -> dict[str, tuple[float, float]]:
    """Return the body centre of each surface-mount footprint whose anchor is off it.

    The centre is that of the footprint's F.Fab shapes (text left out), and a
    footprint with no such shapes, or with any hole, is skipped. The answer is in the
    centroid's frame: origin at the bottom-left corner of a board ``height_mm`` tall,
    y up.
    """
    import pcbnew

    found: dict[str, tuple[float, float]] = {}
    for footprint in board.GetFootprints():
        if any(pad.HasHole() for pad in footprint.Pads()):
            continue
        outline = [
            item
            for item in footprint.GraphicalItems()
            if item.GetLayer() == pcbnew.F_Fab and item.GetClass() != "PCB_TEXT"
        ]
        if not outline:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for item in outline:
            box = item.GetBoundingBox()
            xs += [box.GetLeft(), box.GetRight()]
            ys += [box.GetTop(), box.GetBottom()]
        cx = pcbnew.ToMM((min(xs) + max(xs)) / 2)
        cy = pcbnew.ToMM((min(ys) + max(ys)) / 2)
        off_x = abs(cx - pcbnew.ToMM(footprint.GetX()))
        off_y = abs(cy - pcbnew.ToMM(footprint.GetY()))
        if off_x > BODY_TOLERANCE_MM or off_y > BODY_TOLERANCE_MM:
            found[footprint.GetReference()] = (
                cx - kb.OX,
                (kb.OY + height_mm) - cy,
            )
    return found


def read_positions(path: Path) -> list[dict[str, str]]:
    """Return the rows of a kicad-cli position CSV (Ref, Val, Package, PosX, ...)."""
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def centroid_rows(
    positions: Iterable[Mapping[str, str]],
    refs: Collection[str],
    body: Mapping[str, tuple[float, float]],
) -> list[list[str]]:
    """Return the centroid rows for the positions whose reference is in ``refs``.

    A row is the reference, X, Y, "Top", rotation, value and footprint, with kicad-cli's
    own text for each, except that a reference in ``body`` gets its body centre, to four
    decimals, in place of X and Y. Rows keep the order of ``positions``.
    """
    rows: list[list[str]] = []
    for row in positions:
        ref = row["Ref"]
        if ref not in refs:
            continue
        x, y = row["PosX"], row["PosY"]
        if ref in body:
            x, y = f"{body[ref][0]:.4f}", f"{body[ref][1]:.4f}"
        rows.append([ref, x, y, "Top", row["Rot"], row["Val"], row["Package"]])
    return rows


def write_centroid(path: Path, rows: Sequence[Sequence[str]]) -> None:
    """Write the header and ``rows`` as CSV, with CRLF line endings."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)


def export_centroid(
    pcb: Path, out: Path, refs: Collection[str], height_mm: float
) -> int:
    """Write the centroid file for the parts in ``refs``; return how many rows it has.

    ``height_mm`` is the board's height; the board's aux origin must already sit at its
    bottom-left corner (``pcbkit.fab.gerbers.set_origin``), because kicad-cli measures
    from it. The raw position file goes to a temporary folder.
    """
    import pcbnew

    with tempfile.TemporaryDirectory() as folder:
        raw = Path(folder) / "pos_raw.csv"
        cli.export_positions(pcb, raw)
        positions = read_positions(raw)
    body = body_centres(pcbnew.LoadBoard(str(pcb)), height_mm)
    rows = centroid_rows(positions, refs, body)
    write_centroid(out, rows)
    return len(rows)
