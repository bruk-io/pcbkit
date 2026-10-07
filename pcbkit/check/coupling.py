r"""Sensitive signals must not run alongside high-current or switching copper for long.

Metric: for every sensitive net and every aggressor group, the length of the sensitive
net's tracks whose edge is within ``d_near`` of the aggressor's copper (same layer, edge
to edge; on the opposite layer the distance is hypot(planar gap, h)). Each step of
track counts with the 3H weight 1/(1 + (d/h)^2), so a touching run counts in full and
one at ``d_near`` counts 10 %. The weighted length ("effective length") is compared
with Lmax, which is computed at the touching distance, the same reference the weight
is normalised to. The raw length inside ``d_near`` is reported too. A pair fails when
the effective length exceeds Lmax for the net's class. Aggressor copper is the tracks,
filled zones and pads of the aggressor's nets; for a "bar" only the wide tracks (at
least ``bar_min_w`` mm), not the pour.

The basis of the numbers:

``d_near`` = 3 x h, h the board thickness (the "3H" rule). With no solid return plane,
crosstalk between two traces falls roughly as 1/(1 + (d/h)^2) with spacing d
(microstrip field model). At d = 3h that is 10 % of the touching value, so copper
farther away is treated as not coupled.

Inductive coupling ("current" and "bar" aggressors). Two parallel filaments of length
l at centre distance d have (Grover)

    M = (mu0 / 2pi) * l * [asinh(l/d) - sqrt(1 + (d/l)^2) + d/l]

and the induced series noise on the victim is V = M * dI/dt. Worked example: l = 10 mm,
d = 4.1 mm gives M = 1.9 nH, so V = 9.5 mV at 5 A/us. A class carries its own dI/dt.

Capacitive coupling ("switch" aggressors, the nodes of switching regulators). The
victim sees the smaller of V = Z * C' * l * dV/dt (edge slower than the node's RC) and
V = dV * C' * l / Cn (edge faster than the RC: charge sharing with the node
capacitance Cn). Z is the victim's source impedance and C' the coupling capacitance
per mm, about 0.05 pF/mm for two 0.25 mm coplanar traces a width apart on FR4.

Noise budgets (peak induced voltage allowed from one mechanism) are per class, set by
the board in ``Config.classes``: (budget in V, victim source impedance in ohm, dI/dt in
A/s). Lmax is the length at which the estimate reaches the budget, evaluated at the
closest approach (centre distance (w_victim + w_aggressor) / 2), the worst case inside
``d_near``.

A board also lists ``filters``: a node with an RC filter downstream of the coupling
(``filter_credit``). The attenuation the filter buys multiplies the budget, and the
filter capacitor must sit near the pin it protects.

A pair of tracks that forms a measurement loop (a shunt's sense pair) is judged as a
loop, not as two traces: common-mode pickup cancels, and what matters is the flux
through the area the pair encloses (``loop_area``, ``max_loop_area_mm2``).
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt

from pcbkit.check import _pcb
from pcbkit.check.netlist import Netlist, parse_value
from pcbkit.kicad.board import OX, OY

MU0_2PI = 2e-7  # mu0 / 2pi (H/m)
KINDS = ("bar", "current", "switch")

Region = tuple[float, float, float, float]


def slash(net: str) -> str:
    """Return a net name as KiCad writes it: with one leading "/"."""
    return "/" + net.lstrip("/")


@dataclass(frozen=True)
class Aggressor:
    """A group of copper that couples into its neighbours.

    ``kind`` is "bar" (only tracks at least ``Config.bar_min_w`` wide: the wide copper
    that carries a return current), "current" (all copper of the nets: tracks, pads
    and filled zones) or "switch" (a switching node, which couples by capacitance).
    ``region``, when given, is ``(x0, y0, x1, y1)`` in layout mm: only copper inside
    it counts.
    """

    nets: tuple[str, ...]
    kind: str
    region: Region | None = None


@dataclass(frozen=True)
class Config:
    """What a board tells the coupling check.

    ``classes`` maps a class name to ``(noise budget V, victim source ohm, dI/dt A/s)``;
    ``sensitive`` maps a class to its net names; ``aggressors`` maps a group name to its
    ``Aggressor``. The rest are physical constants with defaults: the raster step, the
    board thickness ``h_board``, the coupling capacitance per mm, the switch-node swing
    and edge rate, and the victim node's capacitance (``cn_net`` has per-net values).
    """

    classes: Mapping[str, tuple[float, float, float]]
    sensitive: Mapping[str, Sequence[str]]
    aggressors: Mapping[str, Aggressor]
    h_board: float = 1.6
    res: float = 0.05
    c_per_mm: float = 0.05e-12
    dv_dt: float = 8.5 / 10e-9
    dv_sw: float = 8.5
    cn_default: float = 10e-12
    cn_net: Mapping[str, float] = field(default_factory=dict)
    bar_min_w: float = 3.0
    bar_w_cap: float = 7.6

    @property
    def d_near(self) -> float:
        """Return the coupling window, edge to edge (mm): three board thicknesses."""
        return 3 * self.h_board

    @property
    def all_sensitive(self) -> list[str]:
        """Return every sensitive net, in the order the classes list them."""
        return [slash(n) for ns in self.sensitive.values() for n in ns]


def from_spec(spec: Mapping[str, Any], h_board: float = 1.6) -> Config:
    """Build a Config from a project's ``COUPLING`` table.

    The table has ``classes``, ``sensitive`` and ``aggressors`` (each aggressor a dict
    with ``nets``, ``kind`` and optionally ``region``), and may set any other Config
    field. ``h_board`` is the board thickness from ``[stackup] thickness_mm``. A key
    that is not a Config field, or a kind that is not bar, current or switch, raises
    ValueError, so a typo does not pass silently. Net names may be written with or
    without the leading "/".
    """
    known = {"classes", "sensitive", "aggressors", "filters"} | {
        f for f in Config.__dataclass_fields__ if f != "h_board"
    }
    unknown = sorted(set(spec) - known)
    if unknown:
        raise ValueError(f"COUPLING has unknown keys {unknown}; known: {sorted(known)}")
    aggressors = {}
    for name, a in spec["aggressors"].items():
        if a["kind"] not in KINDS:
            raise ValueError(f"aggressor {name!r}: kind {a['kind']!r} is not {KINDS}")
        region = tuple(a["region"]) if a.get("region") else None
        aggressors[name] = Aggressor(
            tuple(slash(n) for n in a["nets"]), a["kind"], region
        )
    rest = {
        k: v
        for k, v in spec.items()
        if k not in ("classes", "sensitive", "aggressors", "filters", "cn_net")
    }
    cn_net = {slash(n): c for n, c in spec.get("cn_net", {}).items()}
    sensitive = {c: [slash(n) for n in ns] for c, ns in spec["sensitive"].items()}
    return Config(
        classes=dict(spec["classes"]),
        sensitive=sensitive,
        aggressors=aggressors,
        h_board=h_board,
        cn_net=cn_net,
        **rest,
    )


def mutual_inductance(l_mm: float, d_mm: float) -> float:
    """Return the mutual inductance (H) of two parallel equal filaments."""
    length, d = l_mm * 1e-3, d_mm * 1e-3
    return (
        MU0_2PI
        * length
        * (math.asinh(length / d) - math.sqrt(1 + (d / length) ** 2) + d / length)
    )


def class_of(cfg: Config, net: str) -> str:
    """Return the class of a sensitive net."""
    return next(c for c, ns in cfg.sensitive.items() if net in ns)


def lmax_mm(
    cfg: Config,
    net: str,
    kind: str,
    w_victim: float,
    w_agg: float,
    credit: float = 1.0,
) -> float:
    """Return the touching-distance length (mm) at which the noise reaches the budget.

    ``credit`` is the attenuation a filter downstream of the coupling buys (the budget
    is multiplied by it).
    """
    budget, z, di_dt = cfg.classes[class_of(cfg, net)]
    budget *= credit
    if kind == "switch":
        v_per_mm = min(
            z * cfg.c_per_mm * cfg.dv_dt,
            cfg.dv_sw * cfg.c_per_mm / cfg.cn_net.get(net, cfg.cn_default),
        )
        return budget / v_per_mm
    d = (w_victim + min(w_agg, cfg.bar_w_cap)) / 2
    lo, hi = 1e-3, 1e4
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if mutual_inductance(mid, d) * di_dt < budget:
            lo = mid
        else:
            hi = mid
    return lo


def _draw(dr: Any, ps: Any, res: float) -> None:
    """Paint a polygon set (outlines filled, holes cleared) onto a raster."""
    to_mm = _pcb.to_mm

    def pts(chain: Any) -> list[Any]:
        return [
            (
                (to_mm(chain.CPoint(i).x) - OX) / res,
                (to_mm(chain.CPoint(i).y) - OY) / res,
            )
            for i in range(chain.PointCount())
        ]

    for i in range(ps.OutlineCount()):
        dr.polygon(pts(ps.COutline(i)), fill=1)
        for h in range(ps.HoleCount(i)):
            dr.polygon(pts(ps.CHole(i, h)), fill=0)


class Field:
    """Aggressor copper per group and layer, as edge-distance maps (mm)."""

    def __init__(self, board: Any, extent: tuple[float, float], cfg: Config) -> None:
        """Rasterise each aggressor group's copper and take its distance transform."""
        pcbnew = _pcb.pcbnew()
        to_mm = _pcb.to_mm
        res = cfg.res
        self.nx = int((extent[0] - OX + 2) / res) + 1
        self.ny = int((extent[1] - OY + 2) / res) + 1
        self.dist: dict[Any, Any] = {}
        self.width: dict[str, float] = {}
        err = pcbnew.FromMM(0.005)
        for name, agg in cfg.aggressors.items():
            nets, kind = agg.nets, agg.kind
            widths = [0.0]
            for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                img = Image.new("1", (self.nx, self.ny), 0)
                dr = ImageDraw.Draw(img)
                items = []
                for t in board.GetTracks():
                    if (
                        t.GetNetname() not in nets
                        or not t.IsOnLayer(layer)
                        or t.GetClass() == "PCB_VIA"
                    ):
                        continue
                    if kind == "bar" and to_mm(t.GetWidth()) < cfg.bar_min_w:
                        continue
                    widths.append(to_mm(t.GetWidth()))
                    items.append(t)
                if kind != "bar":
                    items += [
                        p
                        for fp in board.GetFootprints()
                        for p in fp.Pads()
                        if p.GetNetname() in nets and p.IsOnLayer(layer)
                    ]
                    for z in board.Zones():
                        if (
                            z.GetNetname() in nets
                            and not z.GetIsRuleArea()
                            and z.IsOnLayer(layer)
                        ):
                            _draw(dr, z.GetFilledPolysList(layer), res)
                for it in items:
                    ps = pcbnew.SHAPE_POLY_SET()
                    it.TransformShapeToPolygon(ps, layer, 0, err, pcbnew.ERROR_INSIDE)
                    _draw(dr, ps, res)
                m = np.array(img, dtype=bool)
                if agg.region:  # only the copper inside the aggressor's region
                    x0, y0, x1, y1 = agg.region
                    keep = np.zeros_like(m)
                    keep[
                        max(0, int(y0 / res)) : int(y1 / res) + 1,
                        max(0, int(x0 / res)) : int(x1 / res) + 1,
                    ] = True
                    m &= keep
                self.dist[name, layer] = (
                    distance_transform_edt(~m) * res if m.any() else None
                )
            self.width[name] = max(widths)


def extent_of(board: Any) -> tuple[float, float]:
    """Return the right and bottom edge (KiCad mm) of the board outline."""
    bb = board.GetBoardEdgesBoundingBox()
    return _pcb.to_mm(bb.GetRight()), _pcb.to_mm(bb.GetBottom())


def analyse(
    board: Any,
    cfg: Config,
    nets: Sequence[str] | None = None,
    extent: tuple[float, float] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return ``{net: {group: {length, raw, width, width_agg, bbox}}}`` for the nets.

    ``length`` is the 3H-weighted length (mm) of the net's tracks near the group's
    copper, ``raw`` the plain length inside ``cfg.d_near``, ``width`` the widest
    victim track there, ``width_agg`` the widest aggressor track and ``bbox`` the
    ``[x0, y0, x1, y1]`` of the run in layout mm. Groups the net never comes near are
    left out. ``nets`` defaults to every sensitive net.
    """
    pcbnew = _pcb.pcbnew()
    to_mm = _pcb.to_mm
    res_mm = cfg.res
    d_near, h = cfg.d_near, cfg.h_board
    field_ = Field(board, extent or extent_of(board), cfg)
    res: dict[str, dict[str, dict[str, Any]]] = {
        n: {} for n in (nets or cfg.all_sensitive)
    }
    for t in board.GetTracks():
        n = t.GetNetname()
        if n not in res or t.GetClass() == "PCB_VIA":
            continue
        if t.GetClass() == "PCB_ARC":
            chain = [t.GetStart(), t.GetMid(), t.GetEnd()]
        else:
            chain = [t.GetStart(), t.GetEnd()]
        xy = [(to_mm(q.x) - OX, to_mm(q.y) - OY) for q in chain]
        ws = to_mm(t.GetWidth())
        layer = t.GetLayer()
        other = pcbnew.B_Cu if layer == pcbnew.F_Cu else pcbnew.F_Cu
        for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            k = max(1, int(seg / res_mm))
            s = (np.arange(k) + 0.5) / k
            p = np.stack([x0 + (x1 - x0) * s, y0 + (y1 - y0) * s], axis=1)
            ix = np.clip((p[:, 0] / res_mm).round().astype(int), 0, field_.nx - 1)
            iy = np.clip((p[:, 1] / res_mm).round().astype(int), 0, field_.ny - 1)
            for g in cfg.aggressors:
                d = np.full(k, np.inf)  # distance to the aggressor, edge to edge
                if field_.dist[g, layer] is not None:
                    d = np.maximum(field_.dist[g, layer][iy, ix] - ws / 2, 0.0)
                if field_.dist[g, other] is not None:  # far layer: gap and thickness
                    gap = np.maximum(field_.dist[g, other][iy, ix] - ws / 2, 0.0)
                    d = np.minimum(d, np.hypot(gap, h))
                near = d <= d_near
                if not near.any():
                    continue
                e = res[n].setdefault(
                    g,
                    {
                        "length": 0.0,
                        "raw": 0.0,
                        "pts": [],
                        "width": ws,
                        "width_agg": field_.width[g],
                    },
                )
                e["raw"] += near.sum() * seg / k
                e["length"] += (1 / (1 + (d[near] / h) ** 2)).sum() * seg / k
                e["pts"].append(p[near])
                e["width"] = max(e["width"], ws)
    for groups in res.values():
        for e in groups.values():
            allp = np.concatenate(e.pop("pts"))
            e["bbox"] = [
                round(float(v), 1) for v in (*allp.min(axis=0), *allp.max(axis=0))
            ]
    return res


def limit(
    cfg: Config, net: str, group: str, e: Mapping[str, Any], credit: float = 1.0
) -> float:
    """Return Lmax (mm) for a net beside a group, given its entry from ``analyse``."""
    return lmax_mm(
        cfg,
        net,
        cfg.aggressors[group].kind,
        e["width"],
        e["width_agg"],
        credit,
    )


def violations(
    cfg: Config,
    res: Mapping[str, Mapping[str, Mapping[str, Any]]],
    credit: Mapping[str, float] | None = None,
) -> list[tuple[str, str, float, float, Any]]:
    """Return ``(net, group, effective mm, Lmax mm, bbox)`` for each pair over."""
    credit = credit or {}
    return [
        (
            n,
            g,
            round(e["length"], 1),
            round(limit(cfg, n, g, e, credit.get(n, 1.0)), 1),
            e["bbox"],
        )
        for n, groups in res.items()
        for g, e in groups.items()
        if e["length"] > limit(cfg, n, g, e, credit.get(n, 1.0))
    ]


def filter_credit(
    nl: Netlist, net: str, t_edge: float, ground: str = "GND"
) -> tuple[float, float]:
    """Return ``(attenuation, tau seconds)`` of the RC filter on a node, 1.0 if none.

    The resistors on the net (in parallel, as the Thevenin source) and the capacitors
    from the net to ``ground`` make tau. A pulse induced along the trace lasts about
    ``t_edge`` seconds (the servo-current edge, say), and the filter passes about
    t_edge / tau of its peak. Both are read from the netlist, so removing or shrinking
    the capacitor removes the credit.
    """
    name = net.lstrip("/")
    rs = [
        parse_value(nl.parts[r]["value"])
        for r in nl.by_kind("Device:R")
        if name in (nl.net(r, 1), nl.net(r, 2))
    ]
    cs = [
        parse_value(nl.parts[c]["value"])
        for c in nl.by_kind("Device:C", "Device:C_Polarized")
        if {nl.net(c, 1), nl.net(c, 2)} == {name, ground}
    ]
    if not rs or not cs:
        return 1.0, 0.0
    tau = 1 / sum(1 / r for r in rs) * sum(cs)
    return min(1.0, t_edge / tau), tau


def max_loop_area_mm2(m_max_h: float, distance_mm: float) -> float:
    """Return the largest loop area (mm2) with a mutual inductance under ``m_max_h``.

    For a small loop at distance r from a current, M is about mu0 * A / (2 pi r), so
    A_max = M_max * 2 pi r / mu0, here in mm2 with r in mm.
    """
    return m_max_h * 2 * math.pi * distance_mm * 1e-3 / (4e-7 * math.pi) * 1e6


def _track_path(
    board: Any, net: str, a: Sequence[float], z: Sequence[float]
) -> list[Any]:
    """Return the shortest chain of track segments (layout mm points) from a to z."""
    to_mm = _pcb.to_mm
    adj: dict[Any, list[Any]] = {}
    for t in board.GetTracks():
        if t.GetNetname() != net or t.GetClass() != "PCB_TRACK":
            continue
        s = (round(to_mm(t.GetStart().x) - OX, 3), round(to_mm(t.GetStart().y) - OY, 3))
        e = (round(to_mm(t.GetEnd().x) - OX, 3), round(to_mm(t.GetEnd().y) - OY, 3))
        adj.setdefault(s, []).append((e, math.dist(s, e)))
        adj.setdefault(e, []).append((s, math.dist(s, e)))
    s, g = (min(adj, key=lambda q: math.dist(p, q)) for p in (a, z))
    dist: dict[Any, float] = {s: 0.0}
    prev: dict[Any, Any] = {}
    pq = [(0.0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == g:
            break
        for v, w in adj[u]:
            if d + w < dist.get(v, 1e9):
                dist[v], prev[v] = d + w, u
                heapq.heappush(pq, (d + w, v))
    out = [g]
    while out[-1] != s:
        out.append(prev[out[-1]])
    return [tuple(a)] + out[::-1] + [tuple(z)]


def loop_area(
    board: Any,
    nets: tuple[str, str],
    from_ref: str,
    to_ref: str,
    to_pads: Collection[str],
) -> tuple[float, float, float]:
    """Return ``(area mm2, length of first net mm, length of second net mm)`` of a pair.

    The pair is two nets that run from ``from_ref`` (every pad of it on those nets) to
    ``to_ref`` (only its pads numbered ``to_pads``). The loop is the polygon of the
    first net's track path from one part to the other and back along the second's.
    """
    to_mm = _pcb.to_mm
    names = tuple(slash(n) for n in nets)
    pads = {
        (f.GetReference(), p.GetNetname()): (to_mm(p.GetX()) - OX, to_mm(p.GetY()) - OY)
        for f in board.GetFootprints()
        if f.GetReference() in (from_ref, to_ref)
        for p in f.Pads()
        if p.GetNetname() in names
        and (f.GetReference() == from_ref or p.GetNumber() in to_pads)
    }
    paths = [
        _track_path(board, net, pads[(from_ref, net)], pads[(to_ref, net)])
        for net in names
    ]
    poly = paths[0] + paths[1][::-1]
    area = (
        abs(
            sum(
                x0 * y1 - x1 * y0
                for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1])
            )
        )
        / 2
    )

    def length(path: Sequence[Any]) -> float:
        return sum(math.dist(a, b) for a, b in zip(path, path[1:]))

    return area, length(paths[0]), length(paths[1])
