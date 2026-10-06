"""The design DSL: a board's parts and nets, written as plain function calls.

A board's ``design.py`` imports ``part``, ``R``, ``C``, ``LED`` and ``FP`` from here and
makes one call per part, naming the net on each pin::

    from pcbkit.design import FP, LED, R, part

    B = "Indicator"
    part("J1", "Connector_Generic:Conn_01x02", "Supply", FP["HDR2"],
         {"1": "+3V3", "2": "GND"}, block=B)
    R("R1", "330", "+3V3", "LED_A", B)
    LED("D1", "Green", "LED_A", "GND", B, "GRN-0603", "Acme")

Every call appends to a registry that lives in this module, so the registry has to be
reset between boards. ``load_design`` does that: it clears the registry and ``FP``, runs
the project's design.py, copies what it defined into a ``Design`` and clears the
registry again. Two boards loaded one after the other, or one board loaded twice, never
see each other's parts.

Besides its parts, a design.py may define these module-level names, all optional:

- ``BLOCK_ORDER``: block names in the order they are drawn on the sheet. Default: the
  order in which the parts first mention each block.
- ``NOTES``: lines of text printed under the blocks. The first is the heading.
- ``BLOCK_WIDTHS``: ``{block: width in mm}`` for blocks that need more or less than the
  default width.
- ``BLOCK_TITLES``: ``{block: heading}`` for a block headed other than by its name.
- ``COMPANY`` and ``COMMENT``: title-block text, left out of the sheet when empty.

Any other upper-case data it defines (``PIN_TABLE = {...}``) is kept in
``Design.constants``. The board's stem, title and revision are not defined here: they
come from pcbkit.toml.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, TypedDict

import click

from pcbkit import project

# The footprints a board can name by short key. Generic ones only: stock KiCad library
# names for the common resistor, capacitor, LED and JST sizes. A board adds its own in
# its design.py (``FP["HDR3"] = "myboard:Header_1x03_P2.54mm"``).
DEFAULT_FP = {
    "R0603": "Resistor_SMD:R_0603_1608Metric",
    "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "C0805": "Capacitor_SMD:C_0805_2012Metric",
    "C1206": "Capacitor_SMD:C_1206_3216Metric",
    "LED0603": "LED_SMD:LED_0603_1608Metric",
    "XH2": "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical",
    "XH3": "Connector_JST:JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical",
    "XH4": "Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical",
    "XH5": "Connector_JST:JST_XH_B5B-XH-A_1x05_P2.50mm_Vertical",
    "XH6": "Connector_JST:JST_XH_B6B-XH-A_1x06_P2.50mm_Vertical",
    "XH10": "Connector_JST:JST_XH_B10B-XH-A_1x10_P2.50mm_Vertical",
    "PH3": "Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical",
    "ZH4": "Connector_JST:JST_ZH_B4B-ZR_1x04_P1.50mm_Vertical",
    "QWIIC": "Connector_JST:JST_SH_BM04B-SRSS-TB_1x04-1MP_P1.00mm_Vertical",
    "HDR2": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "HDR8": "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
    "WIRE2": "Connector_Wire:SolderWire-2sqmm_1x02_P7.8mm_D2mm_OD3.9mm",
}

# Names load_design reads itself; they are not kept in Design.constants.
_READ_HERE = frozenset(
    {"BLOCK_ORDER", "NOTES", "BLOCK_WIDTHS", "BLOCK_TITLES", "COMPANY", "COMMENT"}
)


class DesignError(click.ClickException):
    """Report a mistake in a board's design.py."""


class Part(TypedDict):
    """One part, as ``part`` records it. ``pins`` maps a pin number to a net name."""

    ref: str
    sym: str
    value: str
    fp: str
    pins: dict[str, str | None]
    mfr: str
    mpn: str
    desc: str
    block: str
    dnp: bool
    bom: bool


@dataclass(frozen=True)
class Design:
    """What a board's design.py defined, as plain data.

    ``parts`` are in the order they were declared. A pin whose net is ``None`` is left
    unconnected on purpose and gets a no-connect flag. ``constants`` holds the other
    upper-case data the file defined.
    """

    parts: tuple[Part, ...]
    block_order: tuple[str, ...]
    notes: tuple[str, ...] = ()
    block_widths: Mapping[str, float] = field(default_factory=dict)
    block_titles: Mapping[str, str] = field(default_factory=dict)
    company: str = ""
    comment: str = ""
    constants: Mapping[str, Any] = field(default_factory=dict)


# The live state a design.py writes to while it runs. Boards extend FP in place
# (``from pcbkit.design import FP`` binds this very dict), so reset() empties and
# refills both and never rebinds them.
FP: dict[str, str] = dict(DEFAULT_FP)
PARTS: list[Part] = []


def reset() -> None:
    """Empty the part registry and put FP back to the generic footprints."""
    PARTS.clear()
    FP.clear()
    FP.update(DEFAULT_FP)


def part(
    ref: str,
    sym: str,
    value: str,
    fp: str,
    pins: Mapping[str, str | None],
    mfr: str = "",
    mpn: str = "",
    desc: str = "",
    block: str = "",
    dnp: bool = False,
    bom: bool = True,
) -> None:
    """Add a part: reference, symbol (``Lib:Name``), value, footprint and pin nets."""
    PARTS.append(
        Part(
            ref=ref,
            sym=sym,
            value=value,
            fp=fp,
            pins=pins,
            mfr=mfr,
            mpn=mpn,
            desc=desc,
            block=block,
            dnp=dnp,
            bom=bom,
        )
    )


def R(
    ref: str,
    value: str,
    a: str,
    b: str,
    block: str,
    mpn: str = "",
    mfr: str = "",
    fp: str | None = None,
    desc: str = "",
) -> None:
    """Add a 0603 1 % resistor between nets ``a`` and ``b``."""
    part(
        ref,
        "Device:R",
        value,
        fp or FP["R0603"],
        {"1": a, "2": b},
        mfr=mfr,
        mpn=mpn or f"0603 {value} 1%",
        desc=desc or f"Resistor {value} 0603 1%",
        block=block,
    )


def C(
    ref: str,
    value: str,
    a: str,
    b: str,
    block: str,
    size: str = "C0603",
    mpn: str = "",
    mfr: str = "",
    desc: str = "",
) -> None:
    """Add a ceramic capacitor (footprint: the FP entry ``size``) between two nets."""
    part(
        ref,
        "Device:C",
        value,
        FP[size],
        {"1": a, "2": b},
        mfr=mfr,
        mpn=mpn,
        desc=desc or f"Ceramic capacitor {value} {size[1:]}",
        block=block,
    )


def LED(
    ref: str,
    color: str,
    anode: str,
    cathode: str,
    block: str,
    mpn: str,
    mfr: str,
) -> None:
    """Add a 0603 LED of the given colour."""
    part(
        ref,
        "Device:LED",
        color,
        FP["LED0603"],
        {"2": anode, "1": cathode},
        mfr=mfr,
        mpn=mpn,
        desc=f"LED {color} 0603",
        block=block,
    )


# --- loading a design.py ----------------------------------------------------------


def _text_list(module: ModuleType, name: str) -> tuple[str, ...] | None:
    """Return the module's list of strings called ``name``, or None if it has none."""
    if not hasattr(module, name):
        return None
    value = getattr(module, name)
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        raise DesignError(f"{name} in design.py should be a list of strings")
    return tuple(value)


def _is_width(value: object) -> bool:
    """Return True for a number above zero (a boolean is not one)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value > 0


def _is_text(value: object) -> bool:
    """Return True for a string."""
    return isinstance(value, str)


def _by_block(
    module: ModuleType,
    name: str,
    blocks: tuple[str, ...],
    valid: Callable[[object], bool],
    kind: str,
) -> dict[str, Any]:
    """Return the module's ``{block: value}`` dict called ``name``; check every entry.

    Each key must be one of ``blocks`` (a misspelt name would otherwise quietly leave
    the default in place) and each value must pass ``valid``; ``kind`` words the error.
    """
    value = getattr(module, name, {})
    if not isinstance(value, dict):
        raise DesignError(
            f"{name} in design.py should be a dict of block name to {kind}"
        )
    for block, item in value.items():
        if block not in blocks:
            known = ", ".join(repr(b) for b in blocks)
            raise DesignError(
                f"{name} in design.py names the block {block!r}, which has no parts "
                f"(blocks: {known})"
            )
        if not valid(item):
            raise DesignError(
                f"{name}[{block!r}] in design.py should be {kind}, got {item!r}"
            )
    return dict(value)


def _plain_text(module: ModuleType, name: str) -> str:
    """Return the module's string called ``name``, or "" if it has none."""
    value = getattr(module, name, "")
    if not isinstance(value, str):
        raise DesignError(f"{name} in design.py should be a string")
    return value


def _constants(module: ModuleType) -> dict[str, Any]:
    """Return the upper-case data a module defines, except what pcbkit reads itself."""
    found: dict[str, Any] = {}
    for name, value in vars(module).items():
        if name.startswith("_") or not name.isupper() or name in _READ_HERE:
            continue
        if value is FP or value is PARTS or callable(value):
            continue  # the DSL's own names, imported into the file
        if isinstance(value, ModuleType):
            continue
        found[name] = value
    return found


def _check_parts(parts: list[Part], path: Path) -> None:
    """Raise a DesignError if there are no parts or a reference is used twice."""
    if not parts:
        raise DesignError(f"{path} defines no parts: call part(), R(), C() or LED()")
    seen: set[str] = set()
    for item in parts:
        if item["ref"] in seen:
            raise DesignError(f"{path}: reference {item['ref']} is used twice")
        seen.add(item["ref"])


def _block_order(module: ModuleType, parts: list[Part], path: Path) -> tuple[str, ...]:
    """Return BLOCK_ORDER (default: first-use order), checked against the parts."""
    used = tuple(dict.fromkeys(item["block"] for item in parts))
    order = _text_list(module, "BLOCK_ORDER")
    if order is None:
        order = used
    for item in parts:
        if not item["block"]:
            raise DesignError(f"{path}: part {item['ref']} has no block (block=...)")
        if item["block"] not in order:
            raise DesignError(
                f"{path}: part {item['ref']} is in block {item['block']!r}, which is "
                "not in BLOCK_ORDER"
            )
    for block in order:
        if block not in used:
            raise DesignError(
                f"{path}: BLOCK_ORDER lists {block!r}, which has no parts"
            )
    if len(set(order)) != len(order):
        raise DesignError(f"{path}: BLOCK_ORDER lists a block twice")
    return order


def _snapshot(module: ModuleType, path: Path) -> Design:
    """Return a Design built from the registry and the names ``module`` defined."""
    parts = copy.deepcopy(PARTS)
    _check_parts(parts, path)
    order = _block_order(module, parts, path)
    return Design(
        parts=tuple(parts),
        block_order=order,
        notes=_text_list(module, "NOTES") or (),
        block_widths=_by_block(
            module, "BLOCK_WIDTHS", order, _is_width, "a number > 0"
        ),
        block_titles=_by_block(module, "BLOCK_TITLES", order, _is_text, "a string"),
        company=_plain_text(module, "COMPANY"),
        comment=_plain_text(module, "COMMENT"),
        constants=_constants(module),
    )


def _is_under(module: ModuleType, folder: Path) -> bool:
    """Return True if ``module`` was imported from a file inside ``folder``."""
    file = getattr(module, "__file__", None)
    if file is None:
        return False
    try:
        Path(file).resolve().relative_to(folder)
    except ValueError:
        return False
    return True


def load_design(path: Path) -> Design:
    """Run the design.py at ``path`` from a clean slate and return what it defined.

    The registry and ``FP`` are reset before and after, and every project module that
    the file imported is dropped from ``sys.modules`` again, so a block of parts kept
    in a sibling module is declared afresh on the next load. ``sys.modules`` is
    otherwise left as it was found. An error in the file propagates as it is, with the
    traceback through the user's code.
    """
    path = Path(path).resolve()
    name = path.stem
    reset()
    before = set(sys.modules)
    previous = sys.modules.pop(name, None)  # run the file again, not a cached copy
    try:
        module = project.import_project_module(path.parent, name)
        return _snapshot(module, path)
    finally:
        for key in set(sys.modules) - before:
            if _is_under(sys.modules[key], path.parent):
                del sys.modules[key]
        if previous is not None:
            sys.modules[name] = previous
        reset()
