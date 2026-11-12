"""The operating points that stress parts hardest, and the tables that tighten a check.

``stress_solutions`` solves each row of the project's ``STRESS_SCENARIOS`` in turn and
yields ``(index, solution)``, so a check can say which scenario was the worst case.
Nothing here imports pytest, so ``pcbkit specs`` can read ``TABLES``.

``CAP_DERATE`` is the optional table of the capacitor check: ``{mpn: fraction}``, the
share of the rated voltage that part may reach. It defaults to 0.8 and cannot be more,
so the table can only tighten (a tantalum on a rail that can be hot-plugged: 0.5).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from pcbkit.check.tables import DictOf, Fraction, Shape, Text

if TYPE_CHECKING:
    from pcbkit.check.dc import Solution

# The share of the rated voltage a capacitor may reach when CAP_DERATE says nothing.
CAP_DERATE_DEFAULT = 0.8

TABLES: dict[str, Shape] = {
    "CAP_DERATE": DictOf(Text("mpn"), Fraction(hi=CAP_DERATE_DEFAULT)),
}


def stress_solutions(
    nl: Any, specs: Any, circuits: Any
) -> Iterator[tuple[int, Solution]]:
    """Yield ``(index, solution)`` for each row of ``specs.STRESS_SCENARIOS``.

    ``index`` is the row's place in the table (0 for the first), which a check records
    as the scenario of a worst case. ``circuits.scenario(nl, **row)`` builds each
    circuit, as the project defines it.
    """
    for index, keywords in enumerate(specs.STRESS_SCENARIOS):
        yield index, circuits.scenario(nl, **keywords).solve()
