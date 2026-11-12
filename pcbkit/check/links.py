"""Enable links: each regulator on a shared enable net can be enabled alone.

Several regulators may share one enable net (``EN_ALL``). At bring-up a regulator can
be brought up alone only if it reaches the shared net through a link of its own, a part
that can be taken out without touching the others, and taking it out isolates it.

A *link* is a two-pad part that is

* a ``Device:R``, ``Device:R_Small``, ``Device:R_US`` or ``Device:R_Small_US`` whose
  value reads as 0 (``0``, ``0R``, ``0R0``, ``0E``, ``R0``, ``0 ohm``) or is a
  not-fitted mark alone (``DNP``, ``DNF``, ``NF``, ``NP``, ``NC``: the footprint is
  there to fit at bring-up), or
* a part from a library whose name starts with ``Jumper``.

A resistor-pack element and a three-pad jumper are not links (the walk still goes
through them).

The walk. From a net it crosses every part that can carry a current from one net to
another (``arcs``): any two-pad part but a capacitor, a diode (two pads named ``A``
and ``K``) one way only, every pad of a ``Jumper`` part to every other, a resistor
pack's elements by its real pinout, and every pad of a pack or network whose pinout is
not known to every other. It never crosses a supply or ground net: exactly the nets
the project lists in ``SUPPLY_NETS``, plus ``GROUND_NET`` when that is defined. Names
are not guessed: a supply that is not declared is walked, and a failure whose path
runs through a net no listed regulator's enable is on says how to declare it. A part
with three or more pads that is not a jumper or pack (a transistor, a gate, a
regulator) is not walked, so a path through an active part is not found.

The rule, for each ``(ref, pad)`` of the shared net being checked, with *targets* the
shared nets of every row of the table and the own enable nets of every other listed
regulator: exactly one link must be such that, with it taken out, no walk from the
regulator's own enable net reaches a target, while with it in place the own net reaches
its shared net. A walk counts in either direction of current (the target driving the
enable or the enable driving the target), so a diode is followed anode to cathode on
the way out and cathode to anode on the way in. On the regulator's side of that link
no 0 ohm resistor or jumper may tie a net to a supply: the link would then control
nothing.

``ENABLE_LINKS`` is ``{enable net: [(regulator ref, enable pad)]}``, pads as the
netlist writes them; ``SUPPLY_NETS`` is ``[net]``. The functions here compare them with
a ``Netlist``; they read nothing else and import no pytest, so ``pcbkit specs`` can
read ``TABLES``.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pcbkit.check.netlist import Netlist, natkey, parse_value
from pcbkit.check.tables import DictOf, ListOf, Net, Pad, Ref, Shape, Tuple

TABLES: dict[str, Shape] = {
    "ENABLE_LINKS": DictOf(
        Net("enable net"), ListOf(Tuple(Ref("regulator ref"), Pad("enable pad")))
    ),
    "SUPPLY_NETS": ListOf(Net("supply or ground net")),
}


# The resistor symbols a link may be: two pins, one resistor.
RESISTOR = re.compile(r"R(_Small)?(_US)?")
# A value that says "not fitted": the link's footprint is still there.
NOT_FITTED = ("dnp", "dnf", "nf", "np", "nc")
# Writing a zero with the R, E or ohm sign as the decimal point: 0R0, R0, 0E, 0 ohm.
ZERO_FORMS = re.compile(r"(\d*)(?:[reΩΩ]|ohms?)(\d*)", re.IGNORECASE)
# Resistor packs whose pinout is known: pad n with pad N+1-n, or adjacent pads.
PACK_FACING = re.compile(r"R_Pack(\d+)(_Split)?")
PACK_SIP = re.compile(r"R_Pack(\d+)_SIP")
# A network with one common pad (pad 1) and every other pad to it.
NETWORK_STAR = re.compile(r"R_Network(\d+)(_US)?")
# Other packs, networks and multi-pad resistors: every pad is taken as joined.
PACK_UNKNOWN = re.compile(r"R_(Pack|Network|Array|Pot|Trim)")
CAPACITOR = re.compile(r"CP?(_|$)")

# A path: net, part, net, part, ..., net.
Path = list[str]


def is_not_fitted(value: str) -> bool:
    """Return True for a value that is a not-fitted mark alone (``DNP``, ``NC``...)."""
    words = value.split()
    return len(words) == 1 and words[0].lower() in NOT_FITTED


def is_zero(value: str) -> bool:
    """Return True if a resistor value reads as 0 ohm, or is a not-fitted mark."""
    if is_not_fitted(value):
        return True
    words = value.split()
    if not words:
        return False
    word = words[0]
    try:
        return parse_value(word) == 0
    except ValueError:
        pass
    found = ZERO_FORMS.fullmatch(word)
    if not found or not (found.group(1) or found.group(2)):
        return False
    return not any(c in "123456789" for c in found.group(1) + found.group(2))


def is_jumper(nl: Netlist, ref: str) -> bool:
    """Return True for a part from a library whose name starts with ``Jumper``."""
    return str(nl.parts[ref]["lib"]).startswith("Jumper")


def is_link(nl: Netlist, ref: str) -> bool:
    """Return True for a two-pad 0 ohm or not-fitted resistor, or two-pad jumper."""
    part = nl.parts[ref]
    if len(nl.functions(ref)) != 2:
        return False
    if is_jumper(nl, ref):
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


def _all_pairs(pads: list[str]) -> list[tuple[str, str]]:
    """Return every ordered pair of two different pads."""
    return [(a, b) for a in pads for b in pads if a != b]


def _both_ways(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return each pair and its reverse."""
    return [arc for a, b in pairs for arc in ((a, b), (b, a))]


def arcs(nl: Netlist, ref: str) -> list[tuple[str, str]]:
    """Return ``(from pad, to pad)`` for each way current can pass through ``ref``.

    A two-pad part but a capacitor both ways, a diode (pads named ``A`` and ``K``)
    anode to cathode only; every pad of a ``Jumper`` part to every other; a
    ``Device:R_Pack<n>`` or ``R_Pack<n>_Split`` pad *k* with pad *2n+1-k*; an
    ``R_Pack<n>_SIP`` pads 1-2, 3-4...; an ``R_Network<n>`` every pad with pad 1; any
    other pack, network or multi-pad resistor every pad with every other. Other parts
    (three pads or more: transistors, gates, regulators) have none.
    """
    part = nl.parts[ref]
    lib, name = str(part["lib"]), str(part["part"])
    functions = nl.functions(ref)
    pads = sorted(functions, key=natkey)
    if lib.startswith("Jumper"):
        return _all_pairs(pads)
    if lib == "Device":
        facing = PACK_FACING.fullmatch(name)
        sip = PACK_SIP.fullmatch(name)
        star = NETWORK_STAR.fullmatch(name)
        numeric = all(p.isdigit() for p in pads)
        if facing and numeric:
            top = 2 * int(facing.group(1)) + 1
            return _both_ways(
                (p, str(top - int(p))) for p in pads if str(top - int(p)) in functions
            )
        if sip and numeric:
            return _both_ways(
                (p, str(int(p) + 1))
                for p in pads
                if int(p) % 2 == 1 and str(int(p) + 1) in functions
            )
        if star and "1" in functions:
            return _both_ways(("1", p) for p in pads if p != "1")
        if PACK_UNKNOWN.match(name):
            return _all_pairs(pads)
    if len(pads) != 2 or (lib == "Device" and CAPACITOR.match(name)):
        return []
    by_function = {f: p for p, f in functions.items()}
    if sorted(by_function) == ["A", "K"]:
        return [(by_function["A"], by_function["K"])]
    return _both_ways([(pads[0], pads[1])])


@dataclass(frozen=True)
class _Graph:
    """The steps of every part, as ``{net: [(part, to net)]}``, out and back."""

    forward: dict[str, list[tuple[str, str]]]
    backward: dict[str, list[tuple[str, str]]]


def _graph(nl: Netlist) -> _Graph:
    """Return the forward and backward steps of every part, net by net."""
    forward: dict[str, list[tuple[str, str]]] = {}
    backward: dict[str, list[tuple[str, str]]] = {}
    for ref in sorted(nl.parts, key=natkey):
        for a, b in arcs(nl, ref):
            here, there = nl.net(ref, a), nl.net(ref, b)
            if here is None or there is None or here == there:
                continue
            forward.setdefault(here, []).append((ref, there))
            backward.setdefault(there, []).append((ref, here))
    return _Graph(forward, backward)


def _walk(
    steps: Mapping[str, list[tuple[str, str]]],
    start: str,
    supplies: frozenset[str],
    removed: str | None,
) -> dict[str, tuple[str, str] | None]:
    """Return ``{net: (net before, part) or None}`` for each net reached from start.

    Breadth first, so the parent chain of a net is a shortest path to it. It does not
    go on from a supply net, nor through part ``removed``.
    """
    parents: dict[str, tuple[str, str] | None] = {start: None}
    queue = deque([start])
    while queue:
        net = queue.popleft()
        if net != start and net in supplies:
            continue
        for ref, other in steps.get(net, []):
            if ref != removed and other not in parents:
                parents[other] = (net, ref)
                queue.append(other)
    return parents


def _path(parents: Mapping[str, tuple[str, str] | None], net: str) -> Path:
    """Return the path from the walk's start to ``net``: net, part, net, ..."""
    path = [net]
    step = parents[net]
    while step is not None:
        before, ref = step
        path += [ref, before]
        step = parents[before]
    return path[::-1]


@dataclass(frozen=True)
class _Walker:
    """Walks one netlist from a regulator's own enable net, both ways."""

    graph: _Graph
    supplies: frozenset[str]

    def reached(self, start: str, removed: str | None) -> list[Path]:
        """Return a shortest path to every net reached, out first, then in."""
        out = _walk(self.graph.forward, start, self.supplies, removed)
        back = _walk(self.graph.backward, start, self.supplies, removed)
        paths = {net: _path(out, net) for net in out}
        for net in back:
            if net not in paths:
                paths[net] = _path(back, net)
        return sorted(paths.values(), key=len)

    def first(self, start: str, removed: str | None, targets: Iterable[str]) -> Path:
        """Return a shortest path to a target, or an empty list if none is reached."""
        wanted = set(targets) - {start}
        for path in self.reached(start, removed):
            if path[-1] in wanted:
                return path
        return []


@dataclass
class EnableReport:
    """What checking one shared enable net found.

    ``links`` maps each regulator to the link part it was found behind, or None; a
    regulator listed on two pads is keyed ``"U3 pad 4"`` and not ``"U3"``. ``problems``
    has one line for each thing wrong.
    """

    links: dict[str, str | None] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def supply_problems(nl: Netlist, supplies: Iterable[str]) -> list[str]:
    """Return one line for each net of ``SUPPLY_NETS`` that is not in the design."""
    problems = []
    for net in supplies:
        if net in nl.nets:
            continue
        line = f"specs.SUPPLY_NETS: {net} is not in the design"
        if net.startswith("/") and net.lstrip("/") in nl.nets:
            line += f" (net names carry no leading slash: write {net.lstrip('/')})"
        problems.append(line)
    return problems


def _hint(path: Path, enables: set[str]) -> str:
    """Return the advice to declare a supply for the nets a path passes through."""
    between = [n for n in path[2:-1:2] if n not in enables]
    if not between:
        return ""
    if len(between) == 1:
        return f"; if {between[0]} is a supply, add it to specs.SUPPLY_NETS"
    return f"; if any of {', '.join(between)} is a supply, add it to specs.SUPPLY_NETS"


def _show(path: Path) -> str:
    """Return a path as a message writes it: ``EN_U2 - R9 - EN_U3``."""
    return " - ".join(path)


def _three_pad_jumpers(nl: Netlist, refs: Iterable[str]) -> list[str]:
    """Return the jumpers among ``refs`` that have more than two pads, in order."""
    return [
        r for r in dict.fromkeys(refs) if is_jumper(nl, r) and len(nl.functions(r)) > 2
    ]


def _own_link(
    nl: Netlist,
    walker: _Walker,
    what: str,
    own: str,
    net: str,
    targets: Mapping[str, str],
    enables: set[str],
) -> tuple[str | None, list[str]]:
    """Return ``(link, problems)`` for one regulator whose own enable net is ``own``.

    ``targets`` maps every net the regulator must not reach once its link is out to
    what to call it; ``enables`` is every net the table names, for the hint.
    """
    whole = walker.reached(own, None)
    to_net = next((p for p in whole if p[-1] == net), None)
    if to_net is None:
        return None, [f"{what}: no 0 ohm resistor or jumper joins {own} to {net}"]
    region = {n for p in whole for n in p[::2]}
    candidates = sorted(
        {r for n in region for r in nl.refs_on(n) if is_link(nl, r)}, key=natkey
    )
    links = [r for r in candidates if not walker.first(own, r, targets)]
    if len(links) > 1:
        names = ", ".join(links[:-1]) + " and " + links[-1]
        return None, [
            f"{what}: {names} each isolate it when removed: a regulator needs "
            "exactly one link"
        ]
    if not links:
        direct = joiners(nl, own, net)
        shown = [r for r in direct if is_link(nl, r)]
        shown = shown or [r for r in to_net[1::2] if is_link(nl, r)]
        if shown:
            link = shown[0]
            path = walker.first(own, link, targets)
            return link, [
                f"{what}: removing {link} does not isolate it: {own} still reaches "
                f"{targets[path[-1]]} ({_show(path)}){_hint(path, enables)}"
            ]
        if direct:
            parts = ", ".join(f"{r} ({nl.parts[r]['value']})" for r in direct)
            note = f"{parts} joins them but is not a link"
        else:
            note = f"{_show(to_net)} joins them, with no link on it"
        three = _three_pad_jumpers(nl, [*to_net[1::2], *nl.refs_on(own)])
        note += "".join(f"; {j} has three pads: a link must have two" for j in three)
        return None, [
            f"{what}: no 0 ohm resistor or jumper joins {own} to {net} ({note})"
            + _hint(to_net, enables)
        ]
    link = links[0]
    problems = []
    for path in walker.reached(own, link):
        side = path[-1]
        if side in walker.supplies:
            continue
        for ref in sorted(nl.refs_on(side), key=natkey):
            if ref == link or not is_link(nl, ref):
                continue
            if is_not_fitted(str(nl.parts[ref]["value"])):
                continue
            for pad in nl.functions(ref):
                tied = nl.net(ref, pad)
                if tied in walker.supplies and tied != side:
                    problems.append(
                        f"{what}: {ref} ({nl.parts[ref]['value']}) ties {side} to "
                        f"{tied}, a net in specs.SUPPLY_NETS, so the link {link} "
                        "controls nothing"
                    )
    return link, problems


def check_enable_net(
    nl: Netlist,
    net: str,
    table: Mapping[str, list[tuple[str, str]]],
    supplies: Iterable[str],
    ground: str | None = None,
) -> EnableReport:
    """Check each ``(ref, pad)`` of shared enable net ``net`` has a link of its own.

    ``table`` is the whole ``ENABLE_LINKS``: the targets of every walk are the shared
    nets and listed enable nets of all its rows. ``supplies`` is ``SUPPLY_NETS`` and
    ``ground`` is ``GROUND_NET`` (or None): the walk crosses neither.
    """
    supplies = list(supplies)
    report = EnableReport(problems=supply_problems(nl, supplies))
    stop = frozenset([*supplies, *([ground] if ground else [])])
    if net not in nl.nets:
        report.problems.append(f"net {net} is not in the design")
    rows: list[tuple[str, str]] = []
    for row in table[net]:
        if row in rows:
            report.problems.append(f"{row[0]} pad {row[1]} is listed twice")
        else:
            rows.append(row)
    refs = [ref for ref, _ in rows]
    listed = {
        (ref, pad): nl.net(ref, pad)
        for row in table.values()
        for ref, pad in row
        if ref in nl.parts
    }
    enables = set(table) | {n for n in listed.values() if n is not None}
    walker = _Walker(_graph(nl), stop)
    used: dict[str, list[str]] = {}
    for ref, pad in rows:
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
        if own in stop:
            report.problems.append(
                f"{ref} pad {pad} is on {own}, a net in specs.SUPPLY_NETS: no link "
                "can switch it"
            )
            continue
        if net not in nl.nets:
            continue
        targets = {n: f"the shared net {n}" for n in table if n in nl.nets}
        for (other, _), other_net in listed.items():
            if other == ref or other_net in (None, own) or other_net in stop:
                continue
            targets.setdefault(other_net, f"the enable net {other_net} of {other}")
        targets.pop(own, None)
        what = f"{ref} pad {pad}"
        link, problems = _own_link(nl, walker, what, own, net, targets, enables)
        report.links[key] = link
        report.problems += problems
        if link is not None and not problems:
            used.setdefault(link, []).append(ref)
    for link, owners in sorted(used.items(), key=lambda kv: natkey(kv[0])):
        if len(owners) > 1:
            report.problems.append(
                f"{' and '.join(owners)} share the link {link}: each regulator "
                "needs its own"
            )
    return report
