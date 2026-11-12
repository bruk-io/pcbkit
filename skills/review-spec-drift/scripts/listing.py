#!/usr/bin/env python3
"""List what a board's design contains, and compare it with the spec's claims.

What the design contains is listable, so the spec-drift review never recounts it by
reading. Run it from the project folder, under the project's own Python (it needs
kicad-cli, as the checks do, for a fresh netlist):

    .venv/bin/python "<plugin>/skills/review-spec-drift/scripts/listing.py" [CLAIMS]

It prints JSON with ``facts``: one key per thing a spec can claim, read from a fresh
netlist, design.py, layout.py and pcbkit.toml:

* ``outline.W``, ``outline.H``, ``holes`` (layout.py), ``stackup.layers``,
  ``stackup.copper_mm``, ``stackup.thickness_mm`` (pcbkit.toml);
* ``part.<ref>.<field>`` for ``category``, ``kind``, ``value``, ``footprint``, ``mpn``,
  ``pads`` (how many pads are on a net) and ``dnp`` (design.py);
* ``parts.<category>``: the references in each category (``connector``, ``ic``,
  ``switch``, ``jumper``, ``test point``, ``other``);
* ``net.<name>``: the pads on each net, ``REF.pad``, so the parts a rail or an enable
  reaches is a lookup, not a trace.

``CLAIMS`` is a JSON file the review writes from the spec, before it looks at the
design: ``{fact key: the value the spec states}``. Each claim is then ``matches``,
``differs`` (with both values) or ``not in design``, under ``claims``. Numbers match
within 1e-6; a list matches another holding the same items in any order.

Exit codes: 0 printed and every claim matches (or none was given), 1 a claim does not
match, 2 no board project, no schematic, or a claims file that is not a JSON table.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

# A part's category, from its library or reference: the first rule that matches wins.
CATEGORIES = (
    ("test point", ("TestPoint",), ("TP",)),
    ("jumper", ("Jumper",), ("JP",)),
    ("connector", ("Connector",), ("J",)),
    ("switch", ("Switch",), ("SW",)),
    ("ic", (), ("U",)),
)


def category(ref: str, lib: str) -> str:
    """Return the category of the part ``ref`` from library ``lib``."""
    letters = ref.rstrip("0123456789")
    for name, libs, prefixes in CATEGORIES:
        if lib.startswith(libs) or letters in prefixes:
            return name
    return "other"


def facts(
    nl: Any,
    dnp: set[str],
    layout: dict[str, Any],
    stackup: dict[str, Any],
) -> dict[str, Any]:
    """Return every claimable fact of the design, one flat key each."""
    found: dict[str, Any] = {}
    for key in ("W", "H"):
        if key in layout:
            found[f"outline.{key}"] = layout[key]
    found["holes"] = len(layout.get("HOLES", []))
    for key, value in stackup.items():
        found[f"stackup.{key}"] = value
    pads: dict[str, int] = {}
    for (ref, _pad), _net in nl.pin_net.items():
        pads[ref] = pads.get(ref, 0) + 1
    groups: dict[str, list[str]] = {}
    for ref, part in nl.parts.items():
        kind = category(ref, part["lib"])
        groups.setdefault(kind, []).append(ref)
        found.update(
            {
                f"part.{ref}.category": kind,
                f"part.{ref}.kind": f"{part['lib']}:{part['part']}",
                f"part.{ref}.value": part["value"],
                f"part.{ref}.footprint": part["footprint"],
                f"part.{ref}.mpn": part["fields"].get("MPN", ""),
                f"part.{ref}.pads": pads.get(ref, 0),
                f"part.{ref}.dnp": ref in dnp,
            }
        )
    for kind, refs in sorted(groups.items()):
        found[f"parts.{kind}"] = sorted(refs, key=natural)
    for net, nodes in sorted(nl.nets.items()):
        found[f"net.{net}"] = sorted((f"{r}.{p}" for r, p, _ in nodes), key=natural)
    return found


def natural(text: str) -> list[Any]:
    """Return a sort key that puts R2 before R10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def same(claimed: Any, design: Any) -> bool:
    """Return True if a claimed value and the design's agree."""
    if isinstance(claimed, bool) or isinstance(design, bool):
        return claimed is design
    if isinstance(claimed, (int, float)) and isinstance(design, (int, float)):
        return abs(claimed - design) <= 1e-6
    if isinstance(claimed, list) and isinstance(design, list):
        return sorted(map(str, claimed)) == sorted(map(str, design))
    return claimed == design


def compare(claims: dict[str, Any], found: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one row per claim: the claimed value, the design's, and the status."""
    rows = []
    for key, claimed in claims.items():
        if key not in found:
            rows.append({"key": key, "claimed": claimed, "status": "not in design"})
            continue
        status = "matches" if same(claimed, found[key]) else "differs"
        rows.append(
            {"key": key, "claimed": claimed, "design": found[key], "status": status}
        )
    return rows


def main(argv: list[str]) -> int:
    """Print the design's facts and the claims compared; return the exit code."""
    from pcbkit.check.netlist import fresh_netlist
    from pcbkit.design import load_design
    from pcbkit.project import (
        ProjectError,
        import_optional_project_module,
        load_project,
    )

    try:
        project = load_project()
    except ProjectError as err:
        print(f"no board project: {err.message}")
        return 2
    claims: dict[str, Any] = {}
    if argv:
        try:
            claims = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            print(f"cannot read the claims file {argv[0]}: {err}")
            return 2
        if not isinstance(claims, dict):
            print(f"{argv[0]} is not a JSON table of fact key to claimed value")
            return 2
    schematic = project.kicad_dir / f"{project.config.board.stem}.kicad_sch"
    if not schematic.is_file():
        print(f"{schematic} not found: run pcbkit sch first")
        return 2
    nl = fresh_netlist(schematic, Path(tempfile.mkdtemp(prefix="pcbkit_listing_")))
    design = load_design(project.root / "design.py")
    layout = import_optional_project_module(project.root, "layout")
    stack = project.config.stackup
    found = facts(
        nl,
        {p["ref"] for p in design.parts if p["dnp"]},
        {k: getattr(layout, k) for k in ("W", "H", "HOLES") if hasattr(layout, k)},
        {
            "layers": stack.layers,
            "copper_mm": stack.copper_mm,
            "thickness_mm": stack.thickness_mm,
        },
    )
    rows = compare(claims, found)
    print(json.dumps({"facts": found, "claims": rows}, indent=1, default=str))
    return 1 if any(row["status"] != "matches" for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
