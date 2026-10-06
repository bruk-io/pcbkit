"""Place the board: every footprint of the netlist, the outline, the holes, the stackup.

``place_board`` is what ``pcbkit build`` runs after the schematic. It reads the
netlist KiCad exported from the schematic (``kicad/<stem>.net``) and the project's
``layout.py``, and writes ``kicad/<stem>.kicad_pcb`` with an identical copy at
``kicad/placed.kicad_pcb``:

* each footprint is loaded from the project's own library (the ``.pretty`` folder
  that ``pcbkit sch`` wrote under kicad/) or from KiCad's stock libraries, put at its
  position in ``layout.P``, and given the nets of its pads. A part that has no entry
  in ``P`` is parked below the board, and reported in ``PlaceResult.missing``;
* a part called ``H<n>`` is a mounting hole and goes to ``layout.HOLES[n - 1]``;
* the outline is a rectangle ``layout.W`` by ``layout.H`` with corners of radius
  ``layout.CORNER_R`` (square when it is missing), unless ``layout.py`` has an
  ``outline(board, api)`` function, which draws it instead;
* the stackup is written into the saved file, with the copper thickness of
  ``[stackup] copper_mm``.

Coordinates in ``layout.py`` are millimetres from the board's top-left corner, y down;
KiCad's own sit (50, 50) mm further on (``pcbkit.kicad.board``). The board is built the
way the board scripts always built it, part by part in reference order, so the same
design gives the same file.
"""

from __future__ import annotations

import numbers
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import click

from pcbkit import libs, project
from pcbkit.kicad import board as kb
from pcbkit.kicad import env
from pcbkit.kicad.sexp import find, findall, parse
from pcbkit.project import Project, ProjectError

# Reference text on a placed board: small and tidy, 0.8 mm with a 0.15 mm stroke.
REF_TEXT_MM = 0.8
REF_THICKNESS_MM = 0.15

# A part with no position in layout.P is parked below the board: x = 5, and y a further
# 6 mm down for each such part, starting 10 mm under the board's bottom edge.
PARK_X = 5.0
PARK_Y_OFFSET = 10.0
PARK_Y_STEP = 6.0

# "H1", "H2", ...: the mounting holes. A reference such as "HS1" is an ordinary part.
HOLE_REF = re.compile(r"H(\d+)")

# The core of the stackup is the finished thickness less this much. The stack was first
# sized for 2 x 0.070 mm copper and 2 x 0.010 mm solder mask around a 1.44 mm core of
# a 1.6 mm board; the core stayed when the copper changed to 1 oz (0.035 mm), which
# leaves the layers 0.07 mm short of the board's thickness. Kept as it was written.
CORE_LESS_MM = 0.16

# What goes after the "(setup" line of the saved board: layer by layer, with the copper
# and core thicknesses filled in. KiCad's Python cannot reach the stackup, so it is
# text.
_STACKUP = (
    "\t(stackup\n"
    '\t\t(layer "F.SilkS" (type "Top Silk Screen"))\n'
    '\t\t(layer "F.Paste" (type "Top Solder Paste"))\n'
    '\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "F.Cu" (type "copper") (thickness {copper}))\n'
    '\t\t(layer "dielectric 1" (type "core") (thickness {core}) (material "FR4") '
    "(epsilon_r 4.5) (loss_tangent 0.02))\n"
    '\t\t(layer "B.Cu" (type "copper") (thickness {copper}))\n'
    '\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "B.Paste" (type "Bottom Solder Paste"))\n'
    '\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))\n'
    '\t\t(copper_finish "HAL lead-free")\n'
    "\t\t(dielectric_constraints no)\n"
    "\t)\n"
)


class Component(NamedTuple):
    """A part of the netlist: its value, footprint id ("Lib:Name") and schematic id."""

    value: str
    footprint: str
    tstamp: str


@dataclass(frozen=True)
class PlaceResult:
    """What ``place_board`` made.

    ``placed`` is the number of footprints put on the board. ``missing`` lists, in
    reference order, the parts that had no position in ``layout.P``: they are on the
    board but parked below it.
    """

    pcb: Path
    placed: int
    missing: list[str]


@dataclass(frozen=True)
class Netlist:
    """The parts of a netlist by reference, and the net of each (reference, pin)."""

    components: dict[str, Component]
    pin_nets: dict[tuple[str, str], str]


# --- the netlist --------------------------------------------------------------------


def parse_netlist(text: str, source: str = "netlist") -> Netlist:
    """Read the parts and the nets from the text of a KiCad netlist.

    Raise a ClickException naming ``source`` if the text is not a netlist.
    """
    try:
        tree = parse(text)
    except ValueError as err:
        raise click.ClickException(f"{source} is not a KiCad netlist: {err}") from None
    parts = find(tree, "components") if isinstance(tree, list) else None
    nets = find(tree, "nets") if isinstance(tree, list) else None
    if parts is None or nets is None:
        raise click.ClickException(
            f"{source} is not a KiCad netlist: it has no components or nets section. "
            "Run `pcbkit sch` to make it again."
        )
    components: dict[str, Component] = {}
    for comp in findall(parts, "comp"):
        footprint = find(comp, "footprint")
        tstamp = find(comp, "tstamps")
        components[str(find(comp, "ref")[1])] = Component(
            value=str(find(comp, "value")[1]),
            footprint=str(footprint[1]) if footprint else "",
            tstamp=str(tstamp[1]),
        )
    pin_nets: dict[tuple[str, str], str] = {}
    for net in findall(nets, "net"):
        name = str(find(net, "name")[1])
        for node in findall(net, "node"):
            pin_nets[(str(find(node, "ref")[1]), str(find(node, "pin")[1]))] = name
    return Netlist(components, pin_nets)


# --- what layout.py says ------------------------------------------------------------


def is_number(value: Any) -> bool:
    """Return True for an int or float (numpy scalars too), and not for a bool."""
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _number(layout: ModuleType, name: str, default: float | None = None) -> float:
    """Return layout.<name> as a float; ``default`` if absent and there is one."""
    if not hasattr(layout, name):
        if default is not None:
            return default
        raise ProjectError(f"layout.py: {name} is missing (board size in mm)")
    value = getattr(layout, name)
    if not is_number(value):
        raise ProjectError(f"layout.py: {name} should be a number, got {value!r}")
    return float(value)


def layout_size(layout: ModuleType) -> tuple[float, float]:
    """Return the board's (width, height) in mm from layout.py's ``W`` and ``H``."""
    width, height = _number(layout, "W"), _number(layout, "H")
    if width <= 0 or height <= 0:
        raise ProjectError(
            f"layout.py: W and H should be above 0, got {width}, {height}"
        )
    return width, height


def _position(where: str, value: Any) -> tuple[float, float, float]:
    """Return an (x, y, rotation) entry of layout.py, or raise ProjectError."""
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 3
        or not all(is_number(v) for v in value)
    ):
        raise ProjectError(
            f"layout.py: {where} should be (x, y, rotation in degrees), got {value!r}"
        )
    return (float(value[0]), float(value[1]), float(value[2]))


@dataclass(frozen=True)
class Layout:
    """The names of layout.py that placement reads, checked."""

    width: float
    height: float
    corner: float
    parts: dict[str, tuple[float, float, float]]
    holes: list[tuple[float, float]]
    outline: Any  # layout.outline(board, api), or None


def read_layout(module: ModuleType) -> Layout:
    """Check layout.py and return what placement needs from it.

    ``W``, ``H`` and ``P`` are required. ``CORNER_R`` (default 0), ``HOLES`` (default
    none) and ``outline`` are optional.
    """
    width, height = _number(module, "W"), _number(module, "H")
    if width <= 0 or height <= 0:
        raise ProjectError(
            f"layout.py: W and H should be above 0, got {width}, {height}"
        )
    corner = _number(module, "CORNER_R", 0.0)
    if not 0 <= corner <= min(width, height) / 2:
        raise ProjectError(
            f"layout.py: CORNER_R should be from 0 to half the shorter side "
            f"({min(width, height) / 2:g} mm), got {corner:g}"
        )
    raw = getattr(module, "P", None)
    if not isinstance(raw, dict):
        raise ProjectError("layout.py: P should be a dict of reference to (x, y, rot)")
    parts = {str(ref): _position(f"P[{ref!r}]", value) for ref, value in raw.items()}
    holes = []
    for index, value in enumerate(getattr(module, "HOLES", []), start=1):
        if (
            not isinstance(value, (tuple, list))
            or len(value) != 2
            or not all(is_number(v) for v in value)
        ):
            raise ProjectError(
                f"layout.py: HOLES[{index - 1}] should be (x, y), got {value!r}"
            )
        holes.append((float(value[0]), float(value[1])))
    outline = getattr(module, "outline", None)
    if outline is not None and not callable(outline):
        raise ProjectError("layout.py: outline should be a function (board, api)")
    return Layout(width, height, corner, parts, holes, outline)


def hole_number(ref: str) -> int | None:
    """Return n for a mounting hole called ``H<n>``; None for any other reference."""
    found = HOLE_REF.fullmatch(ref)
    return int(found.group(1)) if found else None


def parking_spot(height: float, already_parked: int) -> tuple[float, float, float]:
    """Return where the next unplaced part goes: (x, y, rotation) below the board.

    ``already_parked`` counts the parts parked so far, this one included.
    """
    return (PARK_X, height + PARK_Y_OFFSET + already_parked * PARK_Y_STEP, 0.0)


# --- the outline and the stackup ----------------------------------------------------


def rounded_outline(board: Any, w: float, h: float, r: float) -> None:
    """Draw a w by h rectangle on Edge.Cuts with corners of radius r (square if 0)."""
    kb.add_line(board, r, 0, w - r, 0)
    kb.add_line(board, w, r, w, h - r)
    kb.add_line(board, w - r, h, r, h)
    kb.add_line(board, 0, h - r, 0, r)
    if r > 0:
        kb.add_arc(board, w - r, r, w - r, 0, w, r)
        kb.add_arc(board, w - r, h - r, w, h - r, w - r, h)
        kb.add_arc(board, r, h - r, r, h, 0, h - r)
        kb.add_arc(board, r, r, 0, r, r, 0)


def stackup_text(copper_mm: float, thickness_mm: float) -> str:
    """Return the stackup block for a board ``thickness_mm`` thick, 1 or 2 oz copper."""
    return _STACKUP.format(
        copper=f"{copper_mm:g}", core=f"{round(thickness_mm - CORE_LESS_MM, 4):g}"
    )


def add_stackup(path: Path, copper_mm: float, thickness_mm: float) -> None:
    """Write the stackup into the saved board at ``path``, after its "(setup" line.

    A board that has one already keeps it, with the copper thickness set to
    ``copper_mm`` by ``pcbkit.kicad.board.set_copper``.
    """
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    if "(stackup" in text:
        try:
            kb.set_copper(str(path), copper_mm)
        except ValueError as err:
            raise click.ClickException(str(err)) from None
        return
    start = text.index("(setup")
    after = text.index("\n", start) + 1
    text = text[:after] + stackup_text(copper_mm, thickness_mm) + text[after:]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


# --- footprints ---------------------------------------------------------------------


def load_footprint(fpid: str, folders: dict[str, Path]) -> Any:
    """Load footprint ``Lib:Name`` from its library folder; raise if it cannot be.

    ``folders`` maps a library nickname to its ``.pretty`` folder: the project's own.
    Any other library is one of KiCad's stock ones.
    """
    import pcbnew

    lib, colon, name = fpid.partition(":")
    if not colon or not lib or not name:
        raise click.ClickException(f"footprint {fpid!r} should be written Library:Name")
    folder = folders.get(lib) or env.footprints_dir() / f"{lib}.pretty"
    try:
        footprint = pcbnew.FootprintLoad(str(folder), name)
    except (OSError, RuntimeError) as err:  # pcbnew raises IO_ERROR as one of these
        raise click.ClickException(
            f"footprint {fpid} cannot be read from {folder}: {err}"
        ) from None
    if footprint is None:
        raise click.ClickException(f"footprint {fpid} not found in {folder}")
    footprint.SetFPID(pcbnew.LIB_ID(lib, name))
    return footprint


def _own_library(proj: Project) -> dict[str, Path]:
    """Return {nickname: folder} of the project's own footprint library."""
    hooks = project.import_optional_project_module(proj.root, "footprints")
    nickname = libs.library_name(hooks, proj.config.board.stem)
    return {nickname: proj.kicad_dir / f"{nickname}.pretty"}


# --- placing ------------------------------------------------------------------------


def place_board(proj: Project) -> PlaceResult:
    """Build ``kicad/<stem>.kicad_pcb`` from the netlist and layout.py.

    Needs pcbnew, and the netlist ``pcbkit sch`` made. The board is also copied to
    ``kicad/placed.kicad_pcb``. Raise a ClickException for anything the project got
    wrong: a missing netlist or layout.py, a footprint that is not in its library, a
    hole with no position.
    """
    import pcbnew

    stem = proj.config.board.stem
    netlist_path = proj.kicad_dir / f"{stem}.net"
    if not netlist_path.is_file():
        raise click.ClickException(
            f"{netlist_path} not found: run `pcbkit sch` (or `pcbkit build`) first"
        )
    netlist = parse_netlist(netlist_path.read_text(encoding="utf-8"), str(netlist_path))
    layout = read_layout(project.import_project_module(proj.root, "layout"))
    folders = _own_library(proj)

    pcb = proj.kicad_dir / f"{stem}.kicad_pcb"
    board = pcbnew.NewBoard(str(pcb))
    board.SetCopperLayerCount(proj.config.stackup.layers)
    board.GetDesignSettings().SetBoardThickness(kb.mm(proj.config.stackup.thickness_mm))
    nets: dict[str, Any] = {}

    def net(name: str) -> Any:
        """Return the board's net called ``name``, making it the first time."""
        if name not in nets:
            nets[name] = pcbnew.NETINFO_ITEM(board, name)
            board.Add(nets[name])
        return nets[name]

    placed = 0
    missing: list[str] = []
    for ref, comp in sorted(netlist.components.items()):
        if not comp.footprint:
            continue
        footprint = load_footprint(comp.footprint, folders)
        footprint.SetReference(ref)
        footprint.SetValue(comp.value)
        footprint.SetPath(pcbnew.KIID_PATH("/" + comp.tstamp))
        footprint.SetSheetname("/")
        footprint.SetSheetfile(stem + ".kicad_sch")
        board.Add(footprint)
        hole = hole_number(ref)
        if hole is not None:
            if not 1 <= hole <= len(layout.holes):
                raise ProjectError(
                    f"layout.py: the board has the mounting hole {ref} but HOLES has "
                    f"{len(layout.holes)} position(s); H<n> is HOLES[n - 1]"
                )
            (x, y), rot = layout.holes[hole - 1], 0.0
        elif ref not in layout.parts:
            missing.append(ref)
            x, y, rot = parking_spot(layout.height, len(missing))
        else:
            x, y, rot = layout.parts[ref]
        footprint.SetPosition(kb.pt(x, y))
        footprint.SetOrientationDegrees(rot)
        for pad in footprint.Pads():
            key = (ref, pad.GetNumber())
            if key in netlist.pin_nets:
                pad.SetNet(net(netlist.pin_nets[key]))
        # small, tidy reference text
        text = footprint.Reference()
        text.SetTextSize(pcbnew.VECTOR2I(kb.mm(REF_TEXT_MM), kb.mm(REF_TEXT_MM)))
        text.SetTextThickness(kb.mm(REF_THICKNESS_MM))
        placed += 1

    if layout.outline is not None:
        layout.outline(board, kb)
    else:
        rounded_outline(board, layout.width, layout.height, layout.corner)
    pcbnew.SaveBoard(str(pcb), board)
    stackup = proj.config.stackup
    add_stackup(pcb, stackup.copper_mm, stackup.thickness_mm)
    shutil.copy(pcb, proj.kicad_dir / "placed.kicad_pcb")
    return PlaceResult(pcb, placed, missing)
