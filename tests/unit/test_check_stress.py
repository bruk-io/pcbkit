"""Unit tests for pcbkit.check.stress: the stress solves and the CAP_DERATE shape."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from pcbkit.check import stress, tables


class FakeCircuit:
    """A circuit that solves to the keywords it was built with."""

    def __init__(self, keywords: dict[str, Any]) -> None:
        """Keep the keywords."""
        self.keywords = keywords

    def solve(self) -> dict[str, Any]:
        """Return the keywords as the 'solution'."""
        return self.keywords


def test_stress_solutions_yield_each_row_in_order_with_its_index() -> None:
    """Number the scenarios from 0 and solve each with its own keywords."""
    rows = [{"vin": 5.0}, {"vin": 5.5, "drive": {"A": 1}}, {}]
    circuits = SimpleNamespace(scenario=lambda nl, **kw: FakeCircuit(kw))
    found = list(
        stress.stress_solutions("nl", SimpleNamespace(STRESS_SCENARIOS=rows), circuits)
    )
    assert found == [(0, rows[0]), (1, rows[1]), (2, rows[2])]


def test_stress_solutions_pass_the_netlist_to_the_project_scenario() -> None:
    """Call ``circuits.scenario(nl, **row)`` with the netlist given."""
    seen = []

    def scenario(nl: Any, **kw: Any) -> FakeCircuit:
        seen.append(nl)
        return FakeCircuit(kw)

    list(
        stress.stress_solutions(
            "the-netlist",
            SimpleNamespace(STRESS_SCENARIOS=[{}]),
            SimpleNamespace(scenario=scenario),
        )
    )
    assert seen == ["the-netlist"]


def test_stress_solutions_of_no_scenarios_is_empty() -> None:
    """Yield nothing for an empty table."""
    circuits = SimpleNamespace(scenario=lambda nl, **kw: FakeCircuit(kw))
    assert not list(
        stress.stress_solutions("nl", SimpleNamespace(STRESS_SCENARIOS=[]), circuits)
    )


def test_cap_derate_is_registered_here_and_only_here() -> None:
    """Find the shape through the registry, from this module and not ``PROPOSED``."""
    assert "CAP_DERATE" not in tables.PROPOSED
    assert tables.registry()["CAP_DERATE"] is stress.TABLES["CAP_DERATE"]


@pytest.mark.parametrize("fraction", [0.5, 0.8, 0.0001, 1 / 3])
def test_cap_derate_accepts_a_fraction_up_to_the_default(fraction: float) -> None:
    """Take any fraction above 0 up to and including 0.8."""
    table = {"TANT-10U": fraction}
    assert tables.validate("CAP_DERATE", table, stress.TABLES["CAP_DERATE"]) == []


@pytest.mark.parametrize("fraction", [0.9, 1.0, 0, -0.5, True, "0.5", None])
def test_cap_derate_refuses_a_fraction_that_would_loosen_the_check(
    fraction: Any,
) -> None:
    """Refuse above 0.8, zero, negatives, a bool and a non-number, with the path."""
    errors = tables.validate(
        "CAP_DERATE", {"TANT-10U": fraction}, stress.TABLES["CAP_DERATE"]
    )
    assert len(errors) == 1
    assert errors[0].startswith(
        'specs.CAP_DERATE["TANT-10U"]: expected fraction in (0, 0.8]'
    )
    assert errors[0].endswith(f"got {fraction!r}")


def test_cap_derate_is_described_as_the_docs_show_it() -> None:
    """Describe the table as ``{mpn: fraction in (0, 0.8]}``."""
    assert tables.describe(stress.TABLES["CAP_DERATE"]) == "{mpn: fraction in (0, 0.8]}"


def test_the_default_is_the_ceiling() -> None:
    """Make the table able only to tighten: its ceiling is the default."""
    assert stress.CAP_DERATE_DEFAULT == 0.8
