"""A stand-in for pcbnew in unit tests: what pcbkit.kicad.board calls, recorded.

pcbnew only imports under KiCad's own Python, so a unit test installs this module in
``sys.modules["pcbnew"]`` (the ``fake_pcbnew`` fixture) and the helpers under test run
against it. It copies the two behaviours of the real module that matter to the code
under test:

* ``FromMM`` and ``ToMM`` check ``type(x) in [int, float]``, as KiCad 10's do, so a
  float subclass such as ``np.float64`` is refused here exactly as it is there.
* ``VECTOR2I`` takes two ints and nothing else.

Items remember every setter call in ``props`` (method name to the arguments it got), so
a test can read back what a helper did to them. An item refuses a setter it was not
built with: that is how one fake stands for KiCad 10 (``SetDoNotAllowZoneFills``) and
another for KiCad 9 (``SetDoNotAllowCopperPour``).
"""

from __future__ import annotations

import types
from typing import Any, NamedTuple

IU_PER_MM = 1_000_000

F_CU = 0
B_CU = 2
EDGE_CUTS = 25
F_SILKS = 5


class Vec(NamedTuple):
    """The pcbnew VECTOR2I: two ints."""

    x: int
    y: int


def vector2i(x: Any, y: Any) -> Vec:
    """Return a Vec, refusing anything but two ints as pcbnew's constructor does."""
    if type(x) is not int or type(y) is not int:
        raise TypeError("in method 'new_VECTOR2I', argument 2 of type 'int'")
    return Vec(x, y)


def from_mm(mm: Any) -> Any:
    """Convert millimetres to internal units the way pcbnew.FromMM does."""
    if type(mm) in [int, float]:
        return int(float(mm) * IU_PER_MM)
    if type(mm) is Vec:
        return tuple(map(from_mm, mm))
    raise TypeError(
        "FromMM() expects int, float, wxPoint, wxSize, VECTOR2I or VECTOR2L, "
        f"instead got type {type(mm)}"
    )


def to_mm(iu: Any) -> Any:
    """Convert internal units to millimetres the way pcbnew.ToMM does."""
    if type(iu) in [int, float]:
        return float(iu) / IU_PER_MM
    if type(iu) is Vec:
        return tuple(map(to_mm, iu))
    raise TypeError(
        "ToMM() expects int, float, wxPoint, wxSize, VECTOR2I or VECTOR2L, "
        f"instead got type {type(iu)}"
    )


class Outline:
    """The polygon set of a zone: a list of outlines, each a list of points."""

    def __init__(self) -> None:
        """Start with no outline."""
        self.outlines: list[list[Vec]] = []

    def NewOutline(self) -> None:
        """Start a new outline."""
        self.outlines.append([])

    def Append(self, point: Vec) -> None:
        """Add a corner to the latest outline."""
        self.outlines[-1].append(point)


class Lset:
    """A set of layer ids."""

    def __init__(self) -> None:
        """Start with no layers."""
        self.layers: list[int] = []

    def AddLayer(self, layer: int) -> None:
        """Add one layer."""
        self.layers.append(layer)


class Item:
    """A board item that records its setters; subclasses say which setters exist."""

    SETTERS: frozenset[str] = frozenset()

    def __init__(self, board: Any = None) -> None:
        """Remember the board the item was made for, and start with no properties."""
        self.board = board
        self.props: dict[str, tuple[Any, ...]] = {}

    def __getattr__(self, name: str) -> Any:
        """Return a recording setter for a name in SETTERS; anything else is missing."""
        if name in self.SETTERS:

            def record(*args: Any) -> None:
                self.props[name] = args

            return record
        raise AttributeError(name)


class Track(Item):
    """PCB_TRACK."""

    SETTERS = frozenset(
        {"SetStart", "SetEnd", "SetWidth", "SetLayer", "SetNet", "SetLocked"}
    )


class Via(Item):
    """PCB_VIA."""

    SETTERS = frozenset({"SetPosition", "SetWidth", "SetDrill", "SetNet", "SetLocked"})


class Shape(Item):
    """PCB_SHAPE."""

    SETTERS = frozenset(
        {"SetShape", "SetStart", "SetEnd", "SetArcGeometry", "SetLayer", "SetWidth"}
    )


class Text(Item):
    """PCB_TEXT."""

    SETTERS = frozenset(
        {
            "SetText",
            "SetPosition",
            "SetLayer",
            "SetTextSize",
            "SetTextThickness",
            "SetTextAngleDegrees",
            "SetHorizJustify",
        }
    )


class Zone(Item):
    """ZONE as KiCad 10 has it: the pour switch is called SetDoNotAllowZoneFills."""

    SETTERS = frozenset(
        {
            "SetIsRuleArea",
            "SetDoNotAllowTracks",
            "SetDoNotAllowVias",
            "SetDoNotAllowZoneFills",
            "SetDoNotAllowPads",
            "SetDoNotAllowFootprints",
            "SetLayerSet",
            "SetLayer",
            "SetNet",
            "SetAssignedPriority",
            "SetMinThickness",
            "SetLocalClearance",
            "SetPadConnection",
            "SetThermalReliefGap",
            "SetThermalReliefSpokeWidth",
            "SetIslandRemovalMode",
        }
    )

    def __init__(self, board: Any = None) -> None:
        """Start with an empty outline."""
        super().__init__(board)
        self.outline = Outline()

    def Outline(self) -> Outline:
        """Return the zone's polygon set."""
        return self.outline


class Zone9(Zone):
    """ZONE as KiCad 9 has it: the pour switch is called SetDoNotAllowCopperPour."""

    SETTERS = (Zone.SETTERS - {"SetDoNotAllowZoneFills"}) | {"SetDoNotAllowCopperPour"}


class Pad:
    """A pad: a number and a position."""

    def __init__(self, number: str, position: Vec) -> None:
        """Make a pad."""
        self.number = number
        self.position = position

    def GetNumber(self) -> str:
        """Return the pad number."""
        return self.number

    def GetPosition(self) -> Vec:
        """Return the pad centre."""
        return self.position


class Footprint:
    """A footprint: a reference and its pads."""

    def __init__(self, pads: list[Pad]) -> None:
        """Make a footprint holding ``pads``."""
        self.pads = pads

    def Pads(self) -> list[Pad]:
        """Return the pads."""
        return self.pads


class Board:
    """A board that keeps what is added to it and removed from it."""

    def __init__(self) -> None:
        """Start empty, knowing the two copper layers and two nets."""
        self.items: list[Any] = []
        self.removed: list[Any] = []
        self.nets = {"/NET_A": "net-a", "/GND": "net-gnd"}
        self.footprints: dict[str, Footprint] = {}
        self.layer_ids = {"F.Cu": F_CU, "B.Cu": B_CU, "Edge.Cuts": EDGE_CUTS}

    def Add(self, item: Any) -> None:
        """Put an item on the board."""
        self.items.append(item)

    def Remove(self, item: Any) -> None:
        """Take an item off the board."""
        self.items.remove(item)
        self.removed.append(item)

    def FindNet(self, name: str) -> Any:
        """Return the net called ``name``, or None."""
        return self.nets.get(name)

    def FindFootprintByReference(self, ref: str) -> Footprint | None:
        """Return the footprint with this reference, or None."""
        return self.footprints.get(ref)

    def GetLayerID(self, name: str) -> int:
        """Return the id of the layer called ``name``."""
        return self.layer_ids[name]


def make_pcbnew(kicad: int = 10) -> types.ModuleType:
    """Return a fake pcbnew module; ``kicad`` 9 gets the older ZONE setter name."""
    module = types.ModuleType("pcbnew")
    module.F_Cu = F_CU  # type: ignore[attr-defined]
    module.B_Cu = B_CU  # type: ignore[attr-defined]
    module.Edge_Cuts = EDGE_CUTS  # type: ignore[attr-defined]
    module.F_SilkS = F_SILKS  # type: ignore[attr-defined]
    module.SHAPE_T_SEGMENT = "segment"  # type: ignore[attr-defined]
    module.SHAPE_T_ARC = "arc"  # type: ignore[attr-defined]
    module.ZONE_CONNECTION_FULL = "full"  # type: ignore[attr-defined]
    module.ZONE_CONNECTION_THERMAL = "thermal"  # type: ignore[attr-defined]
    module.ISLAND_REMOVAL_MODE_ALWAYS = "always"  # type: ignore[attr-defined]
    module.GR_TEXT_H_ALIGN_LEFT = "left"  # type: ignore[attr-defined]
    module.GR_TEXT_H_ALIGN_RIGHT = "right"  # type: ignore[attr-defined]
    module.FromMM = from_mm  # type: ignore[attr-defined]
    module.ToMM = to_mm  # type: ignore[attr-defined]
    module.VECTOR2I = vector2i  # type: ignore[attr-defined]
    module.LSET = Lset  # type: ignore[attr-defined]
    module.PCB_TRACK = Track  # type: ignore[attr-defined]
    module.PCB_VIA = Via  # type: ignore[attr-defined]
    module.PCB_SHAPE = Shape  # type: ignore[attr-defined]
    module.PCB_TEXT = Text  # type: ignore[attr-defined]
    module.ZONE = Zone if kicad >= 10 else Zone9  # type: ignore[attr-defined]
    return module
