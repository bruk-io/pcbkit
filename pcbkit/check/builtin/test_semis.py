"""Semiconductor voltage: FETs and diodes stay under a share of their ratings.

Switched on by ``power`` in ``[checks] groups``. It runs on the netlist KiCad exports
from the schematic and on the operating points of ``STRESS_SCENARIOS`` (the ``circuit``
group's table, read here too). What the project gives, in ``specs.py``:

* ``SEMI_VRATED``: ``{mpn: row}``, a row being a FET (``vds``, ``vgs``), a diode
  (``vrrm``), a TVS (``vrwm``) or a Zener clamp (``vz``), with an optional ``derate``
  and ``pins`` or ``elements`` (``pcbkit.check.semis`` says each). ``{}`` says the
  design has no semiconductor to judge (a design that has one then fails for its
  missing rating).
* ``SEMI_NOT_JUDGED`` (optional): ``{ref: reason}``, parts declared out of this check's
  reach (a bridge, an array); judged anyway when their MPN has a row.
* ``STRESS_SCENARIOS``, and ``circuits.scenario`` in ``circuits.py``.

``pcbkit.check.semis`` says which parts are judged and when each fails.
"""

from __future__ import annotations

from typing import Any

from pcbkit.check import faults, semis, tables
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
    reason}``, for a part whose pins cannot be read; ``declared not judged``, ``{ref:
    reason}`` from ``SEMI_NOT_JUDGED``, for the parts left out as declared; and
    ``unsolved scenarios`` when a ``STRESS_SCENARIOS`` row cannot be solved, before any
    failure is raised. A ``STRESS_SCENARIOS`` keyword that is not a named parameter of
    ``circuits.scenario`` fails first, naming it.
    """
    table = tables.read(specs, "SEMI_VRATED")
    declared = (
        tables.read(specs, "SEMI_NOT_JUDGED") if "SEMI_NOT_JUDGED" in specs else {}
    )
    ignored = [
        f"STRESS_SCENARIOS[{index}]: circuits.scenario has no parameter named "
        + ", ".join(repr(k) for k in names)
        + " (it would land in **kwargs, or nowhere, so the operating point solved may "
        "not be the one named): name it in scenario's signature"
        for index, names in enumerate(
            faults.ignored_keywords(circuits.scenario, row)
            for row in specs.STRESS_SCENARIOS
        )
        if names
    ]
    assert not ignored, "; ".join(ignored)
    report = semis.judge(nl, table, stress_attempts(nl, specs, circuits), declared)
    record("semiconductor voltage", report.parts)
    if report.not_judged:
        record("not judged", report.not_judged)
    if report.declared:
        record("declared not judged", report.declared)
    if report.unsolved:
        record("unsolved scenarios", report.unsolved)
    assert not report.problems, "; ".join(report.problems)
