"""The stage before Freerouting: rules, hand-made copper, keep-outs, DSN export.

``pre`` loads the placed board that ``pcbkit build`` wrote and prepares it for the
autorouter:

1. applies the net classes and design rules (``route.rules``);
2. runs the project's ``prerouted`` hook, which hand-routes what the autorouter must
   not decide (locked copper, so Freerouting routes around it);
3. runs the project's ``keepouts`` hook;
4. adds a keep-out strip along every board edge, because the autorouter only knows
   the outline and routed copper must stay clear of it;
5. saves the board as ``<stem>.kicad_pcb`` and ``prerouted.kicad_pcb`` and exports the
   Specctra DSN that Freerouting reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from pcbkit.kicad import board as kb
from pcbkit.project import Project
from pcbkit.route.files import route_files
from pcbkit.route.hooks import board_size, load_hooks
from pcbkit.route.rules import apply_rules

# Routed copper stays this far (mm) from every board edge: 0.3 mm of copper-to-edge
# clearance plus a little for the track's own width.
EDGE_KEEPOUT_MM = 0.45


@dataclass(frozen=True)
class PreResult:
    """What ``pre`` wrote: the boards and the DSN."""

    pcb: Path
    prerouted: Path
    dsn: Path


def edge_strips(
    width: float, height: float, depth: float = EDGE_KEEPOUT_MM
) -> list[tuple[float, float, float, float]]:
    """Return the four keep-out strips (x0, y0, x1, y1) along a board's edges.

    The top and bottom strips span the full width; the left and right ones span the
    full height, so the corners are covered twice.
    """
    return [
        (0, 0, width, depth),
        (0, height - depth, width, height),
        (0, 0, depth, height),
        (width - depth, 0, width, height),
    ]


def export_dsn(pcb: Path, dsn: Path) -> None:
    """Export the Specctra DSN of the board at ``pcb``; raise if pcbnew refuses."""
    import pcbnew

    board = pcbnew.LoadBoard(str(pcb))
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        raise click.ClickException(
            f"pcbnew could not export the Specctra DSN of {pcb.name}"
        )


def pre(project: Project) -> PreResult:
    """Prepare ``kicad/placed.kicad_pcb`` for Freerouting and export its DSN.

    Raise a ClickException if there is no placed board (run ``pcbkit build``) or the
    DSN cannot be exported.
    """
    import pcbnew

    files = route_files(project)
    if not files.placed.is_file():
        raise click.ClickException(
            f"{files.placed} not found: run `pcbkit build` first"
        )
    hooks = load_hooks(project)
    width, height = board_size(project)
    board = pcbnew.LoadBoard(str(files.placed))
    apply_rules(board, hooks, project.config.stackup.layers)
    hooks.prerouted(board, kb)
    if hooks.keepouts is not None:
        hooks.keepouts(board, kb)
    for x0, y0, x1, y1 in edge_strips(width, height):
        kb.keepout(board, x0, y0, x1, y1, pours=False)
    save_both(board, files.pcb, files.prerouted)
    export_dsn(files.pcb, files.dsn)
    return PreResult(files.pcb, files.prerouted, files.dsn)


def save_both(board: Any, *paths: Path) -> None:
    """Save ``board`` to each of ``paths``, in order."""
    import pcbnew

    for path in paths:
        pcbnew.SaveBoard(str(path), board)
