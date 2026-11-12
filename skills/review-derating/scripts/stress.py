#!/usr/bin/env python3
"""Solve every stress scenario and print each part's worst stress, rating and margin.

The built-in ``circuit`` checks solve every ``STRESS_SCENARIOS`` row but record only the
four most stressed parts, and judge supply pins at ``SUPPLY_SCENARIO`` alone. The
derating review needs the rest as numbers, never as arithmetic in a conversation. Run
it from the project folder, under the project's own Python (it needs kicad-cli, as the
checks do, for a fresh netlist):

    .venv/bin/python "<plugin>/skills/review-derating/scripts/stress.py"

It prints JSON:

* ``capacitors``, ``resistors``, ``semiconductors``: per part, the worst stress, the
  ``STRESS_SCENARIOS`` index it happens at, the rating on file (``CAP_VRATED`` by MPN,
  ``RESISTOR_POWER_W``, ``SEMI_VRATED`` by MPN), the stress over the rating, the margin
  ``(rating - stress) / rating``, and a verdict against the house target (80 % for
  capacitors and semiconductors, 50 % for resistors): ``OK``, ``FAIL``, ``NO RATING``,
  or ``REVERSED`` for a polarised capacitor reverse biased beyond 0.3 V;
* ``supply_pins``: each ``SUPPLY_PINS`` row's voltage at every stress scenario and at
  ``SUPPLY_SCENARIO`` (``"supply"``), and whether all of them sit in its window;
* ``supply_limits``: each ``SUPPLY_LIMITS`` row as the check evaluates it, with its
  margin ``(limit - value) / limit``.

Exit codes: 0 printed, 2 no board project, no schematic, or no ``STRESS_SCENARIOS``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

CAP_TARGET = 0.8
RESISTOR_TARGET = 0.5
SEMI_TARGET = 0.8
REVERSE_LIMIT = -0.3  # V a polarised capacitor may see backwards, as the check allows


def rounded(value: float) -> float:
    """Return ``value`` to four significant figures, for a readable listing."""
    return float(f"{value:.4g}")


def judged(stress: float, rating: float | None, target: float) -> dict[str, Any]:
    """Return the ratio, margin and verdict of ``stress`` against ``rating``."""
    if not rating:
        return {"rating": None, "ratio": None, "margin": None, "verdict": "NO RATING"}
    ratio = stress / rating
    return {
        "rating": rating,
        "ratio": rounded(ratio),
        "margin": rounded((rating - stress) / rating),
        "target": target,
        "verdict": "OK" if ratio <= target else "FAIL",
    }


def worse(table: dict[str, dict[str, Any]], key: str, value: float, row: int) -> None:
    """Keep ``value`` as ``table[key]``'s worst if it is higher than the one kept."""
    if key not in table or value > table[key]["worst"]:
        table[key] = {"worst": value, "scenario": row}


def mpn(nl: Any, ref: str) -> str:
    """Return the MPN field of ``ref`` in the netlist, or "" if it has none."""
    return nl.parts[ref]["fields"].get("MPN", "") if ref in nl.parts else ""


def stress(nl: Any, specs: Any, circuits: Any) -> dict[str, Any]:
    """Return every part's worst stress over the stress scenarios, with its margin."""
    solved = [circuits.scenario(nl, **kw).solve() for kw in specs.STRESS_SCENARIOS]
    caps: dict[str, dict[str, Any]] = {}
    reverse: dict[str, float] = {}
    res: dict[str, dict[str, Any]] = {}
    semis: dict[str, dict[str, Any]] = {}
    cap_refs = nl.by_kind("Device:C", "Device:C_Polarized")
    for row, sol in enumerate(solved):
        for ref in cap_refs:
            v = sol[nl.net(ref, 1)] - sol[nl.net(ref, 2)]
            worse(caps, ref, abs(v), row)
            if nl.kind(ref) == "Device:C_Polarized":
                reverse[ref] = min(reverse.get(ref, 0.0), v)
        for ref, watts in sol.resistor_power().items():
            if not ref[0].islower() and "." not in ref:
                worse(res, ref, watts, row)
        for fet in sol.c.fets:
            worse(semis, f"{fet['ref']} vds", abs(sol[fet["d"]] - sol[fet["s"]]), row)
            worse(semis, f"{fet['ref']} vgs", abs(sol[fet["g"]] - sol[fet["s"]]), row)
        for anode, cathode, _vf, _ron, ref in sol.c.diodes:
            if "." not in ref:
                worse(semis, f"{ref} vrrm", max(0.0, sol[cathode] - sol[anode]), row)
    cap_rated = dict(getattr(specs, "CAP_VRATED", {}))
    for ref, row in caps.items():
        row.update(
            mpn=mpn(nl, ref),
            **judged(row["worst"], cap_rated.get(mpn(nl, ref)), CAP_TARGET),
        )
        if reverse.get(ref, 0.0) < REVERSE_LIMIT:
            row.update(reverse=rounded(reverse[ref]), verdict="REVERSED")
    res_rated = dict(getattr(specs, "RESISTOR_POWER_W", {}))
    for ref, row in res.items():
        rating = res_rated.get(ref, res_rated.get("default"))
        row.update(judged(row["worst"], rating, RESISTOR_TARGET))
    semi_rated = dict(getattr(specs, "SEMI_VRATED", {}))
    for key, row in semis.items():
        ref, what = key.split(" ")
        rating = semi_rated.get(mpn(nl, ref), {}).get(what)
        row.update(mpn=mpn(nl, ref), **judged(row["worst"], rating, SEMI_TARGET))
    for table in (caps, res, semis):
        for row in table.values():
            row["worst"] = rounded(row["worst"])
    return {
        "scenarios": list(specs.STRESS_SCENARIOS),
        "capacitors": caps,
        "resistors": res,
        "semiconductors": semis,
        "supply_pins": supply_pins(nl, specs, circuits, solved),
        "supply_limits": supply_limits(nl, specs),
    }


def supply_pins(nl: Any, specs: Any, circuits: Any, solved: list[Any]) -> list[Any]:
    """Return each SUPPLY_PINS row's voltage at every scenario, and if all fit."""
    rows = list(getattr(specs, "SUPPLY_PINS", []))
    named = {str(i): sol for i, sol in enumerate(solved)}
    if rows and hasattr(specs, "SUPPLY_SCENARIO"):
        named["supply"] = circuits.scenario(nl, **specs.SUPPLY_SCENARIO).solve()
    found = []
    for ref, pin, (low, high) in rows:
        volts = {k: rounded(sol[nl.net(ref, pin)]) for k, sol in named.items()}
        found.append(
            {
                "ref": ref,
                "pin": str(pin),
                "min": low,
                "max": high,
                "v": volts,
                "ok": all(low <= v <= high for v in volts.values()),
            }
        )
    return found


def supply_limits(nl: Any, specs: Any) -> list[dict[str, Any]]:
    """Return each SUPPLY_LIMITS row as the check evaluates it, with its margin."""
    found = []
    for entry in getattr(specs, "SUPPLY_LIMITS", []):
        what, value, op, limit = entry(nl) if callable(entry) else entry
        ok = value < limit if op == "<" else value <= limit
        margin = rounded((limit - value) / limit) if limit else None
        found.append(
            {
                "what": what,
                "value": value,
                "op": op,
                "limit": limit,
                "ok": ok,
                "margin": margin,
            }
        )
    return found


def main() -> int:
    """Print the stress table of the project here; return the exit code."""
    from pcbkit.check.netlist import fresh_netlist
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
    schematic = project.kicad_dir / f"{project.config.board.stem}.kicad_sch"
    if not schematic.is_file():
        print(f"{schematic} not found: run pcbkit sch first")
        return 2
    specs = import_optional_project_module(project.root, "specs")
    circuits = import_optional_project_module(project.root, "circuits")
    if specs is None or circuits is None or not hasattr(specs, "STRESS_SCENARIOS"):
        print(
            "specs.STRESS_SCENARIOS and circuits.scenario are needed: write them first"
        )
        return 2
    nl = fresh_netlist(schematic, Path(tempfile.mkdtemp(prefix="pcbkit_stress_")))
    print(json.dumps(stress(nl, specs, circuits), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
