"""Small synthetic boards for the check-engine integration tests.

Each builder writes one 60 x 40 mm two-layer board into a folder and returns the path of
the saved file. A test reloads it with ``load`` (or hands it to a fixture), so the
engines read what a saved board holds, as they do for a real one. Nothing here is a real
design: the nets are PWR, GND, SIG and so on, the parts are references such as J1, R1
and U1, and the footprints are KiCad's own stock ones.

* ``power_tracks``: a wide and a narrow 30 mm track, and a track with a break in it.
* ``layer_joints``: top and bottom copper joined by a via, and by a plated pad.
* ``ground_points``: ground vias and pads of every kind, for counting.
* ``stitched_pours``: two ground pours and a lattice of ground vias, with via-free
  islands and a one-sided stretch of pour on request.
* ``reserved_region``: a rectangle that only some nets may enter, with copper of
  several nets and kinds crossing it.
* ``ladder``, ``far_layer``, ``bar_lanes``, ``violation_pair``: victim tracks beside
  aggressor tracks, at gaps the tests know.
* ``shunt_pair``: two nets routed from one part to another, as a tight pair or as a
  wide loop.

All positions are layout millimetres (from the board's top-left corner, y down); the
KiCad file holds them 50 mm further along each axis, as ``pcbkit.kicad.board`` says.
Importing this module needs nothing; every builder imports pcbnew when it runs, so only
a test that has done ``pytest.importorskip("pcbnew")`` calls one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pcbkit.kicad import board as kb
from pcbkit.kicad import env

WIDTH, HEIGHT = 60.0, 40.0

Box = tuple[float, float, float, float]  # x0, y0, x1, y1


def load(path: Path) -> Any:
    """Load a saved board file with pcbnew."""
    import pcbnew

    return pcbnew.LoadBoard(str(path))


# --- the pieces every builder uses ---------------------------------------------------


def _start(path: Path, nets: Sequence[str]) -> Any:
    """Return a new two-layer board with the outline drawn and the nets declared.

    ``nets`` are written without KiCad's leading "/": ``PWR`` becomes the net "/PWR".
    """
    import pcbnew

    path.parent.mkdir(parents=True, exist_ok=True)
    board = pcbnew.NewBoard(str(path))
    board.SetCopperLayerCount(2)
    for name in nets:
        board.Add(pcbnew.NETINFO_ITEM(board, "/" + name))
    kb.add_line(board, 0, 0, WIDTH, 0)
    kb.add_line(board, WIDTH, 0, WIDTH, HEIGHT)
    kb.add_line(board, WIDTH, HEIGHT, 0, HEIGHT)
    kb.add_line(board, 0, HEIGHT, 0, 0)
    return board


def _part(
    board: Any,
    kind: str,
    ref: str,
    at: tuple[float, float],
    nets: Mapping[str, str],
    rotation: float = 0.0,
    bottom: bool = False,
) -> None:
    """Place a stock footprint ``kind`` ("Library:Name") as ``ref`` at layout ``at``.

    ``nets`` maps a pad number to the net it is on (no leading "/"); pads left out stay
    without a net. ``rotation`` is in degrees and ``bottom`` flips the part onto the
    bottom side, which puts its surface-mount pads on the bottom copper.
    """
    import pcbnew

    library, name = kind.split(":")
    footprint = pcbnew.FootprintLoad(
        str(env.footprints_dir() / f"{library}.pretty"), name
    )
    footprint.SetFPID(pcbnew.LIB_ID(library, name))
    footprint.SetReference(ref)
    board.Add(footprint)
    footprint.SetPosition(kb.pt(*at))
    footprint.SetOrientationDegrees(rotation)
    if bottom:
        footprint.Flip(footprint.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    for pad in footprint.Pads():
        if pad.GetNumber() in nets:
            pad.SetNet(kb.N(board, nets[pad.GetNumber()]))


def _save(board: Any, path: Path, fill: bool = False) -> Path:
    """Fill the pours if asked, save the board to ``path`` and return the path."""
    import pcbnew

    if fill:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(path), board)
    return path


def _layers() -> tuple[int, int]:
    """Return the pcbnew ids of the top and bottom copper layers."""
    import pcbnew

    return pcbnew.F_Cu, pcbnew.B_Cu


# --- NetCopper: a wide and a narrow track, a break, and the layer joints -------------

WIDE_MM, NARROW_MM = 2.0, 0.5
TRACK_X = (10.0, 40.0)  # pad centres of every power track: 30 mm apart
TRACK_Y = {"PWR_W": 10.0, "PWR_N": 20.0, "PWR_X": 30.0}
POWER_PAD = 2.0  # the square test-point pads at both ends are 2 x 2 mm
BREAK_X = (24.0, 26.0)  # PWR_X has no copper between these x
POWER_PADS = {  # reference -> (net, x)
    "TP1": ("PWR_W", TRACK_X[0]),
    "TP2": ("PWR_W", TRACK_X[1]),
    "TP3": ("PWR_N", TRACK_X[0]),
    "TP4": ("PWR_N", TRACK_X[1]),
    "TP5": ("PWR_X", TRACK_X[0]),
    "TP6": ("PWR_X", TRACK_X[1]),
}


def power_tracks(directory: Path) -> Path:
    """Write a board with three power nets, each a 30 mm track between two square pads.

    PWR_W is 2 mm wide (pads TP1 and TP2), PWR_N is 0.5 mm wide (TP3 and TP4) and PWR_X
    is 1 mm wide with a 2 mm break in the middle, so its two halves are not joined
    (TP5 and TP6). The pads are 2 x 2 mm test points on the top layer; the tracks run
    between their centres on the top layer.
    """
    top, _ = _layers()
    path = directory / "power_tracks.kicad_pcb"
    board = _start(path, list(TRACK_Y))
    for ref, (net, x) in POWER_PADS.items():
        _part(
            board,
            "TestPoint:TestPoint_Pad_2.0x2.0mm",
            ref,
            (x, TRACK_Y[net]),
            {"1": net},
        )
    x0, x1 = TRACK_X
    kb.track(board, [(x0, TRACK_Y["PWR_W"]), (x1, TRACK_Y["PWR_W"])], WIDE_MM, "PWR_W")
    kb.track(
        board, [(x0, TRACK_Y["PWR_N"]), (x1, TRACK_Y["PWR_N"])], NARROW_MM, "PWR_N"
    )
    y = TRACK_Y["PWR_X"]
    kb.track(board, [(x0, y), (BREAK_X[0], y)], 1.0, "PWR_X", top)
    kb.track(board, [(BREAK_X[1], y), (x1, y)], 1.0, "PWR_X", top)
    return _save(board, path)


VIA_POINT = (12.5, 20.0)  # the via of PWR_V
VIA_TRACK_MM = 0.6  # the top and bottom tracks of PWR_V
PLATED_POINT = (15.0, 30.0)  # the plated-hole pad of PWR_T
PLATED_TRACK_MM = 1.0  # the top and bottom tracks of PWR_T
JOINT_PADS = {  # reference -> (net, x, y, on the bottom side)
    "TP1": ("PWR_V", 10.0, 20.0, False),
    "TP2": ("PWR_V", 15.0, 20.0, True),
    "TP3": ("PWR_T", 10.0, 30.0, False),
    "TP4": ("PWR_T", 20.0, 30.0, True),
}


def layer_joints(directory: Path, via: bool = True) -> Path:
    """Write a board with two nets whose top and bottom copper meet in one place.

    PWR_V runs from a top pad (TP1) along a 0.6 mm top track to a via at ``VIA_POINT``,
    and on along a 0.6 mm bottom track to a bottom pad (TP2); the via is the only joint.
    PWR_T runs the same way through a 1 mm top track to a plated through-hole pad (J1)
    and a 1 mm bottom track to a bottom pad (TP4); the plated hole is the only joint.
    With ``via=False`` the via is left out, so PWR_V's two halves are not joined.
    """
    top, bottom = _layers()
    path = directory / ("joints.kicad_pcb" if via else "joints_no_via.kicad_pcb")
    board = _start(path, ["PWR_V", "PWR_T"])
    for ref, (net, x, y, on_bottom) in JOINT_PADS.items():
        _part(
            board,
            "TestPoint:TestPoint_Pad_2.0x2.0mm",
            ref,
            (x, y),
            {"1": net},
            bottom=on_bottom,
        )
    _part(
        board,
        "Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical",
        "J1",
        PLATED_POINT,
        {"1": "PWR_T"},
    )
    vx, vy = VIA_POINT
    kb.track(board, [(10.0, vy), (vx, vy)], VIA_TRACK_MM, "PWR_V", top)
    kb.track(board, [(vx, vy), (15.0, vy)], VIA_TRACK_MM, "PWR_V", bottom)
    if via:
        kb.via(board, vx, vy, "PWR_V")
    px, py = PLATED_POINT
    kb.track(board, [(10.0, py), (px, py)], PLATED_TRACK_MM, "PWR_T", top)
    kb.track(board, [(px, py), (20.0, py)], PLATED_TRACK_MM, "PWR_T", bottom)
    return _save(board, path)


# --- stitching: ground vias and pours -----------------------------------------------

GROUND_VIAS = [(10.0, 10.0), (20.0, 10.0), (30.0, 10.0)]  # ground, counted
SIGNAL_VIAS = [(10.0, 20.0), (20.0, 20.0)]  # another net, not counted
GROUND_HEADER = (40.0, 10.0)  # J1: both pins ground, plated, counted
SIGNAL_HEADER = (50.0, 25.0)  # J2: both pins SIG, plated, not counted
SMD_RESISTOR = (40.0, 25.0)  # R1: pad 1 SIG, pad 2 GND but surface-mount, not counted


def ground_points(directory: Path) -> Path:
    """Write a board with ground and other copper that has holes in it, to be counted.

    Three ground vias (``GROUND_VIAS``) and a 1 x 2 header J1 with both pins on ground
    are the plated ground holes. Not ground vias: two SIG vias, a header J2 whose pins
    are on SIG, and a resistor R1 whose pad 2 is on ground but has no hole. No pours.
    """
    path = directory / "ground_points.kicad_pcb"
    board = _start(path, ["GND", "SIG"])
    for x, y in GROUND_VIAS:
        kb.via(board, x, y, "GND")
    for x, y in SIGNAL_VIAS:
        kb.via(board, x, y, "SIG")
    header = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    _part(board, header, "J1", GROUND_HEADER, {"1": "GND", "2": "GND"})
    _part(board, header, "J2", SIGNAL_HEADER, {"1": "SIG", "2": "SIG"})
    _part(
        board,
        "Resistor_SMD:R_0603_1608Metric",
        "R1",
        SMD_RESISTOR,
        {"1": "SIG", "2": "GND"},
    )
    return _save(board, path)


PITCH = 5.0  # mm between neighbouring stitching vias
# The first via of the lattice, on x and on y. The check samples the board on a 0.5 mm
# grid whose first point lies half a step plus half the outline's line width inside the
# outline's bounding box, so at layout 0.2 mm with the 0.1 mm lines drawn here. Vias at
# 2.7 + 5 k then fall on sample points, and so do the centres of the lattice cells.
PHASE = 2.7
POUR = (1.0, 1.0, WIDTH - 1.0, HEIGHT - 1.0)  # both ground pours, layout mm
ISLAND_CENTRE = (PHASE + 5 * PITCH, PHASE + 3 * PITCH)  # (27.7, 17.7), on a via
ISLAND = (  # the vias of a 3 x 3 block, centre and neighbours, are left out
    ISLAND_CENTRE[0] - PITCH,
    ISLAND_CENTRE[1] - PITCH,
    ISLAND_CENTRE[0] + PITCH,
    ISLAND_CENTRE[1] + PITCH,
)
LONE_VIA = (PHASE + 2 * PITCH, PHASE + 2 * PITCH)  # (12.7, 12.7)
LONE_GAP = (LONE_VIA[0] - 0.1, LONE_VIA[1] - 0.1, LONE_VIA[0] + 0.1, LONE_VIA[1] + 0.1)
BOTTOM_ONLY_FROM = 40.0  # x from which ``one_sided`` leaves the bottom without a pour


@dataclass(frozen=True)
class Stitched:
    """One stitched board: its file and the position of every ground via placed."""

    pcb: Path
    vias: list[tuple[float, float]]


def lattice() -> list[tuple[float, float]]:
    """Return every position of the full via lattice, in layout millimetres."""
    n_x = int((WIDTH - 1.0 - PHASE) // PITCH) + 1
    n_y = int((HEIGHT - 1.0 - PHASE) // PITCH) + 1
    return [
        (PHASE + PITCH * i, PHASE + PITCH * j) for i in range(n_x) for j in range(n_y)
    ]


def _inside(point: tuple[float, float], box: Box) -> bool:
    """Return True if layout ``point`` lies in ``box``, edges included."""
    eps = 1e-6
    x, y = point
    return box[0] - eps <= x <= box[2] + eps and box[1] - eps <= y <= box[3] + eps


def stitched_pours(
    directory: Path,
    name: str,
    gaps: Sequence[Box] = (),
    one_sided: bool = False,
) -> Stitched:
    """Write two ground pours stitched by a lattice of vias, 5 mm apart.

    Both pours cover ``POUR`` and are filled. The vias sit at ``PHASE + PITCH * k`` on
    both axes, except where a box in ``gaps`` holds them out (each box leaves an island
    with no stitching). With ``one_sided`` the bottom pour is cut away from
    ``BOTTOM_ONLY_FROM`` rightwards, and no vias are placed there either, so that
    stretch has ground on the top layer only.
    """
    path = directory / f"{name}.kicad_pcb"
    board = _start(path, ["GND"])
    top, bottom = _layers()
    kb.zone(board, "GND", top, kb.rect(*POUR))
    kb.zone(board, "GND", bottom, kb.rect(*POUR))
    cut: Box = (BOTTOM_ONLY_FROM, 0.0, WIDTH, HEIGHT)
    if one_sided:
        kb.keepout(board, *cut, tracks=False, vias=False, layers=("B.Cu",))
    placed = []
    for point in lattice():
        if any(_inside(point, box) for box in gaps):
            continue
        if one_sided and _inside(point, cut):
            continue
        kb.via(board, *point, "GND")
        placed.append(point)
    _save(board, path, fill=True)
    return Stitched(path, placed)


# --- reserved: copper in a rectangle --------------------------------------------------

REGION = (20.0, 10.0, 30.0, 20.0)  # the reserved rectangle, layout mm (10 x 10 mm)
SIG_TRACK = ((15.0, 15.0), (35.0, 15.0), 0.25)  # top, crosses the region
GND_TRACK = ((25.0, 5.0), (25.0, 25.0), 0.4)  # bottom, crosses the region
OUTSIDE_TRACK = ((15.0, 22.0), (35.0, 22.0), 0.25)  # top, two millimetres below it
PWR_VIA = (28.0, 18.0)  # inside the region, 0.8 mm across
REGION_HEADER = (22.0, 11.0)  # J1: pin 1 SIG, pin 2 GND, both pads inside the region


def reserved_region(directory: Path) -> Path:
    """Write a board with copper of several nets crossing the rectangle ``REGION``.

    A 0.25 mm SIG track on the top layer crosses it (``SIG_TRACK``), a 0.4 mm GND track
    on the bottom layer crosses it (``GND_TRACK``), a PWR via sits inside it, and a
    header J1 stands in it with pin 1 on SIG and pin 2 on GND. A second SIG track
    (``OUTSIDE_TRACK``) passes just below it. A filled GND pour covers the bottom layer.
    """
    top, bottom = _layers()
    path = directory / "reserved.kicad_pcb"
    board = _start(path, ["GND", "SIG", "PWR"])
    header = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    _part(board, header, "J1", REGION_HEADER, {"1": "SIG", "2": "GND"})
    (ax, ay), (bx, by), w = SIG_TRACK
    kb.track(board, [(ax, ay), (bx, by)], w, "SIG", top)
    (ax, ay), (bx, by), w = GND_TRACK
    kb.track(board, [(ax, ay), (bx, by)], w, "GND", bottom)
    (ax, ay), (bx, by), w = OUTSIDE_TRACK
    kb.track(board, [(ax, ay), (bx, by)], w, "SIG", top)
    kb.via(board, *PWR_VIA, "PWR")
    kb.zone(board, "GND", bottom, kb.rect(*POUR))
    return _save(board, path, fill=True)


# --- coupling: victim tracks beside aggressor tracks ---------------------------

AGG_Y = 20.0  # the aggressor's centre line in the ladder, layout mm
AGG_X = (5.0, 55.0)
VICTIM_X = (15.0, 45.0)  # every victim runs 30 mm, well inside the aggressor's span
VICTIM_MM = 0.2
AGG_MM = 0.4
RUN_MM = VICTIM_X[1] - VICTIM_X[0]
# victim net -> edge-to-edge gap to the aggressor (mm); they alternate below and above
LADDER_GAPS = {"V1": 0.4, "V2": 0.8, "V3": 1.6, "V4": 3.2, "V5": 4.7, "V6": 5.0}


def _victim_y(
    index: int, gap: float, agg_y: float, agg_w: float, victim_w: float
) -> float:
    """Return the centre line of a victim ``gap`` mm (edge to edge) from the aggressor.

    Even ``index`` puts it below the aggressor (larger y), odd above it.
    """
    offset = agg_w / 2 + gap + victim_w / 2
    return agg_y + offset if index % 2 == 0 else agg_y - offset


def ladder_y(net: str) -> float:
    """Return the centre line of victim ``net`` of the ``ladder`` board."""
    index = list(LADDER_GAPS).index(net)
    return _victim_y(index, LADDER_GAPS[net], AGG_Y, AGG_MM, VICTIM_MM)


def ladder(directory: Path) -> Path:
    """Write a 0.4 mm PWR track with six 30 mm victim tracks beside it, all on top.

    The victims V1 to V6 are 0.2 mm wide and lie at the edge-to-edge gaps of
    ``LADDER_GAPS``: from 0.4 mm to 5 mm, past the 4.8 mm window of a 1.6 mm board.
    """
    top, _ = _layers()
    path = directory / "ladder.kicad_pcb"
    board = _start(path, ["PWR", *LADDER_GAPS])
    kb.track(board, [(AGG_X[0], AGG_Y), (AGG_X[1], AGG_Y)], AGG_MM, "PWR", top)
    for net in LADDER_GAPS:
        y = ladder_y(net)
        kb.track(board, [(VICTIM_X[0], y), (VICTIM_X[1], y)], VICTIM_MM, net, top)
    return _save(board, path)


# the far-layer board: victim net -> (copper layer of the victim, planar gap in mm)
FAR_LANES = {
    "T0": ("top", 0.0),
    "T1": ("top", 1.2),
    "T2": ("top", 4.2),
    "T3": ("top", 4.7),
    "B0": ("bottom", 0.0),
    "B1": ("bottom", 1.2),
}
FAR_Y = {"top": 10.0, "bottom": 30.0}  # of the aggressor in each lane


def far_layer_y(net: str) -> float:
    """Return the centre line of victim ``net`` of the ``far_layer`` board."""
    layer, gap = FAR_LANES[net]
    if gap == 0.0:
        return FAR_Y[layer]
    index = list(FAR_LANES).index(net)  # alternate sides for the sake of clearance
    return _victim_y(index, gap, FAR_Y[layer], AGG_MM, VICTIM_MM)


def far_layer(directory: Path) -> Path:
    """Write victims on one copper layer beside PWR aggressors on the other.

    A 0.4 mm PWR track on the bottom layer at y = 10 has four victims T0 to T3 on the
    top layer at planar edge-to-edge gaps of 0, 1.2, 4.2 and 4.7 mm; a PWR track on the
    top layer at y = 30 has two bottom-layer victims B0 and B1 at 0 and 1.2 mm. Gap 0
    puts a victim straight above or below the aggressor.
    """
    top, bottom = _layers()
    path = directory / "far_layer.kicad_pcb"
    board = _start(path, ["PWR", *FAR_LANES])
    kb.track(
        board,
        [(AGG_X[0], FAR_Y["top"]), (AGG_X[1], FAR_Y["top"])],
        AGG_MM,
        "PWR",
        bottom,
    )
    kb.track(
        board,
        [(AGG_X[0], FAR_Y["bottom"]), (AGG_X[1], FAR_Y["bottom"])],
        AGG_MM,
        "PWR",
        top,
    )
    for net, (layer, _) in FAR_LANES.items():
        y = far_layer_y(net)
        kb.track(
            board,
            [(VICTIM_X[0], y), (VICTIM_X[1], y)],
            VICTIM_MM,
            net,
            top if layer == "top" else bottom,
        )
    return _save(board, path)


BAR_TRACKS = {"wide": (12.0, 4.0), "thin": (30.0, 0.6)}  # name -> (y, width mm)
BAR_GAP = 1.0  # edge to edge, for both victims
BAR_VICTIMS = {"VA": "wide", "VB": "thin"}  # victim -> the BAR track it runs beside


def bar_y(victim: str) -> float:
    """Return the centre line of victim ``victim`` of the ``bar_lanes`` board."""
    y, w = BAR_TRACKS[BAR_VICTIMS[victim]]
    return y + w / 2 + BAR_GAP + VICTIM_MM / 2


def bar_lanes(directory: Path) -> Path:
    """Write two BAR-net tracks, a 4 mm one and a 0.6 mm one, each with a victim.

    VA runs beside the 4 mm track and VB beside the 0.6 mm track, both 1 mm away edge to
    edge and 30 mm long. Both tracks are on net BAR, so only their widths tell them
    apart.
    """
    top, _ = _layers()
    path = directory / "bar_lanes.kicad_pcb"
    board = _start(path, ["BAR", *BAR_VICTIMS])
    for y, w in BAR_TRACKS.values():
        kb.track(board, [(AGG_X[0], y), (AGG_X[1], y)], w, "BAR", top)
    for victim in BAR_VICTIMS:
        y = bar_y(victim)
        kb.track(board, [(VICTIM_X[0], y), (VICTIM_X[1], y)], VICTIM_MM, victim, top)
    return _save(board, path)


PAIR_AGG_MM = 1.0
PAIR_GAP = 0.5  # edge to edge, for both victims
LONG_X = (15.0, 45.0)  # the 30 mm victim
SHORT_X = (25.0, 28.0)  # the 3 mm victim


def pair_y(victim: str) -> float:
    """Return the centre line of ``LONG`` (above the aggressor) or ``SHORT`` (below)."""
    offset = PAIR_AGG_MM / 2 + PAIR_GAP + VICTIM_MM / 2
    return AGG_Y - offset if victim == "LONG" else AGG_Y + offset


def violation_pair(directory: Path) -> Path:
    """Write a 1 mm AGG track with a 30 mm victim LONG and a 3 mm victim SHORT.

    Both victims are 0.2 mm wide and 0.5 mm from the aggressor's edge, LONG above it
    and SHORT below, so the same parallel run is long for one and short for the other.
    """
    top, _ = _layers()
    path = directory / "violation_pair.kicad_pcb"
    board = _start(path, ["AGG", "LONG", "SHORT"])
    kb.track(board, [(AGG_X[0], AGG_Y), (AGG_X[1], AGG_Y)], PAIR_AGG_MM, "AGG", top)
    for victim, (x0, x1) in (("LONG", LONG_X), ("SHORT", SHORT_X)):
        y = pair_y(victim)
        kb.track(board, [(x0, y), (x1, y)], VICTIM_MM, victim, top)
    return _save(board, path)


# --- coupling: a pair of sense nets as a tight pair or a wide loop -------------

SHUNT_AT = (10.0, 20.0)  # R1, turned so that its pad 1 is above its pad 2
SENSE_AT = (40.0, 20.0)  # U1, an eight-pin package
SENSE_MM = 0.2
WIDE_X, WIDE_TOP, WIDE_BOTTOM = 34.0, 10.0, 30.0  # the wide loop's corners


@dataclass(frozen=True)
class Shunt:
    """A board with a sense pair and the positions its geometry is built from."""

    pcb: Path
    sa_from: tuple[float, float]  # R1 pad 1, on SA
    sb_from: tuple[float, float]  # R1 pad 2, on SB
    sa_to: tuple[float, float]  # U1 pad 1, on SA
    sb_to: tuple[float, float]  # U1 pad 2, on SB
    sb_extra: tuple[float, float]  # U1 pad 7, also on SB, not part of the pair


def shunt_pair(directory: Path, tight: bool) -> Shunt:
    """Write a board with two nets, SA and SB, routed from a resistor to an IC.

    R1 (0603, standing on end) has SA on pad 1 and SB on pad 2. U1 (an eight-pin SOIC)
    has SA on pad 1, SB on pad 2 and SB again on pad 7, which the pair does not use.
    With ``tight`` both nets run straight from R1 to U1 side by side. Otherwise SA
    goes up and over (to ``WIDE_TOP``) and SB down and under (to ``WIDE_BOTTOM``),
    meeting again at U1: a wide loop.
    """
    top, _ = _layers()
    path = directory / ("shunt_tight.kicad_pcb" if tight else "shunt_wide.kicad_pcb")
    board = _start(path, ["SA", "SB"])
    _part(
        board,
        "Resistor_SMD:R_0603_1608Metric",
        "R1",
        SHUNT_AT,
        {"1": "SA", "2": "SB"},
        rotation=270,
    )
    _part(
        board,
        "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        "U1",
        SENSE_AT,
        {"1": "SA", "2": "SB", "7": "SB"},
    )
    shunt = Shunt(
        path,
        kb.ppos(board, "R1", 1),
        kb.ppos(board, "R1", 2),
        kb.ppos(board, "U1", 1),
        kb.ppos(board, "U1", 2),
        kb.ppos(board, "U1", 7),
    )
    if tight:
        kb.track(board, [shunt.sa_from, shunt.sa_to], SENSE_MM, "SA", top)
        kb.track(board, [shunt.sb_from, shunt.sb_to], SENSE_MM, "SB", top)
    else:
        for net, start, end, y in (
            ("SA", shunt.sa_from, shunt.sa_to, WIDE_TOP),
            ("SB", shunt.sb_from, shunt.sb_to, WIDE_BOTTOM),
        ):
            route = [start, (start[0], y), (WIDE_X, y), (WIDE_X, end[1]), end]
            kb.track(board, route, SENSE_MM, net, top)
    _save(board, path)
    return shunt
