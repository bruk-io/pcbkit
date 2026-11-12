"""The port map: each connector pad carries the net the spec says.

Switched on by ``ports`` in ``[checks] groups``. They run on the netlist KiCad exports
from the schematic. What the project gives, in ``specs.py``:

* ``PIN_NETS``: ``{ref: {pad: net or None}}``, the spec's port map in the pad numbers
  the netlist writes (``None``: nothing is wired to the pad). Every connector (a part
  from a ``Connector`` library, or whose reference starts with ``J``) must be a key,
  and every wired pad of a connector must be listed. A controller or any other part
  may be listed too: only the pads listed are compared. ``{}`` says the design has no
  connector.
"""

from __future__ import annotations

from typing import Any

import pytest

from pcbkit.check import ports, tables
from pcbkit.check.netlist import Netlist, natkey
from pcbkit.check.plugin import ProjectModule, spec_params


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the per-part check from the rows of the port map."""
    spec_params(
        metafunc,
        "ref",
        lambda specs: sorted(tables.read(specs, "PIN_NETS"), key=natkey),
    )


def test_port_pins_carry_their_nets(
    nl: Netlist, record: Any, specs: ProjectModule, ref: str
) -> None:
    """Give each pad of a listed part the net its row says, and list a connector's all.

    Records ``pins``: ``{pad: net or None}`` as the netlist has each listed pad the
    part has (None: unwired), and ``not pads``, the listed pads the part does not
    have, when there are any. A part that is not in the design records ``pins`` as
    None.
    """
    row = tables.read(specs, "PIN_NETS")[ref]
    report = ports.compare(nl, ref, row)
    if ref not in nl.parts:
        record("pins", None)
        raise AssertionError(f"{ref} is in specs.PIN_NETS but not in the design")
    record("pins", report.found)
    if report.not_pads:
        record("not pads", report.not_pads)
    problems = list(report.wrong)
    problems += [
        f"pad {pad}: wired to {net} but not listed"
        for pad, net in report.unlisted.items()
    ]
    assert not problems, f"{ref}: " + "; ".join(problems)


def test_every_connector_is_in_the_port_map(
    nl: Netlist, record: Any, specs: ProjectModule
) -> None:
    """Put every connector of the design in the port map.

    Records ``port map``: every connector as the design has it, ``{ref: {pad: net or
    None}}``, before any failure is raised.
    """
    port_map = tables.read(specs, "PIN_NETS")
    record("port map", ports.port_map_found(nl))
    missing = ports.unlisted_connectors(nl, port_map)
    assert not missing, f"connectors not in specs.PIN_NETS: {missing}"
