"""Load the KiCad netlist exported from the schematic into plain Python structures.

The checks read the netlist KiCad exports from the schematic, not ``design.py``, so
they see the connectivity the board is really built from. ``export_netlist`` asks
kicad-cli for a fresh one (through ``pcbkit.kicad.cli``) and ``Netlist`` reads it.

KiCad 10 writes a pin's function as ``<name>_<number>`` ("VIN_3" for pin 3); KiCad 9
writes ``<name>``. The suffix is removed only when the netlist's own header says it
was written by KiCad 10 or later, so a pin whose real name ends in ``_<number>`` is
left alone in a KiCad 9 netlist.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Union

from pcbkit.kicad import cli
from pcbkit.kicad.sexp import find, findall, parse

StrPath = Union[str, Path]

# The first KiCad major version that writes pin functions as "<name>_<number>".
SUFFIXED_FROM = 10


class Netlist:
    """The components and nets of one netlist, with the lookups the checks need.

    ``parts`` maps a reference to its ``ref``, ``value``, ``footprint``, ``lib``,
    ``part`` and ``fields``; ``pin_net`` maps (reference, pin) to the net name, with
    the leading "/" removed; ``nets`` maps a net name to its (reference, pin, pin
    function) nodes. Order is the netlist's own, which the DC solver depends on.
    """

    def __init__(self, tree: list[Any]) -> None:
        """Read a parsed netlist (the s-expression tree of the whole file)."""
        self.parts: dict[str, dict[str, Any]] = {}
        for c in findall(find(tree, "components"), "comp"):
            ref = str(find(c, "ref")[1])
            fields = {}
            fl = find(c, "fields")
            if fl:
                for f in findall(fl, "field"):
                    fields[str(find(f, "name")[1])] = str(f[2]) if len(f) > 2 else ""
            ls = find(c, "libsource")
            footprint = find(c, "footprint")
            self.parts[ref] = {
                "ref": ref,
                "value": str(find(c, "value")[1]),
                "footprint": str(footprint[1]) if footprint else "",
                "lib": str(find(ls, "lib")[1]),
                "part": str(find(ls, "part")[1]),
                "fields": fields,
            }
        suffixed = _suffixed_pin_names(tree)
        self.pin_net: dict[tuple[str, str], str] = {}
        self.nets: dict[str, list[tuple[str, str, str]]] = {}
        for net in findall(find(tree, "nets"), "net"):
            name = str(find(net, "name")[1]).lstrip("/")
            nodes = []
            for node in findall(net, "node"):
                ref = str(find(node, "ref")[1])
                pin = str(find(node, "pin")[1])
                pf = find(node, "pinfunction")
                func = str(pf[1]) if pf else ""
                if suffixed and func.endswith("_" + pin):
                    func = func[: -len(pin) - 1]
                nodes.append((ref, pin, func))
                self.pin_net[(ref, pin)] = name
            self.nets[name] = nodes

    @classmethod
    def from_text(cls, text: str) -> Netlist:
        """Read netlist text, as kicad-cli writes it."""
        return cls(parse(text))

    @classmethod
    def from_file(cls, path: StrPath) -> Netlist:
        """Read a netlist file written by kicad-cli."""
        return cls.from_text(Path(path).read_text(encoding="utf-8"))

    def net(self, ref: str, pin: Any) -> str | None:
        """Return the net on pin ``pin`` of ``ref``, or None if it is not listed."""
        return self.pin_net.get((ref, str(pin)))

    def refs_on(self, net: str) -> set[str]:
        """Return the references that have a pin on ``net``."""
        return {r for r, _, _ in self.nets.get(net, [])}

    def kind(self, ref: str) -> str:
        """Return ``library:symbol`` for a reference, for example ``Device:R``."""
        return self.parts[ref]["lib"] + ":" + self.parts[ref]["part"]

    def by_kind(self, *kinds: str) -> list[str]:
        """Return the references of the given kinds, in natural order: R2, R10."""
        return sorted((r for r in self.parts if self.kind(r) in kinds), key=natkey)


def _suffixed_pin_names(tree: list[Any]) -> bool:
    """Return True if the netlist's header says KiCad 10 or later wrote it."""
    design = find(tree, "design")
    tool = find(design, "tool") if design else None
    found = re.search(r"(\d+)\.", str(tool[1])) if tool else None
    return bool(found) and int(found.group(1)) >= SUFFIXED_FROM


def export_netlist(schematic: StrPath, out: StrPath) -> Path:
    """Export a schematic's netlist to ``out`` with kicad-cli; return the file."""
    cli.export_netlist(schematic, out)
    return Path(out)


def fresh_netlist(schematic: StrPath, folder: StrPath | None = None) -> Netlist:
    """Export the schematic's netlist now and read it.

    The netlist goes to ``folder`` (a new temporary folder when it is None), so a
    stale ``.net`` file next to the schematic is never read by mistake.
    """
    work = Path(folder) if folder is not None else Path(tempfile.mkdtemp())
    return Netlist.from_file(export_netlist(schematic, work / "fresh.net"))


def natkey(s: str) -> list[Any]:
    """Return a sort key that orders ``R2`` before ``R10``."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


_MULT = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,
    "": 1.0,
}


def parse_value(v: str) -> float:
    """Return a component value as a number: ``2.2k`` is 2200, ``4k7`` is 4700.

    Only the first word counts (``10u 25V`` is 1e-5), and in ``4x220`` the part after
    the ``x`` is the value. Raise ValueError for text that is not a value.
    """
    v = v.strip().split()[0]
    if "x" in v:
        v = v.split("x", 1)[1]
    m = re.fullmatch(r"(\d+)([pnumkMµ])(\d+)", v)
    if m:
        return float(m.group(1) + "." + m.group(3)) * _MULT[m.group(2)]
    m = re.fullmatch(r"([\d.]+)\s*([pnumkMµ]?)[FfHhRΩ]?", v)
    if not m:
        raise ValueError(f"cannot parse value {v!r}")
    return float(m.group(1)) * _MULT[m.group(2)]
