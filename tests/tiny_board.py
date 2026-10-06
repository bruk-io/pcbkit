"""A tiny board built the way pcbkit builds real ones, for tests that need real KiCad.

``build(directory)`` writes ``tiny.kicad_sch`` and ``tiny.kicad_pcb``: two 0603
resistors on a 30 x 20 mm board with rounded corners, wired as

    R1.1 /NET_A    R1.2 -- /NET_B track -- R2.1    R2.2 -- /GND track, via -- GND pour

The pour is on the bottom layer. The top has two rule areas (a rectangle and a
triangle) and a piece of silkscreen text. It follows the production order: schematic,
then a board made with NewBoard (nets, footprints, outline), saved and reloaded, then
the helpers in ``pcbkit.kicad.board`` add the copper, the pour is filled and the board
is saved again. KiCad's own checks should find nothing wrong with it, schematic parity
included, so a test can plant one mistake and see exactly that mistake reported.

Importing this module needs nothing; building a board needs pcbnew and KiCad's stock
libraries, so only a test that has done ``pytest.importorskip("pcbnew")`` calls it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pcbkit.kicad import board as kb
from pcbkit.kicad import env
from pcbkit.kicad.sexp import dump, find, findall, parse, q

STEM = "tiny"
WIDTH, HEIGHT, CORNER = 30.0, 20.0, 2.0
FOOTPRINT = ("Resistor_SMD", "R_0603_1608Metric")

# Where each resistor sits, in layout millimetres, and which net each pad is on.
PLACEMENT = {"R1": (8.0, 10.0), "R2": (22.0, 10.0)}
PAD_NETS = {
    ("R1", "1"): "/NET_A",
    ("R1", "2"): "/NET_B",
    ("R2", "1"): "/NET_B",
    ("R2", "2"): "/GND",
}
# The one via and the keep-outs (layout millimetres).
GND_VIA = (24.5, 10.0)
KEEPOUT_RECT = (2.0, 15.0, 8.0, 18.0)
KEEPOUT_TRIANGLE = [(24.0, 15.0), (28.0, 15.0), (26.0, 18.0)]


@dataclass(frozen=True)
class TinyBoard:
    """The files of one tiny board and the schematic's symbol ids."""

    directory: Path
    pcb: Path
    sch: Path
    symbols: dict[str, str]


def _uid() -> Any:
    """Return a fresh id as a quoted string for the schematic."""
    return q(str(uuid.uuid4()))


def write_schematic(directory: Path) -> tuple[Path, dict[str, str]]:
    """Write tiny.kicad_sch; return its path and the id of each symbol by reference.

    Two Device:R symbols from KiCad's own library, a label on each pin tip to name its
    net (a label at a pin tip joins that pin to the net), nothing else.
    """
    library = parse(
        (env.symbols_dir() / "Device.kicad_sym").read_text(encoding="utf-8")
    )
    resistor = next(s for s in findall(library, "symbol") if s[1] == "R")
    resistor[1] = q("Device:R")
    root = str(uuid.uuid4())
    items: list[list[Any]] = []
    symbols: dict[str, str] = {}
    for ref, x in (("R1", 100.33), ("R2", 120.65)):
        y = 80.01
        symbols[ref] = str(uuid.uuid4())
        items.append(
            [
                "symbol",
                ["lib_id", q("Device:R")],
                ["at", x, y, 0],
                ["unit", 1],
                ["exclude_from_sim", "no"],
                ["in_bom", "yes"],
                ["on_board", "yes"],
                ["dnp", "no"],
                ["uuid", q(symbols[ref])],
                ["property", q("Reference"), q(ref), ["at", x + 2.54, y - 1.27, 0]],
                ["property", q("Value"), q("10k"), ["at", x + 2.54, y + 1.27, 0]],
                ["property", q("Footprint"), q(":".join(FOOTPRINT)), ["at", x, y, 0]],
                ["pin", q("1"), ["uuid", _uid()]],
                ["pin", q("2"), ["uuid", _uid()]],
                [
                    "instances",
                    [
                        "project",
                        q(STEM),
                        ["path", q("/" + root), ["reference", q(ref)], ["unit", 1]],
                    ],
                ],
            ]
        )
        for pin, tip in (("1", y - 3.81), ("2", y + 3.81)):
            net = PAD_NETS[(ref, pin)].lstrip("/")
            items.append(
                [
                    "label",
                    q(net),
                    ["at", x, tip, 0],
                    ["effects", ["font", ["size", 1.27, 1.27]], ["justify", "left"]],
                    ["uuid", _uid()],
                ]
            )
    sch = (
        ["kicad_sch", ["version", 20250114], ["generator", q("eeschema")]]
        + [["generator_version", q("9.0")], ["uuid", q(root)], ["paper", q("A4")]]
        + [["lib_symbols", resistor]]
        + items
        + [["sheet_instances", ["path", q("/"), ["page", q("1")]]]]
        + [["embedded_fonts", "no"]]
    )
    path = directory / f"{STEM}.kicad_sch"
    path.write_text(dump(sch) + "\n", encoding="utf-8")
    return path, symbols


def _load_footprint(pcbnew: Any) -> Any:
    """Return a fresh copy of the stock resistor footprint."""
    library, name = FOOTPRINT
    folder = env.footprints_dir() / f"{library}.pretty"
    footprint = pcbnew.FootprintLoad(str(folder), name)
    footprint.SetFPID(pcbnew.LIB_ID(library, name))
    return footprint


def _outline(board: Any) -> None:
    """Draw the board outline: four sides and four quarter-circle corners."""
    w, h, r = WIDTH, HEIGHT, CORNER
    kb.add_line(board, r, 0, w - r, 0)
    kb.add_line(board, w, r, w, h - r)
    kb.add_line(board, w - r, h, r, h)
    kb.add_line(board, 0, h - r, 0, r)
    kb.add_arc(board, w - r, r, w - r, 0, w, r)
    kb.add_arc(board, w - r, h - r, w, h - r, w - r, h)
    kb.add_arc(board, r, h - r, r, h, 0, h - r)
    kb.add_arc(board, r, r, 0, r, r, 0)


def place(directory: Path, symbols: dict[str, str]) -> Path:
    """Make the board with NewBoard: nets, footprints, the outline. Save it."""
    import pcbnew

    pcb = directory / f"{STEM}.kicad_pcb"
    board = pcbnew.NewBoard(str(pcb))
    board.SetCopperLayerCount(2)
    nets = {}
    for name in sorted(set(PAD_NETS.values())):
        nets[name] = pcbnew.NETINFO_ITEM(board, name)
        board.Add(nets[name])
    for ref, (x, y) in PLACEMENT.items():
        footprint = _load_footprint(pcbnew)
        footprint.SetReference(ref)
        footprint.SetValue("10k")
        footprint.SetPath(pcbnew.KIID_PATH("/" + symbols[ref]))
        footprint.SetSheetname("/")
        footprint.SetSheetfile(f"{STEM}.kicad_sch")
        board.Add(footprint)
        footprint.SetPosition(kb.pt(x, y))
        footprint.SetOrientationDegrees(0)
        for pad in footprint.Pads():
            pad.SetNet(nets[PAD_NETS[(ref, pad.GetNumber())]])
    _outline(board)
    pcbnew.SaveBoard(str(pcb), board)
    return pcb


def route(pcb: Path) -> None:
    """Reload the board, add copper, rule areas and text with the helpers, and fill."""
    import pcbnew

    board = pcbnew.LoadBoard(str(pcb))
    a, b = kb.ppos(board, "R1", 2), kb.ppos(board, "R2", 1)
    kb.track(board, [a, b], 0.25, "NET_B")
    g = kb.ppos(board, "R2", 2)
    kb.track(board, [g, GND_VIA], 0.25, "GND")
    kb.via(board, *GND_VIA, "GND")
    kb.zone(board, "GND", pcbnew.B_Cu, kb.rect(0.3, 0.3, WIDTH - 0.3, HEIGHT - 0.3))
    kb.keepout(board, *KEEPOUT_RECT, pours=False)
    kb.keepout(board, 0, 0, 0, 0, vias=True, pts=KEEPOUT_TRIANGLE)
    kb.add_text(board, "TINY", 15, 16, size=1.0)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(pcb), board)


def build(directory: Path) -> TinyBoard:
    """Write the schematic and the board into ``directory`` and return the files."""
    directory.mkdir(parents=True, exist_ok=True)
    sch, symbols = write_schematic(directory)
    pcb = place(directory, symbols)
    route(pcb)
    return TinyBoard(directory, pcb, sch, symbols)


def coordinates_in(pcb: Path, head: str) -> list[tuple[float, float]]:
    """Return the (x, y) of each ``head`` item: its ``(start x y)`` or ``(at x y)``.

    Read straight from the saved text with the s-expression parser, so the numbers are
    the ones in the file, offset and all: ``head`` is "segment", "via" or "gr_line".
    """
    tree = parse(pcb.read_text(encoding="utf-8"))
    out: list[tuple[float, float]] = []
    for item in findall(tree, head):
        point = find(item, "start") or find(item, "at")
        assert point is not None, head
        out.append((float(point[1]), float(point[2])))
    return out
