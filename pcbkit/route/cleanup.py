"""Clean-up of what the autorouter and the pre-routes leave behind.

``drop_one_sided_vias`` takes out signal and power vias that carry copper on one layer
only: a pre-placed fan-out via the autorouter ended up not using on its far layer does
nothing but hold a hole.

``trim_dead_ends`` then removes, or cuts back, tracks with a free end:

* a tiny fragment (under 0.5 mm) lying wholly inside another track, or only bridging
  copper that is joined anyway (KiCad 10 flags such a bridge as dangling);
* a track wholly inside one other track of its net and layer;
* a track with both ends free, which carries no current;
* a track with one free end, which is cut back to the join nearest that end, or
  removed if nothing joins it part-way.

Positions are compared to within 10 um, because a session import rounds to half a
micrometre. Items go off the board through ``pcbkit.kicad.board.remove``, which holds
them until the process ends: KiCad 10's Python layer breaks if one is freed while the
board still refers to it.
"""

from __future__ import annotations

from typing import Any

from pcbkit.kicad import board as kb

SAME_PLACE_NM = 10000  # 10 um
FRAGMENT_MM = 0.5  # a track shorter than this may be a redundant fragment
COVER_SLACK_NM = 5000  # 5 um of slack when a fragment must lie inside a track
MAX_PASSES = 6


def _near(a: Any, x: float, y: float) -> bool:
    """Return True if point ``a`` is within SAME_PLACE_NM of (x, y) on both axes."""
    return abs(a.x - x) < SAME_PLACE_NM and abs(a.y - y) < SAME_PLACE_NM


def _same(a: Any, b: Any) -> bool:
    """Return True if two items are the same board item (compared by id)."""
    return bool(a.m_Uuid.AsString() == b.m_Uuid.AsString())


def _distance(track: Any, point: Any) -> float:
    """Return the distance from ``point`` to the centre line of ``track``."""
    return kb.segment_distance(track.GetStart(), track.GetEnd(), point)


def drop_one_sided_vias(board: Any) -> int:
    """Remove non-ground vias that touch copper on one layer only; return how many.

    A via is also taken to touch a layer where a track of its net passes under it,
    where a pour of its net is filled under it, or where a pad of its net covers it. A
    via with a single track ending on it takes that track with it, unless another via
    sits at the same place.
    """
    import pcbnew

    dropped = 0
    for v in [
        t
        for t in board.GetTracks()
        if t.GetClass() == "PCB_VIA" and t.GetNetname() != "/GND"
    ]:
        vx, vy, net = v.GetPosition().x, v.GetPosition().y, v.GetNetCode()
        at = [
            t
            for t in board.GetTracks()
            if t.GetClass() == "PCB_TRACK"
            and t.GetNetCode() == net
            and (_near(t.GetStart(), vx, vy) or _near(t.GetEnd(), vx, vy))
        ]
        used = {t.GetLayer() for t in at}
        # a via the router dropped part-way along a track (often a pre-route) is
        # joined on that layer too
        for t in board.GetTracks():
            if (
                t.GetClass() == "PCB_TRACK"
                and t.GetNetCode() == net
                and t.GetLayer() not in used
                and _distance(t, v.GetPosition()) <= t.GetWidth() / 2
            ):
                used.add(t.GetLayer())
        used |= {
            lay
            for z in board.Zones()
            if z.GetNetCode() == net and not z.GetIsRuleArea()
            for lay in (pcbnew.F_Cu, pcbnew.B_Cu)
            if z.IsOnLayer(lay) and z.HitTestFilledArea(lay, v.GetPosition())
        }
        used |= {
            lay
            for f in board.GetFootprints()
            for pd in f.Pads()
            if pd.GetNetCode() == net
            for lay in (pcbnew.F_Cu, pcbnew.B_Cu)
            if pd.IsOnLayer(lay) and pd.GetBoundingBox().Contains(v.GetPosition())
        }
        if len(used) < 2:
            kb.remove(board, v)
            dropped += 1
            others = [
                o
                for o in board.GetTracks()
                if o.GetClass() == "PCB_VIA"
                and o.GetNetCode() == net
                and _near(o.GetPosition(), vx, vy)
            ]
            if len(at) == 1 and not others:
                kb.remove(board, at[0])
    return dropped


def _attached(board: Any, t: Any, p: Any) -> bool:
    """Return True if point ``p`` of track ``t`` touches same-net copper.

    That is another track of the net on the same layer, a via of the net, a pad of
    the net on that layer, or a pour of the net filled under the point.
    """
    import pcbnew

    net, lay = t.GetNetCode(), t.GetLayer()
    for o in board.GetTracks():
        if o.GetNetCode() != net or _same(o, t):
            continue
        if o.GetClass() == "PCB_VIA":
            if (o.GetPosition() - p).EuclideanNorm() <= o.GetWidth(pcbnew.F_Cu) // 2:
                return True
        elif o.GetLayer() == lay and (
            _near(o.GetStart(), p.x, p.y)
            or _near(o.GetEnd(), p.x, p.y)
            or kb.point_in_track(o, p, o.GetWidth() // 2)
        ):
            return True
    for f in board.GetFootprints():
        for pd in f.Pads():
            if (
                pd.GetNetCode() == net
                and pd.IsOnLayer(lay)
                and pd.GetBoundingBox().Contains(p)
            ):
                return True
    return any(
        z.GetNetCode() == net and z.IsOnLayer(lay) and z.HitTestFilledArea(lay, p)
        for z in board.Zones()
    )


def _covers(o: Any, t: Any, p: Any) -> bool:
    """Return True if ``t``'s copper around ``p`` lies wholly inside track ``o``'s."""
    return bool(_distance(o, p) + t.GetWidth() / 2 <= o.GetWidth() / 2 + COVER_SLACK_NM)


def _inside(o: Any, p: Any) -> bool:
    """Return True if point ``p`` falls within track ``o``'s copper."""
    return bool(_distance(o, p) <= o.GetWidth() / 2)


def _inside_one(board: Any, t: Any) -> bool:
    """Return True if ``t`` lies wholly inside one other same-net, same-layer track."""
    return any(
        o.GetClass() == "PCB_TRACK"
        and o.GetNetCode() == t.GetNetCode()
        and o.GetLayer() == t.GetLayer()
        and not _same(o, t)
        and _covers(o, t, t.GetStart())
        and _covers(o, t, t.GetEnd())
        for o in board.GetTracks()
    )


def _redundant(board: Any, t: Any) -> bool:
    """Return True if fragment ``t`` adds no copper and no connection.

    Both ends are inside one other track or pad of its net, or each end is inside a
    different track and those two tracks already overlap, so the fragment only bridges
    copper that is joined anyway.
    """
    if _inside_one(board, t):
        return True
    for f in board.GetFootprints():
        for pd in f.Pads():
            if (
                pd.GetNetCode() == t.GetNetCode()
                and pd.IsOnLayer(t.GetLayer())
                and pd.GetBoundingBox().Contains(t.GetStart())
                and pd.GetBoundingBox().Contains(t.GetEnd())
            ):
                return True
    same = [
        o
        for o in board.GetTracks()
        if o.GetClass() == "PCB_TRACK"
        and o.GetNetCode() == t.GetNetCode()
        and o.GetLayer() == t.GetLayer()
        and not _same(o, t)
    ]
    for oa in [o for o in same if _covers(o, t, t.GetStart())]:
        for ob in [o for o in same if _covers(o, t, t.GetEnd())]:
            if not _same(oa, ob) and (
                any(_inside(oa, q) for q in (ob.GetStart(), ob.GetEnd()))
                or any(_inside(ob, q) for q in (oa.GetStart(), oa.GetEnd()))
            ):
                return True
    return False


def _joins(board: Any, t: Any) -> list[tuple[float, Any]]:
    """Return (u, point) of same-net copper meeting track ``t`` away from its ends.

    ``u`` runs along ``t`` from 0 at its start to 1 at its end.
    """
    import pcbnew

    a, b = t.GetStart(), t.GetEnd()
    dx, dy = b.x - a.x, b.y - a.y
    length2 = float(dx * dx + dy * dy) or 1.0
    found = []
    for o in board.GetTracks():
        if o.GetNetCode() != t.GetNetCode() or _same(o, t):
            continue
        if o.GetClass() == "PCB_VIA":
            cands, reach = [o.GetPosition()], o.GetWidth(pcbnew.F_Cu) / 2
        elif o.GetLayer() == t.GetLayer():
            cands, reach = [o.GetStart(), o.GetEnd()], o.GetWidth() / 2
        else:
            continue
        for p in cands:
            u = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2))
            d = ((p.x - a.x - u * dx) ** 2 + (p.y - a.y - u * dy) ** 2) ** 0.5
            if d <= t.GetWidth() / 2 + reach:
                found.append((u, pcbnew.VECTOR2I(int(a.x + u * dx), int(a.y + u * dy))))
    return found


def trim_dead_ends(board: Any) -> int:
    """Remove or cut back dead-end tracks and redundant fragments; return the count.

    Up to MAX_PASSES passes run, each over every track, until one changes nothing. A
    track removed or cut back counts once per pass.
    """
    changes = 0
    for _ in range(MAX_PASSES):
        changed = 0
        for t in [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]:
            free_start = not _attached(board, t, t.GetStart())
            free_end = not _attached(board, t, t.GetEnd())
            if _inside_one(board, t) or (
                t.GetLength() < kb.mm(FRAGMENT_MM) and _redundant(board, t)
            ):
                kb.remove(board, t)
            elif free_start and free_end:
                kb.remove(board, t)
            elif free_start or free_end:
                cuts = [(u if free_end else 1 - u, p) for u, p in _joins(board, t)]
                cuts = [c for c in cuts if 1e-3 < c[0] < 1 - 1e-3]
                if not cuts:
                    kb.remove(board, t)
                else:
                    p = max(cuts, key=lambda c: c[0])[1]
                    (t.SetEnd if free_end else t.SetStart)(p)
            else:
                continue
            changed += 1
            changes += 1
        if not changed:
            break
    return changes
