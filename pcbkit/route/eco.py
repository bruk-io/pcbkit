"""The eco stage: keep a previous route and only route what a change broke.

``eco`` is ``pre`` that keeps a previous route. It runs ``pre`` (new placement, rules,
pre-routes and keep-outs), then copies the routed copper of ``base`` (a folder like
``golden/``, with ``prerouted.kicad_pcb`` and ``<stem>.ses``) onto the new board, drops
the copied pieces that now clash with anything that changed, and locks the rest.
Freerouting then only completes what is missing, so a small placement or netlist
change does not route the whole board again.

Two rules keep Freerouting from hanging on the board it is handed:

* Copied ground copper is skipped. Ground comes from the pours, and copied ground
  fragments can stop Freerouting while it loads the board ("normalization of net ...
  failed", then no routing), so the router redoes them.
* Copper on the changed parts' nets, and copper within ``[route] eco_unlock_reach_mm``
  of a changed pad, stays unlocked so Freerouting may rework it: a new connector can
  otherwise be boxed in by the frozen route around it.

If Freerouting still stalls on an eco board, ``pcbkit route`` falls back to routing the
whole board (see ``route.freerouting``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from pcbkit.kicad import board as kb
from pcbkit.project import Project
from pcbkit.route import pre as pre_stage
from pcbkit.route.files import route_files

# Positions closer than this (10 um, in nm) are the same place: a session import rounds
# to half a micrometre.
SAME_PLACE_NM = 10000
# Copper this close (mm) to a changed pad or new pre-route piece is a clash.
CLASH_GAP_MM = 0.3
GROUND_NET = "/GND"


@dataclass(frozen=True)
class EcoResult:
    """What ``eco`` did: what changed, and what became of the base route.

    ``changed`` lists the footprints that are new or moved; ``new_preroute_pieces``
    counts the pre-route tracks and vias the base route did not have. ``kept``
    pieces were copied from the base (``unlocked`` of them left free for Freerouting
    to rework) and ``dropped`` ones were left out because they clash with a change.
    """

    changed: tuple[str, ...]
    new_preroute_pieces: int
    kept: int
    unlocked: int
    dropped: int
    base: Path

    def summary(self) -> str:
        """Return the two lines the stage reports."""
        return (
            f"changed footprints: {list(self.changed)} "
            f"new pre-route pieces: {self.new_preroute_pieces}\n"
            f"kept {self.kept} routed pieces from {self.base} "
            f"({self.unlocked} left unlocked near the change), "
            f"dropped {self.dropped} that clash with it"
        )


def _near(a: Any, b: Any) -> bool:
    """Return True if two points are within SAME_PLACE_NM on both axes."""
    return abs(a.x - b.x) < SAME_PLACE_NM and abs(a.y - b.y) < SAME_PLACE_NM


def _key(item: Any) -> tuple[str, int]:
    """Return the net and layer that group copper items (a via has no one layer)."""
    return (
        item.GetNetname(),
        item.GetLayer() if item.GetClass() == "PCB_TRACK" else -1,
    )


def _same_piece(a: Any, b: Any, either_way: bool = True) -> bool:
    """Return True if two items of one net and layer are the same via or track.

    A track matches when its ends are the other's, in the same order or, with
    ``either_way``, reversed. Anything that is not a via or a track never matches.
    """
    if a.GetClass() != b.GetClass():
        return False
    if a.GetClass() == "PCB_VIA":
        return _near(a.GetPosition(), b.GetPosition())
    if a.GetClass() != "PCB_TRACK":
        return False
    if _near(a.GetStart(), b.GetStart()) and _near(a.GetEnd(), b.GetEnd()):
        return True
    return either_way and (
        _near(a.GetStart(), b.GetEnd()) and _near(a.GetEnd(), b.GetStart())
    )


def _placement(footprint: Any) -> tuple[int, int, int]:
    """Return a footprint's position (in um) and angle, to see if it moved."""
    pos = footprint.GetPosition()
    return (
        round(pos.x / 1000),
        round(pos.y / 1000),
        round(footprint.GetOrientationDegrees()) % 360,
    )


def eco(project: Project, base: Path) -> EcoResult:
    """Prepare the board as ``pre`` does, keeping the route in the folder ``base``.

    Raise a ClickException if ``base`` has no ``prerouted.kicad_pcb`` and session file.
    """
    import pcbnew

    files = route_files(project)
    base = Path(base)
    base_pcb = base / "prerouted.kicad_pcb"
    base_ses = base / f"{files.stem}.ses"
    for needed in (base_pcb, base_ses):
        if not needed.is_file():
            raise click.ClickException(
                f"{needed} not found: --eco needs a folder like golden/, "
                f"with prerouted.kicad_pcb and {files.stem}.ses"
            )
    reach = kb.mm(project.config.route.eco_unlock_reach_mm)
    gap = kb.mm(CLASH_GAP_MM)
    pre_stage.pre(project)  # new placement, rules, pre-routes, keep-outs
    new = pcbnew.LoadBoard(str(files.prerouted))
    old = pcbnew.LoadBoard(str(base_pcb))
    if not pcbnew.ImportSpecctraSES(old, str(base_ses)):
        raise click.ClickException(f"pcbnew could not import {base_ses}")

    have: dict[tuple[str, int], list[Any]] = {}
    for item in new.GetTracks():
        have.setdefault(_key(item), []).append(item)

    def duplicate(item: Any) -> bool:
        return any(_same_piece(other, item) for other in have.get(_key(item), []))

    # what changed: footprints that are new or moved, and new pre-route copper
    old_places = {f.GetReference(): _placement(f) for f in old.GetFootprints()}
    changed_pads = [
        pad
        for f in new.GetFootprints()
        if old_places.get(f.GetReference()) != _placement(f)
        for pad in f.Pads()
    ]
    old_keys: dict[tuple[str, int], list[Any]] = {}
    for item in old.GetTracks():
        old_keys.setdefault(_key(item), []).append(item)
    new_copper = [
        item
        for item in new.GetTracks()
        if not any(
            _same_piece(other, item, either_way=False)
            for other in old_keys.get(_key(item), [])
        )
    ]
    changed = tuple(
        sorted({p.GetParentFootprint().GetReference() for p in changed_pads})
    )

    def clashes(item: Any) -> bool:
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            if not item.IsOnLayer(layer):
                continue
            shape = item.GetEffectiveShape(layer)
            for pad in changed_pads:
                if (
                    pad.IsOnLayer(layer)
                    and pad.GetNetname() != item.GetNetname()
                    and shape.Collide(pad.GetEffectiveShape(layer), gap)
                ):
                    return True
            for other in new_copper:
                if (
                    other.IsOnLayer(layer)
                    and other.GetNetname() != item.GetNetname()
                    and shape.Collide(other.GetEffectiveShape(layer), gap)
                ):
                    return True
        return False

    # copper on the changed parts' nets, or near them, stays unlocked
    changed_nets = {p.GetNetname() for p in changed_pads} - {GROUND_NET, ""}

    def near_change(item: Any) -> bool:
        if item.GetNetname() in changed_nets:
            return True
        points = (
            [item.GetPosition()]
            if item.GetClass() == "PCB_VIA"
            else [item.GetStart(), item.GetEnd()]
        )
        return any(
            (q - p.GetPosition()).EuclideanNorm() < reach
            for q in points
            for p in changed_pads
        )

    kept = dropped = unlocked = 0
    for item in list(old.GetTracks()):
        if duplicate(item):
            continue
        if item.GetNetname() == GROUND_NET:
            continue
        if new.FindNet(item.GetNetname()) is None or clashes(item):
            dropped += 1
            continue
        copy = item.Duplicate()
        copy.SetParent(new)
        copy.SetNet(new.FindNet(item.GetNetname()))
        loose = near_change(item)
        copy.SetLocked(not loose)
        unlocked += loose
        new.Add(copy)
        kept += 1
    pre_stage.save_both(new, files.pcb, files.prerouted)
    pre_stage.export_dsn(files.pcb, files.dsn)
    return EcoResult(changed, len(new_copper), kept, unlocked, dropped, base)
