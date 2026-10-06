"""Compare two boards' routed copper: tracks, vias, zone fills and the copper itself.

``compare_boards(old, new)`` loads both boards with pcbnew and returns a
``CompareReport``; ``pcbkit compare OLD NEW`` prints it and exits 1 when the boards
differ. Use it after a change that should not move any copper (a refactor, a new
KiCad, a rebuilt route) to see exactly what did move.

Two halves, kept apart so the second needs no KiCad:

* Measuring (``load_board``, ``summarise_board``, ``copper_one_sided``): the only code
  that touches pcbnew, which is imported when first needed. It turns each board into
  plain numbers, and the copper XOR into a short list of pieces.
* Judging (``build_report``, ``format_report``, ``report_to_dict``): pure functions of
  those numbers. The tolerances and what counts as a difference live here.

What is compared, and when it is a difference:

* Tracks: how many straight or arc track segments, and their total length. A different
  count, or lengths more than ``length_tol_mm`` apart, is a difference (the tolerance
  only absorbs rounding: a sum of lengths depends on the order of the items).
* Vias: a different count is a difference.
* Zones: each zone's filled area. Zones that share a net, a first layer and an outline
  area (to 0.1 mm2) are one entry, and a zone's fill is added up over every layer it
  fills, so a pour that a router re-split into pieces still compares as one. Keep-out
  areas are entries with no fill. An entry whose fill differs by ``fill_tol_mm2`` or
  more, or that only one board has, is a difference.
* Copper: each outer layer's copper is turned into polygons and each board's is
  subtracted from the other's. What is left is copper that exists on one board only.
  It is reported in pieces, and a piece larger than ``piece_tol_mm2`` is a difference.
  A piece's area is the area inside its outer outline, so a thin ring (a track made
  slightly wider, say) counts as the whole area it surrounds: that can flag too much,
  never too little. The layer totals are printed for information only: a pour filled
  twice comes out with slivers that add up to a few hundredths of a square millimetre
  without any one piece being large, which is why the judgement is per piece.

Positions are layout millimetres (measured from the board's top-left corner), as in
``pcbkit.kicad.board``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from pcbkit.kicad import board as kb

# A piece of copper on one board only is a difference when it is larger than this.
PIECE_TOLERANCE_MM2 = 0.01
# A zone's fill is a difference when it moved by this much or more.
FILL_TOLERANCE_MM2 = 0.5
# Total track lengths further apart than this are a difference (rounding noise is nm).
LENGTH_TOLERANCE_MM = 0.001

# The layers whose copper is compared, in report order.
COPPER_LAYERS = ("F.Cu", "B.Cu")

# How many pieces the text report lists for each layer and direction.
DEFAULT_TOP = 8

# pcbnew's internal unit is a nanometre: nm to mm, and nm2 to mm2.
_NM_PER_MM = 1e6
_NM2_PER_MM2 = 1e12


class CompareError(click.ClickException):
    """Say that two boards could not be compared, which is not "they differ".

    The exit code is 2, like ``diff`` and ``cmp``: 0 means the boards match, 1 means
    they differ, and 2 means there is no answer.
    """

    exit_code = 2


@dataclass(frozen=True)
class Tolerances:
    """The limits a comparison was judged by."""

    piece_mm2: float = PIECE_TOLERANCE_MM2
    fill_mm2: float = FILL_TOLERANCE_MM2
    length_mm: float = LENGTH_TOLERANCE_MM


@dataclass(frozen=True)
class TrackStats:
    """How many track segments a board has and how long they are in all."""

    count: int
    length_mm: float


@dataclass(frozen=True)
class BoardSummary:
    """What is measured on one board, apart from its copper polygons.

    ``zones`` maps (net, first layer, outline area to 0.1 mm2) to the fill area in mm2.
    The net is as KiCad stores it, with the leading "/", and "" for a keep-out.
    """

    tracks: TrackStats
    vias: int
    zones: Mapping[tuple[str, str, float], float]


@dataclass(frozen=True)
class Piece:
    """A piece of copper that one board has and the other lacks."""

    area_mm2: float
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class OneSidedCopper:
    """The copper of one layer that exists on one board only.

    ``only_in`` is "old" or "new". ``total_mm2`` and ``pieces`` cover everything left
    after the subtraction, noise included; ``over`` lists just the pieces larger than
    the tolerance, biggest first, and is what makes the boards differ.
    """

    layer: str
    only_in: str
    total_mm2: float
    pieces: int
    over: tuple[Piece, ...]


@dataclass(frozen=True)
class ZoneChange:
    """One zone entry on each board: a fill of None means the board has no such zone."""

    net: str
    layer: str
    outline_mm2: float
    old_fill_mm2: float | None
    new_fill_mm2: float | None
    differs: bool

    @property
    def delta_mm2(self) -> float | None:
        """Return new fill minus old fill, or None if only one board has the zone."""
        if self.old_fill_mm2 is None or self.new_fill_mm2 is None:
            return None
        return self.new_fill_mm2 - self.old_fill_mm2


@dataclass(frozen=True)
class CompareReport:
    """Everything a comparison found, and the tolerances it was judged by."""

    old: str
    new: str
    tolerances: Tolerances
    old_tracks: TrackStats
    new_tracks: TrackStats
    old_vias: int
    new_vias: int
    zones: tuple[ZoneChange, ...]
    copper: tuple[OneSidedCopper, ...]

    @property
    def tracks_differ(self) -> bool:
        """Return True if the track count or the total track length differs."""
        old, new = self.old_tracks, self.new_tracks
        return (
            old.count != new.count
            or abs(new.length_mm - old.length_mm) > self.tolerances.length_mm
        )

    @property
    def vias_differ(self) -> bool:
        """Return True if the via counts differ."""
        return self.old_vias != self.new_vias

    @property
    def differences(self) -> list[str]:
        """Return one line for each difference found; empty when the boards match."""
        found: list[str] = []
        if self.tracks_differ:
            old, new = self.old_tracks, self.new_tracks
            found.append(
                f"tracks: old {old.count} ({old.length_mm:.3f} mm), "
                f"new {new.count} ({new.length_mm:.3f} mm)"
            )
        if self.vias_differ:
            found.append(f"vias: old {self.old_vias}, new {self.new_vias}")
        for zone in self.zones:
            if zone.differs:
                found.append(f"zone {_zone_label(zone)}: {_fill_change(zone)}")
        for side in self.copper:
            if side.over:
                biggest = side.over[0]
                found.append(
                    f"{side.layer}: {_count(len(side.over), 'piece')} over "
                    f"{self.tolerances.piece_mm2:g} mm2 only in {side.only_in} "
                    f"(largest {biggest.area_mm2:.3f} mm2 at "
                    f"({biggest.x_mm:.1f}, {biggest.y_mm:.1f}))"
                )
        return found

    @property
    def differs(self) -> bool:
        """Return True if the boards differ in anything that is compared."""
        return bool(self.differences)


# --- measuring: the only code that touches pcbnew -----------------------------------


def load_board(path: Path) -> Any:
    """Load a board file with pcbnew; raise CompareError if it is not a board.

    KiCad 10 answers a file it cannot read with None, not an exception, and a program
    that carried on would then compare nothing with nothing and call it a match.
    """
    import pcbnew

    try:
        board = pcbnew.LoadBoard(str(path))
    except (OSError, RuntimeError) as err:
        raise CompareError(f"cannot read {path} as a KiCad board: {err}") from None
    if board is None:
        raise CompareError(f"cannot read {path} as a KiCad board")
    return board


def track_stats(board: Any) -> TrackStats:
    """Return the number and total length of a board's track segments, arcs included."""
    import pcbnew

    count = 0
    length_nm = 0.0
    for item in board.GetTracks():
        if item.Type() in (pcbnew.PCB_TRACE_T, pcbnew.PCB_ARC_T):
            count += 1
            length_nm += item.GetLength()
    return TrackStats(count, length_nm / _NM_PER_MM)


def via_count(board: Any) -> int:
    """Return the number of vias on a board."""
    import pcbnew

    return sum(1 for item in board.GetTracks() if item.Type() == pcbnew.PCB_VIA_T)


def zone_fills(board: Any) -> dict[tuple[str, str, float], float]:
    """Return each zone entry's fill area in mm2, keyed as ``BoardSummary.zones`` is."""
    fills: dict[tuple[str, str, float], float] = {}
    for zone in board.Zones():
        area_nm2 = 0.0
        for layer in zone.GetLayerSet().Seq():
            if zone.HasFilledPolysForLayer(layer):
                area_nm2 += zone.GetFilledPolysList(layer).Area()
        key = (
            zone.GetNetname(),
            board.GetLayerName(zone.GetFirstLayer()),
            round(zone.Outline().Area() / _NM2_PER_MM2, 1),
        )
        fills[key] = fills.get(key, 0.0) + area_nm2 / _NM2_PER_MM2
    return fills


def summarise_board(board: Any) -> BoardSummary:
    """Return a loaded board's track, via and zone numbers."""
    return BoardSummary(track_stats(board), via_count(board), zone_fills(board))


def copper_polygons(board: Any, layer: int) -> Any:
    """Return one layer's copper (tracks, pads, vias, pours) as simplified polygons."""
    import pcbnew

    polygons = pcbnew.SHAPE_POLY_SET()
    board.ConvertBrdLayerToPolygonalContours(layer, polygons)
    polygons.Simplify()
    return polygons


def copper_one_sided(
    layer: str, only_in: str, mine: Any, other: Any, piece_tol_mm2: float
) -> OneSidedCopper:
    """Return the copper in ``mine`` that ``other`` lacks, judged piece by piece.

    ``mine`` and ``other`` are polygon sets from ``copper_polygons``; ``only_in`` names
    the board ``mine`` came from.
    """
    import pcbnew

    left = pcbnew.SHAPE_POLY_SET(mine)
    left.BooleanSubtract(other)
    over: list[Piece] = []
    for index in range(left.OutlineCount()):
        outline = left.Outline(index)
        area = abs(outline.Area()) / _NM2_PER_MM2
        if area > piece_tol_mm2:
            x, y = kb.to_local(outline.BBox().Centre())
            over.append(Piece(area, x, y))
    over.sort(key=lambda piece: (-piece.area_mm2, piece.x_mm, piece.y_mm))
    return OneSidedCopper(
        layer,
        only_in,
        abs(left.Area()) / _NM2_PER_MM2,
        left.OutlineCount(),
        tuple(over),
    )


def compare_boards(
    old: Path,
    new: Path,
    *,
    piece_tol_mm2: float = PIECE_TOLERANCE_MM2,
    fill_tol_mm2: float = FILL_TOLERANCE_MM2,
    length_tol_mm: float = LENGTH_TOLERANCE_MM,
) -> CompareReport:
    """Compare two board files and return what differs.

    Needs pcbnew. Raise CompareError if either file is not a KiCad board. Both boards
    stay loaded until the comparison is done: the polygons belong to them.
    """
    import pcbnew

    old_board = load_board(Path(old))
    new_board = load_board(Path(new))
    layer_ids = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
    copper: list[OneSidedCopper] = []
    for layer in COPPER_LAYERS:
        before = copper_polygons(old_board, layer_ids[layer])
        after = copper_polygons(new_board, layer_ids[layer])
        copper.append(copper_one_sided(layer, "old", before, after, piece_tol_mm2))
        copper.append(copper_one_sided(layer, "new", after, before, piece_tol_mm2))
    return build_report(
        str(old),
        str(new),
        summarise_board(old_board),
        summarise_board(new_board),
        copper,
        Tolerances(piece_tol_mm2, fill_tol_mm2, length_tol_mm),
    )


# --- judging: pure functions of the numbers ------------------------------------------


def build_report(
    old: str,
    new: str,
    old_summary: BoardSummary,
    new_summary: BoardSummary,
    copper: Sequence[OneSidedCopper],
    tolerances: Tolerances,
) -> CompareReport:
    """Judge two boards' numbers against ``tolerances`` and return the report."""
    zones: list[ZoneChange] = []
    for key in sorted(set(old_summary.zones) | set(new_summary.zones)):
        before = old_summary.zones.get(key)
        after = new_summary.zones.get(key)
        moved = (
            before is None
            or after is None
            or abs(after - before) >= tolerances.fill_mm2
        )
        zones.append(ZoneChange(key[0], key[1], key[2], before, after, moved))
    return CompareReport(
        old=old,
        new=new,
        tolerances=tolerances,
        old_tracks=old_summary.tracks,
        new_tracks=new_summary.tracks,
        old_vias=old_summary.vias,
        new_vias=new_summary.vias,
        zones=tuple(zones),
        copper=tuple(copper),
    )


def _zone_label(zone: ZoneChange) -> str:
    """Return a zone entry's name: its net (or "keep-out"), layer and outline area."""
    net = zone.net or "keep-out"
    return f"{net} {zone.layer} (outline {zone.outline_mm2:.1f} mm2)"


def _fill_change(zone: ZoneChange) -> str:
    """Say how one zone's fill changed, in a few words."""
    if zone.old_fill_mm2 is None:
        return f"only in new (fill {zone.new_fill_mm2:.1f} mm2)"
    if zone.new_fill_mm2 is None:
        return f"only in old (fill {zone.old_fill_mm2:.1f} mm2)"
    return (
        f"fill {zone.old_fill_mm2:.1f} -> {zone.new_fill_mm2:.1f} mm2 "
        f"({_signed(zone.new_fill_mm2 - zone.old_fill_mm2)})"
    )


def _count(number: int, noun: str) -> str:
    """Return "1 piece" or "3 pieces"."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _signed(value: float) -> str:
    """Return a change to one decimal place with its sign, and "0.0" for none."""
    text = f"{value:+.1f}"
    return "0.0" if text in ("+0.0", "-0.0") else text


def _fill(value: float | None) -> str:
    """Return a fill area for a table cell, or a dash for a zone a board lacks."""
    return "-" if value is None else f"{value:.1f}"


def format_report(report: CompareReport, top: int = DEFAULT_TOP) -> str:
    """Return the report as text, listing at most ``top`` pieces per layer and side."""
    tol = report.tolerances
    old, new = report.old_tracks, report.new_tracks
    lines = [
        "pcbkit compare",
        f"  old  {report.old}",
        f"  new  {report.new}",
        f"  a copper piece over {tol.piece_mm2:g} mm2 is a difference, and so is "
        f"a zone fill that moved by {tol.fill_mm2:g} mm2 or more",
        "",
        f"Tracks  old {old.count} ({old.length_mm:.1f} mm)  "
        f"new {new.count} ({new.length_mm:.1f} mm)  "
        + ("DIFFERENT" if report.tracks_differ else "same"),
        f"Vias    old {report.old_vias}  new {report.new_vias}  "
        + ("DIFFERENT" if report.vias_differ else "same"),
        "",
        "Zone fill, mm2 (a zone is matched by net, first layer and outline area)",
    ]
    if report.zones:
        rows = [
            (
                zone.net or "keep-out",
                zone.layer,
                f"{zone.outline_mm2:.1f}",
                _fill(zone.old_fill_mm2),
                _fill(zone.new_fill_mm2),
                "-" if zone.delta_mm2 is None else _signed(zone.delta_mm2),
                "DIFFERENT" if zone.differs else "",
            )
            for zone in report.zones
        ]
        head = ("net", "layer", "outline", "old", "new", "change", "")
        widths = [max(len(r[i]) for r in [head, *rows]) for i in range(6)]
        for row in [head, *rows]:
            cells = [
                row[i].ljust(widths[i]) if i < 2 else row[i].rjust(widths[i])
                for i in range(6)
            ]
            lines.append("  " + "  ".join(cells) + (f"  {row[6]}" if row[6] else ""))
    else:
        lines.append("  neither board has a zone")
    lines += [
        "",
        "Copper on one board only (the layer totals include slivers: they are for "
        "information)",
    ]
    for side in report.copper:
        lines.append(
            f"  {side.layer} only in {side.only_in}: total {side.total_mm2:.3f} mm2 "
            f"in {_count(side.pieces, 'piece')}, {len(side.over)} over "
            f"{tol.piece_mm2:g} mm2" + ("  DIFFERENT" if side.over else "")
        )
        for piece in side.over[:top]:
            where = f"({piece.x_mm:.1f}, {piece.y_mm:.1f})"
            lines.append(f"      {piece.area_mm2:9.3f} mm2 at {where}")
        if len(side.over) > top:
            lines.append(
                f"      ... and {len(side.over) - top} more (--top N lists more, "
                "--json lists all)"
            )
    found = report.differences
    lines.append("")
    if found:
        lines.append(f"The boards differ: {_count(len(found), 'difference')}.")
    else:
        lines.append("The boards match: no differences.")
    return "\n".join(lines)


def report_to_dict(report: CompareReport) -> dict[str, Any]:
    """Return the report as plain data for ``--json``: numbers to 4 places."""

    def area(value: float | None) -> float | None:
        return None if value is None else round(value, 4)

    tol = report.tolerances
    return {
        "old": report.old,
        "new": report.new,
        "identical": not report.differs,
        "differences": report.differences,
        "tolerances": {
            "piece_mm2": tol.piece_mm2,
            "fill_mm2": tol.fill_mm2,
            "length_mm": tol.length_mm,
        },
        "tracks": {
            "old": {
                "count": report.old_tracks.count,
                "length_mm": round(report.old_tracks.length_mm, 4),
            },
            "new": {
                "count": report.new_tracks.count,
                "length_mm": round(report.new_tracks.length_mm, 4),
            },
            "differs": report.tracks_differ,
        },
        "vias": {
            "old": report.old_vias,
            "new": report.new_vias,
            "differs": report.vias_differ,
        },
        "zones": [
            {
                "net": zone.net,
                "layer": zone.layer,
                "outline_mm2": zone.outline_mm2,
                "old_fill_mm2": area(zone.old_fill_mm2),
                "new_fill_mm2": area(zone.new_fill_mm2),
                "delta_mm2": area(zone.delta_mm2),
                "differs": zone.differs,
            }
            for zone in report.zones
        ],
        "copper": [
            {
                "layer": side.layer,
                "only_in": side.only_in,
                "total_mm2": round(side.total_mm2, 4),
                "pieces": side.pieces,
                "over_tolerance": [
                    {
                        "area_mm2": round(piece.area_mm2, 4),
                        "x_mm": round(piece.x_mm, 2),
                        "y_mm": round(piece.y_mm, 2),
                    }
                    for piece in side.over
                ],
            }
            for side in report.copper
        ],
    }
