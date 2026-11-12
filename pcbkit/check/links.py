"""Enable links: each regulator on a shared enable net can be enabled alone.

Several regulators may share one enable net (``EN_ALL``). At bring-up a regulator can
be brought up alone only if it reaches the shared net through a link of its own, a part
that can be taken out without touching the others, and taking it out isolates it.

A *link* is a two-pad part that is

* a resistor (reference letters ``R``, from any library) whose value reads as 0
  (``0``, ``0R``, ``0R0``, ``0E``, ``R0``, ``0 ohm``) or is a not-fitted mark alone
  (``DNP``, ``DNF``, ``NF``, ``NP``, ``NC``: the footprint is there to fit at
  bring-up), or
* a part from a library whose name starts with ``Jumper``, with two pads (read from
  the symbol name, ``SolderJumper_2_Open``, when it gives one).

The walk goes through every part by default: a part with two or more pads joins all
its pads to each other, from any library. The exceptions are a closed list (``arcs``):

* not walked: a part whose reference letters are ``U``, ``IC`` or ``Q`` (integrated
  circuits and transistors), a capacitor with two pads (reference letters ``C``), a
  connector as the port-map check defines it (``ports.is_connector``) and a test point;
* walked by its real pinout: the ``Device`` resistor packs (``R_Pack<n>`` and
  ``_Split`` pad *k* to pad *2n+1-k*, ``R_Pack<n>_SIP`` pads 1-2, 3-4...,
  ``R_Network<n>`` every pad to pad 1), and a diode, a part whose pads are all named
  ``A``/``A<n>`` or ``K``/``K<n>``: each anode pad to each cathode pad, in the
  direction of current.

It never crosses a supply or ground net: exactly the nets the project lists in
``SUPPLY_NETS``, plus the ground net it is given. Names are not guessed: a supply that
is not declared is walked, and a failure whose path runs through a net no listed
regulator's enable is on says how to tell a rail from a real path.

The rule, for each ``(ref, pad)`` of the shared net being checked, with *targets* the
shared nets of every row of the table and the own enable nets of every other listed
regulator: exactly one link must be such that, with it taken out, no walk from the
regulator's own enable net reaches a target, while with it in place the own net reaches
its shared net. A walk counts in either direction of current (the target driving the
enable or the enable driving the target). Between the enable and that link, and on any
net held to them by fitted 0 ohm parts, nothing fitted and 0 ohm may tie a net to a
supply: the link would then control nothing. For that rule a jumper whose symbol name
has ``_Open`` (or whose value is a not-fitted mark) is not fitted, a ``_Bridged`` one is
fitted on the pads its name gives (``Bridged12``), any other jumper is fitted, and a 0
ohm element of a known pack ties its two pads.

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
from pcbkit.check.ports import is_connector, letters
from pcbkit.check.tables import DictOf, ListOf, Net, Pad, Ref, Shape, Tuple

TABLES: dict[str, Shape] = {
    "ENABLE_LINKS": DictOf(
        Net("enable net"), ListOf(Tuple(Ref("regulator ref"), Pad("enable pad")))
    ),
    "SUPPLY_NETS": ListOf(Net("supply or ground net")),
}


# A value that says "not fitted": the link's footprint is still there.
NOT_FITTED = ("dnp", "dnf", "nf", "np", "nc")
# Writing a zero with the R, E or ohm sign as the decimal point: 0R0, R0, 0E, 0 ohm.
ZERO_FORMS = re.compile(r"(\d*)(?:[reΩΩ]|ohms?)(\d*)", re.IGNORECASE)
# Resistor packs whose pinout is known: pad n with pad N+1-n, or adjacent pads.
PACK_FACING = re.compile(r"R_Pack(\d+)(_Split)?")
PACK_SIP = re.compile(r"R_Pack(\d+)_SIP")
# A network with one common pad (pad 1) and every other pad to it.
NETWORK_STAR = re.compile(r"R_Network(\d+)(_US)?")
# The pin names of a diode's anodes and cathodes.
ANODE = re.compile(r"A\d*", re.IGNORECASE)
CATHODE = re.compile(r"K\d*", re.IGNORECASE)
# Reference letters of the parts the walk does not go through.
NOT_WALKED = ("U", "IC", "Q")
# The pad count a jumper's symbol name gives (SolderJumper_3_Bridged12), and the pads
# a bridged jumper joins.
JUMPER_PADS = re.compile(r"(?:^|_)(\d+)(?:_|$)")
BRIDGED = re.compile(r"_Bridged(\d*)")
# Device resistor symbols with more than two pads: never a link, whatever the netlist
# shows of their pads.
MULTI_PAD_RESISTOR = re.compile(r"R_(Pack|Network|Array|Pot|Trim|Shunt)")
NUMBER_WORDS = {3: "three", 4: "four", 5: "five", 6: "six"}

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


def pad_count(nl: Netlist, ref: str) -> int:
    """Return how many pads ``ref`` has: a jumper's symbol name says, else the netlist.

    A pad left off every net is missing from the netlist, so a three-pad jumper with
    one pad unused would otherwise look like a two-pad one.
    """
    found = JUMPER_PADS.search(str(nl.parts[ref]["part"]))
    if is_jumper(nl, ref) and found:
        return int(found.group(1))
    return len(nl.functions(ref))


def is_link(nl: Netlist, ref: str) -> bool:
    """Return True for a two-pad 0 ohm or not-fitted resistor, or two-pad jumper."""
    if len(nl.functions(ref)) != 2 or pad_count(nl, ref) != 2:
        return False
    if is_jumper(nl, ref):
        return True
    part = nl.parts[ref]
    if part["lib"] == "Device" and MULTI_PAD_RESISTOR.match(str(part["part"])):
        return False
    return letters(ref) == "R" and is_zero(str(part["value"]))


def not_a_link(nl: Netlist, ref: str) -> str:
    """Return why two-pad part ``ref`` is not a link."""
    if is_jumper(nl, ref):
        count = pad_count(nl, ref)
        return f"{ref} has {NUMBER_WORDS.get(count, count)} pads: a link must have two"
    part = nl.parts[ref]
    if part["lib"] == "Device" and MULTI_PAD_RESISTOR.match(str(part["part"])):
        return "it is a resistor pack, network or multi-pad resistor"
    if letters(ref) == "R":
        return "its value is not 0 ohm or a not-fitted mark"
    return "a link is a resistor (reference R) or a two-pad jumper"


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


def _is_test_point(nl: Netlist, ref: str) -> bool:
    """Return True for a test point: letters ``TP``, or ``TestPoint`` in its symbol."""
    part = nl.parts[ref]
    lib, name = str(part["lib"]).lower(), str(part["part"]).lower()
    return letters(ref) == "TP" or "testpoint" in lib or "testpoint" in name


def _pack_pairs(nl: Netlist, ref: str) -> list[tuple[str, str]] | None:
    """Return the element pads of a known ``Device`` pack, or None for other parts."""
    part = nl.parts[ref]
    if part["lib"] != "Device":
        return None
    name = str(part["part"])
    functions = nl.functions(ref)
    pads = sorted(functions, key=natkey)
    numeric = all(p.isdigit() for p in pads)
    facing = PACK_FACING.fullmatch(name)
    if facing and numeric:
        top = 2 * int(facing.group(1)) + 1
        return [
            (p, str(top - int(p)))
            for p in pads
            if int(p) < top - int(p) and str(top - int(p)) in functions
        ]
    sip = PACK_SIP.fullmatch(name)
    if sip and numeric:
        return [
            (p, str(int(p) + 1))
            for p in pads
            if int(p) % 2 == 1 and str(int(p) + 1) in functions
        ]
    if NETWORK_STAR.fullmatch(name) and "1" in functions:
        return [("1", p) for p in pads if p != "1"]
    return None


def arcs(nl: Netlist, ref: str) -> list[tuple[str, str]]:
    """Return ``(from pad, to pad)`` for each way current can pass through ``ref``.

    Every pad to every other, unless ``ref`` is on the closed list: none for a part
    whose reference letters are ``U``, ``IC`` or ``Q``, a two-pad capacitor (letters
    ``C``), a connector or a test point; a known ``Device`` resistor pack by its
    elements; a diode (every pad named ``A``/``A<n>`` or ``K``/``K<n>``) from each
    anode pad to each cathode pad only. Jumpers join all their pads.
    """
    functions = nl.functions(ref)
    pads = sorted(functions, key=natkey)
    if len(pads) < 2:
        return []
    found = letters(ref)
    if found in NOT_WALKED or (found == "C" and len(pads) == 2):
        return []
    if _is_test_point(nl, ref) or is_connector(nl, ref):
        return []
    if is_jumper(nl, ref):
        return _all_pairs(pads)
    pack = _pack_pairs(nl, ref)
    if pack is not None:
        return _both_ways(pack)
    anodes = [p for p in pads if ANODE.fullmatch(functions[p])]
    cathodes = [p for p in pads if CATHODE.fullmatch(functions[p])]
    if anodes and cathodes and len(anodes) + len(cathodes) == len(pads):
        return [(a, k) for a in anodes for k in cathodes]
    return _all_pairs(pads)


def tie_pairs(nl: Netlist, ref: str) -> list[tuple[str, str]]:
    """Return the pairs of pads a fitted 0 ohm part ties together, both ways.

    A two-pad resistor (letters ``R``) whose value reads as 0; a known pack whose value
    reads as 0, by its elements; a jumper that is fitted: not one whose symbol name has
    ``_Open`` or whose value is a not-fitted mark, a ``_Bridged`` one on the pads its
    name gives (``Bridged12``: pads 1 and 2; plain ``_Bridged``: all), any other on
    all its pads. A not-fitted value ties nothing.
    """
    part = nl.parts[ref]
    value, name = str(part["value"]), str(part["part"])
    if is_not_fitted(value):
        return []
    pads = sorted(nl.functions(ref), key=natkey)
    if is_jumper(nl, ref):
        if "_Open" in name:
            return []
        bridged = BRIDGED.search(name)
        if bridged and bridged.group(1):
            return _all_pairs([p for p in pads if p in bridged.group(1)])
        return _all_pairs(pads)
    if not is_zero(value):
        return []
    if letters(ref) == "R" and len(pads) == 2:
        return _all_pairs(pads)
    pack = _pack_pairs(nl, ref)
    return _both_ways(pack) if pack else []


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


def _path(parents: Mapping[str, tuple[str, str] | None], net: str) -> Path:
    """Return the path from the walk's start to ``net``: net, part, net, ..."""
    path = [net]
    step = parents[net]
    while step is not None:
        before, ref = step
        path += [ref, before]
        step = parents[before]
    return path[::-1]


@dataclass
class _Walker:
    """Walks one netlist from a regulator's own enable net, out and back.

    ``steps`` counts every part step looked at, for the tests that bound the search.
    """

    graph: _Graph
    supplies: frozenset[str]
    steps: int = 0

    def _walk(
        self,
        steps: Mapping[str, list[tuple[str, str]]],
        start: str,
        removed: str | None,
        wanted: set[str],
    ) -> tuple[dict[str, tuple[str, str] | None], str | None]:
        """Walk breadth first from ``start``; stop at the first net in ``wanted``.

        Return the parents of every net reached and the wanted net found, or None.
        The walk does not go on from a supply net, nor through part ``removed``.
        """
        parents: dict[str, tuple[str, str] | None] = {start: None}
        queue = deque([start])
        while queue:
            net = queue.popleft()
            if net != start and net in self.supplies:
                continue
            for ref, other in steps.get(net, []):
                self.steps += 1
                if ref == removed or other in parents:
                    continue
                parents[other] = (net, ref)
                if other in wanted:
                    return parents, other
                queue.append(other)
        return parents, None

    def first(self, start: str, removed: str | None, targets: Iterable[str]) -> Path:
        """Return a shortest path to a target, out or back, or [] if none is reached."""
        wanted = set(targets) - {start}
        if not wanted:
            return []
        found = []
        for steps in (self.graph.forward, self.graph.backward):
            parents, net = self._walk(steps, start, removed, wanted)
            if net is not None:
                found.append(_path(parents, net))
        return min(found, key=len) if found else []


@dataclass
class EnableReport:
    """What checking one shared enable net found.

    ``links`` maps each regulator to the link part it was found behind, or None; a
    regulator listed on two pads is keyed ``"U3 pad 4"`` and not ``"U3"``. ``problems``
    has one line for each thing wrong. ``steps`` is how many part steps the walks
    looked at, a measure of the work done.
    """

    links: dict[str, str | None] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    steps: int = 0


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
    """Return the note for the nets a path passes through that could be a supply."""
    between = [n for n in path[2:-1:2] if n not in enables]
    if not between:
        return ""
    if len(between) == 1:
        return (
            f" (if {between[0]} is a supply rail, declare it in specs.SUPPLY_NETS; "
            "if it is a signal, this path is real)"
        )
    return (
        f" (if any of {', '.join(between)} is a supply rail, declare it in "
        "specs.SUPPLY_NETS; if they are signals, this path is real)"
    )


def _show(path: Path) -> str:
    """Return a path as a message writes it: ``EN_U2 - R9 - EN_U3``."""
    return " - ".join(path)


def _ties(
    nl: Netlist, walker: _Walker, what: str, held: list[str], link: str
) -> list[str]:
    """Return a line for each fitted 0 ohm tie to a supply from the nets ``held``.

    ``held`` is the nets between the enable and its link; the nets fitted 0 ohm parts
    join to them are held too.
    """
    seen = set(held)
    queue = deque(held)
    problems: list[str] = []
    while queue:
        net = queue.popleft()
        for ref in sorted(nl.refs_on(net), key=natkey):
            if ref == link:
                continue
            for a, b in tie_pairs(nl, ref):
                here, there = nl.net(ref, a), nl.net(ref, b)
                if here != net or there is None or there in seen:
                    continue
                if there in walker.supplies:
                    line = (
                        f"{what}: {ref} ({nl.parts[ref]['value']}) ties {net} to "
                        f"{there}, a net in specs.SUPPLY_NETS, so the link {link} "
                        "controls nothing"
                    )
                    if line not in problems:
                        problems.append(line)
                else:
                    seen.add(there)
                    queue.append(there)
    return problems


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
    to_net = walker.first(own, None, {net})
    if not to_net:
        return None, [f"{what}: no 0 ohm resistor or jumper joins {own} to {net}"]
    # a link that isolates the regulator is on every path to its shared net, so on
    # this one
    candidates = list(dict.fromkeys(r for r in to_net[1::2] if is_link(nl, r)))
    links = [r for r in candidates if not walker.first(own, r, targets)]
    if len(links) > 1:
        names = ", ".join(links[:-1]) + " and " + links[-1]
        return None, [
            f"{what}: {names} each isolate it when removed: a regulator needs "
            "exactly one link"
        ]
    if not links:
        direct = joiners(nl, own, net)
        shown = [r for r in direct if is_link(nl, r)] or candidates
        if shown:
            link = shown[0]
            path = walker.first(own, link, targets)
            return link, [
                f"{what}: removing {link} does not isolate it: {own} still reaches "
                f"{targets[path[-1]]} ({_show(path)}){_hint(path, enables)}"
            ]
        if direct:
            note = "; ".join(
                f"{r} ({nl.parts[r]['value']}) joins them but is not a link: "
                + not_a_link(nl, r)
                for r in direct
            )
        else:
            note = f"{_show(to_net)} joins them, with no link on it"
            three = [
                r
                for r in dict.fromkeys([*to_net[1::2], *nl.refs_on(own)])
                if is_jumper(nl, r) and pad_count(nl, r) > 2
            ]
            note += "".join(f"; {not_a_link(nl, j)}" for j in three)
        return None, [
            f"{what}: no 0 ohm resistor or jumper joins {own} to {net} ({note})"
            + _hint(to_net, enables)
        ]
    link = links[0]
    held = to_net[: to_net.index(link)][::2]
    return link, _ties(nl, walker, what, held, link)


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
    ``ground`` the ground net (or None): the walk crosses neither.
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
            used.setdefault(link, []).append(key)
    for link, owners in sorted(used.items(), key=lambda kv: natkey(kv[0])):
        if len(owners) > 1:
            report.problems.append(
                f"{' and '.join(owners)} share the link {link}: each regulator "
                "needs its own"
            )
    report.steps = walker.steps
    return report
