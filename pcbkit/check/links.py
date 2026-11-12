"""Enable links: each regulator on a shared enable net can be enabled alone.

Several regulators may share one enable net (``EN_ALL``). At bring-up a regulator can
be brought up alone only if it reaches the shared net through a link of its own, a part
that can be taken out without touching the others, and taking it out isolates it.

A *link* is a two-pad part that is

* a resistor (reference letters ``R``, from any library) whose value reads as 0
  (``0``, ``0R``, ``0R0``, ``0E``, ``R0``, ``0 ohm``) or is a not-fitted mark alone
  (``DNP``, ``DNF``, ``NF``, ``NP``, ``NC``: the footprint is there to fit at
  bring-up), or
* a jumper: a part from a library whose name starts with ``Jumper`` (two pads, read
  from the symbol name, ``SolderJumper_2_Open``, when it gives one), or any two-pad
  part with reference letters ``JP`` (a header with a shunt).

The walk goes through every part by default: a part with two or more pads joins all
its pads to each other, from any library. The exceptions are a closed list (``arcs``):

* not walked: a part whose reference letters are ``U``, ``IC``, ``Q`` or ``A``
  (integrated circuits, transistors, modules; a ``U``-referenced resistor array or
  analogue switch too), a capacitor with two pads (reference letters ``C``), a
  connector as the port-map check defines it (``ports.is_connector``) and a test point;
* walked by its real pinout: the ``Device`` resistor packs (``R_Pack<n>`` and
  ``_Split`` pad *k* to pad *2n+1-k*, ``R_Pack<n>_SIP`` pads 1-2, 3-4...,
  ``R_Network<n>`` every pad to pad 1), a ``Switch:SW_DIP_x<n>`` (pad *k* to pad
  *2n+1-k*), and a two-pad diode whose pins are named ``A`` and ``K``, anode to
  cathode only. A diode with three or more pads is joined on all of them: pin names
  cannot say how a series pair is wired.

It never crosses a supply or ground net: exactly the nets the project lists in
``SUPPLY_NETS``, plus the ground net it is given. Names are not guessed: a supply that
is not declared is walked, and a failure whose path runs through a net no listed
regulator's enable is on says how to tell a rail from a real path (a name that looks
like a ground only changes the words of that hint).

The rule, for each ``(ref, pad)`` of the shared net being checked, with *targets* the
shared nets of every row of the table and the own enable nets of every other listed
regulator: exactly one link must be such that, with it taken out, no walk from the
regulator's own enable net reaches a target, while with it in place the own net reaches
its shared net. A walk counts in either direction of current (the target driving the
enable or the enable driving the target).

Ties: between the enable and that link, and on any net tied to those, nothing may tie
a net to a supply, or the link controls nothing. Everything walked with two pads ties
unless it is a resistor with a real value above 0 ohm, a diode, or not fitted (a
not-fitted value alone, or a jumper whose symbol name has ``_Open`` or ``_NO``). With
three or more pads only a fitted jumper (``_Bridged12``: on the pads its name gives)
and a 0 ohm element of a known pack tie.

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
# A DIP switch: element k joins pad k and pad 2n+1-k, like a facing pack.
DIP_SWITCH = re.compile(r"SW_DIP_x(\d+)")
# A network with one common pad (pad 1) and every other pad to it.
NETWORK_STAR = re.compile(r"R_Network(\d+)(_US)?")
# Reference letters of the parts the walk does not go through.
NOT_WALKED = ("U", "IC", "Q", "A")
# The pad count a jumper's symbol name gives (SolderJumper_3_Bridged12), and the pads
# a bridged jumper joins.
JUMPER_PADS = re.compile(r"(?:^|_)(\d+)(?:_|$)")
BRIDGED = re.compile(r"_Bridged(\d*)")
# Device resistor symbols with more than two pads: never a link, whatever the netlist
# shows of their pads.
MULTI_PAD_RESISTOR = re.compile(r"R_(Pack|Network|Array|Pot|Trim|Shunt)")
NUMBER_WORDS = {
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}

# Net names that read like a ground, for the hint only: no net is ever taken as a
# supply because of its name.
GROUND_LIKE = re.compile(r"0V|[ADPSE]?GND\w*|[ADP]?VSS\w*", re.IGNORECASE)

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


def _jumper_like(nl: Netlist, ref: str) -> bool:
    """Return True for a jumper: a ``Jumper`` library part or reference letters JP."""
    return is_jumper(nl, ref) or letters(ref) == "JP"


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
    """Return True for a two-pad 0 ohm or not-fitted resistor, or a two-pad jumper.

    A jumper is a part from a ``Jumper`` library or with reference letters ``JP`` (a
    header with a shunt), from any library.
    """
    if len(nl.functions(ref)) != 2 or pad_count(nl, ref) != 2:
        return False
    if _jumper_like(nl, ref):
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
    """Return the element pads of a known pack or DIP switch, or None for other parts.

    ``Device:R_Pack<n>`` and ``_Split``, and ``Switch:SW_DIP_x<n>``: pad k with pad
    2n+1-k; ``Device:R_Pack<n>_SIP``: pads 1-2, 3-4...; ``Device:R_Network<n>``: every
    pad with pad 1.
    """
    part = nl.parts[ref]
    lib, name = str(part["lib"]), str(part["part"])
    functions = nl.functions(ref)
    pads = sorted(functions, key=natkey)
    numeric = all(p.isdigit() for p in pads)
    facing = PACK_FACING.fullmatch(name) if lib == "Device" else None
    facing = facing or (DIP_SWITCH.fullmatch(name) if lib == "Switch" else None)
    if facing and numeric:
        top = 2 * int(facing.group(1)) + 1
        return [
            (p, str(top - int(p)))
            for p in pads
            if int(p) < top - int(p) and str(top - int(p)) in functions
        ]
    if lib != "Device":
        return None
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


def _not_walked(nl: Netlist, ref: str) -> str | None:
    """Return why ``ref`` is on the not-walked list, or None if it is walked."""
    found = letters(ref)
    if found in NOT_WALKED:
        return f"parts with reference letters {', '.join(NOT_WALKED)} are not"
    if found == "C" and len(nl.functions(ref)) == 2:
        return "two-pad capacitors are not"
    if _is_test_point(nl, ref):
        return "test points are not"
    if is_connector(nl, ref):
        return "connectors are not; a header used as a link takes the reference JP"
    return None


def _walk_rule(nl: Netlist, ref: str) -> tuple[list[tuple[str, str]], bool]:
    """Return ``ref``'s arcs, and True when they join all pads only by default."""
    functions = nl.functions(ref)
    pads = sorted(functions, key=natkey)
    if len(pads) < 2 or _not_walked(nl, ref) is not None:
        return [], False
    if _jumper_like(nl, ref):
        return _all_pairs(pads), False
    pack = _pack_pairs(nl, ref)
    if pack is not None:
        return _both_ways(pack), False
    if len(pads) == 2:
        by_name = {functions[p].upper(): p for p in pads}
        if sorted(by_name) == ["A", "K"]:
            return [(by_name["A"], by_name["K"])], False
        return _all_pairs(pads), False
    return _all_pairs(pads), True


def arcs(nl: Netlist, ref: str) -> list[tuple[str, str]]:
    """Return ``(from pad, to pad)`` for each way current can pass through ``ref``.

    Every pad to every other, unless ``ref`` is on the closed list: none for a part
    whose reference letters are ``U``, ``IC``, ``Q`` or ``A``, a two-pad capacitor
    (letters ``C``), a connector or a test point; a known pack or DIP switch by its
    elements; a two-pad diode (pads named ``A`` and ``K``) anode to cathode only.
    Jumpers join all their pads; a diode with three or more pads is joined on all of
    them, because pin names cannot say how a series pair is wired.
    """
    return _walk_rule(nl, ref)[0]


def joined_by_default(nl: Netlist, ref: str) -> bool:
    """Return True for a part of three or more pads joined on all of them by default.

    Its pinout is not known (it is not a jumper, a known pack or a DIP switch), so the
    walk takes every pad as joined to every other.
    """
    return _walk_rule(nl, ref)[1]


def _is_diode(nl: Netlist, ref: str) -> bool:
    """Return True for a two-pad diode: letters ``D`` or ``LED``, or pins A and K."""
    names = sorted(f.upper() for f in nl.functions(ref).values())
    return letters(ref).startswith(("D", "LED")) or names == ["A", "K"]


def _real_resistance(value: str) -> bool:
    """Return True for a value that reads as a resistance above 0 ohm."""
    words = value.split()
    if not words or is_zero(value):
        return False
    try:
        return parse_value(words[0]) > 0
    except ValueError:
        found = ZERO_FORMS.fullmatch(words[0])
        return bool(found and (found.group(1) or found.group(2)))


def tie_pairs(nl: Netlist, ref: str) -> list[tuple[str, str]]:
    """Return the pairs of pads ``ref`` ties together for the tie rule, both ways.

    Everything walked with two pads ties, unless it is a resistor (letters ``R``) with
    a real value above 0 ohm, a diode, or not fitted: a value that is a not-fitted mark
    alone, or a jumper whose symbol name has ``_Open`` or ``_NO``. A part with three or
    more pads ties only as a fitted jumper (a ``_Bridged`` one on the pads its name
    gives, ``Bridged12``; one that is neither open nor bridged on all its pads) or as
    a known pack whose value reads as 0, by its elements.
    """
    part = nl.parts[ref]
    value, name = str(part["value"]), str(part["part"])
    if is_not_fitted(value):
        return []
    pads = sorted(nl.functions(ref), key=natkey)
    if _jumper_like(nl, ref) and ("_Open" in name or "_NO" in name):
        return []
    if len(pads) == 2:
        if not arcs(nl, ref) or _is_diode(nl, ref):
            return []
        if letters(ref).startswith("R") and _real_resistance(value):
            return []
        return _all_pairs(pads)
    if _jumper_like(nl, ref):
        bridged = BRIDGED.search(name)
        if bridged and bridged.group(1):
            return _all_pairs([p for p in pads if p in bridged.group(1)])
        return _all_pairs(pads)
    pack = _pack_pairs(nl, ref)
    return _both_ways(pack) if pack and is_zero(value) else []


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


def _either(nets: list[str]) -> tuple[str, str]:
    """Return how a hint names ``nets``, and the word for what a signal would be."""
    if len(nets) == 1:
        return nets[0], "it is a signal"
    return f"any of {', '.join(nets)}", "they are signals"


def _notes(nl: Netlist, path: Path, enables: set[str]) -> str:
    """Return the notes a failing path ends with, each in brackets.

    A part joined on all its pads by default says so, and then no supply hint is
    given: the path is the part's, not a rail's. A walked part that looks like a
    connector says why it is walked. Otherwise each net between that no listed enable
    is on may be a rail: a name like a ground points at ``GROUND_NET``, any other at
    ``SUPPLY_NETS``. Names only choose the words: no net is taken as a supply by name.
    """
    notes = []
    parts = list(dict.fromkeys(path[1::2]))
    default = [r for r in parts if joined_by_default(nl, r)]
    for ref in default:
        count = len(nl.functions(ref))
        notes.append(
            f" ({ref} has {NUMBER_WORDS.get(count, count)} pads and is joined on all "
            "of them because its pinout is not known)"
        )
    for ref in parts:
        part = nl.parts[ref]
        text = f"{part['lib']}:{part['part']}".lower()
        if "conn" in text or "header" in text:
            notes.append(
                f" ({ref} is walked: only a part from a Connector library or with "
                "reference letters J is a connector)"
            )
    if default:
        return "".join(notes)
    between = [n for n in path[2:-1:2] if n not in enables]
    grounds = [n for n in between if GROUND_LIKE.fullmatch(n)]
    rails = [n for n in between if n not in grounds]
    if grounds:
        named, signal = _either(grounds)
        notes.append(
            f" (if {named} is a ground, set specs.GROUND_NET to it or declare it in "
            f"specs.SUPPLY_NETS; if {signal}, this path is real)"
        )
    if rails:
        named, signal = _either(rails)
        notes.append(
            f" (if {named} is a supply rail, declare it in specs.SUPPLY_NETS; if "
            f"{signal}, this path is real)"
        )
    return "".join(notes)


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
        line = f"{what}: no 0 ohm resistor or jumper joins {own} to {net}"
        skipped = [
            (r, _not_walked(nl, r))
            for r in sorted(nl.refs_on(own) & nl.refs_on(net), key=natkey)
            if _not_walked(nl, r) is not None
        ]
        if skipped:
            line += (
                " ("
                + "; ".join(
                    f"{r} is on both nets but is not walked: {why}"
                    for r, why in skipped
                )
                + ")"
            )
        return None, [line]
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
                f"{targets[path[-1]]} ({_show(path)}){_notes(nl, path, enables)}"
            ]
        if direct:
            note = "; ".join(
                f"{r} ({nl.parts[r]['value']}) joins them but is not a link: "
                + not_a_link(nl, r)
                for r in direct
            )
        else:
            note = f"{_show(to_net)} joins them, with no link on it"
            # a jumper or resistor with more pads than a link may have
            many = [
                r
                for r in dict.fromkeys([*to_net[1::2], *nl.refs_on(own)])
                if (is_jumper(nl, r) or letters(r).startswith("R"))
                and pad_count(nl, r) > 2
            ]
            note += "".join(
                f"; {r} has {NUMBER_WORDS.get(pad_count(nl, r), pad_count(nl, r))} "
                "pads: a link must have two"
                for r in many
            )
        return None, [
            f"{what}: no 0 ohm resistor or jumper joins {own} to {net} ({note})"
            + _notes(nl, to_net, enables)
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
