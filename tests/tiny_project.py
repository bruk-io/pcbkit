"""A tiny board project to place and silk with real KiCad, built in a folder of a test.

It is tests/fixtures/tiny_board (a header and a resistor from the project's own
library, a stock LED) with two mounting holes, a resistor whose reference starts with
H, and a layout.py: a 40 x 24 mm board with 2 mm corners. J1, R1 and HR1 have
positions; the LED D1 has none, so placement parks it below the board and reports it.

``make_project`` writes the folder; ``run_stages`` runs the schematic build and the
placement on it, as `pcbkit build` does. Both need real KiCad (kicad-cli, and pcbnew
for the placement), so only a test that has done ``pytest.importorskip("pcbnew")``
calls them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pcbkit import place, sch
from pcbkit.place import PlaceResult
from pcbkit.project import Project, load_project

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tiny_board"

HOLE_PART = """
part("H1", "Mechanical:MountingHole", "M3", "MountingHole:MountingHole_3.2mm_M3",
     {}, block=B, bom=False)
"""
# two holes, and a part called HR1 that is a part, not a hole
EXTRA_PARTS = (
    HOLE_PART
    + HOLE_PART.replace('"H1"', '"H2"')
    + '\nR("HR1", "1k", "+3V3", "GND", B)\n'
)

LAYOUT = """\
W, H = 40.0, 24.0
CORNER_R = 2.0
HOLES = [(4.0, 4.0), (36.0, 20.0)]
P = {
    "J1": (10.0, 8.0, 0),
    "R1": (26.0, 12.0, 90),
    "HR1": (18.0, 18.0, 180),
}
"""


def make_project(
    root: Path,
    layout: str = LAYOUT,
    extra_design: str = EXTRA_PARTS,
    silk: str | None = None,
) -> Path:
    """Copy the tiny board into ``root`` and add the holes, layout.py and silk.py."""
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("expected_*"))
    with open(root / "design.py", "a", encoding="utf-8") as handle:
        handle.write(extra_design)
    (root / "layout.py").write_text(layout, encoding="utf-8")
    if silk is not None:
        (root / "silk.py").write_text(silk, encoding="utf-8")
    return root


def run_stages(root: Path) -> tuple[Project, PlaceResult]:
    """Run the schematic build and the placement the way `pcbkit build` does."""
    proj = load_project(root)
    sch.build_schematic(proj)
    return proj, place.place_board(proj)
