"""Synthetic projects for the copper, fab and outputs check-group tests.

Each builder writes a real board (and the ``.kicad_pro`` that KiCad saves beside it)
into a folder and returns what a test needs to know about what was built. The nets are
GND, VIN, RTN and so on, the parts are references such as TP1, R1 and U1, and the
footprints are KiCad's own stock ones: nothing here is a real design. Positions are
layout millimetres (from the board's top-left corner, y down); the KiCad file holds
them 50 mm further along each axis, as ``pcbkit.kicad.board`` says.

* ``copper_board``: a 60 x 40 mm board with a stitched ground, a supply and its return
  as 3 mm tracks, a 1 mm aggressor with a sensitive net beside it and a filter
  capacitor on it, a shunt's sense pair, and two regions reserved for ground.
* ``fab_board``: a 60 x 40 mm board with 0603 resistors and a capacitor, an IC, a pin
  header with polarity marks, a power transistor, vias and silkscreen text, laid out
  to pass the ``fab`` group's checks.
* ``outputs_project``: the tiny board of tests/tiny_board.py (two 10k resistors on a
  30 x 20 mm board) with a header and a mounting hole added, and the files the
  ``outputs`` group reads, exported from it: Gerbers and drill files in a zip, a BOM and
  a centroid.

The first two take keyword arguments that each plant one mistake, and come with the
netlist they were built from (``toy``), so a board and its netlist can never disagree
about a reference or a net. Importing this module needs no pcbnew; every builder
imports it when it runs, so only a test that has done
``pytest.importorskip("pcbnew")`` calls one.
"""

from __future__ import annotations

import csv
import math
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pcbkit.fab import bom as fab_bom
from pcbkit.kicad import board as kb
from pcbkit.kicad import cli, env
from pcbkit.kicad.sexp import dump, find, findall, parse, q
from tests import tiny_board
from tests.check_toy import Toy

WIDTH, HEIGHT = 60.0, 40.0
Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1

RES = "Resistor_SMD:R_0603_1608Metric"
CAP = "Capacitor_SMD:C_0603_1608Metric"
TEST_POINT = "TestPoint:TestPoint_Pad_2.0x2.0mm"
SOIC = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
SOT = "Package_TO_SOT_SMD:SOT-23-5"
HEADER = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
POWER_FET = "Package_TO_SOT_SMD:TO-252-2"
MOUNTING_HOLE = "MountingHole:MountingHole_3.2mm_M3"


# --- the pieces every builder uses ---------------------------------------------------


@dataclass(frozen=True)
class Part:
    """One part of a board: its footprint, its symbol, where it sits and its nets.

    ``footprint`` is a stock "Library:Name"; ``symbol`` is the schematic symbol the
    netlist gives it ("Device:R"). ``nets`` maps a pad number to its net (no leading
    "/"). ``at`` is where the part's origin goes, or, when ``anchor`` names a pad, where
    that pad's centre goes.
    """

    ref: str
    footprint: str
    symbol: str
    value: str
    at: Point
    nets: Mapping[str, str] = field(default_factory=dict)
    rotation: float = 0.0
    anchor: str = ""


def start(path: Path, nets: Sequence[str]) -> Any:
    """Return a new two-layer board with the outline drawn and the nets declared.

    ``nets`` are written without KiCad's leading "/": ``VIN`` becomes the net "/VIN".
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


def place(board: Any, part: Part) -> Any:
    """Place a part on the board, its pads on their nets, and return the footprint.

    The footprint's value text is the part's value. A part with an ``anchor`` is moved
    after it is placed, so that the anchor pad's centre is exactly at ``part.at``.
    """
    import pcbnew

    library, name = part.footprint.split(":")
    footprint = pcbnew.FootprintLoad(
        str(env.footprints_dir() / f"{library}.pretty"), name
    )
    footprint.SetFPID(pcbnew.LIB_ID(library, name))
    footprint.SetReference(part.ref)
    footprint.SetValue(part.value)
    board.Add(footprint)
    footprint.SetOrientationDegrees(part.rotation)
    footprint.SetPosition(kb.pt(*part.at))
    if part.anchor:
        have = kb.ppos(board, part.ref, part.anchor)
        footprint.SetPosition(kb.pt(2 * part.at[0] - have[0], 2 * part.at[1] - have[1]))
    for pad in footprint.Pads():
        if pad.GetNumber() in part.nets:
            pad.SetNet(kb.N(board, part.nets[pad.GetNumber()]))
    return footprint


def toy_of(parts: Sequence[Part]) -> Toy:
    """Return the netlist of the parts: each pad that has a net, on that net."""
    toy = Toy()
    for part in parts:
        toy.add(
            part.ref,
            part.value,
            part.symbol,
            {pad: (net, "~") for pad, net in part.nets.items()},
        )
    return toy


def set_rules(
    board: Any, min_track_mm: float, min_clearance_mm: float, origin: Point | None
) -> Any:
    """Set the board's minimum track and clearance and its place origin; return them.

    The rules are saved in the ``.kicad_pro`` beside the board. ``origin`` is the layout
    point that becomes the drill and place origin, which the Gerbers and the position
    file are measured from.
    """
    settings = board.GetDesignSettings()
    settings.m_TrackMinWidth = kb.mm(min_track_mm)
    settings.m_MinClearance = kb.mm(min_clearance_mm)
    if origin is not None:
        settings.SetAuxOrigin(kb.pt(*origin))
    return settings


def save(
    board: Any,
    path: Path,
    copper_mm: float = 0.035,
    fill: bool = False,
    min_track_mm: float = 0.2,
    min_clearance_mm: float = 0.2,
    origin: Point | None = None,
    stackup: bool = True,
) -> Path:
    """Fill the pours if asked, save the board and write its rules and its stackup.

    A board made with NewBoard has no stackup to save, so it is saved, loaded again,
    asked for one (``m_HasStackup``) and saved once more, the order pcbkit builds a
    board in. The copper in the stackup is then set to ``copper_mm`` after the last
    save, as pcbkit does it. With ``stackup`` False the board is saved once, with none.
    """
    import pcbnew

    if fill:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    set_rules(board, min_track_mm, min_clearance_mm, origin)
    pcbnew.SaveBoard(str(path), board)
    if not stackup:
        return path
    reloaded = pcbnew.LoadBoard(str(path))
    set_rules(reloaded, min_track_mm, min_clearance_mm, origin).m_HasStackup = True
    pcbnew.SaveBoard(str(path), reloaded)
    kb.set_copper(str(path), copper_mm)
    return path


def assert_matches_netlist(pcb: Path, parts: Sequence[Part]) -> None:
    """Assert every pad of the saved board is on the net ``parts`` give it."""
    import pcbnew

    pins = toy_of(parts).pins
    count = 0
    for footprint in pcbnew.LoadBoard(str(pcb)).GetFootprints():
        for pad in footprint.Pads():
            if pad.GetNetname():
                ref, number = footprint.GetReference(), pad.GetNumber()
                assert pins[ref][number][0] == pad.GetNetname().lstrip("/"), (
                    ref,
                    number,
                )
                count += 1
    assert count == sum(len(part.nets) for part in parts)


def polyline_length(points: Sequence[Point]) -> float:
    """Return the length of the path through ``points``."""
    return sum(math.dist(a, b) for a, b in zip(points[:-1], points[1:]))


def serpentine(start_at: Point, run: float, pitch: float, passes: int) -> list[Point]:
    """Return the corners of a track that runs ``passes`` times back and forth.

    Each pass is ``run`` mm long, to the right on the first and back on the next, and
    the next pass is ``pitch`` mm further up the board (smaller y).
    """
    x, y = start_at
    points = [(x, y)]
    for i in range(passes):
        x = start_at[0] + run if i % 2 == 0 else start_at[0]
        points.append((x, y))
        if i < passes - 1:
            y -= pitch
            points.append((x, y))
    return points


# --- the copper board ----------------------------------------------------------------

PITCH = 5.0  # mm between neighbouring stitching vias, on both axes
# The first via of the lattice. The stitching check samples the board on a 0.5 mm grid
# that starts half a step plus half the outline's 0.1 mm line inside the outline, so
# vias at 2.7 + 5 k fall on sample points, and so do the centres of the lattice cells.
PHASE = 2.7
POUR: Box = (1.0, 1.0, WIDTH - 1.0, HEIGHT - 1.0)  # both ground pours, layout mm
# Between two rows of the lattice runs a corridor, 2.5 mm from each; the copper of the
# other nets stays in the corridors and the cells between four vias, so that none of
# the stitching vias has to move.
CORRIDOR_Y = [PHASE + PITCH / 2 + PITCH * j for j in range(7)]  # 5.2, 10.2, ... 35.2
LONE_VIA: Point = (PHASE + 10 * PITCH, PHASE + 6 * PITCH)  # (52.7, 32.7): a lone gap
ISLAND_CENTRE: Point = (PHASE + 5 * PITCH, PHASE + 3 * PITCH)  # (27.7, 17.7): a via
ISLAND: Box = (  # the 3 x 3 block of vias round that centre
    ISLAND_CENTRE[0] - PITCH,
    ISLAND_CENTRE[1] - PITCH,
    ISLAND_CENTRE[0] + PITCH,
    ISLAND_CENTRE[1] + PITCH,
)

FEED_MM = 3.0  # the width of the supply and return tracks
VIN_Y, RTN_Y = CORRIDOR_Y[1], CORRIDOR_Y[2]  # 10.2 and 15.2
LOAD_X = {"source": 8.0, "near": 32.0, "far": 56.0}  # the pads along each track
SUPPLY_PADS = {"source": "TP1", "near": "TP2", "far": "TP3"}
RETURN_PADS = {"source": "TP4", "near": "TP5", "far": "TP6"}
PAD_MM = 2.0  # the test-point pads are 2 x 2 mm squares

AGG_MM = 1.0  # the aggressor PWR, a bar
VICTIM_MM = 0.2  # the sensitive net SENSE
VICTIM_GAP_MM = 0.5  # edge to edge, from the aggressor to SENSE
AGG_X0 = 14.0  # where the aggressor starts; it runs ``run_mm`` from there
VICTIM_Y = 24.25  # SENSE runs along U1's pin 1 row, from R10 to pin 1
AGG_Y = VICTIM_Y + AGG_MM / 2 + VICTIM_GAP_MM + VICTIM_MM / 2  # below SENSE
SENSE_Y = CORRIDOR_Y[5]  # 30.2: the shunt pair's resistors stand on end here
SENSE_X = (10.2, 30.2)
SENSE_MM = 0.2
WIDE_Y = CORRIDOR_Y[6]  # 35.2: the corridor the wide loop's SB takes

STRIP: Box = (1.5, 33.6, WIDTH - 1.5, 36.8)  # bottom: ground only (a return strip)
KEEPOUT: Box = (41.0, 3.6, WIDTH - 1.5, 6.8)  # top: ground only (antenna keep-out)
# a 0.25 mm track of SIG across each: its two ends, 20 mm and 12 mm apart, in the box
INTRUDERS = {
    "bottom": [(10.0, WIDE_Y), (30.0, WIDE_Y)],
    "top": [(43.0, CORRIDOR_Y[0]), (55.0, CORRIDOR_Y[0])],
}


@dataclass(frozen=True)
class CopperBoard:
    """One copper-group board: its file, its parts and the geometry of its copper."""

    pcb: Path
    parts: list[Part]
    vias: list[Point]  # every ground stitching via placed
    victim: list[Point]  # SENSE's path, R10's pad 2 to U1's pin 1
    aggressor: tuple[Point, Point]  # the ends of PWR's centre line
    pair_loop: list[Point]  # the sense pair's loop, as a polygon
    cap_pad: Point  # C1's pad on SENSE (the origin when C1 is left out)
    adc_pin: Point  # U1's pin 1

    @property
    def toy(self) -> Toy:
        """Return the netlist of the board's parts."""
        return toy_of(self.parts)


def lattice() -> list[Point]:
    """Return every position of the full via lattice, in layout millimetres."""
    n_x = int((WIDTH - 1.0 - PHASE) // PITCH) + 1
    n_y = int((HEIGHT - 1.0 - PHASE) // PITCH) + 1
    return [
        (PHASE + PITCH * i, PHASE + PITCH * j) for i in range(n_x) for j in range(n_y)
    ]


def inside(point: Point, box: Box) -> bool:
    """Return True if layout ``point`` lies in ``box``, edges included."""
    eps = 1e-6
    return (
        box[0] - eps <= point[0] <= box[2] + eps
        and box[1] - eps <= point[1] <= box[3] + eps
    )


def copper_parts(
    sensitive: str = "SENSE", cap_mm: float = 2.0, with_cap: bool = True
) -> list[Part]:
    """Return the parts of the copper-group board; ``copper_board`` says what they are.

    ``sensitive`` is the net the sensitive net is called, ``cap_mm`` how far C1's pad on
    it is from U1's pin 1 (along the net) and ``with_cap`` leaves C1 out.
    """
    parts = []
    for pads, net, y in ((SUPPLY_PADS, "VIN", VIN_Y), (RETURN_PADS, "RTN", RTN_Y)):
        for where, ref in pads.items():
            at = (LOAD_X[where], y)
            parts.append(
                Part(ref, TEST_POINT, "Connector:TestPoint", "TP", at, {"1": net})
            )
    u1_at = (50.2, 25.2)  # pin 1 is 1.1375 mm left of it and 0.95 mm above
    pin1 = (u1_at[0] - 1.1375, u1_at[1] - 0.95)
    assert abs(pin1[1] - VICTIM_Y) < 1e-9
    parts += [
        Part(
            "R10",
            RES,
            "Device:R",
            "10k",
            (10.2, VICTIM_Y),
            {"1": "SENSE_IN", "2": sensitive},
        ),
        Part("U1", SOT, "Sensor:ADC", "ADC", u1_at, {"1": sensitive, "2": "GND"}),
    ]
    if with_cap:
        parts.append(
            Part(
                "C1",
                CAP,
                "Device:C",
                "100n",
                (pin1[0] - cap_mm, VICTIM_Y),
                {"1": sensitive, "2": "GND"},
                rotation=90.0,
                anchor="1",
            )
        )
    for ref, x in zip(("R1", "R2"), SENSE_X):
        at = (x, SENSE_Y)
        nets = {"1": "SA", "2": "SB"}
        parts.append(Part(ref, RES, "Device:R", "0R01", at, nets, rotation=270.0))
    return parts


def copper_board(
    directory: Path,
    stem: str = "board",
    *,
    island: bool = False,
    gap: bool = False,
    intruder: str = "",
    run_mm: float = 3.0,
    cap_mm: float = 2.0,
    with_cap: bool = True,
    wide_loop: bool = False,
    far_feed_mm: float = FEED_MM,
    far_return_mm: float = FEED_MM,
    sensitive: str = "SENSE",
    copper_mm: float = 0.035,
) -> CopperBoard:
    """Write the copper-group board into ``directory`` and return what was built.

    The control (every argument at its default) is clean for each ``copper`` check:
    ground stitched by the complete lattice of vias under two filled pours, ground
    alone in the two reserved boxes, the aggressor PWR running 3 mm beside the sensitive
    net SENSE, C1 2 mm from U1's pin 1, the pair SA and SB running side by side from R1
    to R2, and 3 mm tracks for the supply VIN and its return RTN. Planting a mistake:

    * ``island``: leave out the 3 x 3 block of stitching vias round ``ISLAND_CENTRE``;
    * ``gap``: leave out the one stitching via at ``LONE_VIA``;
    * ``intruder``: a 0.25 mm track of the net SIG across a reserved box: "bottom" for
      the strip on the bottom layer, "top" for the keep-out on the top one;
    * ``run_mm``: how long the aggressor runs beside SENSE;
    * ``cap_mm`` and ``with_cap``: how far C1 is from U1's pin 1, and if it is there;
    * ``wide_loop``: SB takes the long way round, by the bottom corridor;
    * ``far_feed_mm`` and ``far_return_mm``: the width of VIN, and of RTN, from the
      near load to the far one;
    * ``sensitive``: the name the board gives the net the specs call SENSE;
    * ``copper_mm``: the copper thickness written into the board's stackup.
    """
    import pcbnew

    path = directory / f"{stem}.kicad_pcb"
    parts = copper_parts(sensitive, cap_mm, with_cap)
    nets = ["GND", "VIN", "RTN", "PWR", "SENSE_IN", "SA", "SB", "SIG", sensitive]
    board = start(path, nets)
    top, bottom = pcbnew.F_Cu, pcbnew.B_Cu
    kb.zone(board, "GND", top, kb.rect(*POUR))
    kb.zone(board, "GND", bottom, kb.rect(*POUR))
    vias = [
        p
        for p in lattice()
        if not (island and inside(p, ISLAND)) and not (gap and p == LONE_VIA)
    ]
    for point in vias:
        kb.via(board, *point, "GND")
    for part in parts:
        place(board, part)
    for net, y, far in (("VIN", VIN_Y, far_feed_mm), ("RTN", RTN_Y, far_return_mm)):
        near = [(LOAD_X["source"], y), (LOAD_X["near"], y)]
        kb.track(board, near, FEED_MM, net, top)
        kb.track(board, [(LOAD_X["near"], y), (LOAD_X["far"], y)], far, net, top)
    adc_pin = kb.ppos(board, "U1", 1)
    victim = [kb.ppos(board, "R10", 2), adc_pin]
    assert abs(victim[0][1] - VICTIM_Y) < 1e-9 and abs(adc_pin[1] - VICTIM_Y) < 1e-9
    kb.track(board, victim, VICTIM_MM, sensitive, top)
    aggressor = ((AGG_X0, AGG_Y), (AGG_X0 + run_mm, AGG_Y))
    kb.track(board, list(aggressor), AGG_MM, "PWR", top)
    cap_pad = kb.ppos(board, "C1", 1) if with_cap else (0.0, 0.0)
    sa = [kb.ppos(board, "R1", 1), kb.ppos(board, "R2", 1)]
    sb_from, sb_to = kb.ppos(board, "R1", 2), kb.ppos(board, "R2", 2)
    sb = [sb_from, sb_to]
    if wide_loop:
        sb = [sb_from, (sb_from[0], WIDE_Y), (sb_to[0], WIDE_Y), sb_to]
    kb.track(board, sa, SENSE_MM, "SA", top)
    kb.track(board, sb, SENSE_MM, "SB", top)
    if intruder:
        where = INTRUDERS[intruder]
        kb.track(board, where[:2], 0.25, "SIG", bottom if intruder == "bottom" else top)
    save(board, path, copper_mm=copper_mm, fill=True)
    return CopperBoard(
        path, parts, vias, victim, aggressor, sa + sb[::-1], cap_pad, adc_pin
    )


# --- the fab board -------------------------------------------------------------------

SDA_CORNERS = [(15.0, 10.09), (15.0, 8.0)]  # SDA's track bends here, U1.1 to R1.2
SCL_CORNERS = [(14.0, 11.36), (14.0, 16.0)]  # SCL's track bends here, U1.2 to R2.2
GND_VIAS = [(12.0, 30.0), (16.0, 30.0), (20.0, 30.0)]
STUB_AT = (15.0, 7.2)  # where a long stub of SDA leaves its track, going up the board


@dataclass(frozen=True)
class FabBoard:
    """One fab-group board: its file, its parts and the geometry it was built from."""

    pcb: Path
    parts: list[Part]
    sda: list[Point]  # SDA's main track, U1's pin 1 to R1's pad 2
    scl: list[Point]  # SCL's track
    stub: list[Point]  # the extra track on SDA, from its first corner (may be empty)
    pin8: Point  # U1's supply pin
    cap_pad: Point  # C1's pad nearest U1's pin 8
    header_pads: tuple[Point, Point]  # J1's pad 1 and pad 2

    @property
    def toy(self) -> Toy:
        """Return the netlist of the board's parts."""
        return toy_of(self.parts)


def fab_parts(
    cap_mm: float = 2.0, pullup: str = "5.1k", scl_pullup: str = ""
) -> list[Part]:
    """Return the parts of the fab-group board; ``fab_board`` says what they are.

    C1's pad 1 is ``cap_mm`` to the right of U1's pin 8; ``pullup`` is the value of
    the I2C pull-up on SDA and ``scl_pullup`` of the one on SCL (``pullup`` if empty).
    """
    u1_at = (24.0, 12.0)
    pin8 = (u1_at[0] + 2.475, u1_at[1] - 1.905)
    return [
        Part("R1", RES, "Device:R", pullup, (8.0, 8.0), {"1": "+3V3", "2": "SDA"}),
        Part(
            "R2",
            RES,
            "Device:R",
            scl_pullup or pullup,
            (8.0, 16.0),
            {"1": "+3V3", "2": "SCL"},
        ),
        Part(
            "U1",
            SOIC,
            "Sensor:S1",
            "SENSOR",
            u1_at,
            {"1": "SDA", "2": "SCL", "4": "GND", "8": "+3V3"},
        ),
        Part(
            "C1",
            CAP,
            "Device:C",
            "100n",
            (pin8[0] + cap_mm, pin8[1]),
            {"1": "+3V3", "2": "GND"},
            anchor="1",
        ),
        Part(
            "J1",
            HEADER,
            "Connector:Conn_01x02",
            "VBUS IN",
            (52.0, 8.0),
            {"1": "VBUS", "2": "GND"},
        ),
        Part(
            "Q1",
            POWER_FET,
            "Device:Q_NMOS",
            "FET",
            (40.0, 28.0),
            {"1": "GATE", "2": "VBUS", "3": "SW"},
        ),
    ]


def fab_board(
    directory: Path,
    stem: str = "board",
    *,
    cap_mm: float = 2.0,
    pullup: str = "5.1k",
    scl_pullup: str = "",
    via_mm: tuple[float, float] = (0.8, 0.4),
    slot_mm: tuple[float, float] = (0.6, 1.2),
    text_mm: float = 1.0,
    bold_text: bool = False,
    overlap: bool = False,
    marks: str = "both",
    tab_net: str = "VBUS",
    stub: Sequence[Point] = (),
    copper_mm: float = 0.035,
    min_track_mm: float = 0.2,
    min_clearance_mm: float = 0.2,
    stackup: bool = True,
) -> FabBoard:
    """Write the fab-group board into ``directory`` and return what was built.

    The control (every argument at its default) is clean for each ``fab`` check: three
    ground vias of 0.8 mm over a 0.4 mm drill, a 0.6 x 1.2 mm slot in J1's pad 2, a
    1 mm silkscreen text, C1 2 mm from U1's pin 8, the "+" and "-" of J1 beside their
    own pads, Q1's tab (its largest pad, number 2) on VBUS, nothing printed over
    anything on the assembly drawing, 5.1 kohm I2C pull-ups, and rules of 0.2 mm for
    tracks and clearance. Planting a mistake:

    * ``cap_mm``: how far C1 is from U1's pin 8;
    * ``pullup`` and ``scl_pullup``: the I2C pull-up on SDA, and the one on SCL (the
      same when left out);
    * ``via_mm``: the diameter and drill of the vias;
    * ``slot_mm``: the width and length of the slot in J1's pad 2;
    * ``text_mm`` and ``bold_text``: the height of the board's silkscreen text, and
      whether its stroke is 20 % of that rather than 15 %;
    * ``overlap``: R2 sits on R1, so that their texts overlap on the assembly drawing;
    * ``marks``: which polarity marks J1 has: "both", "none", "swapped" or "plus_only";
    * ``tab_net``: the net Q1's tab is on;
    * ``stub``: the corners of an extra track on SDA, joined to its first corner;
    * ``copper_mm``: the copper thickness written into the board's stackup;
    * ``min_track_mm`` and ``min_clearance_mm``: the board's rules;
    * ``stackup``: False saves the board with no stackup in its file.
    """
    import pcbnew

    path = directory / f"{stem}.kicad_pcb"
    parts = fab_parts(cap_mm, pullup, scl_pullup)
    if overlap:
        parts = [replace(p, at=(8.0, 8.0)) if p.ref == "R2" else p for p in parts]
    parts = [
        replace(p, nets={"1": "GATE", "2": tab_net, "3": "SW"}) if p.ref == "Q1" else p
        for p in parts
    ]
    board = start(path, ["+3V3", "GND", "SDA", "SCL", "VBUS", "GATE", "SW"])
    footprints = {part.ref: place(board, part) for part in parts}
    for pad in footprints["J1"].Pads():
        if pad.GetNumber() == "2":
            pad.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
            pad.SetDrillSize(pcbnew.VECTOR2I(kb.mm(slot_mm[0]), kb.mm(slot_mm[1])))
    for x, y in GND_VIAS:
        kb.via(board, x, y, "GND", d=via_mm[0], drill=via_mm[1])
    sda = [kb.ppos(board, "U1", 1), *SDA_CORNERS, kb.ppos(board, "R1", 2)]
    scl = [kb.ppos(board, "U1", 2), *SCL_CORNERS, kb.ppos(board, "R2", 2)]
    kb.track(board, sda, 0.25, "SDA")
    kb.track(board, scl, 0.25, "SCL")
    if stub:
        kb.track(board, [sda[1], *stub], 0.25, "SDA")
    pads = (kb.ppos(board, "J1", 1), kb.ppos(board, "J1", 2))
    plus, minus = (pads[0][0] - 2.2, pads[0][1]), (pads[1][0] - 2.2, pads[1][1])
    if marks == "swapped":
        plus, minus = minus, plus
    if marks != "none":
        kb.add_text(board, "+", *plus, size=1.0)
    if marks not in ("none", "plus_only"):
        kb.add_text(board, "-", *minus, size=1.0)
    kb.add_text(board, "FAB TEST", 14.0, 36.0, size=text_mm, bold=bold_text)
    cap_pad = kb.ppos(board, "C1", 1)
    pin8 = kb.ppos(board, "U1", 8)
    save(
        board,
        path,
        copper_mm=copper_mm,
        min_track_mm=min_track_mm,
        min_clearance_mm=min_clearance_mm,
        stackup=stackup,
    )
    return FabBoard(path, parts, sda, scl, list(stub), pin8, cap_pad, pads)


# --- the outputs project -------------------------------------------------------------

MPN = "RES-10K-0603"  # the part number the tiny board's two resistors carry
HEADER_AT = (5.0, 4.0)  # J1, a header that is not in the schematic
HOLE_AT = (26.0, 4.0)  # H1, a mounting hole
BOARD_W, BOARD_H = tiny_board.WIDTH, tiny_board.HEIGHT
FAB_NAME = f"{tiny_board.STEM}_revA"  # [board] fab_name of the outputs project
ORIGIN = (0.0, BOARD_H)  # the drill and place origin: the board's bottom-left corner


def add_mpn(schematic: Path, mpn: str = MPN) -> None:
    """Give every placed symbol of the schematic an MPN property."""
    tree = parse(schematic.read_text(encoding="utf-8"))
    for symbol in findall(tree, "symbol"):
        at = find(symbol, "at")
        assert at is not None
        symbol.append(["property", q("MPN"), q(mpn), ["at", at[1], at[2], 0]])
    schematic.write_text(dump(tree) + "\n", encoding="utf-8")


def add_extras(pcb: Path, copper_mm: float = 0.035) -> None:
    """Add a header and a mounting hole to the tiny board, and its drill origin.

    Neither is in the schematic, as a mounting hole usually is not. The place origin
    goes to the board's bottom-left corner, so the position file counts y up from the
    bottom edge, as the fab house's centroid does.
    """
    import pcbnew

    board = pcbnew.LoadBoard(str(pcb))
    place(board, Part("J1", HEADER, "Connector:Conn_01x02", "HDR", HEADER_AT))
    place(board, Part("H1", MOUNTING_HOLE, "Mechanical:MountingHole", "M3", HOLE_AT))
    save(board, pcb, copper_mm=copper_mm, origin=ORIGIN)


def zip_gerbers(plot: Path, archive: Path, skip: Sequence[str] = ()) -> None:
    """Zip the files of ``plot`` flat into ``archive``, less the names in ``skip``."""
    with zipfile.ZipFile(archive, "w") as handle:
        for found in sorted(plot.iterdir()):
            if found.name not in skip:
                handle.write(found, found.name)


def export_fab(root: Path, stem: str, fab_name: str) -> Path:
    """Export the board's Gerbers, drill files, BOM and centroid into ``out/fab``.

    The Gerbers and drill files are kicad-cli's own, zipped flat as the fab house takes
    them; the centroid is kicad-cli's position file with the columns the fab house
    names them; the BOM (pcbkit's own format) lists R1 and R2 as one line. Return
    the folder. The plot files
    stay in ``out/plot``, so a test can zip them again with one left out.
    """
    pcb = root / "kicad" / f"{stem}.kicad_pcb"
    fab, plot = root / "out" / "fab", root / "out" / "plot"
    for folder in (fab, plot):
        folder.mkdir(parents=True, exist_ok=True)
    cli.export_gerbers(pcb, plot)
    cli.export_drill(pcb, plot)
    zip_gerbers(plot, fab / f"{fab_name}_gerbers.zip")
    positions = fab / "positions.csv"
    cli.export_positions(pcb, positions)
    with open(positions, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    positions.unlink()
    with open(fab / f"{fab_name}_centroid.csv", "w", encoding="utf-8", newline="") as h:
        out = csv.writer(h)
        out.writerow(["Designator", "Mid X (mm)", "Mid Y (mm)", "Layer", "Rotation"])
        for row in rows:
            out.writerow(
                [row["Ref"], row["PosX"], row["PosY"], row["Side"], row["Rot"]]
            )
    line = fab_bom.BomLine(
        item=1,
        qty=2,
        refs=("R1", "R2"),
        mfr="Acme",
        mpn=MPN,
        value="10k",
        desc="Resistor 10k 0603",
        footprint="R_0603_1608Metric",
        through_hole=False,
    )
    fab_bom.write_csv(fab / f"{fab_name}_BOM.csv", [line])
    return fab


# The design behind the tiny board's schematic, for checks that read design.py.
OUTPUTS_DESIGN = f"""\
from pcbkit.design import R

R("R1", "10k", "NET_A", "NET_B", "Tiny", mpn={MPN!r})
R("R2", "10k", "NET_B", "GND", "Tiny", mpn={MPN!r})
"""


def outputs_project(root: Path, copper_mm: float = 0.035) -> Path:
    """Build the outputs-group project in ``root``: the tiny board, extras and exports.

    ``kicad/`` holds the tiny board's schematic (with an MPN on each resistor) and its
    board (30 x 20 mm, with a header and a mounting hole added, at ``copper_mm``);
    ``out/fab`` holds the files exported from them; ``layout.py`` gives the board's
    size; ``design.py`` is the design behind the schematic. The caller writes
    ``pcbkit.toml``. Return ``root``.
    """
    built = tiny_board.build(root / "kicad")
    add_mpn(built.sch)
    add_extras(built.pcb, copper_mm)
    (root / "layout.py").write_text(f"W, H = {BOARD_W}, {BOARD_H}\n", encoding="utf-8")
    (root / "design.py").write_text(OUTPUTS_DESIGN, encoding="utf-8")
    export_fab(root, tiny_board.STEM, FAB_NAME)
    return root
