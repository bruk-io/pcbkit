"""DC current-flow solver over the real copper of one net, and the IPC-2221 rise.

``NetCopper`` rasterises every piece of copper on the net (filled zones, tracks, pads)
on both layers, joins the layers at vias and plated holes, then solves Laplace's
equation with scipy for given current injections. It gives path resistance, voltage
drop, I^2R loss and a current-density map.

The copper thickness is an argument, not a constant: pass the board's
``[stackup] copper_mm`` (0.035 for 1 oz, 0.070 for 2 oz) to both ``NetCopper`` and
``ipc2221_rise``, so the drop and the heating agree with the board that is ordered.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Union

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from PIL import Image, ImageDraw
from scipy.sparse import csgraph

from pcbkit.check import _pcb
from pcbkit.kicad.board import OX, OY

RHO_CU = 1.72e-8  # ohm*m at 20 C
PLATING = 20e-6  # via barrel plating, m
OZ_MM = 0.035  # one ounce of finished copper, mm

Cell = tuple[Any, int, int]  # (layer, row, column) of the raster
Terminal = tuple[Sequence[Cell], float]
StrPath = Union[str, Path]


def thickness_m(copper_mm: float) -> float:
    """Return a copper thickness in metres, rounded to a picometre.

    The rounding makes 0.035 mm come out as exactly 35e-6; a plain 0.035 * 1e-3 is
    3.5000000000000004e-05.
    """
    return round(copper_mm * 1e-3, 12)


def _chain_pts(chain: Any, org: tuple[float, float], res: float) -> list[Any]:
    """Return a polygon chain's points as raster coordinates."""
    to_mm = _pcb.to_mm
    return [
        (
            (to_mm(chain.CPoint(i).x) - org[0]) / res,
            (to_mm(chain.CPoint(i).y) - org[1]) / res,
        )
        for i in range(chain.PointCount())
    ]


class NetCopper:
    """The copper of one net as a resistor network on a raster of ``res`` mm cells."""

    def __init__(
        self,
        board: Any,
        net: str,
        res: float = 0.1,
        W: float | None = None,
        H: float | None = None,
        copper_mm: float = OZ_MM,
    ) -> None:
        """Rasterise net ``net`` (KiCad's name, with its leading "/") on ``board``.

        ``W`` and ``H`` are the raster's size in layout millimetres; by default it
        covers the board outline plus 1 mm (a connector may overhang the edge).
        """
        pcbnew = _pcb.pcbnew()
        to_mm = _pcb.to_mm
        self.board, self.net, self.res = board, net, res
        self.copper_mm = copper_mm
        self.t_cu = thickness_m(copper_mm)
        self.layers = (pcbnew.F_Cu, pcbnew.B_Cu)
        self.org = (OX, OY)
        if W is None or H is None:
            # raster the whole outline (+1 mm for a connector that overhangs the edge)
            bb = board.GetBoardEdgesBoundingBox()
            W = to_mm(bb.GetRight()) - OX + 1.0
            H = to_mm(bb.GetBottom()) - OY + 1.0
        self.nx, self.ny = int(math.ceil(W / res)) + 1, int(math.ceil(H / res)) + 1
        self.masks: dict[Any, Any] = {}
        err = pcbnew.FromMM(0.005)
        for layer in self.layers:
            img = Image.new("1", (self.nx, self.ny), 0)
            dr = ImageDraw.Draw(img)
            for z in board.Zones():
                if z.GetNetname() != net or z.GetIsRuleArea() or not z.IsOnLayer(layer):
                    continue
                self._draw(dr, z.GetFilledPolysList(layer))
            items = [
                t
                for t in board.GetTracks()
                if t.GetNetname() == net and t.IsOnLayer(layer)
            ]
            pads = [
                p
                for fp in board.GetFootprints()
                for p in fp.Pads()
                if p.GetNetname() == net and p.IsOnLayer(layer)
            ]
            for it in items + pads:
                ps = pcbnew.SHAPE_POLY_SET()
                it.TransformShapeToPolygon(ps, layer, 0, err, pcbnew.ERROR_INSIDE)
                self._draw(dr, ps)
            self.masks[layer] = np.array(img, dtype=bool)
        # layer joints: vias and plated holes of this net
        self.joints: list[tuple[float, float, float, float]] = []
        for t in board.GetTracks():
            if t.GetClass() == "PCB_VIA" and t.GetNetname() == net:
                d = to_mm(t.GetDrillValue())
                g = (math.pi * d * 1e-3 * PLATING) / (RHO_CU * 1.6e-3)
                self.joints.append(
                    (to_mm(t.GetX()) - OX, to_mm(t.GetY()) - OY, d / 2 + 0.1, g)
                )
        for fp in board.GetFootprints():
            for p in fp.Pads():
                plated = p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
                if p.GetNetname() == net and p.HasHole() and plated:
                    d = to_mm(p.GetDrillSize().x)
                    # soldered component lead: treat as a near-ideal joint
                    self.joints.append(
                        (to_mm(p.GetX()) - OX, to_mm(p.GetY()) - OY, d / 2 + 0.1, 1e4)
                    )

    def _draw(self, dr: Any, ps: Any) -> None:
        """Paint a polygon set (outlines filled, holes cleared) onto the raster."""
        for i in range(ps.OutlineCount()):
            dr.polygon(_chain_pts(ps.COutline(i), self.org, self.res), fill=1)
            for h in range(ps.HoleCount(i)):
                dr.polygon(_chain_pts(ps.CHole(i, h), self.org, self.res), fill=0)

    def pad_cells(self, ref: str, num: Any) -> list[Cell]:
        """Return the raster cells (layer, row, column) a pad covers, on every layer."""
        pcbnew = _pcb.pcbnew()
        fp = next(f for f in self.board.GetFootprints() if f.GetReference() == ref)
        pads = [p for p in fp.Pads() if p.GetNumber() == str(num)]
        out: list[Cell] = []
        err = pcbnew.FromMM(0.005)
        for p in pads:
            for layer in self.layers:
                if not p.IsOnLayer(layer):
                    continue
                img = Image.new("1", (self.nx, self.ny), 0)
                ps = pcbnew.SHAPE_POLY_SET()
                p.TransformShapeToPolygon(ps, layer, 0, err, pcbnew.ERROR_INSIDE)
                self._draw(ImageDraw.Draw(img), ps)
                m = np.array(img, dtype=bool) & self.masks[layer]
                for iy, ix in zip(*np.nonzero(m)):
                    out.append((layer, iy, ix))
        if not out:
            raise ValueError(f"pad {ref}.{num} has no copper on net {self.net}")
        return out

    def solve(self, terminals: Sequence[Terminal]) -> dict[str, Any]:
        """Solve the net for current injections; return voltages and loss.

        ``terminals`` is a list of ``(cells, amps)``; positive amps enter the copper
        there. The last terminal is the reference (0 V) and absorbs the balance. The
        result has ``v_terminals`` (one voltage per terminal) and ``loss_w``; the
        current density per mm of width is left in ``self.jw``.
        """
        idx: dict[Cell, int] = {}
        cells: list[Cell] = []
        for layer in self.layers:
            ys, xs = np.nonzero(self.masks[layer])
            for iy, ix in zip(ys, xs):
                idx[(layer, iy, ix)] = len(cells)
                cells.append((layer, iy, ix))
        n = len(cells)
        gs = self.t_cu / RHO_CU  # sheet conductance per square
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []

        def edge(a: int, b: int, g: float) -> None:
            rows.extend((a, b, a, b))
            cols.extend((a, b, b, a))
            vals.extend((g, g, -g, -g))

        for (layer, iy, ix), i in idx.items():
            for dy, dx in ((0, 1), (1, 0)):
                j = idx.get((layer, iy + dy, ix + dx))
                if j is not None:
                    edge(i, j, gs)
        front, back = self.layers
        for x, y, r, g in self.joints:
            cx, cy, rc = x / self.res, y / self.res, r / self.res
            fr = [
                idx[(front, iy, ix)]
                for iy in range(int(cy - rc), int(cy + rc) + 1)
                for ix in range(int(cx - rc), int(cx + rc) + 1)
                if (ix - cx) ** 2 + (iy - cy) ** 2 <= rc * rc and (front, iy, ix) in idx
            ]
            bk = [
                idx[(back, iy, ix)]
                for iy in range(int(cy - rc), int(cy + rc) + 1)
                for ix in range(int(cx - rc), int(cx + rc) + 1)
                if (ix - cx) ** 2 + (iy - cy) ** 2 <= rc * rc and (back, iy, ix) in idx
            ]
            if fr and bk:
                gpair = g / (len(fr) * len(bk))
                for a in fr:
                    for b in bk:
                        edge(a, b, gpair)
        rhs = np.zeros(n)
        term_idx = []
        for cl, amps in terminals:
            ii = [idx[c] for c in cl if c in idx]
            term_idx.append(ii)
            # tie a terminal's cells together (pin/solder is far better than the copper)
            for a in ii[1:]:
                edge(ii[0], a, 1e3)
            rhs[ii[0]] += amps
        G = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))
        ref = term_idx[-1][0]
        keep = np.ones(n, dtype=bool)
        keep[ref] = False
        # only cells connected to the reference are solvable
        _, lab = csgraph.connected_components(G, directed=False)
        reach = lab == lab[ref]
        for ii, (_, amps) in zip(term_idx, terminals):
            if amps and not reach[ii[0]]:
                raise ValueError(
                    f"terminal not connected to reference through copper on {self.net}"
                )
        sel = keep & reach
        Gr = G[sel][:, sel]
        V = np.zeros(n)
        V[sel] = spla.spsolve(Gr.tocsc(), rhs[sel])
        self.cells, self.idx, self.V = cells, idx, V
        # per-cell current density magnitude (A per mm of width) and loss
        jw = {layer: np.zeros((self.ny, self.nx)) for layer in self.layers}
        ploss = 0.0
        for (layer, iy, ix), i in idx.items():
            jx = jy = 0.0
            for dy, dx in ((0, 1), (1, 0)):
                j = idx.get((layer, iy + dy, ix + dx))
                if j is not None:
                    cur = gs * (V[i] - V[j])
                    ploss += cur * cur / gs
                    if dx:
                        jx = cur
                    else:
                        jy = cur
            jw[layer][iy, ix] = math.hypot(jx, jy) / self.res  # A per mm of width
        self.jw = jw
        vt = [V[ii[0]] for ii in term_idx]
        return {"v_terminals": vt, "loss_w": ploss}

    def heatmap(self, path: StrPath, title: str) -> None:
        """Save the current-per-width map of both layers, cropped to the current."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        front, back = self.layers
        both = np.maximum(self.jw[front], self.jw[back])
        vmax = float(np.percentile(both[both > 0], 99.5)) if (both > 0).any() else 1.0
        ys, xs = np.nonzero(both > 0.03 * vmax)
        pad = int(3 / self.res)
        y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, self.ny)
        x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad, self.nx)
        ext = (x0 * self.res, x1 * self.res, y1 * self.res, y0 * self.res)
        fig, axs = plt.subplots(1, 2, figsize=(10, 4.2))
        for ax, layer, name in zip(axs, self.layers, ("Top copper", "Bottom copper")):
            m = self.masks[layer][y0:y1, x0:x1]
            ax.imshow(
                np.where(m, 1.0, np.nan), cmap="Greys", vmin=0, vmax=3, extent=ext
            )
            data = np.where(m, self.jw[layer][y0:y1, x0:x1], np.nan)
            im = ax.imshow(data, cmap="inferno", vmin=0, vmax=vmax, extent=ext)
            ax.set_title(name, fontsize=9)
            ax.set_xlabel("x (mm)")
            ax.set_ylabel("y (mm)")
        fig.colorbar(im, ax=axs, shrink=0.85, label="A per mm of copper width")
        fig.suptitle(title, fontsize=10)
        fig.savefig(str(path), dpi=120, bbox_inches="tight")
        plt.close(fig)


def ipc2221_rise(
    amps: float, width_mm: float, copper_mm: float = OZ_MM, external: bool = True
) -> float:
    """Return the temperature rise (C) of a trace carrying ``amps``, from IPC-2221.

    IPC-2221 gives I = k * dT^0.44 * A^0.725 with A in square mils, k = 0.048 for an
    external conductor and 0.024 for an internal one. The area comes from the width
    and the finished copper thickness ``copper_mm`` (pass ``[stackup] copper_mm``;
    0.035 mm is 1 oz, and the rise is about three times higher at 1 oz than at 2 oz for
    the same trace and current).
    """
    oz = copper_mm / OZ_MM
    k = 0.048 if external else 0.024
    area = width_mm / 0.0254 * oz * 1.378
    return (amps / (k * area**0.725)) ** (1 / 0.44)
