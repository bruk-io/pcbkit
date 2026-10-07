"""pcbnew helpers for the board generators: units, nets, pads, copper, text, shields.

Coordinates. Every helper here takes and returns *layout* millimetres: measured from
the board's top-left corner, x to the right, y down, which is how ``layout.py`` and the
routing hooks write them. KiCad's own page has no such corner, so a board's coordinates
in KiCad's files sit at a fixed offset from layout ones: ``OX, OY = 50, 50`` mm, the
same on every board. ``pt`` adds the offset on the way into pcbnew (layout mm to a
VECTOR2I) and ``to_local`` takes it off on the way out; ``mm`` converts millimetres to
KiCad's internal units (an int count of nanometres) and adds nothing. Positions printed
in a DRC or ERC report and numbers read from a saved board are *file* coordinates:
subtract ``OX, OY`` to get layout ones. Nets are named without KiCad's leading "/":
``N(board, "VIN")`` finds the net "/VIN".

pcbnew is imported when a helper first needs it, so this module imports on a machine
with no KiCad. The import goes through ``env.import_pcbnew``, which also hides pcbnew's
wx noise (see ``pcbkit.kicad.quiet``). The helpers that need no pcbnew (``rect``,
``segment_distance``, ``point_in_track``, ``remove``, ``set_copper``) work anywhere; a
tier 2 command calls ``pcbkit.kicad.env.require_pcbnew`` before it uses the rest.

Four pcbnew hazards on KiCad 10 are shielded here, so no caller has to remember them:

* ``mm`` turns numpy scalars into plain floats, and so do ``pt`` and every helper built
  on it. pcbnew's ``FromMM`` checks ``type(x) in [int, float]``, so even ``np.float64``,
  which is a float subclass, raises TypeError.
* ``remove`` keeps every removed item referenced for the life of the process. Letting
  Python free one breaks pcbnew's list wrappers for the whole process (see ``remove``).
* ``point_in_track`` does pure geometry where ``PCB_TRACK.HitTest`` would be called.
  pcbkit never calls HitTest, and a unit test fails if any module under pcbkit/ does.
* ``set_copper`` rewrites the copper thickness in a saved board's text, because
  KiCad 10's Python cannot reach the stackup.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

from pcbkit.kicad import env

# KiCad's file coordinates minus layout coordinates, in millimetres (see above).
OX = 50.0
OY = 50.0

Point = Sequence[float]


def _pcbnew() -> Any:
    """Return the pcbnew module, imported on first use, with its wx noise quieted."""
    return env.import_pcbnew()


def _plain(v: Any) -> Any:
    """Return v as a plain float if it is a number pcbnew refuses, such as np.float64.

    Anything else is returned unchanged: ints and floats, and also what pcbnew itself
    handles or rejects (vectors, bools, strings).
    """
    if type(v) in (int, float) or isinstance(v, bool) or not hasattr(v, "__float__"):
        return v
    return float(v)


# --- units and lookups -----------------------------------------------------------


def mm(v: Any) -> Any:
    """Return v millimetres in KiCad's internal units (an int count of nanometres)."""
    return _pcbnew().FromMM(_plain(v))


def pt(x: Any, y: Any) -> Any:
    """Return layout position (x, y) mm as a VECTOR2I: the offset added."""
    return _pcbnew().VECTOR2I(mm(OX + x), mm(OY + y))


def to_local(v: Any) -> tuple[float, float]:
    """Return a VECTOR2I as layout millimetres (x, y): the offset taken off."""
    pcbnew = _pcbnew()
    return (pcbnew.ToMM(_plain(v.x)) - OX, pcbnew.ToMM(_plain(v.y)) - OY)


def N(board: Any, name: str) -> Any:
    """Return the net called "/" + name on the board, or raise KeyError."""
    n = board.FindNet("/" + name)
    if n is None:
        raise KeyError(name)
    return n


def pad(board: Any, ref: str, num: Any) -> Any:
    """Return pad ``num`` of the footprint ``ref``, or raise KeyError."""
    fp = board.FindFootprintByReference(ref)
    for p in fp.Pads():
        if p.GetNumber() == str(num):
            return p
    raise KeyError(ref + ":" + str(num))


def ppos(board: Any, ref: str, num: Any) -> tuple[float, float]:
    """Return the centre of pad ``num`` of ``ref`` in layout millimetres."""
    return to_local(pad(board, ref, num).GetPosition())


def rect(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    """Return a rectangle's four corners, for ``zone`` and ``keepout``."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# --- copper, zones and rule areas ------------------------------------------------


def track(
    board: Any,
    pts: Sequence[Point],
    width: float,
    netname: str,
    layer: int | None = None,
) -> None:
    """Add a locked track of ``width`` mm through ``pts``, on F.Cu unless told."""
    pcbnew = _pcbnew()
    layer = pcbnew.F_Cu if layer is None else layer
    net = N(board, netname)
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pt(x0, y0))
        t.SetEnd(pt(x1, y1))
        t.SetWidth(mm(width))
        t.SetLayer(layer)
        t.SetNet(net)
        t.SetLocked(True)
        board.Add(t)


def via(
    board: Any,
    x: float,
    y: float,
    netname: str,
    d: float = 0.8,
    drill: float = 0.4,
) -> None:
    """Add a locked through-hole via of diameter ``d`` and ``drill`` mm at (x, y)."""
    v = _pcbnew().PCB_VIA(board)
    v.SetPosition(pt(x, y))
    v.SetWidth(mm(d))
    v.SetDrill(mm(drill))
    v.SetNet(N(board, netname))
    v.SetLocked(True)
    board.Add(v)


def keepout(
    board: Any,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    tracks: bool = True,
    vias: bool = True,
    pours: bool = True,
    layers: Sequence[str] = ("F.Cu", "B.Cu"),
    pts: Sequence[Point] | None = None,
) -> None:
    """Add a rule area (keep-out); ``tracks``, ``vias`` and ``pours`` say what it bans.

    The area is the rectangle (x0, y0)-(x1, y1), or the polygon ``pts`` if one is given.
    ``layers`` are KiCad layer names.
    """
    pcbnew = _pcbnew()
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(tracks)
    z.SetDoNotAllowVias(vias)
    # KiCad 10 renamed the copper-pour switch from SetDoNotAllowCopperPour.
    set_pours = (
        z.SetDoNotAllowZoneFills
        if hasattr(z, "SetDoNotAllowZoneFills")
        else z.SetDoNotAllowCopperPour
    )
    set_pours(pours)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowFootprints(False)
    layer_set = pcbnew.LSET()
    for name in layers:
        layer_set.AddLayer(board.GetLayerID(name))
    z.SetLayerSet(layer_set)
    outline = z.Outline()
    outline.NewOutline()
    for x, y in pts or ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        outline.Append(pt(x, y))
    board.Add(z)


def zone(
    board: Any,
    netname: str,
    layer: int,
    pts: Sequence[Point],
    priority: int = 0,
    full: bool = False,
    min_w: float = 0.25,
    clearance: float = 0.25,
) -> Any:
    """Add a copper pour on ``layer`` (a pcbnew layer id) and return it.

    ``full`` joins its pads solid; otherwise they get thermal reliefs.
    """
    pcbnew = _pcbnew()
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(N(board, netname))
    z.SetAssignedPriority(priority)
    z.SetMinThickness(mm(min_w))
    z.SetLocalClearance(mm(clearance))
    z.SetPadConnection(
        pcbnew.ZONE_CONNECTION_FULL if full else pcbnew.ZONE_CONNECTION_THERMAL
    )
    z.SetThermalReliefGap(mm(0.4))
    z.SetThermalReliefSpokeWidth(mm(0.6))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    outline = z.Outline()
    outline.NewOutline()
    for x, y in pts:
        outline.Append(pt(x, y))
    board.Add(z)
    return z


def clear_spot(
    board: Any,
    near: Point,
    net: str = "GND",
    via_d: float = 0.6,
    drill: float = 0.3,
    reach: float = 2.5,
    gap: float = 0.25,
) -> tuple[float, float]:
    """Return the nearest (x, y) to ``near`` where a via and a track to it clear copper.

    "Clear" means ``gap`` mm from every item of a net other than ``net``, the usual
    spacing from every hole, and outside every keep-out that bans vias. The candidates
    lie on a 0.25 mm grid between 0.6 mm and ``reach`` from ``near``, nearest first.
    Raise SystemExit if none fits.
    """
    pcbnew = _pcbnew()
    others = [t for t in board.GetTracks() if t.GetNetname() != "/" + net]
    pads = [
        p
        for f in board.GetFootprints()
        for p in f.Pads()
        if p.GetNetname() != "/" + net
    ]
    holes = [
        p.GetPosition() for f in board.GetFootprints() for p in f.Pads() if p.HasHole()
    ] + [t.GetPosition() for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    keep = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    cands = sorted(
        (
            (dx * 0.25, dy * 0.25)
            for dx in range(-10, 11)
            for dy in range(-10, 11)
            if 0.6 <= math.hypot(dx * 0.25, dy * 0.25) <= reach
        ),
        key=lambda d: math.hypot(*d),
    )
    for dx, dy in cands:
        x, y = near[0] + dx, near[1] + dy
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt(x, y))
        v.SetWidth(mm(via_d))
        v.SetDrill(mm(drill))
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pt(*near))
        t.SetEnd(pt(x, y))
        t.SetWidth(mm(0.3))
        t.SetLayer(pcbnew.F_Cu)
        if any(z.Outline().Collide(pt(x, y), mm(via_d / 2 + 0.1)) for z in keep):
            continue
        if any(
            (h - v.GetPosition()).EuclideanNorm() < mm(drill / 2 + 0.25 + 0.5)
            for h in holes
        ):
            continue
        bad = False
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            shapes = [v.GetEffectiveShape(layer)] + (
                [t.GetEffectiveShape(layer)] if layer == pcbnew.F_Cu else []
            )
            for o in [o for o in others if o.IsOnLayer(layer)] + [
                p for p in pads if p.IsOnLayer(layer)
            ]:
                if any(s.Collide(o.GetEffectiveShape(layer), mm(gap)) for s in shapes):
                    bad = True
                    break
            if bad:
                break
        if not bad:
            return x, y
    raise SystemExit(f"no clear GND via spot near {near}")


# --- outline and text --------------------------------------------------------------


def add_line(
    board: Any,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    layer: int | None = None,
    w: float = 0.1,
) -> None:
    """Add a graphic line of width ``w`` mm; on Edge.Cuts (the outline) by default."""
    pcbnew = _pcbnew()
    layer = pcbnew.Edge_Cuts if layer is None else layer
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(pt(x0, y0))
    s.SetEnd(pt(x1, y1))
    s.SetLayer(layer)
    s.SetWidth(mm(w))
    board.Add(s)


def add_arc(
    board: Any,
    cx: float,
    cy: float,
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    layer: int | None = None,
    w: float = 0.1,
) -> None:
    """Add a graphic arc about centre (cx, cy), from (sx, sy) to (ex, ey) the short way.

    pcbnew wants a start, a middle and an end point: the middle is found on the arc
    half way round, so a quarter-circle corner is just its centre and two end points.
    """
    pcbnew = _pcbnew()
    layer = pcbnew.Edge_Cuts if layer is None else layer
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_ARC)
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    am = a0 + d / 2
    r = math.hypot(sx - cx, sy - cy)
    s.SetArcGeometry(
        pt(sx, sy), pt(cx + r * math.cos(am), cy + r * math.sin(am)), pt(ex, ey)
    )
    s.SetLayer(layer)
    s.SetWidth(mm(w))
    board.Add(s)


def add_text(
    board: Any,
    text: str,
    x: float,
    y: float,
    size: float = 1.0,
    layer: int | None = None,
    rot: float = 0,
    bold: bool = False,
    justify: str | None = None,
) -> Any:
    """Add a text item at (x, y) and return it; F.SilkS by default.

    ``size`` is the character height and width in mm, the stroke is 0.15 of it (0.2
    when ``bold``), ``rot`` is in degrees and ``justify`` is "left", "right" or None
    for centred. The silkscreen pass has its own variant of this with a minimum stroke.
    """
    pcbnew = _pcbnew()
    layer = pcbnew.F_SilkS if layer is None else layer
    t = pcbnew.PCB_TEXT(board)
    t.SetText(text)
    t.SetPosition(pt(x, y))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
    t.SetTextThickness(mm(size * (0.2 if bold else 0.15)))
    t.SetTextAngleDegrees(rot)
    if justify == "left":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    elif justify == "right":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)
    board.Add(t)
    return t


# --- the shields --------------------------------------------------------------------

# Every item taken off a board by ``remove``, kept referenced until the process ends.
_REMOVED: list[Any] = []


def remove(board: Any, item: Any) -> None:
    """Take ``item`` off ``board`` and keep it referenced for the rest of the process.

    board.Remove hands the item back to Python, which then owns it. If that reference
    lapses and Python frees the item, pcbnew's SWIG layer is left broken for the whole
    process: ``board.GetTracks()`` raises "'SwigPyObject' object is not iterable", and
    ``pcbnew.LoadBoard`` returns a bare SwigPyObject with no methods. Measured on KiCad
    10.0.6, in a process that only loads and edits boards (one that never constructs a
    PCB_TRACK itself, as the post-route pass does). Letting go after the board is
    saved, or after the board itself is gone, breaks it just the same, so the items are
    never released: a retry loop that runs the post-route pass again in the same
    process is covered too. The cost is a few small objects, and some "swig/python
    detected a memory leak" lines when the interpreter exits.
    """
    board.Remove(item)
    _REMOVED.append(item)


def segment_distance(a: Any, b: Any, p: Any) -> float:
    """Return the distance from point ``p`` to the segment from ``a`` to ``b``.

    Each point is anything with ``.x`` and ``.y``: a pcbnew VECTOR2I, in internal units,
    or a namedtuple. The distance comes back in the same units. The foot of the
    perpendicular is clamped to the segment, so past an end it is the distance to that
    end (a track has round ends); a zero-length segment is a point.
    """
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    u = (
        0.0
        if length2 == 0
        else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2))
    )
    return ((p.x - a.x - u * dx) ** 2 + (p.y - a.y - u * dy) ** 2) ** 0.5


def point_in_track(track: Any, point: Any, accuracy: float = 0) -> bool:
    """Return True if ``point`` is on a straight track, widened by ``accuracy``.

    That is: its distance to the track's centre line is at most ``accuracy`` plus half
    the track's width. It answers what ``PCB_TRACK.HitTest(point, accuracy)`` answers
    (KiCad rounds to whole nanometres, so the two can differ for a point within 1 nm of
    the edge), without calling it. Units are pcbnew's internal ones, as ``GetWidth``
    returns them. An arc track is not handled: its start and end are only the chord.
    """
    return (
        segment_distance(track.GetStart(), track.GetEnd(), point)
        <= accuracy + track.GetWidth() / 2
    )


_COPPER_LAYER = re.compile(
    r'(\(layer "[FB]\.Cu"\s*\(type "copper"\)\s*\(thickness )[0-9.]+\)'
)


def set_copper_text(text: str, copper_mm: float) -> str:
    """Return board file text with both outer copper layers ``copper_mm`` thick.

    The stackup is found with a pattern that allows any whitespace, line breaks
    included, between the parts of a copper layer, so it matches both the one-line form
    written by hand and the multi-line form KiCad saves. Nothing else changes: the text
    comes back byte for byte but for the two thicknesses. Raise ValueError unless it
    holds exactly two such layers.
    """
    out, n = _COPPER_LAYER.subn(rf"\g<1>{copper_mm:g})", text)
    if n != 2:
        raise ValueError(f"expected two copper layers in the stackup, found {n}")
    return out


def set_copper(path: str, copper_mm: float) -> None:
    """Write ``copper_mm`` into the stackup of the saved board at ``path``.

    KiCad 10's Python cannot reach the stackup, so the saved file is rewritten. Do it
    after the last ``pcbnew.SaveBoard``, which would write the old value back.
    """
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    try:
        text = set_copper_text(text, copper_mm)
    except ValueError as err:
        raise ValueError(f"{path}: {err}") from err
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
