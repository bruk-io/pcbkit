"""The Gerber and drill files for the fab house, and the zip they travel in.

``export`` plots the nine fabrication layers and the Excellon drill files (with their
maps and KiCad's job file) into one folder, from the board's bottom-left corner, then
zips every file in that folder. ``set_origin`` puts that corner in the board file,
because kicad-cli measures from the board's aux origin when asked to use the drill and
place origin.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from pcbkit.kicad import board as kb
from pcbkit.kicad import cli


def set_origin(pcb: Path, height_mm: float) -> None:
    """Make the bottom-left corner of the board the aux origin, and save the board.

    The board is loaded fresh and saved in place. Layout coordinates start at the
    board's top-left corner, so the corner is (0, ``height_mm``) in layout millimetres.
    """
    import pcbnew

    board = pcbnew.LoadBoard(str(pcb))
    board.GetDesignSettings().SetAuxOrigin(kb.pt(0, height_mm))
    pcbnew.SaveBoard(str(pcb), board)


def make_zip(folder: Path, zip_path: Path) -> list[str]:
    """Zip the files directly in ``folder``, by name, sorted; return the names."""
    names = sorted(item.name for item in folder.iterdir() if item.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(folder / name, name)
    return names


def export(pcb: Path, folder: Path, zip_path: Path) -> list[Path]:
    """Write the Gerbers, drill files, maps and job file into ``folder``, and zip them.

    ``folder`` is emptied first, so a file left from an earlier run cannot end up in the
    zip. The board's aux origin must already be its bottom-left corner (``set_origin``).
    Return every file written: the ones in ``folder`` by name, then the zip.
    """
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    cli.export_gerbers(pcb, folder)
    cli.export_drill(pcb, folder)
    names = make_zip(folder, zip_path)
    return [folder / name for name in names] + [zip_path]
