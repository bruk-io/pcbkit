"""Enable links: each regulator on a shared enable net can be enabled alone.

Several regulators may share one enable net (``EN_ALL``). At bring-up a regulator can
be brought up alone only if its enable pin reaches the shared net through a link of its
own, a part that can be removed or left unfitted without touching the others. A link
is a two-pin part that joins the regulator's own enable net to the shared one:

* a two-pin ``Device:R``, ``Device:R_Small``, ``Device:R_US`` or ``Device:R_Small_US``
  whose value reads as 0 (``0``, ``0R``, ``0R0``, ``0E``, ``R0``, ``0 ohm``), or whose
  value is ``DNP`` or ``DNF`` (a link that is not fitted is still a link: the footprint
  is there to fit at bring-up), or
* a part from a library whose name starts with ``Jumper``.

A resistor-pack element is not a link, though the walk below goes through one.

Finding a link is not enough: with the link taken out, the regulator's enable net must
still reach neither the shared net nor another listed regulator's own enable net.
``bypass`` walks from the enable net through every other part that can carry a current
from one net to another: any two-pin part but a capacitor, every pad of a ``Jumper``
part, a resistor pack's elements (``Device:R_Pack*``: pad *n* to pad *N+1-n*) and a
``Device:R_Network*`` (every pad to pad 1). It does not cross a ground or supply net (a
pull-down or pull-up is fine): a net whose name starts with ``+`` or ``-``, or reads as
``GND``, ``VSS``, ``VIN``, ``VCC``, ``VDD``, ``VBUS``, ``VBAT``, ``VSYS``, ``VEE`` or
``VREF`` (with a letter before ``GND``/``VSS``, such as ``AGND``, or after the rest), or
that has a ground or supply pin on it (pin function ``GND``, ``VSS``, ``VIN``...).
Parts with three or more pins that are not packs or jumpers (transistors, gates,
regulators) are not walked through.

``ENABLE_LINKS`` is ``{enable net: [(regulator ref, enable pad)]}``, pads as the
netlist writes them. The functions here compare it with a ``Netlist``; they read
nothing else and import no pytest, so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from pcbkit.check.netlist import Netlist, natkey, parse_value
from pcbkit.check.tables import DictOf, ListOf, Net, Pad, Ref, Shape, Tuple

TABLES: dict[str, Shape] = {
    "ENABLE_LINKS": DictOf(
        Net("enable net"), ListOf(Tuple(Ref("regulator ref"), Pad("enable pad")))
    ),
}


# The resistor symbols a link may be: two pins, one resistor.
RESISTOR = re.compile(r"R(_Small)?(_US)?")
# A value that says "not fitted": the link's footprint is still there.
NOT_FITTED = ("dnp", "dnf")
# Writing a zero with the R, E or ohm sign as the decimal point: 0R0, R0, 0E, 0 ohm.
ZERO_FORMS = re.compile(r"(\d*)(?:[re\u03a9\u2126]|ohms?)(\d*)", re.IGNORECASE)
# Names of the nets that are ground or a supply, and of the pin functions on them.
RAIL_NET = re.compile(
    r"(?i)[+-].*|[adps]?gnd\w*|[adp]?vss\w*|v(?:in|cc|dd|bus|bat|sys|ee|ref)\w*"
)
RAIL_PIN = re.compile(
    r"(?i)[adps]?gnd\w*|[adp]?vss\w*|v(?:in|cc|dd|bus|bat|sys|ee|ref)\w*"
)


def is_zero(value: str) -> bool:
    """Return True if a resistor value reads as 0 ohm, or as DNP or DNF."""
    words = value.split()
    if not words:
        return False
    word = words[0]
    if word.lower() in NOT_FITTED and len(words) == 1:
        return True
    try:
        return parse_value(word) == 0
    except ValueError:
        pass
    found = ZERO_FORMS.fullmatch(word)
    if not found or not (found.group(1) or found.group(2)):
        return False
    return not any(c in "123456789" for c in found.group(1) + found.group(2))


def is_link(nl: Netlist, ref: str) -> bool:
    """Return True for a 0 ohm or DNP two-pin resistor, or a ``Jumper`` part."""
    part = nl.parts[ref]
    if str(part["lib"]).startswith("Jumper"):
        return True
    if part["lib"] != "Device" or not RESISTOR.fullmatch(str(part["part"])):
        return False
    return is_zero(str(part["value"]))


def joiners(nl: Netlist, a: str, b: str) -> list[str]:
    """Return the two-pin parts with one pin on net ``a`` and the other on ``b``."""
    found = []
    for ref in nl.refs_on(a) & nl.refs_on(b):
        pads = nl.functions(ref)
        if len(pads) == 2 and {nl.net(ref, pad) for pad in pads} == {a, b}:
            found.append(ref)
    return sorted(found, key=natkey)


def is_rail(nl: Netlist, net: str) -> bool:
    """Return True for a ground or supply net: the walk does not cross it."""
    if RAIL_NET.fullmatch(net):
        return True
    return any(RAIL_PIN.fullmatch(func) for _, _, func in nl.nets.get(net, []))


def _numeric(pads: dict[str, str]) -> list[int] | None:
    """Return the pad numbers when every pad is a whole number, else None."""
    return [int(p) for p in pads] if all(p.isdigit() for p in pads) else None


def conducting_pairs(nl: Netlist, ref: str) -> list[tuple[str, str]]:
    """Return the pairs of pads of ``ref`` that can carry current between them.

    A two-pin part but a capacitor; every pad of a ``Jumper`` part to every other; the
    elements of a ``Device:R_Pack*`` (pad n to pad N+1-n, N the highest pad); every pad
    of a ``Device:R_Network*`` to pad 1. Other parts have none.
    """
    part = nl.parts[ref]
    lib, name = str(part["lib"]), str(part["part"])
    pads = sorted(nl.functions(ref), key=natkey)
    if lib.startswith("Jumper"):
        return [(a, b) for i, a in enumerate(pads) for b in pads[i + 1 :]]
    if lib == "Device" and name.startswith("R_Pack"):
        numbers = _numeric(nl.functions(ref))
        top = max(numbers) if numbers else 0
        return [
            (str(n), str(top + 1 - n)) for n in sorted(numbers or []) if n < top + 1 - n
        ]
    if lib == "Device" and name.startswith("R_Network"):
        return [("1", p) for p in pads if p != "1" and "1" in pads]
    if len(pads) == 2 and not (lib == "Device" and re.match(r"CP?(_|$)", name)):
        return [(pads[0], pads[1])]
    return []


def bypass(
    nl: Netlist, link: str, start: str, targets: dict[str, str]
) -> tuple[str, list[str]] | None:
    """Walk from net ``start`` without ``link``; return the first target reached.

    ``targets`` maps the nets that must not be reached to what to call each. The result
    is ``(target net, path)``, the path alternating net, part, net, shortest first. The
    walk crosses every part ``conducting_pairs`` gives, and stops at a ground or supply
    net unless it is a target.
    """
    parents: dict[str, tuple[str, str] | None] = {start: None}
    queue = deque([start])
    while queue:
        net = queue.popleft()
        if net != start and net in targets:
            path = [net]
            while parents[path[-1]] is not None:
                before, ref = parents[path[-1]]  # type: ignore[misc]
                path += [ref, before]
            return net, path[::-1]
        if net != start and is_rail(nl, net):
            continue
        for ref in sorted(nl.refs_on(net), key=natkey):
            if ref == link:
                continue
            for a, b in conducting_pairs(nl, ref):
                for here, there in ((a, b), (b, a)):
                    if nl.net(ref, here) != net:
                        continue
                    other = nl.net(ref, there)
                    if other is not None and other not in parents:
                        parents[other] = (net, ref)
                        queue.append(other)
    return None


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
    rows: list[tuple[str, str]] = []
    for row in regulators:
        if row in rows:
            report.problems.append(f"{row[0]} pad {row[1]} is listed twice")
        else:
            rows.append(row)
    regulators = rows
    refs = [ref for ref, _ in regulators]
    owns = {(ref, pad): nl.net(ref, pad) for ref, pad in regulators if ref in nl.parts}
    used: dict[str, list[str]] = {}
    walks: list[tuple[str, str, str, str]] = []  # key, ref, own net, link
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
            walks.append((f"{ref} pad {pad}", ref, own, links[0]))
    for what, ref, own, link in walks:
        targets = {net: f"the shared net {net}"}
        for (other, _), other_net in owns.items():
            if other != ref and other_net not in (None, own, net):
                targets[other_net] = f"the enable net {other_net} of {other}"
        found = bypass(nl, link, own, targets)
        if found is not None:
            reached, path = found
            report.problems.append(
                f"{what}: removing {link} does not isolate it: {own} still reaches "
                f"{targets[reached]} ({' - '.join(path)})"
            )
    for link, owners in sorted(used.items(), key=lambda kv: natkey(kv[0])):
        if len(owners) > 1:
            report.problems.append(
                f"{' and '.join(owners)} share the link {link}: each regulator "
                "needs its own"
            )
    return report
