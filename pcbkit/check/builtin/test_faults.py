"""Fault states: each listed pin is driven, and at a safe level, in each condition.

Switched on by ``power`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic and on ``circuits.scenario``. What the project gives, in
``specs.py``:

* ``FAULT_STATES``: ``{condition: {"scenario": {keyword: value}, "pins": [(ref, pad,
  (min V, max V))]}}``, pads as the netlist writes them. ``scenario`` is the keyword
  arguments of ``circuits.scenario`` that describe the condition ("controller absent"
  is whatever that scenario fixes and leaves free); the window comes from the part's
  datasheet (VIL and VIH of an enable). ``{}`` says there is no fault state to check.

Each keyword of a condition's scenario must be a named parameter of
``circuits.scenario``. Each listed pin must be driven (a floating pin fails whatever its
window says), every pad on the nets that set its level must be one the model holds, no
FET there may be decided by a floating gate, and its level must be inside the window.
``pcbkit.check.faults`` says exactly what that means.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import faults, semis, tables
from pcbkit.check.netlist import Netlist
from pcbkit.check.plugin import ProjectModule, spec_params


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the check from the conditions of the table."""
    spec_params(
        metafunc, "condition", lambda specs: sorted(tables.read(specs, "FAULT_STATES"))
    )


def test_fault_state_pins(
    nl: Netlist,
    record: Any,
    specs: ProjectModule,
    circuits: ProjectModule,
    condition: str,
) -> None:
    """Hold each listed pin driven and inside its window in the condition.

    Records ``pins``, ``{"U2 pad 5": [V, min, max, state]}`` (state ``driven``,
    ``fixed``, ``floating``, ``no net`` or ``unsolved``; V is null when there is no net
    or no solution), and ``not modelled``, ``{"U2 pad 5": ["U1 pad 3 (lib:part) on
    EN"]}``, the pads on the pin's nets the model leaves out, and ``fet gates``,
    ``{"U2 pad 5": {fet: {"vgs", "vth", "on"}}}`` for each FET whose state the verdict
    on a pin leaned on, when there are any, before any failure is raised. A
    ``SEMI_VRATED`` row of kind ``vrrm``, when that table is defined, says a part
    ``extended`` holds forward only blocks up to its VRRM.
    """
    row = tables.read(specs, "FAULT_STATES")[condition]
    pins = row["pins"]
    ignored = faults.ignored_keywords(circuits.scenario, row["scenario"])
    if ignored:
        record("pins", faults.unsolved(pins))
        names = ", ".join(repr(k) for k in ignored)
        raise AssertionError(
            f"condition {condition}: circuits.scenario has no parameter named {names} "
            "(it would land in **kwargs, or nowhere, so the condition solved may not "
            "be the one named): name it in scenario's signature"
        )
    try:
        circuit = circuits.scenario(nl, **row["scenario"])
    except Exception as err:  # noqa: BLE001 - the project's code: say what it raised
        record("pins", faults.unsolved(pins))
        raise AssertionError(
            f"condition {condition}: circuits.scenario(nl, **{row['scenario']!r}) "
            f"raised {type(err).__name__}: {err}"
        ) from None
    try:
        solved = circuit.solve()
    except Exception as err:  # noqa: BLE001 - a solve that fails is the verdict
        record("pins", faults.unsolved(pins))
        if isinstance(err, RuntimeError) and "converge" in str(err):
            raise AssertionError(
                f"the DC solve did not converge in condition {condition}"
            ) from None
        raise AssertionError(
            f"condition {condition}: the scenario could not be solved: "
            f"{type(err).__name__}: {err}"
        ) from None
    try:
        recheck = faults.low_leak_solution(circuit)
    except Exception as err:  # noqa: BLE001 - the second solve is a test of the first
        record("pins", faults.unsolved(pins))
        raise AssertionError(
            f"condition {condition}: the solve with a smaller leak, which shows a "
            f"level held only by the solver's leak, failed: {type(err).__name__}: {err}"
        ) from None
    blocking = (
        semis.blocking_ratings(nl, tables.read(specs, "SEMI_VRATED"))
        if "SEMI_VRATED" in specs
        else {}
    )
    report = faults.judge(nl, circuit, solved, pins, recheck, blocking)
    record("pins", report.pins)
    if report.not_modelled:
        record("not modelled", report.not_modelled)
    if report.fet_gates:
        record("fet gates", report.fet_gates)
    assert not report.problems, f"condition {condition}: " + "; ".join(report.problems)
