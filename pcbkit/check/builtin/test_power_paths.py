"""Power paths: which judged rails can feed which, read from the netlist.

Switched on by ``power`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic. What the project gives, in ``specs.py``:

* ``POWER_PATHS``: ``{"nets": [net], "paths": [(from net, to net, [ref])], "parts":
  {ref: [(in pad, out pad)]} (optional), "max_ohm": ohm (optional, default 10)}``.
  ``nets`` are the judged rails; each row of ``paths`` is a path the design means,
  through the parts it names; ``parts`` states what an IC, module or connector
  passes, pads as the netlist writes them (``{"U1": []}``: nothing).
* ``SUPPLY_NETS``: ``[net]``. The judged nets must be exactly its nets other than the
  ground net (``GROUND_NET``, default ``"GND"`` when the design has that net).

Three checks: each declared path exists and runs only through its named parts; no
other ordered pair of judged nets is joined at ``max_ohm`` or less; and no part the
walk does not go through reaches two judged nets unless it is declared.
``pcbkit.check.power_paths`` says how each part conducts.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import power_paths, tables
from pcbkit.check.netlist import Netlist
from pcbkit.check.plugin import ProjectModule, spec_params


def _rows(specs: ProjectModule) -> list[tuple[str, str, list[str]]]:
    """Return the paths rows; a missing or wrong table fails by name here."""
    table = tables.read(specs, "POWER_PATHS")
    tables.read(specs, "SUPPLY_NETS")
    return power_paths.read_rows(table)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the path check from the rows of the table."""
    spec_params(metafunc, "row", _rows, ids=power_paths.path_id)


def _read(
    nl: Netlist, specs: ProjectModule
) -> tuple[dict[str, Any], power_paths.Model, list[str]]:
    """Return the table, the conduction model and the problems with the table."""
    table = tables.read(specs, "POWER_PATHS")
    supplies = tables.read(specs, "SUPPLY_NETS")
    # GROUND_NET's documented default is "GND", used when the design has that net
    if hasattr(specs, "GROUND_NET"):
        ground = tables.read(specs, "GROUND_NET")
    else:
        ground = "GND" if "GND" in nl.nets else None
    problems = power_paths.table_problems(nl, table, supplies, ground)
    return table, power_paths.build_model(nl, table, ground), problems


def test_power_path(
    nl: Netlist, record: Any, specs: ProjectModule, row: tuple[str, str, list[str]]
) -> None:
    """Find the declared path, at most ``max_ohm``, and none without its named parts.

    Records ``power path``, ``{"from", "to", "refs", "ohms", "path", "without
    named"}``, before any failure is raised.
    """
    table, model, problems = _read(nl, specs)
    report = power_paths.check_path(nl, model, table, row)
    for key, value in report.numbers.items():
        record(key, value)
    problems = problems + report.problems
    assert not problems, f"{power_paths.path_id(row)}: " + "; ".join(problems)


def test_no_unlisted_power_path(nl: Netlist, record: Any, specs: ProjectModule) -> None:
    """Join no ordered pair of judged nets at ``max_ohm`` or less unless a row says so.

    Records ``max_ohm`` and ``power paths``, ``{"X->Y": {"ohms", "path ohms", "path",
    "power path", "listed"}}`` for every pair current can flow between, at any
    resistance, before any failure is raised.
    """
    table, model, problems = _read(nl, specs)
    report = power_paths.check_unlisted(nl, model, table)
    for key, value in report.numbers.items():
        record(key, value)
    problems = problems + report.problems
    assert not problems, "; ".join(problems)


def test_power_path_parts_are_modelled(
    nl: Netlist, record: Any, specs: ProjectModule
) -> None:
    """Leave no IC, module or connector across two judged nets undeclared.

    Records ``not modelled``, ``{ref: {judged net: how}}``, and ``declared parts``,
    ``{ref: ["1 -> 3 (VIN -> +5V)"]}``, before any failure is raised.
    """
    table, model, problems = _read(nl, specs)
    report = power_paths.check_modelled(nl, model, table)
    for key, value in report.numbers.items():
        record(key, value)
    problems = problems + report.problems
    assert not problems, "; ".join(problems)
