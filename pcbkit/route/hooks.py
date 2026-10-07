"""Load the project's ``routing.py`` and check what it defines.

The routing stages call the hooks of the board project's ``routing.py`` (the table is in
docs/project-interface.md). Only ``prerouted`` is required. ``load_hooks`` imports the
module once, checks the shape of each name it defines and returns them as plain data,
so the stages never test for a missing hook themselves: an absent one is ``None`` (or
empty).

An error raised inside a hook is the project's own and reaches the user as an ordinary
traceback through their file; only a hook that is missing or the wrong shape is reported
here, as a ``ProjectError`` that names the file and the hook.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pcbkit.project import Project, ProjectError, import_project_module

# Every name routing.py may define: the same list as the table in the docs.
HOOK_NAMES = (
    "NETCLASSES",
    "design_rules",
    "prerouted",
    "keepouts",
    "gnd_links",
    "zones",
    "solid_pad_refs",
)

Hook = Callable[..., None]


@dataclass(frozen=True)
class Hooks:
    """What a project's routing.py defines.

    ``netclasses`` maps a class name to (track, clearance, via_d, via_drill, patterns),
    in millimetres, and is empty when the project has none. A hook it does not define
    is None, and ``solid_pad_refs`` is then the empty set.
    """

    netclasses: dict[str, tuple[float, float, float, float, tuple[str, ...]]]
    design_rules: Hook | None
    prerouted: Hook
    keepouts: Hook | None
    gnd_links: Hook | None
    zones: Hook | None
    solid_pad_refs: frozenset[str]


def _number(value: object) -> bool:
    """Return True for a finite int or float above zero (a bool is not a number)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def _netclasses(source: str, raw: Any) -> dict[str, Any]:
    """Return NETCLASSES checked and normalised, or raise a ProjectError."""
    if not isinstance(raw, dict):
        raise ProjectError(f"{source}: NETCLASSES should be a dict of net classes")
    shape = "(track, clearance, via_d, via_drill, patterns)"
    checked: dict[str, Any] = {}
    for name, entry in raw.items():
        where = f"{source}: NETCLASSES[{name!r}]"
        if not isinstance(name, str) or not name:
            raise ProjectError(f"{source}: a NETCLASSES key should be a class name")
        if not isinstance(entry, (tuple, list)) or len(entry) != 5:
            raise ProjectError(f"{where} should be {shape}, got {entry!r}")
        *sizes, patterns = entry
        if not all(_number(size) for size in sizes):
            raise ProjectError(f"{where} needs four numbers above 0 in mm: {entry!r}")
        listed = isinstance(patterns, (list, tuple))
        if not listed or not all(isinstance(p, str) and p for p in patterns):
            raise ProjectError(f"{where} needs a list of net name patterns last")
        checked[name] = (*(float(size) for size in sizes), tuple(patterns))
    return checked


def _hook(source: str, module: Any, name: str) -> Hook | None:
    """Return the callable ``name`` of the module, or None if it is absent."""
    found = getattr(module, name, None)
    if found is not None and not callable(found):
        raise ProjectError(f"{source}: {name} should be a function, got {found!r}")
    return found


def _required_hook(source: str, module: Any, name: str) -> Hook:
    """Return the callable ``name`` of the module; raise if it is absent."""
    found = _hook(source, module, name)
    if found is None:
        raise ProjectError(
            f"{source}: routing.py must define {name}(board, api) "
            "(it may do nothing); see docs/project-interface.md"
        )
    return found


def _refs(source: str, raw: Any) -> frozenset[str]:
    """Return solid_pad_refs as a frozenset of references, or raise."""
    if isinstance(raw, str) or not isinstance(raw, (set, frozenset, list, tuple)):
        raise ProjectError(
            f"{source}: solid_pad_refs should be a set of references, got {raw!r}"
        )
    if not all(isinstance(ref, str) and ref for ref in raw):
        raise ProjectError(f"{source}: solid_pad_refs should hold only references")
    return frozenset(raw)


def load_hooks(proj: Project) -> Hooks:
    """Import the project's routing.py and return its hooks, checked."""
    module = import_project_module(proj.root, "routing")
    source = str(proj.root / "routing.py")
    return Hooks(
        netclasses=_netclasses(source, getattr(module, "NETCLASSES", {})),
        design_rules=_hook(source, module, "design_rules"),
        prerouted=_required_hook(source, module, "prerouted"),
        keepouts=_hook(source, module, "keepouts"),
        gnd_links=_hook(source, module, "gnd_links"),
        zones=_hook(source, module, "zones"),
        solid_pad_refs=_refs(source, getattr(module, "solid_pad_refs", frozenset())),
    )


def board_size(proj: Project) -> tuple[float, float]:
    """Return the board's width and height in mm: ``W`` and ``H`` of layout.py."""
    module = import_project_module(proj.root, "layout")
    source = str(proj.root / "layout.py")
    size = (getattr(module, "W", None), getattr(module, "H", None))
    if not all(_number(side) for side in size):
        raise ProjectError(
            f"{source}: layout.py must define W and H, the board's width and height "
            f"in mm (got W={size[0]!r}, H={size[1]!r})"
        )
    return float(size[0]), float(size[1])
