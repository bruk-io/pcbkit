"""Ground stitching between the top and bottom pours is dense enough.

Metric: on a 0.5 mm grid, every point where BOTH layers carry ground fill, the distance
to the nearest ground via (stitching or otherwise) or ground plated through-hole pad.
It is reported as the maximum and the 99th percentile.

Basis of the limits (``Basis``):

* A complete square grid of pitch P has its worst point at the cell centre, P / sqrt(2).
  99 % of the points must meet that: the grid's own design intent.
* RF rule of thumb: a return-path via fence no wider than lambda / 20 at the top of the
  highest band the board radiates in (by default Wi-Fi's 2.4 GHz band, channel 14's
  upper edge, the shortest wavelength). The wavelength is taken in an effective
  permittivity of (er + 1) / 2, a crude half-in-the-air microstrip estimate.
* A point beyond lambda / 20 from a ground via is allowed only as a sliver: each
  connected patch of such points must itself be smaller than lambda / 20 across. The
  hazard is an unstitched patch large enough to carry a resonance; a sliver beside a pad
  or along the board edge, too narrow to fit a via, is not one.

Distances are in KiCad file millimetres; ``local`` converts a point to layout ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from pcbkit.check import _pcb
from pcbkit.kicad.board import OX, OY

GRID = 0.5  # mm, sampling pitch of the check
LIGHT_SPEED = 299792458.0  # m/s


@dataclass(frozen=True)
class Basis:
    """The numbers the stitching limits are derived from.

    ``pitch_mm`` is the grid pitch of the stitching vias (``[stitch] pitch_mm``);
    ``f_hz`` the highest frequency that matters (2.484 GHz is the top of the Wi-Fi
    band's channel 14, the shortest wavelength there); ``er_fr4`` the board's
    permittivity at that frequency (4.5 is typical FR4 at 2.4 GHz).
    """

    pitch_mm: float
    f_hz: float = 2.484e9
    er_fr4: float = 4.5

    @property
    def er_eff(self) -> float:
        """Return the effective permittivity, taking half the field as in air."""
        return (self.er_fr4 + 1) / 2

    @property
    def lambda_mm(self) -> float:
        """Return the wavelength (mm) at ``f_hz`` in the effective permittivity."""
        return LIGHT_SPEED / self.f_hz / math.sqrt(self.er_eff) * 1e3

    @property
    def rf_limit_mm(self) -> float:
        """Return lambda / 20: the widest unstitched stretch the RF rule allows."""
        return self.lambda_mm / 20

    @property
    def grid_worst_mm(self) -> float:
        """Return the worst distance a complete grid of ``pitch_mm`` leaves."""
        return self.pitch_mm / math.sqrt(2)


def gnd_via_points(board: Any, net: str = "/GND") -> Any:
    """Return the KiCad-frame (x, y) mm of every ground via and plated ground pad."""
    pcbnew = _pcb.pcbnew()
    to_mm = _pcb.to_mm
    pts = []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA" and t.GetNetname() == net:
            p = t.GetPosition()
            pts.append((to_mm(p.x), to_mm(p.y)))
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if (
                pad.GetNetname() == net
                and pad.HasHole()
                and pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
            ):
                p = pad.GetPosition()
                pts.append((to_mm(p.x), to_mm(p.y)))
    return np.array(pts)


def stitching_gaps(board: Any, net: str = "/GND") -> tuple[Any, Any]:
    """Return (distances, points): each grid point with ground fill on both layers.

    ``distances`` is the distance from each point to the nearest ground via or pad.
    """
    pcbnew = _pcb.pcbnew()
    to_mm = _pcb.to_mm
    from scipy.spatial import cKDTree

    polys = {}
    for z in board.Zones():
        if (
            z.GetNetname() == net
            and not z.GetIsRuleArea()
            and z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu)
        ):
            polys[z.GetLayer()] = z.GetFilledPolysList(z.GetLayer())
    ft, fb = polys[pcbnew.F_Cu], polys[pcbnew.B_Cu]
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0 = to_mm(bb.GetX()), to_mm(bb.GetY())
    x1, y1 = x0 + to_mm(bb.GetWidth()), y0 + to_mm(bb.GetHeight())
    found = []
    for y in np.arange(y0 + GRID / 2, y1, GRID):
        for x in np.arange(x0 + GRID / 2, x1, GRID):
            v = pcbnew.VECTOR2I(pcbnew.FromMM(float(x)), pcbnew.FromMM(float(y)))
            if ft.Contains(v) and fb.Contains(v):
                found.append((x, y))
    pts = np.array(found)
    vias = gnd_via_points(board, net)
    d, _ = cKDTree(vias).query(pts)
    return d, pts


def summarise(board: Any, net: str = "/GND") -> tuple[float, float, Any, Any, Any]:
    """Return (max, 99th percentile, distances, points, indices worst first)."""
    d, pts = stitching_gaps(board, net)
    order = np.argsort(-d)
    return float(d.max()), float(np.percentile(d, 99)), d, pts, order


def local(p: Any) -> tuple[float, float]:
    """Return a KiCad-frame point as layout millimetres, to 0.1 mm."""
    return (round(float(p[0]) - OX, 1), round(float(p[1]) - OY, 1))


def worst_locations(
    d: Any, pts: Any, order: Any, limit: float, n: int = 5, sep: float = 4.0
) -> list[tuple[tuple[float, float], float]]:
    """Return up to ``n`` worst points beyond ``limit``, ``sep`` mm apart."""
    out: list[tuple[Any, Any]] = []
    for i in order:
        if d[i] <= limit or len(out) >= n:
            break
        if all(math.hypot(pts[i][0] - q[0], pts[i][1] - q[1]) > sep for q, _ in out):
            out.append((pts[i], d[i]))
    return [(local(p), round(float(dd), 2)) for p, dd in out]


def far_patches(
    d: Any, pts: Any, limit: float
) -> list[tuple[float, float, tuple[float, float]]]:
    """Return ``(extent, max distance, centre)`` of each patch beyond ``limit``.

    A patch is a set of 8-connected grid points; ``extent`` is its diagonal plus one
    grid step and the centre is in layout mm. The list is sorted largest first.
    """
    far = {
        (round(float(x) / GRID), round(float(y) / GRID)): (
            float(x),
            float(y),
            float(dd),
        )
        for (x, y), dd in zip(pts, d)
        if dd > limit
    }
    seen: set[tuple[int, int]] = set()
    out = []
    for k in far:
        if k in seen:
            continue
        stack, cells = [k], []
        seen.add(k)
        while stack:
            c = stack.pop()
            cells.append(far[c])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (c[0] + dx, c[1] + dy)
                    if n in far and n not in seen:
                        seen.add(n)
                        stack.append(n)
        xs, ys = [c[0] for c in cells], [c[1] for c in cells]
        extent = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) + GRID
        out.append(
            (
                round(extent, 2),
                round(max(c[2] for c in cells), 2),
                local((sum(xs) / len(xs), sum(ys) / len(ys))),
            )
        )
    return sorted(out, reverse=True)
