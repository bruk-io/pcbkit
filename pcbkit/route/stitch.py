"""Ground stitching: vias that tie the top and bottom ground pours together.

Two passes put them in, both after the pours are filled once:

* ``stitch`` drops vias on a grid wherever both ground pours have solid copper. The
  base grid covers the board with ``[stitch] pitch_mm``; each ``[stitch] dense`` box
  gets a finer grid of its own (under a switching regulator, say).
* ``fill_gaps`` then adds vias wherever the grid left both pours further than
  ``[stitch] gap_limit_mm`` from any ground via or plated ground pad, so no patch of
  two-layer ground is left unstitched. Pick the limit a little under a twentieth of
  a wavelength at the highest frequency you care about.

The ground net must be called ``GND``, and each stitching pass needs a ground pour on
both layers (``ground_pours`` finds them).
"""

from __future__ import annotations

import math
from typing import Any

from pcbkit.kicad import board as kb

GROUND = "GND"

# Stitching and gap vias: size, drill and the ring (mm) that must be solid around one.
VIA_MM = 0.7
DRILL_MM = 0.3
PROBE_MARGIN_MM = 0.55
# A via keeps this far (mm) from any hole: the hole's radius, the new via's radius and
# the drill-to-drill gap. They are added one after the other, as they always were.
HOLE_WALL_MM = 0.3
HOLE_GAP_MM = 0.5


def ground_pours(board: Any) -> tuple[Any, Any] | None:
    """Return the GND pour on F.Cu and the one on B.Cu, or None if either is missing."""
    import pcbnew

    found = []
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        pours = [
            z
            for z in board.Zones()
            if z.GetNetname() == "/" + GROUND and z.GetLayer() == layer
        ]
        if not pours:
            return None
        found.append(pours[0])
    return found[0], found[1]


def _holes(board: Any) -> list[tuple[tuple[float, float], float]]:
    """Return every drilled hole as ((x, y), radius to keep clear), layout mm."""
    import pcbnew

    holes = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.HasHole():
                size = max(p.GetSize().x, p.GetSize().y)
                holes.append((kb.to_local(p.GetPosition()), pcbnew.ToMM(size) / 2))
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            holes.append((kb.to_local(t.GetPosition()), 0.4))
    return holes


def _new_via(board: Any, x: float, y: float, via_d: float, drill: float) -> Any:
    """Return an unplaced via of the given size at layout position (x, y)."""
    import pcbnew

    via = pcbnew.PCB_VIA(board)
    via.SetPosition(kb.pt(x, y))
    via.SetWidth(kb.mm(via_d))
    via.SetDrill(kb.mm(drill))
    return via


def stitch(
    board: Any,
    width: float,
    height: float,
    pitch: float = 5.0,
    via_d: float = VIA_MM,
    drill: float = DRILL_MM,
    margin: float = PROBE_MARGIN_MM,
    box: tuple[float, float, float, float] | None = None,
) -> list[Any]:
    """Drop GND vias on a grid wherever both GND pours have solid copper.

    The grid has ``pitch`` mm between vias and covers ``box`` (x0, y0, x1, y1 in
    layout mm), or the board less a 2 mm margin on the top and left and 1.5 mm on the
    bottom and right. A via goes in where the pours are solid at its centre and all
    round a ring of ``margin`` mm, and where it stays clear of every hole. Return the
    vias added, so the caller can take back any that turn out to do nothing.
    """
    import pcbnew

    pours = ground_pours(board)
    if pours is None:
        return []
    gnd = kb.N(board, GROUND)
    ft = pours[0].GetFilledPolysList(pcbnew.F_Cu)
    fb = pours[1].GetFilledPolysList(pcbnew.B_Cu)
    holes = _holes(board)
    added: list[Any] = []
    bx0, by0, bx1, by1 = box if box else (2.0, 2.0, width - 1.5, height - 1.5)
    y = by0
    while y < by1:
        x = bx0
        while x < bx1:
            ok = True
            probes = [(x, y)] + [
                (
                    x + margin * math.cos(a * math.pi / 4),
                    y + margin * math.sin(a * math.pi / 4),
                )
                for a in range(8)
            ]
            for px, py in probes:
                v = kb.pt(px, py)
                if not ft.Contains(v) or not fb.Contains(v):
                    ok = False
                    break
            if ok:
                for (hx, hy), r in holes:
                    if math.hypot(hx - x, hy - y) < r + HOLE_WALL_MM + HOLE_GAP_MM:
                        ok = False
                        break
            if ok:
                via = _new_via(board, x, y, via_d, drill)
                via.SetNet(gnd)
                board.Add(via)
                added.append(via)
                holes.append(((x, y), 0.3))
            x += pitch
        y += pitch
    return added


def fill_gaps(
    board: Any,
    width: float,
    height: float,
    limit: float = 3.4,
    via_d: float = VIA_MM,
    drill: float = DRILL_MM,
    margin: float = PROBE_MARGIN_MM,
    reach: float = 3.0,
    grid: float = 0.5,
) -> int:
    """Add GND vias where the stitch grid left both pours far from any GND via.

    Every point with ground on both layers must be within ``limit`` mm of a GND via
    or a plated GND pad. A via goes at the worst point, or at the nearest spot within
    ``reach`` that passes ``stitch``'s rules. A second pass handles strips too narrow
    for the probe ring: a 0.6 mm via whose centre is in both pours and whose copper
    clears every other-net item by 0.2 mm (KiCad's own shape test). Return how many
    vias were added.
    """
    import numpy as np
    import pcbnew
    from scipy.spatial import cKDTree

    pours = ground_pours(board)
    if pours is None:
        return 0
    gnd = kb.N(board, GROUND)
    ft = pours[0].GetFilledPolysList(pcbnew.F_Cu)
    fb = pours[1].GetFilledPolysList(pcbnew.B_Cu)
    holes: list[tuple[tuple[float, float], float]] = []
    anchors: list[tuple[float, float]] = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.HasHole():
                size = max(p.GetSize().x, p.GetSize().y)
                holes.append((kb.to_local(p.GetPosition()), pcbnew.ToMM(size) / 2))
                if p.GetNetname() == "/" + GROUND and (
                    p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
                ):
                    anchors.append(kb.to_local(p.GetPosition()))
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            holes.append((kb.to_local(t.GetPosition()), 0.4))
            if t.GetNetname() == "/" + GROUND:
                anchors.append(kb.to_local(t.GetPosition()))

    def both(x: float, y: float) -> bool:
        v = kb.pt(float(x), float(y))
        return bool(ft.Contains(v) and fb.Contains(v))

    keepouts = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    # a via's mask opening would clip a footprint's silkscreen text
    silk = []
    for fp in board.GetFootprints():
        for it in list(fp.GraphicalItems()) + [fp.Reference(), fp.Value()]:
            if (
                it.GetClass() == "PCB_TEXT"
                and it.IsVisible()
                and it.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS)
            ):
                bb = it.GetBoundingBox()
                silk.append(
                    (
                        pcbnew.ToMM(bb.GetX()) - kb.OX,
                        pcbnew.ToMM(bb.GetY()) - kb.OY,
                        pcbnew.ToMM(bb.GetRight()) - kb.OX,
                        pcbnew.ToMM(bb.GetBottom()) - kb.OY,
                    )
                )

    def legal(x: float, y: float) -> bool:
        probes = [(x, y)] + [
            (
                x + margin * math.cos(a * math.pi / 4),
                y + margin * math.sin(a * math.pi / 4),
            )
            for a in range(8)
        ]
        if any(
            z.Outline().Collide(kb.pt(float(x), float(y)), kb.mm(via_d / 2 + 0.1))
            for z in keepouts
        ):
            return False
        r = via_d / 2 + 0.15
        if any(x0 - r < x < x1 + r and y0 - r < y < y1 + r for x0, y0, x1, y1 in silk):
            return False
        return all(both(px, py) for px, py in probes) and all(
            math.hypot(hx - x, hy - y) >= r + HOLE_WALL_MM + HOLE_GAP_MM
            for (hx, hy), r in holes
        )

    pts = np.array(
        [
            (x, y)
            for y in np.arange(grid / 2, height, grid)
            for x in np.arange(grid / 2, width, grid)
            if both(x, y)
        ]
    )
    offsets = sorted(
        (
            (dx, dy)
            for dx in np.arange(-reach, reach + 0.01, 0.25)
            for dy in np.arange(-reach, reach + 0.01, 0.25)
            if math.hypot(dx, dy) <= reach
        ),
        key=lambda o: math.hypot(*o),
    )
    added, tried = 0, set()
    while True:
        d, _ = cKDTree(np.array(anchors)).query(pts)
        bad = [i for i in np.argsort(-d) if d[i] > limit and i not in tried]
        if not bad:
            break
        i = bad[0]
        tried.add(i)
        x0, y0 = pts[i]
        for dx, dy in offsets:
            x, y = float(x0 + dx), float(y0 + dy)
            if legal(x, y):
                via = _new_via(board, x, y, via_d, drill)
                via.SetNet(gnd)
                board.Add(via)
                holes.append(((x, y), 0.3))
                anchors.append((x, y))
                added += 1
                break
    # second pass for strips too narrow for the probe ring
    others = [
        (layer, o.GetEffectiveShape(layer))
        for o in list(board.GetTracks())
        + [q for f in board.GetFootprints() for q in f.Pads()]
        if o.GetNetname() != "/" + GROUND
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu)
        if o.IsOnLayer(layer)
    ]
    d, _ = cKDTree(np.array(anchors)).query(pts)
    for i in [i for i in np.argsort(-d) if d[i] > limit]:
        if cKDTree(np.array(anchors)).query(pts[i])[0] <= limit:
            continue
        x0, y0 = pts[i]
        for dx, dy in offsets:
            x, y = float(x0 + dx), float(y0 + dy)
            if not both(x, y) or any(
                z.Outline().Collide(kb.pt(x, y), kb.mm(0.4)) for z in keepouts
            ):
                continue
            if any(
                math.hypot(hx - x, hy - y) < r + HOLE_WALL_MM + HOLE_GAP_MM
                for (hx, hy), r in holes
            ):
                continue
            if any(
                x0s - 0.45 < x < x1s + 0.45 and y0s - 0.45 < y < y1s + 0.45
                for x0s, y0s, x1s, y1s in silk
            ):
                continue
            via = _new_via(board, x, y, 0.6, drill)
            if any(
                via.GetEffectiveShape(layer).Collide(shape, kb.mm(0.2))
                for layer, shape in others
            ):
                continue
            via.SetNet(gnd)
            board.Add(via)
            holes.append(((x, y), 0.3))
            anchors.append((x, y))
            added += 1
            break
    return added
