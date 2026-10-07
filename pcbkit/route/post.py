"""The stage after Freerouting: import its route, make the pours, tidy the copper.

``post`` turns ``kicad/prerouted.kicad_pcb`` plus Freerouting's session file into the
finished board ``kicad/<stem>.kicad_pcb``. In order:

1. reload ``prerouted.kicad_pcb`` with the rules applied (see ``route.rules`` for why),
   and import the session file;
2. run the project's ``gnd_links`` and ``zones`` hooks, and give the pads of
   ``solid_pad_refs`` solid ground connections;
3. fill the pours with ground islands kept, so stitching vias can rescue them, then
   stitch the two ground pours together (``route.stitch``) with ``[stitch]``'s grid
   and dense boxes, fill again without islands, and take back stitching vias that
   ended up tying only orphan islands;
4. add vias where the grid left ground far from any ground via (``[stitch]
   gap_limit_mm``);
5. clean up (``route.cleanup``): vias that carry copper on one layer only, and dead-end
   tracks and fragments, filling the pours again if anything went;
6. copy each part's BOM fields from ``design.py`` onto its footprint, so DRC's
   schematic parity matches, save, and write the stackup's copper weight last.

Nothing here is specific to one board: what a board needs on top comes from its
``routing.py`` and ``pcbkit.toml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pcbkit.design import Design, load_design
from pcbkit.kicad import board as kb
from pcbkit.project import Project
from pcbkit.route import cleanup, stitch
from pcbkit.route.files import route_files
from pcbkit.route.hooks import board_size, load_hooks
from pcbkit.route.rules import apply_rules

# A stitching via is dropped when it is no longer inside both filled pours; this many
# fill-and-drop rounds are run at most.
DROP_ROUNDS = 3


@dataclass(frozen=True)
class PostResult:
    """What ``post`` did.

    ``stitching_vias`` counts the vias that tie the ground pours together (grid, dense
    boxes and gap fillers, less the ones taken back). ``dropped`` counts the vias,
    tracks and fragments the clean-up removed or cut back; the first of its two jobs
    is dropping one-sided vias, which gives the count its usual name.
    ``ses_imported`` is False if pcbnew refused the session file: the board then has
    only its pre-routes.
    """

    pcb: Path
    ses_imported: bool
    stitching_vias: int
    dropped: int

    def summary(self) -> str:
        """Return the line the stage reports."""
        return (
            f"ses import {self.ses_imported}\n"
            f"zones filled, stitching vias: {self.stitching_vias}, "
            f"dropped one-sided vias: {self.dropped}"
        )


def sync_fields(board: Any, design: Design) -> None:
    """Copy each part's BOM fields from ``design`` onto its footprint, hidden.

    The fields are Description, and MPN and Manufacturer where the design gives them.
    DRC's schematic parity compares them with the schematic's, so they must match.
    """
    import pcbnew

    for part in design.parts:
        fp = board.FindFootprintByReference(part["ref"])
        if fp is None:
            continue
        fields = {"Description": part["desc"]}
        if part["mpn"]:
            fields["MPN"] = part["mpn"]
        if part["mfr"]:
            fields["Manufacturer"] = part["mfr"]
        for name, value in fields.items():
            fp.SetField(name, value)
            field = fp.GetField(name)
            field.SetLayer(pcbnew.F_Fab)
            field.SetVisible(False)  # a footprint text's hidden flag isn't saved


def _use_solid_pads(board: Any, refs: frozenset[str]) -> None:
    """Join the ground pads of the footprints in ``refs`` to the pour solid."""
    import pcbnew

    for fp in board.GetFootprints():
        if fp.GetReference() in refs:
            for pad in fp.Pads():
                if pad.GetNetname() == "/" + stitch.GROUND:
                    pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)


def _set_islands(board: Any, mode: int, only_ground: bool) -> None:
    """Set the island-removal mode of the ground pours, or of every pour."""
    for zone in board.Zones():
        if only_ground:
            if zone.GetNetname() == "/" + stitch.GROUND:
                zone.SetIslandRemovalMode(mode)
        elif not zone.GetIsRuleArea():
            zone.SetIslandRemovalMode(mode)


def _drop_orphan_vias(board: Any, stitched: list[Any], filler: Any) -> int:
    """Take back stitching vias that are not inside both pours; return how many.

    A via that ended up tying only orphan islands together goes, and the pours are
    filled again; up to DROP_ROUNDS times, or until none is left.
    """
    import pcbnew

    taken = 0
    for _ in range(DROP_ROUNDS):
        pours = stitch.ground_pours(board)
        if pours is None:
            break
        ft = pours[0].GetFilledPolysList(pcbnew.F_Cu)
        fb = pours[1].GetFilledPolysList(pcbnew.B_Cu)
        dead = [
            v
            for v in stitched
            if not (ft.Contains(v.GetPosition()) and fb.Contains(v.GetPosition()))
        ]
        if not dead:
            break
        for v in dead:
            kb.remove(board, v)
            stitched[:] = [s for s in stitched if s is not v]
            taken += 1
        filler.Fill(board.Zones())
    return taken


def post(project: Project) -> PostResult:
    """Import the router's session and finish the board; return what it did.

    Reads ``kicad/prerouted.kicad_pcb`` and ``kicad/<stem>.ses`` and writes
    ``kicad/routed_nozones.kicad_pcb`` (before the pours) and the finished
    ``kicad/<stem>.kicad_pcb``.
    """
    import pcbnew

    files = route_files(project)
    config = project.config
    hooks = load_hooks(project)
    width, height = board_size(project)

    # KiCad keeps design rules and net classes in the .kicad_pro, which a copied
    # .kicad_pcb doesn't carry, and the zone filler reads the rules compiled at load
    # time. So apply them, save (which writes the .kicad_pro) and reload before
    # filling: a rebuild from golden/ then fills exactly like the original run.
    prerouted = str(files.prerouted)
    board = pcbnew.LoadBoard(prerouted)
    apply_rules(board, hooks, config.stackup.layers)
    pcbnew.SaveBoard(prerouted, board)
    board = pcbnew.LoadBoard(prerouted)
    ses_imported = bool(pcbnew.ImportSpecctraSES(board, str(files.ses)))
    pcbnew.SaveBoard(str(files.routed_nozones), board)

    # Fine-pitch ground pads that only reach the pour through thin necks get their
    # own copper before the pours exist; then the pours.
    if hooks.gnd_links is not None:
        hooks.gnd_links(board, kb)
    if hooks.zones is not None:
        hooks.zones(board, kb)
    _use_solid_pads(board, hooks.solid_pad_refs)
    filler = pcbnew.ZONE_FILLER(board)

    # first fill keeps ground islands so stitching vias can rescue them
    _set_islands(board, pcbnew.ISLAND_REMOVAL_MODE_NEVER, only_ground=True)
    filler.Fill(board.Zones())
    stitched = stitch.stitch(board, width, height, config.stitch.pitch_mm)
    for x0, y0, x1, y1, pitch in config.stitch.dense:
        stitched += stitch.stitch(board, width, height, pitch, box=(x0, y0, x1, y1))
    count = len(stitched)
    _set_islands(board, pcbnew.ISLAND_REMOVAL_MODE_ALWAYS, only_ground=False)
    filler.Fill(board.Zones())
    count -= _drop_orphan_vias(board, stitched, filler)
    count += stitch.fill_gaps(board, width, height, config.stitch.gap_limit_mm)
    filler.Fill(board.Zones())

    dropped = cleanup.drop_one_sided_vias(board) + cleanup.trim_dead_ends(board)
    if dropped:
        filler.Fill(board.Zones())
    sync_fields(board, load_design(project.root / "design.py"))
    pcbnew.SaveBoard(str(files.pcb), board)
    # KiCad 10's Python cannot reach the stackup, so the saved file is rewritten, after
    # the last save (which would write the old weight back)
    kb.set_copper(str(files.pcb), config.stackup.copper_mm)
    return PostResult(files.pcb, ses_imported, count, dropped)
