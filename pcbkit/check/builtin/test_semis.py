"""Semiconductor voltage: FETs and diodes stay under a share of their ratings.

Switched on by ``power`` in ``[checks] groups``. It runs on the netlist KiCad exports
from the schematic and on the operating points of ``STRESS_SCENARIOS`` (the ``circuit``
group's table, read here too). What the project gives, in ``specs.py``:

* ``SEMI_VRATED``: ``{mpn: {"vds": V, "vgs": V} or {"vrrm": V}}``, each with an
  optional ``derate`` (at most and by default 0.8) and optional ``pins`` (``{"g", "d",
  "s"}`` or ``{"a", "k"}`` to pads) for a part whose pin names do not read as one FET or
  one diode. ``{}`` says the design has no semiconductor to judge (a design that has one
  then fails for its missing rating).
* ``STRESS_SCENARIOS``, and ``circuits.scenario`` in ``circuits.py``.

``pcbkit.check.semis`` says which parts are judged and when each fails.
"""

from __future__ import annotations

from typing import Any

from pcbkit.check import semis, tables
from pcbkit.check.netlist import Netlist
from pcbkit.check.plugin import ProjectModule
from pcbkit.check.stress import stress_attempts


def test_semiconductor_voltage_derated(
    nl: Netlist, record: Any, specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Keep every FET's |Vds| and |Vgs| and every diode's voltage under its target.

    Records ``semiconductor voltage``: ``{ref: {"kind", "mpn", "target", <quantity>:
    {"v", "rated", "limit_v", "scenario"}}}`` for each judged part whose pins were read
    (``vds`` and ``vgs`` for a FET, ``vr`` for a diode; ``rated`` and ``limit_v`` null
    without a row, ``v`` null when no scenario gave a reading); ``not judged``, ``{ref:
    reason}``, for a part whose pins cannot be read; and ``unsolved scenarios`` when a
    ``STRESS_SCENARIOS`` row cannot be solved, before any failure is raised.
    """
    table = tables.read(specs, "SEMI_VRATED")
    report = semis.judge(nl, table, stress_attempts(nl, specs, circuits))
    record("semiconductor voltage", report.parts)
    if report.not_judged:
        record("not judged", report.not_judged)
    if report.unsolved:
        record("unsolved scenarios", report.unsolved)
    assert not report.problems, "; ".join(report.problems)
