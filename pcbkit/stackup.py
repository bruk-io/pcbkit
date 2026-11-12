"""The board's copper layers as a pure model: order, roles, thicknesses and heights.

Built from a validated ``[stackup]`` table (``project.StackupConfig``), with no pcbnew.
Placement, the copper solver, coupling and the checks read this one model instead of
each working the layers out again.

Layers are named ``top``, ``in1``, ``in2`` and ``bottom`` (``in1`` is the first inner
layer below the top). ``kicad_name`` gives KiCad's name for each: ``F.Cu``, ``In1.Cu``,
``In2.Cu``, ``B.Cu``. Every ``z`` and thickness is in millimetres.

The four-layer stack is symmetric: top copper, prepreg, ``in1``, core, ``in2``, prepreg,
bottom copper. The core is what is left of ``thickness_mm``, so copper plus dielectric
adds up to the board thickness exactly; solder masks are outside the sum. The two-layer
model is the same idea with one dielectric (the placement's two-layer stackup text
keeps its own short build-up and does not read this model).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcbkit.project import StackupConfig

# The thinnest core a four-layer stack may have, in millimetres.
MIN_CORE_MM = 0.1

# Decimals kept in a worked-out thickness: a micrometre is finer than any fab quotes.
_DECIMALS = 6


@dataclass(frozen=True)
class Layer:
    """One copper layer: where it sits, what it is for, and how thick it is."""

    name: str  # "top", "in1", "in2" or "bottom"
    kicad_name: str  # "F.Cu", "In1.Cu", "In2.Cu" or "B.Cu"
    role: str  # "signal", "plane" or "split"
    net: str | None  # the net of a "plane" layer, else None
    thickness_mm: float
    z_mm: float  # depth of the layer's centre below the top face of the top copper

    @property
    def kicad_type(self) -> str:
        """Return KiCad's layer type: ``power`` for a plane or split, else signal."""
        return "signal" if self.role == "signal" else "power"


@dataclass(frozen=True)
class Stackup:
    """The copper layers top to bottom, and the dielectric between each pair."""

    layers: tuple[Layer, ...]
    gaps_mm: tuple[float, ...]  # gaps_mm[i] lies between layers[i] and layers[i + 1]
    thickness_mm: float

    def layer(self, name: str) -> Layer:
        """Return the layer called ``name``; raise ValueError for an unknown one."""
        for layer in self.layers:
            if layer.name == name:
                return layer
        known = ", ".join(layer.name for layer in self.layers)
        raise ValueError(f"no layer {name!r} on this board (it has {known})")

    def nearest_copper(self, name: str) -> str:
        """Return the name of the nearest other copper layer, whatever its role.

        This is geometry only. Which layer is a trace's reference, and so its coupling
        height, depends on layer roles and is worked out where it is used.
        """
        return self._nearest(name)[0]

    def gap_to_nearest_copper(self, name: str) -> float:
        """Return the dielectric thickness between a layer and its nearest copper."""
        return self._nearest(name)[1]

    def span_mm(self, first: str, second: str) -> float:
        """Return the length from the outer face of one layer to that of the other.

        That is the barrel a via between the two layers passes through: top to bottom
        is the board thickness. A layer to itself is its own thickness.
        """
        a, b = self.layer(first), self.layer(second)
        upper, lower = sorted((a, b), key=lambda layer: layer.z_mm)
        return round(
            lower.z_mm + lower.thickness_mm / 2 - (upper.z_mm - upper.thickness_mm / 2),
            _DECIMALS,
        )

    def _nearest(self, name: str) -> tuple[str, float]:
        """Return the closest other layer and the dielectric between the two."""
        index = self.layers.index(self.layer(name))
        options = []
        if index > 0:
            options.append((self.gaps_mm[index - 1], index - 1))
        if index < len(self.layers) - 1:
            options.append((self.gaps_mm[index], index + 1))
        gap, other = min(options)  # on a tie the layer nearer the top wins
        return self.layers[other].name, gap


def core_mm(config: StackupConfig) -> float:
    """Return the core thickness: what is left after copper and prepreg.

    Four layers: ``thickness_mm`` less two outer coppers, two inner coppers and two
    prepregs. Two layers: ``thickness_mm`` less the two coppers.
    """
    if config.layers == 2:
        taken = 2 * config.copper_mm
    else:
        taken = 2 * (config.copper_mm + config.inner_copper_mm + config.prepreg_mm)
    return round(config.thickness_mm - taken, _DECIMALS)


def build_stackup(config: StackupConfig) -> Stackup:
    """Return the layer model for a validated ``[stackup]`` table.

    Raise ValueError if ``layers`` is not 2 or 4, if ``inner`` does not hold
    ``layers - 2`` entries, or if the core would be under ``MIN_CORE_MM`` on a
    four-layer board. A core of exactly ``MIN_CORE_MM`` is accepted.
    """
    if config.layers not in (2, 4):
        raise ValueError(f"a board has 2 or 4 layers, got {config.layers!r}")
    if len(config.inner) != config.layers - 2:
        raise ValueError(
            f"a {config.layers}-layer board needs {config.layers - 2} inner entries, "
            f"got {len(config.inner)}"
        )
    core = core_mm(config)
    if config.layers == 2:
        specs = [("top", "F.Cu", "signal", None, config.copper_mm)]
        specs.append(("bottom", "B.Cu", "signal", None, config.copper_mm))
        gaps = [core]
    else:
        if core < MIN_CORE_MM:
            raise ValueError(
                f"the core is {core} mm, under the {MIN_CORE_MM} mm minimum"
            )
        specs = [("top", "F.Cu", "signal", None, config.copper_mm)]
        for number, inner in enumerate(config.inner, start=1):
            specs.append(
                (
                    f"in{number}",
                    f"In{number}.Cu",
                    inner.role,
                    inner.net,
                    config.inner_copper_mm,
                )
            )
        specs.append(("bottom", "B.Cu", "signal", None, config.copper_mm))
        gaps = [config.prepreg_mm, core, config.prepreg_mm]
    layers = []
    top_face = 0.0  # depth of the top face of the layer being placed
    for index, (name, kicad_name, role, net, thickness) in enumerate(specs):
        z = round(top_face + thickness / 2, _DECIMALS)
        layers.append(Layer(name, kicad_name, role, net, thickness, z))
        top_face += thickness + (gaps[index] if index < len(gaps) else 0.0)
    return Stackup(tuple(layers), tuple(gaps), config.thickness_mm)
