"""Write everything a fab house and an assembler need from a finished board.

``export_fab`` is what ``pcbkit finalize`` calls last. From the saved board
``kicad/<stem>.kicad_pcb`` and its schematic it writes two folders:

* ``out/fab/``: the Gerber and drill files in ``gerbers/``, the zip of them, the BOM as
  CSV and as a workbook, and the centroid file;
* ``out/docs/``: the schematic and copper PDFs, the assembly drawing and, unless asked
  not to, three 3D renders.

Only those two folders are emptied first, so no file from an earlier export survives
and nothing else under ``out/`` is touched. The BOM comes from design.py, the optional
project ``bom.py`` and which footprints have plated through-hole pads on the board.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import click

from pcbkit.design import load_design
from pcbkit.fab import bom, centroid, docs, gerbers, pcbway
from pcbkit.kicad import env
from pcbkit.project import Project, import_project_module


@dataclass(frozen=True)
class FabResult:
    """What an export wrote: BOM size, every file, and the parts that cannot be ordered.

    ``total_parts`` is the sum of the BOM quantities. ``without_mpn`` lists fitted parts
    whose part number is missing, a stand-in or a description (see
    ``pcbkit.fab.bom.parts_without_mpn``); the export still succeeds with them.
    """

    bom_lines: int
    total_parts: int
    files: list[Path]
    without_mpn: list[bom.MpnProblem] = field(default_factory=list)


def board_height(project: Project) -> float:
    """Return ``H``, the board's height in millimetres, from the project's layout.py."""
    layout = import_project_module(project.root, "layout")
    height = getattr(layout, "H", None)
    if isinstance(height, bool) or not isinstance(height, (int, float)) or height <= 0:
        raise click.ClickException(
            f"layout.py should define H, the board height in mm, got {height!r}"
        )
    return float(height)


def export_fab(project: Project, render: bool = True) -> FabResult:
    """Export the fab files and documents of the project's finished board.

    ``render`` false leaves out the 3D renders, the slowest part. The board must have
    been placed, routed and silkscreened; the board file is saved once more here, to
    put its aux origin at the bottom-left corner. Raise a ClickException naming what is
    missing or wrong; a kicad-cli failure reports its own output.
    """
    env.require_pcbnew()
    config = project.config
    pcb = project.kicad_dir / f"{config.board.stem}.kicad_pcb"
    sch = project.kicad_dir / f"{config.board.stem}.kicad_sch"
    for needed in (pcb, sch):
        if not needed.is_file():
            raise click.ClickException(
                f"{needed} not found: build and route the board first "
                "(`pcbkit finalize`)"
            )
    parts = load_design(project.root / "design.py").parts
    height = board_height(project)
    overrides = bom.load_overrides(project.root)
    names = pcbway.fab_names(config.board.fab_name)

    fab_dir, docs_dir = project.out_dir / "fab", project.out_dir / "docs"
    for folder in (fab_dir, docs_dir):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)

    gerbers.set_origin(pcb, height)
    written = gerbers.export(pcb, fab_dir / "gerbers", fab_dir / names.gerber_zip)

    footprints = bom.board_footprints(pcb)
    absent = [p["ref"] for p in bom.fitted(parts) if p["ref"] not in footprints]
    if absent:
        raise bom.BomError(
            f"design.py has parts that are not on the board: {', '.join(absent)}. "
            "Run `pcbkit build` again"
        )
    lines = bom.bom_lines(
        parts, {ref for ref, through in footprints.items() if through}, overrides
    )
    bom.write_csv(fab_dir / names.bom_csv, lines)
    bom.write_xlsx(fab_dir / names.bom_xlsx, lines, overrides.not_in_bom)
    refs = {ref for line in lines for ref in line.refs}
    centroid.export_centroid(pcb, fab_dir / names.centroid, refs, height)
    written += [fab_dir / names.bom_csv, fab_dir / names.bom_xlsx]
    written.append(fab_dir / names.centroid)

    written += docs.export_documents(
        sch, pcb, docs_dir, config.board.fab_name, render=render
    )
    return FabResult(
        bom_lines=len(lines),
        total_parts=bom.total_parts(lines),
        files=written,
        without_mpn=bom.parts_without_mpn(parts, overrides),
    )
