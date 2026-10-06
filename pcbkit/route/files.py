"""The names of the files the routing stages hand to each other.

All of them live in the project's ``kicad/`` folder and are named after the board's
stem, as the board scripts have always named them. ``golden/`` keeps three of them
(``prerouted.kicad_pcb``, the session file and the DSN): see ``golden_files``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pcbkit.project import Project


@dataclass(frozen=True)
class RouteFiles:
    """Where each routing stage reads and writes, for one project.

    ``placed`` is what ``pcbkit build`` writes and ``pre`` reads. ``prerouted`` is the
    board with the hand-made copper and keep-outs and no autorouted copper; ``dsn``
    is its Specctra export, ``ses`` is what Freerouting wrote, and ``pcb`` is the
    finished board that ``post`` writes. ``routed_nozones`` is ``pcb`` before the
    pours. ``drc`` is the DRC report and ``log`` Freerouting's console output.
    """

    stem: str
    kicad: Path
    placed: Path
    prerouted: Path
    routed_nozones: Path
    pcb: Path
    dsn: Path
    ses: Path
    drc: Path
    log: Path
    schematic: Path


def route_files(project: Project) -> RouteFiles:
    """Return the routing file names of ``project``."""
    stem = project.config.board.stem
    kicad = project.kicad_dir
    return RouteFiles(
        stem=stem,
        kicad=kicad,
        placed=kicad / "placed.kicad_pcb",
        prerouted=kicad / "prerouted.kicad_pcb",
        routed_nozones=kicad / "routed_nozones.kicad_pcb",
        pcb=kicad / f"{stem}.kicad_pcb",
        dsn=kicad / f"{stem}.dsn",
        ses=kicad / f"{stem}.ses",
        drc=kicad / "drc.rpt",
        log=kicad / "freerouting.log",
        schematic=kicad / f"{stem}.kicad_sch",
    )


def golden_files(project: Project) -> dict[str, Path]:
    """Return the files of ``golden/`` by role: "prerouted", "ses" and "dsn"."""
    stem = project.config.board.stem
    folder = project.golden_dir
    return {
        "prerouted": folder / "prerouted.kicad_pcb",
        "ses": folder / f"{stem}.ses",
        "dsn": folder / f"{stem}.dsn",
    }
