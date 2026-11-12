"""Enable links: each regulator on a shared enable net can be enabled alone.

Several regulators may share one enable net (``EN_ALL``). At bring-up a regulator can
be brought up alone only if its enable pin reaches the shared net through a link of its
own, a part that can be removed or left unfitted without touching the others. A link
is a two-pin part that joins the regulator's own enable net to the shared one:

* a ``Device:R`` whose value reads as 0 (``0``, ``0R``; a value ``parse_value`` cannot
  read is not a link), or
* a part from a library whose name starts with ``Jumper``.

``ENABLE_LINKS`` is ``{enable net: [(regulator ref, enable pad)]}``, pads as the
netlist writes them. The functions here compare it with a ``Netlist``; they read
nothing else and import no pytest, so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pcbkit.check.netlist import Netlist, natkey, parse_value
from pcbkit.check.tables import DictOf, ListOf, Net, Pad, Ref, Shape, Tuple

TABLES: dict[str, Shape] = {
    "ENABLE_LINKS": DictOf(
        Net("enable net"), ListOf(Tuple(Ref("regulator ref"), Pad("enable pad")))
    ),
}


def is_link(nl: Netlist, ref: str) -> bool:
    """Return True for a 0 ohm ``Device:R`` or a part from a ``Jumper`` library."""
    part = nl.parts[ref]
    if str(part["lib"]).startswith("Jumper"):
        return True
    if nl.kind(ref) != "Device:R":
        return False
    try:
        return parse_value(str(part["value"])) == 0
    except (ValueError, IndexError):  # parse_value: text that is not a value, or none
        return False


def joiners(nl: Netlist, a: str, b: str) -> list[str]:
    """Return the two-pin parts with one pin on net ``a`` and the other on ``b``."""
    found = []
    for ref in nl.refs_on(a) & nl.refs_on(b):
        pads = nl.functions(ref)
        if len(pads) == 2 and {nl.net(ref, pad) for pad in pads} == {a, b}:
            found.append(ref)
    return sorted(found, key=natkey)


@dataclass
class EnableReport:
    """What checking one shared enable net found.

    ``links`` maps each regulator to the link part it was found behind, or None; a
    regulator listed on two pads is keyed ``"U3 pad 4"`` and not ``"U3"``. ``problems``
    has one line for each thing wrong.
    """

    links: dict[str, str | None] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def check_enable_net(
    nl: Netlist, net: str, regulators: list[tuple[str, str]]
) -> EnableReport:
    """Check each ``(ref, pad)`` of shared enable net ``net`` has a link of its own."""
    report = EnableReport()
    if net not in nl.nets:
        report.problems.append(f"net {net} is not in the design")
    refs = [ref for ref, _ in regulators]
    used: dict[str, list[str]] = {}
    for ref, pad in regulators:
        key = ref if refs.count(ref) == 1 else f"{ref} pad {pad}"
        report.links[key] = None
        if ref not in nl.parts:
            report.problems.append(f"{ref} is not in the design")
            continue
        own = nl.net(ref, pad)
        if own is None:
            report.problems.append(f"{ref} pad {pad} is on no net")
            continue
        if own == net:
            report.problems.append(
                f"{ref} pad {pad} is wired straight to {net}: no link of its own"
            )
            continue
        if net not in nl.nets:
            continue
        found = joiners(nl, own, net)
        links = [r for r in found if is_link(nl, r)]
        if not links:
            others = ", ".join(f"{r} ({nl.parts[r]['value']})" for r in found)
            report.problems.append(
                f"{ref} pad {pad}: no 0 ohm resistor or jumper joins {own} to {net}"
                + (f" ({others} joins them but is not a link)" if found else "")
            )
        elif len(links) > 1:
            report.problems.append(
                f"{ref} pad {pad}: {len(links)} links ({', '.join(links)}) join "
                f"{own} to {net}, so removing one does not isolate it"
            )
        else:
            report.links[key] = links[0]
            used.setdefault(links[0], []).append(ref)
    for link, owners in sorted(used.items(), key=lambda kv: natkey(kv[0])):
        if len(owners) > 1:
            report.problems.append(
                f"{' and '.join(owners)} share the link {link}: each regulator "
                "needs its own"
            )
    return report
