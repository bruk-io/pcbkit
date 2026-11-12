"""Enable links: a regulator on a shared enable can be enabled alone.

Switched on by ``power`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic. What the project gives, in ``specs.py``:

* ``ENABLE_LINKS``: ``{enable net: [(regulator ref, enable pad)]}``, in the pad numbers
  the netlist writes. Each regulator on the shared net ``enable net`` must reach it
  through exactly one link of its own (a two-pad 0 ohm or not-fitted ``Device:R``,
  ``R_Small`` or ``R_US``, or a two-pad part from a ``Jumper`` library), anywhere on
  the path, such that with the link taken out its enable net reaches no shared net of
  any row and no other listed regulator's enable net (``links`` walks the parts
  between). ``{}`` says no enable is shared.
* ``SUPPLY_NETS``: ``[net]``, the supply and ground nets. The walk never crosses one,
  nor ``GROUND_NET`` when that is defined; a net that is a supply but is not listed is
  walked. Needed whenever ``power`` is on.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import links, tables
from pcbkit.check.netlist import Netlist
from pcbkit.check.plugin import ProjectModule, spec_params


def _shared_nets(specs: ProjectModule) -> list[str]:
    """Return the shared enable nets; a missing or wrong table fails by name here."""
    table = tables.read(specs, "ENABLE_LINKS")
    tables.read(specs, "SUPPLY_NETS")
    return sorted(table)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the check from the shared enable nets of the table."""
    spec_params(metafunc, "net", _shared_nets)


def test_each_regulator_on_a_shared_enable_has_its_own_link(
    nl: Netlist, record: Any, specs: ProjectModule, net: str
) -> None:
    """Join each regulator to the shared enable through a link no other one uses.

    Fails, naming the path, when the regulator is still joined to a shared net or
    another regulator's enable with its link removed. Records ``enable links``:
    ``{net: {ref: link ref or None}}``, before any failure is raised.
    """
    table = tables.read(specs, "ENABLE_LINKS")
    supplies = tables.read(specs, "SUPPLY_NETS")
    ground = tables.read(specs, "GROUND_NET") if hasattr(specs, "GROUND_NET") else None
    report = links.check_enable_net(nl, net, table, supplies, ground)
    record("enable links", {net: report.links})
    assert not report.problems, "; ".join(report.problems)
