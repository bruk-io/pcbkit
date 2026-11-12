"""Enable links: a regulator on a shared enable can be enabled alone.

Switched on by ``power`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic. What the project gives, in ``specs.py``:

* ``ENABLE_LINKS``: ``{enable net: [(regulator ref, enable pad)]}``, in the pad numbers
  the netlist writes. Each regulator on the shared net ``enable net`` must reach it
  through a link of its own: a two-pin 0 ohm ``Device:R`` or a part from a ``Jumper``
  library, between the regulator's own enable net and the shared one. ``{}`` says no
  enable is shared.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import links, tables
from pcbkit.check.netlist import Netlist
from pcbkit.check.plugin import ProjectModule, spec_params


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the check from the shared enable nets of the table."""
    spec_params(
        metafunc, "net", lambda specs: sorted(tables.read(specs, "ENABLE_LINKS"))
    )


def test_each_regulator_on_a_shared_enable_has_its_own_link(
    nl: Netlist, record: Any, specs: ProjectModule, net: str
) -> None:
    """Join each regulator to the shared enable through a link no other one uses.

    Records ``enable links``: ``{net: {ref: link ref or None}}``, before any failure is
    raised.
    """
    regulators = tables.read(specs, "ENABLE_LINKS")[net]
    report = links.check_enable_net(nl, net, regulators)
    record("enable links", {net: report.links})
    assert not report.problems, "; ".join(report.problems)
