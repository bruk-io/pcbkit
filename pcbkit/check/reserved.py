"""Copper inside a rectangle: for regions reserved for one job.

A board keeps some regions for particular nets (a return-current strip, solid ground
under a switching node, the ground under an antenna). Freerouting only keeps clear of
a region when the project adds a keep-out for it, so a check reads the routed copper
and lists whatever does not belong. ``copper_in_rect`` does the reading;
``intruders`` applies a region's rule.

Rectangles are in layout millimetres, as ``layout.py`` writes them.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from pcbkit.check import _pcb
from pcbkit.kicad.board import OX, OY

# Overlaps smaller than this (mm2) are rounding, not copper.
MIN_AREA_MM2 = 0.01

Hit = tuple[str, str, float]  # (what, "top" or "bottom", area in mm2)


def rect_poly(x0: float, y0: float, x1: float, y1: float) -> Any:
    """Return the rectangle (layout mm) as a pcbnew polygon set in KiCad's frame."""
    pcbnew = _pcb.pcbnew()
    ps = pcbnew.SHAPE_POLY_SET()
    ps.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        ps.Append(pcbnew.FromMM(x + OX), pcbnew.FromMM(y + OY))
    return ps


def copper_in_rect(
    board: Any,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    skip_refs: Collection[str] = (),
) -> list[Hit]:
    """Return ``(what, layer, area mm2)`` for each piece of copper overlapping the box.

    ``what`` reads ``zone NET``, ``track NET``, ``via NET`` or ``pad REF.NUM NET``, so
    the net is its last word; ``layer`` is "top" or "bottom". Pads of the references in
    ``skip_refs`` are ignored. Overlaps under 0.01 mm2 are not reported.
    """
    pcbnew = _pcb.pcbnew()
    hits: list[Hit] = []
    err = pcbnew.FromMM(0.005)
    for layer, name in ((pcbnew.F_Cu, "top"), (pcbnew.B_Cu, "bottom")):
        polys = []
        for z in board.Zones():
            if not z.GetIsRuleArea() and z.IsOnLayer(layer):
                polys.append(("zone " + z.GetNetname(), z.GetFilledPolysList(layer)))
        for t in board.GetTracks():
            if t.IsOnLayer(layer):
                ps = pcbnew.SHAPE_POLY_SET()
                t.TransformShapeToPolygon(ps, layer, 0, err, pcbnew.ERROR_INSIDE)
                kind = "via" if t.GetClass() == "PCB_VIA" else "track"
                polys.append((f"{kind} {t.GetNetname()}", ps))
        for f in board.GetFootprints():
            if f.GetReference() in skip_refs:
                continue
            for p in f.Pads():
                if p.IsOnLayer(layer):
                    ps = pcbnew.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(ps, layer, 0, err, pcbnew.ERROR_INSIDE)
                    label = f"pad {f.GetReference()}.{p.GetNumber()} {p.GetNetname()}"
                    polys.append((label, ps))
        for what, ps in polys:
            r = rect_poly(x0, y0, x1, y1)
            r.BooleanIntersection(ps)
            a = r.Area() / 1e12
            if a > MIN_AREA_MM2:
                hits.append((what, name, round(a, 2)))
    return hits


def intruders(
    board: Any,
    layer: str,
    box: Sequence[float],
    allowed: Collection[str],
) -> list[tuple[str, float]]:
    """Return ``(what, area)`` of the copper in ``box`` that does not belong there.

    ``layer`` is "top" or "bottom"; ``box`` is ``(x0, y0, x1, y1)``; ``allowed`` holds
    the nets that may be there, spelled as KiCad names them ("/GND").
    """
    if layer not in ("top", "bottom"):
        raise ValueError(f"layer must be 'top' or 'bottom', not {layer!r}")
    return [
        (what, area)
        for what, lay, area in copper_in_rect(board, *box)
        if lay == layer and what.split()[-1] not in allowed
    ]
