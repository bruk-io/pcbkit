"""A small board project for the routing tests: files, placement and a router session.

``write_project`` writes the modules of a board with six parts (a supply header, an
LED with its resistor, a two-resistor divider and a sense header) on a 40 x 30 mm board,
with a ``routing.py`` whose hooks make every stage of ``pcbkit.route`` do something.
``place`` builds ``kicad/placed.kicad_pcb`` from the netlist that ``pcbkit sch``
writes, the way ``pcbkit build`` will, and ``session_text`` writes a Freerouting session
file for a few wires, so a test can import "routed" copper without running the router.

Importing this module needs nothing; ``place`` and ``pad_file_position`` need pcbnew and
KiCad's stock libraries, so only a test that has done ``pytest.importorskip("pcbnew")``
calls them.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pcbkit.kicad import board as kb
from pcbkit.kicad import env
from pcbkit.kicad.sexp import find, findall, parse
from pcbkit.project import Project, load_project

STEM = "my_board"
WIDTH, HEIGHT = 40.0, 30.0

TOML = f"""\
[board]
stem = "{STEM}"
title = "My Board"
rev = "A"
fab_name = "My_Board_revA"

[stackup]
copper_mm = 0.07

[route]
freerouting_passes = 20
tries = 2
stall_timeout_s = 60

[stitch]
pitch_mm = 5.0
dense = [[22.0, 4.0, 38.0, 12.0, 2.0]]
gap_limit_mm = 3.4
"""

DESIGN = """\
from __future__ import annotations

from pcbkit.design import FP, LED, R, part

BLOCK_ORDER = ["Supply", "Divider"]
NOTES = ["Routing test board: 3V3 in, an LED and a divider."]

B = "Supply"
part(
    "J1",
    "Connector_Generic:Conn_01x02",
    "Supply",
    FP["HDR2"],
    {"1": "+3V3", "2": "GND"},
    "Acme",
    "HDR-2",
    "Supply header",
    B,
)
R("R1", "330", "+3V3", "LED_A", B)
LED("D1", "Green", "LED_A", "GND", B, "GRN-0603", "Acme")

B = "Divider"
R("R2", "10k", "+3V3", "SENSE", B)
R("R3", "10k", "SENSE", "GND", B)
part(
    "J2",
    "Connector_Generic:Conn_01x02",
    "Sense",
    FP["HDR2"],
    {"1": "SENSE", "2": "GND"},
    "Acme",
    "HDR-2",
    "Sense header",
    B,
)
"""

LAYOUT = f"""\
from __future__ import annotations

W, H = {WIDTH}, {HEIGHT}

P = {{
    "J1": (6.0, 8.0, 0),
    "R1": (14.0, 8.0, 0),
    "D1": (22.0, 8.0, 0),
    "R2": (14.0, 20.0, 0),
    "R3": (22.0, 20.0, 0),
    "J2": (32.0, 18.0, 0),
}}
"""

# Hooks that make each stage do something visible. EVENTS records the order they run in.
ROUTING = """\
from __future__ import annotations

import pcbnew

import layout

EVENTS = []

NETCLASSES = {"Power": (0.5, 0.2, 0.8, 0.4, ["/+3V3"])}

solid_pad_refs = {"J1"}


def design_rules(ds):
    EVENTS.append("design_rules")
    ds.m_TrackMinWidth = 150000  # 0.15 mm in nm: the generic rules say 0.2


def prerouted(board, api):
    EVENTS.append("prerouted")
    # a hand-routed power run, J1 pin 1 to R1 pad 1
    a, b = api.ppos(board, "J1", 1), api.ppos(board, "R1", 1)
    api.track(board, [a, (a[0] + 3.0, a[1]), b], 0.5, "+3V3")
    # a stub nothing joins: it leaves R2's supply pad and goes nowhere
    s = api.ppos(board, "R2", 1)
    api.track(board, [s, (s[0] - 3.0, s[1])], 0.25, "+3V3")
    # a via that touches nothing, so it carries copper on neither layer
    api.via(board, 30.0, 26.0, "SENSE", d=0.7, drill=0.3)


def keepouts(board, api):
    EVENTS.append("keepouts")
    api.keepout(board, 30.0, 4.0, 34.0, 6.0, vias=False, pours=False)


def gnd_links(board, api):
    EVENTS.append("gnd_links")


def zones(board, api):
    EVENTS.append("zones")
    outline = api.rect(0.3, 0.3, layout.W - 0.3, layout.H - 0.3)
    api.zone(board, "GND", pcbnew.B_Cu, outline)
    api.zone(board, "GND", pcbnew.F_Cu, outline)
"""


# The stackup a placed board is saved with: pcbnew's Python cannot write one, so the
# placement stage inserts this block into the saved file as text.
STACKUP = """\
\t(stackup
\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
\t\t(layer "F.Cu" (type "copper") (thickness {copper}))
\t\t(layer "dielectric 1" (type "core") (thickness 1.44) (material "FR4"))
\t\t(layer "B.Cu" (type "copper") (thickness {copper}))
\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t(copper_finish "HAL lead-free")
\t\t(dielectric_constraints no)
\t)
"""


def add_stackup(path: Path, copper_mm: float = 0.035) -> None:
    """Insert a 1.6 mm two-layer stackup into the saved board, after ``(setup``."""
    text = path.read_text(encoding="utf-8")
    start = text.index("(setup")
    end = text.index("\n", start) + 1
    block = STACKUP.format(copper=f"{copper_mm:g}")
    path.write_text(text[:end] + block + text[end:], encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    """Write dedented ``text`` to ``path``, making its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def write_project(
    root: Path,
    toml: str = TOML,
    routing: str = ROUTING,
    layout: str = LAYOUT,
    design: str = DESIGN,
) -> Project:
    """Write the project's modules into ``root`` and return the loaded project."""
    write_text(root / "pcbkit.toml", toml)
    write_text(root / "design.py", design)
    write_text(root / "layout.py", layout)
    write_text(root / "routing.py", routing)
    return load_project(root)


def layout_positions(proj: Project) -> dict[str, tuple[float, float, float]]:
    """Return each part's (x, y, rotation) from the project's layout.py."""
    from pcbkit.project import import_project_module

    return dict(import_project_module(proj.root, "layout").P)


def place(
    proj: Project, positions: dict[str, tuple[float, float, float]] | None = None
) -> Path:
    """Build ``kicad/placed.kicad_pcb`` from the netlist and ``positions``.

    Needs ``kicad/<stem>.net`` (from ``pcbkit sch``). Every footprint is loaded from
    KiCad's stock libraries, put at its layout position, tied to its schematic symbol
    and given the net of each pad. The outline is the 40 x 30 mm rectangle.
    """
    import pcbnew

    positions = positions or layout_positions(proj)
    netlist = parse((proj.kicad_dir / f"{STEM}.net").read_text(encoding="utf-8"))
    comps = findall(find(netlist, "components"), "comp")
    pad_nets: dict[tuple[str, str], str] = {}
    for net in findall(find(netlist, "nets"), "net"):
        name = str(find(net, "name")[1])
        for node in findall(net, "node"):
            pad_nets[(str(find(node, "ref")[1]), str(find(node, "pin")[1]))] = name

    pcb = proj.kicad_dir / "placed.kicad_pcb"
    board = pcbnew.NewBoard(str(pcb))
    board.SetCopperLayerCount(2)
    nets: dict[str, Any] = {}
    for name in sorted(set(pad_nets.values())):
        nets[name] = pcbnew.NETINFO_ITEM(board, name)
        board.Add(nets[name])
    for comp in comps:
        ref = str(find(comp, "ref")[1])
        library, name = str(find(comp, "footprint")[1]).split(":")
        footprint = pcbnew.FootprintLoad(
            str(env.footprints_dir() / f"{library}.pretty"), name
        )
        footprint.SetFPID(pcbnew.LIB_ID(library, name))
        footprint.SetReference(ref)
        footprint.SetValue(str(find(comp, "value")[1]))
        footprint.SetPath(pcbnew.KIID_PATH("/" + str(find(comp, "tstamps")[1])))
        footprint.SetSheetname("/")
        footprint.SetSheetfile(f"{STEM}.kicad_sch")
        board.Add(footprint)
        x, y, rotation = positions[ref]
        footprint.SetPosition(kb.pt(x, y))
        footprint.SetOrientationDegrees(rotation)
        for pad in footprint.Pads():
            net = pad_nets.get((ref, pad.GetNumber()))
            if net is not None:
                pad.SetNet(nets[net])
    kb.add_line(board, 0, 0, WIDTH, 0)
    kb.add_line(board, WIDTH, 0, WIDTH, HEIGHT)
    kb.add_line(board, WIDTH, HEIGHT, 0, HEIGHT)
    kb.add_line(board, 0, HEIGHT, 0, 0)
    pcbnew.SaveBoard(str(pcb), board)
    add_stackup(pcb)
    return pcb


def pad_file_position(pcb: Path, ref: str, num: int) -> tuple[float, float]:
    """Return a pad's centre in KiCad's file coordinates, in millimetres."""
    import pcbnew

    board = pcbnew.LoadBoard(str(pcb))
    pos = kb.pad(board, ref, num).GetPosition()
    return pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)


Wire = tuple[str, float, Sequence[tuple[float, float]]]


def session_text(
    wires: dict[str, Sequence[Wire]], vias: dict[str, Sequence[tuple[float, float]]]
) -> str:
    """Return a Freerouting session file with the given wires and vias.

    ``wires`` maps a net name (with its "/") to (layer, width in mm, points) wires;
    ``vias`` maps a net to via positions. Points are in KiCad's file millimetres. The
    session has no placement section: pcbnew does not need one.
    """

    def units(x: float, y: float) -> str:
        """Return a point in the session's 0.1 um units, with y pointing up."""
        return f"{round(x * 10000)} {round(-y * 10000)}"

    nets = []
    for net in sorted(set(wires) | set(vias)):
        body = []
        for layer, width, points in wires.get(net, ()):
            path = "\n            ".join(units(x, y) for x, y in points)
            body.append(
                f"        (wire\n          (path {layer} {round(width * 10000)}\n"
                f"            {path}\n          )\n        )"
            )
        for x, y in vias.get(net, ()):
            body.append(f'        (via "Via[0-1]_800:400_um" {units(x, y)}\n        )')
        nets.append(f"      (net {net}\n" + "\n".join(body) + "\n      )")
    network = "\n".join(nets)
    return f"""\
(session {STEM}
  (base_design {STEM})
  (placement
    (resolution um 10)
  )
  (was_is
  )
  (routes
    (resolution um 10)
    (parser
      (host_cad "KiCad's Pcbnew")
      (host_version 10.0.6)
    )
    (library_out
      (padstack "Via[0-1]_800:400_um"
        (shape
          (circle F.Cu 8000 0 0)
        )
        (shape
          (circle B.Cu 8000 0 0)
        )
        (attach off)
      )
    )
    (network_out
{network}
    )
  )
)
"""
