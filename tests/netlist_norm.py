"""Reduce a KiCad netlist to what a test compares: components, fields and nets.

The result is ``{"components": {ref: {"value", "footprint", "fields": {name: text}}},
"nets": {net name: sorted ["REF.PIN", ...]}}``. Dates, the source path, UUIDs, sheet
paths, libsource and pin functions are left out, as they change between runs and
between KiCad versions. A missing footprint, or a field with no text, is "". Net names
are kept as KiCad writes them, with the leading "/".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pcbkit.kicad.sexp import findall, parse


def _text(node: list, key: str) -> str:
    """Return the first value of the child called ``key``, or "" if it has none."""
    found = findall(node, key)
    return str(found[0][1]) if found and len(found[0]) > 1 else ""


def _field_text(field: list) -> str:
    """Return the text of a ``(field (name N) "text")`` node, or "" if it has none."""
    rest = [item for item in field[1:] if not isinstance(item, list)]
    return str(rest[0]) if rest else ""


def normalise(path: Path) -> dict[str, Any]:
    """Read the netlist at ``path`` and return its normalised form."""
    root = parse(path.read_text(encoding="utf-8"))
    components = {}
    for comp in findall(findall(root, "components")[0], "comp"):
        fields = findall(comp, "fields")
        components[_text(comp, "ref")] = {
            "value": _text(comp, "value"),
            "footprint": _text(comp, "footprint"),
            "fields": {
                _text(f, "name"): _field_text(f)
                for f in (findall(fields[0], "field") if fields else [])
            },
        }
    nets = {}
    for net in findall(findall(root, "nets")[0], "net"):
        nets[_text(net, "name")] = sorted(
            f"{_text(node, 'ref')}.{_text(node, 'pin')}"
            for node in findall(net, "node")
        )
    return {"components": components, "nets": nets}
