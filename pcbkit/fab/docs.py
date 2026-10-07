"""The documents that go with the fab files: PDFs, an assembly drawing and 3D renders.

``export_documents`` writes into one folder: the schematic as a PDF, the assembly
drawing (the F.Fab outline and board edge, black on white, as a PDF and a PNG), the top
and bottom copper as PDFs and, when asked, three 3D renders (an isometric view and the
top and bottom views). The renders take the longest, so they can be left out. The
assembly drawing goes through rsvg-convert, which kicad-cli's own PDF export cannot
replace: it keeps the vector outline sharp at any zoom.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import click

from pcbkit.kicad import cli, env

# Seconds to give rsvg-convert for the assembly drawing.
TIMEOUT_RSVG = 300.0

# Zoom factors the assembly drawing has always been converted at.
PDF_ZOOM = "2.6"
PNG_ZOOM = "9"


@dataclass(frozen=True)
class DocNames:
    """The file names of the documents, which all start with the board's fab name."""

    schematic: str
    assembly_pdf: str
    assembly_png: str
    top_copper: str
    bottom_copper: str
    render_iso: str
    render_top: str
    render_bottom: str


def doc_names(fab_name: str) -> DocNames:
    """Return the document file names for a board whose fab name is ``fab_name``."""
    return DocNames(
        schematic=f"{fab_name}_schematic.pdf",
        assembly_pdf=f"{fab_name}_assembly_top.pdf",
        assembly_png=f"{fab_name}_assembly_top.png",
        top_copper=f"{fab_name}_top_copper.pdf",
        bottom_copper=f"{fab_name}_bottom_copper.pdf",
        render_iso=f"{fab_name}_render_iso.png",
        render_top=f"{fab_name}_render_top.png",
        render_bottom=f"{fab_name}_render_bottom.png",
    )


def _rsvg_convert(tool: str, args: Sequence[str]) -> None:
    """Run rsvg-convert; raise a ClickException with its output if it fails."""
    done = env._run([tool, *args], timeout=TIMEOUT_RSVG)
    if done.returncode != 0:
        reason = done.error or f"exit {done.returncode}"
        tail = "\n".join(done.output.strip().splitlines()[-6:])
        raise click.ClickException(f"rsvg-convert failed ({reason})\n{tail}".rstrip())


def assembly_drawing(pcb: Path, pdf: Path, png: Path) -> None:
    """Draw the board's F.Fab outline and edge, black on white, as a PDF and a PNG."""
    tool = env.find_rsvg_convert()
    if tool is None:
        raise click.ClickException(
            "rsvg-convert not found: the assembly drawing needs it "
            "(brew install librsvg; `pcbkit doctor` shows what is missing)"
        )
    with tempfile.TemporaryDirectory() as folder:
        svg = Path(folder) / "assembly.svg"
        cli.export_svg(pcb, svg, ["F.Fab", "Edge.Cuts"], black_and_white=True)
        _rsvg_convert(
            tool.path, ["-f", "pdf", "-z", PDF_ZOOM, "-b", "white"] + _to(pdf, svg)
        )
        _rsvg_convert(tool.path, ["-z", PNG_ZOOM, "-b", "white"] + _to(png, svg))


def _to(out: Path, source: Path) -> list[str]:
    """Return the tail of an rsvg-convert command: where to write, what to read."""
    return ["-o", str(out), str(source)]


def renders(pcb: Path, names: DocNames, folder: Path) -> list[Path]:
    """Render the board in 3D: an isometric view, then the top and the bottom.

    The views are ``pcbkit.shots.RENDER_VIEWS``, so the fab export and ``pcbkit
    shots`` make the same three pictures; these file names start with the fab name.
    """
    from pcbkit.shots import render_views

    prefix = names.render_iso[: -len("render_iso.png")]
    return render_views(pcb, folder, prefix=prefix)


def export_documents(
    sch: Path, pcb: Path, folder: Path, fab_name: str, *, render: bool = True
) -> list[Path]:
    """Write the documents into ``folder`` and return the files, in the order written.

    ``folder`` must exist. The 3D renders are made only when ``render`` is true.
    """
    names = doc_names(fab_name)
    cli.export_sch_pdf(sch, folder / names.schematic)
    assembly_drawing(pcb, folder / names.assembly_pdf, folder / names.assembly_png)
    cli.export_pcb_pdf(pcb, folder / names.top_copper, ["F.Cu", "F.SilkS", "Edge.Cuts"])
    cli.export_pcb_pdf(pcb, folder / names.bottom_copper, ["B.Cu", "Edge.Cuts"])
    written = [
        folder / names.schematic,
        folder / names.assembly_pdf,
        folder / names.assembly_png,
        folder / names.top_copper,
        folder / names.bottom_copper,
    ]
    if render:
        written += renders(pcb, names, folder)
    return written
