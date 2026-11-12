"""The port map: which net each pad of a connector carries.

A spec's port map says, for every connector, what each pin is wired to. ``PIN_NETS``
holds it as ``{ref: {pad: net or None}}`` (``None``: nothing is wired to that pad), in
the pad numbers the netlist writes. The functions here compare the map with a
``Netlist``; they read nothing else and import no pytest, so ``pcbkit specs`` can read
``TABLES``.

A connector is a part whose symbol library starts with ``Connector``, or whose
reference starts with ``J``. Every connector must be in the map, and every pad of one
that is wired must be listed. A part that is not a connector (a controller whose pins
the spec also fixes) may be listed too, and only the pads listed are compared.

KiCad gives a pin the schematic leaves unwired the net ``unconnected-(...)``; that and
a pad that is on no net at all both count as unwired here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pcbkit.check.netlist import Netlist, natkey
from pcbkit.check.tables import DictOf, Net, Nullable, Pad, Ref, Shape

TABLES: dict[str, Shape] = {
    "PIN_NETS": DictOf(Ref(), DictOf(Pad(), Nullable(Net()))),
}

# The start of the net KiCad gives a pin that nothing is wired to.
UNCONNECTED = "unconnected-("


def is_connector(nl: Netlist, ref: str) -> bool:
    """Return True for a part from a ``Connector`` library or with a J... reference."""
    part = nl.parts.get(ref)
    return ref.startswith("J") or (
        part is not None and str(part["lib"]).startswith("Connector")
    )


def connectors(nl: Netlist) -> list[str]:
    """Return the references of every connector in the design, in natural order."""
    return sorted((r for r in nl.parts if is_connector(nl, r)), key=natkey)


def found_nets(nl: Netlist, ref: str) -> dict[str, str | None]:
    """Return ``{pad: net or None}`` for every pad of ``ref`` the netlist lists.

    ``None`` is a pad that is wired to nothing. The pads come in natural order (1, 2,
    10), which is the order a message lists them in.
    """
    found: dict[str, str | None] = {}
    for pad in sorted(nl.functions(ref), key=natkey):
        net = nl.net(ref, pad)
        found[pad] = None if net is None or net.startswith(UNCONNECTED) else net
    return found


@dataclass
class PortReport:
    """What comparing one part with its row of the map found.

    ``found`` is the net of every pad listed in the row; ``wrong`` has one line for
    each listed pad whose net is not the one the row says; ``unlisted`` is the wired
    pads the row does not list (``{pad: net}``), only for a connector.
    """

    found: dict[str, str | None] = field(default_factory=dict)
    wrong: list[str] = field(default_factory=list)
    unlisted: dict[str, str] = field(default_factory=dict)


def _net(net: str | None) -> str:
    """Return a net as a message writes it: its name, or "unconnected"."""
    return "unconnected" if net is None else net


def compare(nl: Netlist, ref: str, row: dict[str, str | None]) -> PortReport:
    """Compare part ``ref`` with its row of the port map.

    ``row`` is ``{pad: net or None}`` as ``tables.read`` returns it (pads as text).
    """
    actual = found_nets(nl, ref)
    report = PortReport()
    for pad in sorted(row, key=natkey):
        got = actual.get(pad)
        report.found[pad] = got
        if got != row[pad]:
            report.wrong.append(
                f"pad {pad}: expected {_net(row[pad])}, found {_net(got)}"
            )
    if is_connector(nl, ref):
        report.unlisted = {
            pad: net
            for pad, net in actual.items()
            if net is not None and pad not in row
        }
    return report


def unlisted_connectors(nl: Netlist, port_map: dict[str, object]) -> list[str]:
    """Return the connectors of the design that are not keys of the port map."""
    return [ref for ref in connectors(nl) if ref not in port_map]


def port_map_found(nl: Netlist) -> dict[str, dict[str, str | None]]:
    """Return the port map as the design has it: every connector, every pad listed."""
    return {ref: found_nets(nl, ref) for ref in connectors(nl)}
