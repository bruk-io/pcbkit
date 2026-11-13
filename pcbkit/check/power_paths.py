"""Power paths: which judged rails can feed which, by DC conduction through the netlist.

The review question "can source X power rail Y, and can anything back-feed?" has a DC
conduction part that the netlist answers. ``POWER_PATHS`` names the judged rails
(``nets``), the paths the design means (``paths``: ``(from net, to net, [refs])``), the
parts whose conduction the designer states (``parts``: ``{ref: [(in pad, out pad)]}``,
``[]`` for "passes no power") and the most a path may resist and still count as a power
path (``max_ohm``, default 10).

The judged nets are exactly ``POWER_PATHS["nets"]``, and they must be exactly the nets
of ``SUPPLY_NETS`` other than the ground net (``GROUND_NET``, or ``GND`` when that is
not defined and the design has it). The two tables cannot then disagree silently: a
rail the enable-link walk stops at is a rail judged here, and the reverse. The ground
net is never judged and never crossed: it is the return of every path, and current
that reaches it does not come back out into a rail at a higher voltage.

How each part conducts, from any library (fail safe: whatever is not on the closed
lists below conducts, so a part the check does not understand can only add a path):

* declared in ``parts``: exactly the arcs given, in pad to out pad, at 0 ohm; this
  overrides every rule below, and the designer is trusted;
* not walked, and so a part that fails ``check_modelled`` when it reaches two judged
  nets: reference letters ``U``, ``IC`` or ``A`` (ICs and modules), a connector (as the
  port map defines it), a test point;
* open at DC and never named: a two-pad capacitor (reference letters ``C``);
* a FET (pins that read as one FET, G, D and S): drain to source both ways at 0 ohm,
  whatever its gate does (the body diode is inside that), the gate joined to nothing;
* any other part with reference letters ``Q`` (a bipolar transistor, a dual FET): every
  pad to every other at 0 ohm;
* everything else as the enable-link walk goes through it (``links.arcs``): resistor
  packs and DIP switches by their pinout, a two-pad diode with pins ``A`` and ``K``
  from anode to cathode only, every other part every pad to every other.

A resistor (reference letters ``R`` with two pads, or a ``Device`` ``R``, ``R_Small``,
``R_US``, ``R_Small_US``, ``R_Pack*``, ``R_Network*`` or ``R_Array*`` symbol) weighs its
value; every other part 0 ohm. A value that does not read as ohms counts as 0 ohm, and
a not-fitted mark counts as fitted (the netlist does not say which builds fit it), each
named in a message whose path it is on.

A path runs from a judged net through parts, never through another judged net or the
ground net, to a judged net, in the direction current can flow. Its resistance is the
effective resistance of every such route between the two nets, taken as resistors in
parallel and series (a diode's direction is used to find the routes, not inside them,
which can only lower the figure). It is a power path when that is at most ``max_ohm``.

The functions here read a ``Netlist`` and the tables and nothing else, and import no
pytest, so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pcbkit.check import links, semis
from pcbkit.check.netlist import Netlist, natkey, parse_value
from pcbkit.check.ports import is_connector, letters
from pcbkit.check.tables import (
    DictOf,
    ListOf,
    Net,
    Ohms,
    Opt,
    Pad,
    Record,
    Ref,
    Shape,
    Tuple,
)

TABLES: dict[str, Shape] = {
    "POWER_PATHS": Record(
        required={
            "nets": ListOf(Net()),
            "paths": ListOf(Tuple(Net("from net"), Net("to net"), ListOf(Ref()))),
        },
        optional={
            "parts": Opt(
                DictOf(Ref(), ListOf(Tuple(Pad("in pad"), Pad("out pad")))), default={}
            ),
            "max_ohm": Opt(Ohms(positive=True), default=10),
        },
    ),
}

# How a part is walked.
DECLARED = "declared"
NOT_WALKED = "not walked"
CAPACITOR = "capacitor"
FET = "fet"
DEFAULT = "joined by default"
WALKED = "walked"

# Reference letters of the parts that are not walked here: ICs and modules. A
# transistor (Q) is walked, by its pinout when it reads as one FET.
IC_LETTERS = ("U", "IC", "A")
# Device symbols that are resistors whose value is their resistance (per element).
RESISTOR_SYMBOL = re.compile(r"R(_Small)?(_US)?|R_(Pack|Network|Array)\w*")
# A resistance written with a multiplier or R/E as the decimal point: 4K7, 10K, 4R7.
_OHMS = re.compile(
    r"(\d+(?:\.\d+)?|\.\d+)?([pnumµkKMRrEeΩ]?)(\d*)(?:[oO]hms?|Ω)?(?:[_/@%,;(].*)?"
)
_SCALE = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
}
# Below this a resistance is a short: nets joined by it are merged before the solve.
SHORT = 1e-9
PATHS_KEY = 'POWER_PATHS["paths"]'
NETS_KEY = 'POWER_PATHS["nets"]'
PARTS_KEY = 'POWER_PATHS["parts"]'
UNCONNECTED = "unconnected-("


@dataclass(frozen=True)
class Arc:
    """One way current can pass through part ``ref``: from net ``src`` to ``dst``.

    ``pads`` are the two pads of the element it runs through; an element that
    conducts both ways has an arc each way, and counts once as a resistor.
    """

    ref: str
    src: str
    dst: str
    ohms: float
    pads: frozenset[str] = frozenset()


@dataclass
class Model:
    """How every part of a netlist conducts, as arcs between nets.

    ``kinds`` says how each part is walked (``DECLARED``, ``NOT_WALKED``,
    ``CAPACITOR``, ``FET``, ``DEFAULT`` or ``WALKED``), ``why`` why a part is not
    walked, and ``notes`` what a message whose path runs through a part must say about
    it (a value read as 0 ohm, a FET's channel, a part joined on all its pads).
    ``stops`` is the judged nets and the ground net: no path goes through one.
    """

    judged: list[str]
    stops: frozenset[str]
    max_ohm: float
    arcs: list[Arc] = field(default_factory=list)
    forward: dict[str, list[Arc]] = field(default_factory=dict)
    backward: dict[str, list[Arc]] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)


@dataclass
class Route:
    """The cheapest path between two nets and the effective resistance between them.

    ``path`` is net, part, net, ..., net; ``path_ohms`` its own resistance; ``ohms``
    the effective resistance of every route between the nets (at most ``path_ohms``).
    """

    path: list[str]
    path_ohms: float
    ohms: float


@dataclass
class Report:
    """What one check found: what it records and one line per failure."""

    numbers: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


# --- values ---------------------------------------------------------------------


def read_ohms(value: str) -> float | None:
    """Return a resistor value in ohms, or None if it does not read as one.

    Only the first word counts; in ``4x10k`` the part after the ``x`` is the value.
    Read: ``2.2k``, ``4k7``, ``10K``, ``4R7``, ``R47``, ``0R``, ``0E``, ``10kohm``,
    ``100K_0402``, ``1M`` (mega) and ``1m`` (milli). Not read: ``x``, ``R``, ``DNP``.
    """
    words = value.split()
    if not words:
        return None
    word = words[0]
    if "x" in word:
        word = word.split("x", 1)[1]
    try:
        return parse_value(word)
    except ValueError:
        pass
    found = _OHMS.fullmatch(word)
    if not found:
        return None
    whole, mark, frac = found.group(1), found.group(2), found.group(3)
    if not whole and not (mark and frac):
        return None
    if frac and whole and "." in whole:
        return None
    number = float(f"{whole or 0}.{frac}" if frac else (whole or "0"))
    return number * _SCALE.get(mark, 1.0)


def _is_resistor(nl: Netlist, ref: str) -> bool:
    """Return True for a part whose value is its resistance (per element of a pack)."""
    part = nl.parts[ref]
    if part["lib"] == "Device" and RESISTOR_SYMBOL.fullmatch(str(part["part"])):
        return True
    return letters(ref) == "R" and len(nl.functions(ref)) == 2


def _weight(nl: Netlist, ref: str) -> tuple[float, str | None]:
    """Return the ohms each element of ``ref`` counts as, and a note when guessed."""
    if not _is_resistor(nl, ref):
        return 0.0, None
    value = str(nl.parts[ref]["value"])
    if links.is_not_fitted(value):
        return 0.0, (
            f"{ref} is marked not fitted ({value}) and counts as fitted at 0 ohm: the "
            "netlist does not say which builds fit it"
        )
    words = value.split()
    ohms = read_ohms(value)
    note = None
    if len(words) > 1 and words[1].lower() in links.NOT_FITTED:
        note = (
            f"{ref} is marked not fitted ({value}) and counts as fitted: the netlist "
            "does not say which builds fit it"
        )
    if ohms is None:
        return 0.0, (
            f"{ref}'s value {value!r} does not read as ohms, so it counts as 0 ohm"
        )
    return ohms, note


# --- the model ------------------------------------------------------------------


def _pads_text(count: int) -> str:
    """Return "five pads" and the like."""
    return f"{links.NUMBER_WORDS.get(count, count)} pads"


def _walk(
    nl: Netlist, ref: str, declared: Mapping[str, Any]
) -> tuple[str, list[tuple[str, str, float]], str | None]:
    """Return how ``ref`` is walked, its ``(from pad, to pad, ohms)`` arcs, a note."""
    pads = sorted(nl.functions(ref), key=natkey)
    if ref in declared:
        return DECLARED, [(str(a), str(b), 0.0) for a, b in declared[ref]], None
    found = letters(ref)
    if found in IC_LETTERS:
        return NOT_WALKED, [], None
    if is_connector(nl, ref) or links.is_test_point(nl, ref):
        return NOT_WALKED, [], None
    if found == "C" and len(pads) == 2:
        return CAPACITOR, [], None
    pins = semis.read_pins(nl, ref)
    if pins.kind == semis.FET:
        d, s = pins.pads["d"], pins.pads["s"]
        note = (
            f"{ref} is a FET: its channel counts as conducting both ways, whatever its "
            "gate does"
        )
        return FET, [(d, s, 0.0), (s, d, 0.0)], note
    ohms, note = _weight(nl, ref)
    if found == "Q":
        pairs = [(a, b) for a in pads for b in pads if a != b]
        default = len(pads) > 2
    else:
        pairs = links.arcs(nl, ref)
        default = links.joined_by_default(nl, ref)
    if default:
        joined = (
            f"{ref} has {_pads_text(len(pads))} and is joined on all of them because "
            f"its pinout is not known: declare what it passes in {PARTS_KEY}"
        )
        note = joined if note is None else f"{note}; {joined}"
    return (DEFAULT if default else WALKED), [(a, b, ohms) for a, b in pairs], note


def build_model(
    nl: Netlist, table: Mapping[str, Any], ground: str | None = None
) -> Model:
    """Return how every part of ``nl`` conducts under ``POWER_PATHS`` ``table``."""
    judged = list(dict.fromkeys(table["nets"]))
    stops = frozenset([*judged, *([ground] if ground else [])])
    out = Model(judged=judged, stops=stops, max_ohm=float(table.get("max_ohm", 10)))
    declared = table.get("parts", {})
    for ref in sorted(nl.parts, key=natkey):
        kind, pairs, note = _walk(nl, ref, declared)
        out.kinds[ref] = kind
        if note:
            out.notes[ref] = note
        if kind == NOT_WALKED:
            out.why[ref] = _why_not_walked(nl, ref)
        for a, b, ohms in pairs:
            here, there = nl.net(ref, a), nl.net(ref, b)
            if here is None or there is None or here == there:
                continue
            arc = Arc(ref, here, there, float(ohms), frozenset((a, b)))
            out.arcs.append(arc)
            out.forward.setdefault(here, []).append(arc)
            out.backward.setdefault(there, []).append(arc)
    return out


def _why_not_walked(nl: Netlist, ref: str) -> str:
    """Return why ``ref`` is not walked, for a message."""
    if letters(ref) in IC_LETTERS:
        return "an IC or module (reference letters U, IC or A) is not walked"
    if links.is_test_point(nl, ref):
        return "a test point is not walked"
    return (
        "a connector is not walked: it joins nothing on the board, but what is "
        "plugged into it can"
    )


# --- searching ------------------------------------------------------------------


def _search(
    model: Model,
    start: str,
    removed: Iterable[str] = (),
    backward: bool = False,
    both: bool = False,
) -> tuple[dict[str, float], dict[str, tuple[str, Arc] | None]]:
    """Return the least resistance to each net from ``start``, and how it was reached.

    Forward follows arcs in the direction of current, ``backward`` against it (the nets
    that can feed ``start``), ``both`` either way. The search does not go on from a
    stop net other than ``start``, nor through the parts ``removed``.
    """
    gone = set(removed)
    steps: list[Mapping[str, list[Arc]]] = []
    if both or not backward:
        steps.append(model.forward)
    if both or backward:
        steps.append(model.backward)
    dist: dict[str, float] = {start: 0.0}
    parent: dict[str, tuple[str, Arc] | None] = {start: None}
    heap: list[tuple[float, int, str]] = [(0.0, 0, start)]
    count = 0
    done: set[str] = set()
    while heap:
        cost, _, net = heapq.heappop(heap)
        if net in done:
            continue
        done.add(net)
        if net != start and net in model.stops:
            continue
        for adjacency in steps:
            for arc in adjacency.get(net, []):
                if arc.ref in gone:
                    continue
                other = arc.dst if arc.src == net else arc.src
                new = cost + arc.ohms
                if other not in dist or new < dist[other] - SHORT:
                    dist[other] = new
                    parent[other] = (net, arc)
                    count += 1
                    heapq.heappush(heap, (new, count, other))
    return dist, parent


def _path_to(parents: Mapping[str, tuple[str, Arc] | None], net: str) -> list[str]:
    """Return the path from the search's start to ``net``: net, part, net, ..."""
    path = [net]
    step = parents[net]
    while step is not None:
        before, arc = step
        path += [arc.ref, before]
        step = parents[before]
    return path[::-1]


def _ohms_to(
    arcs: Iterable[Arc], target: str, wanted: Iterable[str]
) -> dict[str, float]:
    """Return the effective resistance from each net in ``wanted`` to ``target``.

    Each arc is a resistor of its ohms, whatever its direction; arcs under ``SHORT``
    merge their nets. A wanted net the arcs do not join to ``target`` is left out.
    """
    # numpy and scipy only where a solve is needed: reading the tables needs neither
    import numpy as np
    import scipy.sparse as sp
    from scipy.sparse.linalg import splu

    # an element is one resistor, however many ways it conducts
    arcs = list({(arc.ref, arc.pads): arc for arc in arcs}.values())
    wanted = list(dict.fromkeys(wanted))
    group: dict[str, str] = {}

    def find(net: str) -> str:
        group.setdefault(net, net)
        while group[net] != net:
            group[net] = group[group[net]]
            net = group[net]
        return net

    for net in [target, *wanted]:
        find(net)
    for arc in arcs:
        if arc.ohms <= SHORT:
            group[find(arc.src)] = find(arc.dst)
    root = find(target)
    links_of: dict[str, list[str]] = {}
    for arc in arcs:
        i, j = find(arc.src), find(arc.dst)
        if i != j:
            links_of.setdefault(i, []).append(j)
            links_of.setdefault(j, []).append(i)
    seen = {root}
    todo = [root]
    while todo:
        for other in links_of.get(todo.pop(), []):
            if other not in seen:
                seen.add(other)
                todo.append(other)
    out = {net: 0.0 for net in wanted if find(net) == root}
    nodes = sorted(seen - {root}, key=natkey)
    asked = [net for net in wanted if find(net) in seen and find(net) != root]
    if not asked:
        return out
    index = {n: i for i, n in enumerate(nodes)}
    diag = np.zeros(len(nodes))
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for arc in arcs:
        i, j = find(arc.src), find(arc.dst)
        if i == j or i not in seen:
            continue
        g = 1.0 / arc.ohms
        for here, there in ((i, j), (j, i)):
            if here != root:
                diag[index[here]] += g
                if there != root:
                    rows.append(index[here])
                    cols.append(index[there])
                    vals.append(-g)
    size = len(nodes)
    matrix = sp.csc_matrix((vals, (rows, cols)), shape=(size, size)) + sp.diags(diag)
    classes = list(dict.fromkeys(find(net) for net in asked))
    rhs = np.zeros((size, len(classes)))
    for k, c in enumerate(classes):
        rhs[index[c], k] = 1.0
    volts = splu(sp.csc_matrix(matrix)).solve(rhs)
    by_class = {c: float(volts[index[c], k]) for k, c in enumerate(classes)}
    out.update({net: by_class[find(net)] for net in asked})
    return out


def route(model: Model, x: str, y: str, removed: Iterable[str] = ()) -> Route | None:
    """Return the cheapest path from judged net ``x`` to ``y`` and the effective ohms.

    None when current cannot flow from ``x`` to ``y`` without crossing another judged
    net or the ground net (or through the parts ``removed``).
    """
    gone = list(removed)
    ahead, parents = _search(model, x, gone)
    if y not in ahead or x == y:
        return None
    behind, _ = _search(model, y, gone, backward=True)
    path = _path_to(parents, y)

    def inner(net: str) -> bool:
        return net not in model.stops

    used = [
        arc
        for arc in model.arcs
        if arc.ref not in gone
        and (arc.src == x or (inner(arc.src) and arc.src in ahead))
        and (arc.dst == y or (inner(arc.dst) and arc.dst in behind))
    ]
    ohms = _ohms_to(used, y, [x]).get(x)
    found = ahead[y] if ohms is None else min(ohms, ahead[y])
    return Route(path, ahead[y], found)


def _cheapest_through(model: Model, x: str, y: str, ref: str) -> float | None:
    """Return the ohms of the cheapest path from ``x`` to ``y`` through ``ref``."""
    ahead, _ = _search(model, x)
    behind, _ = _search(model, y, backward=True)
    best = None
    for arc in model.arcs:
        if arc.ref != ref:
            continue
        if arc.src != x and (arc.src in model.stops or arc.src not in ahead):
            continue
        if arc.dst != y and (arc.dst in model.stops or arc.dst not in behind):
            continue
        cost = ahead[arc.src] + arc.ohms + behind[arc.dst]
        best = cost if best is None else min(best, cost)
    return best


# --- messages -------------------------------------------------------------------


def show(path: list[str]) -> str:
    """Return a path as a message writes it: ``VIN - U3 - +5V``."""
    return " - ".join(path)


def ohm_text(value: float) -> str:
    """Return ``0.1 ohm``, ``4.7e+03 ohm`` and the like."""
    return f"{value:.3g} ohm"


def _notes(model: Model, path: list[str], found: Route | None = None) -> str:
    """Return the notes a message about ``path`` ends with, each in brackets."""
    notes = [model.notes[r] for r in dict.fromkeys(path[1::2]) if r in model.notes]
    if found is not None and found.ohms < found.path_ohms - 1e-6:
        notes.append(
            f"routes in parallel join the nets at {ohm_text(found.ohms)}, though this "
            f"path alone is {ohm_text(found.path_ohms)}"
        )
    between = path[2:-1:2]
    if between:
        nets = ", ".join(dict.fromkeys(between))
        notes.append(
            f"{nets} {'is' if len(set(between)) == 1 else 'are'} walked as a signal: a "
            f"rail goes in specs.SUPPLY_NETS and {NETS_KEY}, and a path stops there"
        )
    return "".join(f" ({n})" for n in notes)


def _row_text(x: str, y: str, refs: Iterable[str]) -> str:
    """Return a paths row as it is written: ``("VIN", "+5V", ["U3"])``."""
    shown = ", ".join(f'"{r}"' for r in refs)
    return f'("{x}", "{y}", [{shown}])'


def _pads_of(nl: Netlist, model: Model, ref: str, declared: Mapping[str, Any]) -> str:
    """Return where ``ref``'s pads are and how it is walked, for a "not found" line."""
    pads = sorted(nl.functions(ref), key=natkey)
    where = ", ".join(f"{p} on {nl.net(ref, p)}" for p in pads) or "no pads on a net"
    kind = model.kinds.get(ref)
    if kind == DECLARED:
        arcs = ", ".join(f"{a} -> {b}" for a, b in declared[ref]) or "nothing"
        return f"{ref}: pads {where}; declared to pass {arcs}"
    if kind == NOT_WALKED:
        return (
            f"{ref}: pads {where}; {model.why[ref]}, so declare what it passes in "
            f"{PARTS_KEY}"
        )
    if kind == CAPACITOR:
        return f"{ref}: pads {where}; a two-pad capacitor is open at DC"
    return f"{ref}: pads {where}"


# --- the table ------------------------------------------------------------------


def table_problems(
    nl: Netlist,
    table: Mapping[str, Any],
    supplies: Iterable[str],
    ground: str | None,
) -> list[str]:
    """Return one line for each problem that makes the whole table untrustworthy.

    The judged nets must be in the design, listed once, and exactly the nets of
    ``SUPPLY_NETS`` other than the ground net; each declared part must be in the design
    and its pads its own.
    """
    problems = links.supply_problems(nl, supplies)
    nets = list(table["nets"])
    supplies = list(supplies)
    for net in dict.fromkeys(n for n in nets if nets.count(n) > 1):
        problems.append(f"{NETS_KEY}: {net} is listed twice")
    for net in dict.fromkeys(nets):
        if net not in nl.nets:
            line = f"{NETS_KEY}: {net} is not in the design"
            if net.startswith("/") and net.lstrip("/") in nl.nets:
                line += f" (net names carry no leading slash: write {net.lstrip('/')})"
            problems.append(line)
        if ground is not None and net == ground:
            problems.append(
                f"{NETS_KEY}: {net} is the ground net, the return of every path and "
                "not a rail: leave it out (it is never judged and never crossed)"
            )
        elif net not in supplies:
            problems.append(
                f"{NETS_KEY}: {net} is not in specs.SUPPLY_NETS: every judged rail is "
                "a supply, so add it there (the enable-link walk must stop at it too)"
            )
    for net in dict.fromkeys(supplies):
        if net != ground and net not in nets:
            problems.append(
                f"specs.SUPPLY_NETS: {net} is not in {NETS_KEY}: every supply other "
                f"than the ground net ({ground or 'none'}) is judged, so add it there "
                "(a second ground, such as AGND, too: set specs.GROUND_NET if it is "
                'the ground, else an IC across it and a rail is declared {"U1": []})'
            )
    for ref, arcs in table.get("parts", {}).items():
        if ref not in nl.parts:
            problems.append(f"{PARTS_KEY}: {ref} is not in the design")
            continue
        pads = sorted(nl.functions(ref), key=natkey)
        for pad in dict.fromkeys(p for arc in arcs for p in arc):
            if str(pad) not in pads:
                problems.append(
                    f"{PARTS_KEY}: pad {pad} is not a pad of {ref} on a net (its "
                    f"pads are {', '.join(pads) or 'none'})"
                )
    return problems


def _ids(table: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Return the ``(from, to)`` of each paths row."""
    return [(x, y) for x, y, _ in table["paths"]]


def path_id(row: Any) -> str:
    """Return a row's check id: ``VIN->+5V``."""
    return f"{row[0]}->{row[1]}"


# --- the three checks -----------------------------------------------------------


def check_path(
    nl: Netlist,
    model: Model,
    table: Mapping[str, Any],
    row: tuple[str, str, list[str]],
) -> Report:
    """Check one declared path: it exists, and only through the parts it names.

    Records ``power path``: ``{"from", "to", "refs", "ohms", "path", "without named"}``
    with ``ohms`` and ``path`` null when no path is found and ``without named`` the
    route left once the named parts are taken out (null when none is left).
    """
    x, y, refs = row
    refs = list(refs)
    record: dict[str, Any] = {
        "from": x,
        "to": y,
        "refs": refs,
        "ohms": None,
        "path": None,
        "without named": None,
    }
    report = Report({"power path": record})
    named = f"{x}->{y} through {', '.join(refs) or 'no part'}"
    judged = set(model.judged)
    for net in (x, y):
        if net not in judged:
            report.problems.append(f"{net} is not in {NETS_KEY}")
    if x == y:
        report.problems.append(f"the row runs from {x} to itself")
    if not refs:
        report.problems.append(
            f"the row {path_id(row)} names no part: name the parts the path runs "
            "through"
        )
    for ref in dict.fromkeys(refs):
        if ref not in nl.parts:
            report.problems.append(f"{ref} is not in the design")
    if _ids(table).count((x, y)) > 1:
        report.problems.append(f"{path_id(row)} is listed twice in {PATHS_KEY}")
    if report.problems:
        return report
    found = route(model, x, y)
    if found is not None:
        record["ohms"] = round(found.ohms, 6)
        record["path"] = found.path
    if found is None or found.ohms > model.max_ohm:
        declared = table.get("parts", {})
        where = "; ".join(_pads_of(nl, model, r, declared) for r in dict.fromkeys(refs))
        if found is None:
            detail = f"current cannot flow from {x} to {y} ({where})"
        else:
            detail = (
                f"the least resistance from {x} to {y} is {ohm_text(found.ohms)} "
                f"({show(found.path)}), above max_ohm {model.max_ohm:g} ({where})"
                + _notes(model, found.path, found)
            )
        report.problems.append(f"declared path {named} not found: {detail}")
        return report
    for ref in dict.fromkeys(refs):
        through = _cheapest_through(model, x, y, ref)
        if through is None or through > model.max_ohm:
            report.problems.append(
                f"{ref} is named in {path_id(row)} but no power path from {x} to {y} "
                f"runs through it (the path is {show(found.path)})"
            )
    left = route(model, x, y, refs)
    if left is not None:
        record["without named"] = {
            "ohms": round(left.ohms, 6),
            "path": left.path,
        }
        if left.ohms <= model.max_ohm:
            others = list(dict.fromkeys(left.path[1::2]))
            report.problems.append(
                f"{x}->{y} also runs through parts the row does not name: "
                f"{show(left.path)} ({ohm_text(left.ohms)}): name them in the row "
                f"({_row_text(x, y, [*refs, *others])}) if the path is meant, or "
                "remove the path" + _notes(model, left.path, left)
            )
    return report


def check_unlisted(nl: Netlist, model: Model, table: Mapping[str, Any]) -> Report:
    """Check that no ordered pair of judged nets is joined unless a row declares it.

    Records ``power paths``: ``{"X->Y": {"ohms", "path ohms", "path", "power path",
    "listed"}}`` for every pair current can flow between at any resistance, so a review
    sees the near misses, and ``max_ohm``.
    """
    listed = set(_ids(table))
    pairs: dict[str, Any] = {}
    report = Report({"max_ohm": model.max_ohm, "power paths": pairs})
    for x in model.judged:
        if x not in nl.nets:
            continue
        ahead, _ = _search(model, x)
        for y in sorted((n for n in ahead if n in model.judged), key=natkey):
            if y == x:
                continue
            found = route(model, x, y)
            if found is None:
                continue
            power = found.ohms <= model.max_ohm
            pairs[f"{x}->{y}"] = {
                "ohms": round(found.ohms, 6),
                "path ohms": round(found.path_ohms, 6),
                "path": found.path,
                "power path": power,
                "listed": (x, y) in listed,
            }
            if power and (x, y) not in listed:
                refs = list(dict.fromkeys(found.path[1::2]))
                report.problems.append(
                    f"{x} -> {y} is joined at {ohm_text(found.ohms)} "
                    f"({show(found.path)}) and no row of {PATHS_KEY} declares it, so "
                    f"current can flow from {x} into {y}: remove the path, or if it is "
                    f"meant add {_row_text(x, y, refs)}"
                    + _notes(model, found.path, found)
                )
    return report


def _rail_ohms(model: Model, nets: Iterable[str]) -> dict[str, dict[str, float]]:
    """Return ``{judged net: {net: ohms}}``: how far each of ``nets`` is from each rail.

    The resistance is the effective one through walked parts either way, not crossing
    another judged net or the ground net; a net no route joins to a rail is left out.
    """
    nets = [n for n in dict.fromkeys(nets) if n not in model.stops]
    inner = {n for arc in model.arcs for n in (arc.src, arc.dst)} - model.stops
    found: dict[str, dict[str, float]] = {}
    for rail in model.judged:
        arcs = [
            arc
            for arc in model.arcs
            if {arc.src, arc.dst} <= inner | {rail} and (arc.src != arc.dst)
        ]
        found[rail] = _ohms_to(arcs, rail, nets)
    return found


def _how(model: Model, net: str, rail: str, ohms: float) -> str:
    """Return how ``net`` is joined to ``rail``, with its cheapest path."""
    _, parents = _search(model, net, both=True)
    path = _path_to(parents, rail)
    return f"joined to it ({show(path)}, {ohm_text(ohms)})" + _notes(model, path)


def check_modelled(nl: Netlist, model: Model, table: Mapping[str, Any]) -> Report:
    """Check that no part the walk does not go through bridges two judged nets.

    A part that is not walked and not declared, whose pads are on, or joined at most
    ``max_ohm`` to, two judged nets or more, fails by name. Records ``not modelled``,
    ``{ref: {judged net: how}}``, and ``declared parts``, ``{ref: ["1 -> 3 (VIN ->
    +5V)"]}``.
    """
    declared: dict[str, list[str]] = {}
    for ref, arcs in table.get("parts", {}).items():
        declared[ref] = [
            f"{a} -> {b} ({nl.net(ref, a)} -> {nl.net(ref, b)})" for a, b in arcs
        ]
    found: dict[str, dict[str, str]] = {}
    report = Report({"not modelled": found, "declared parts": declared})
    candidates = [
        ref
        for ref in sorted(nl.parts, key=natkey)
        if model.kinds.get(ref) == NOT_WALKED
    ]
    pad_nets = [
        net
        for ref in candidates
        for net in (nl.net(ref, pad) for pad in nl.functions(ref))
        if net is not None and not net.startswith(UNCONNECTED)
    ]
    far = _rail_ohms(model, pad_nets)
    for ref in candidates:
        reached: dict[str, tuple[str, str, float | None]] = {}
        for pad in sorted(nl.functions(ref), key=natkey):
            net = nl.net(ref, pad)
            if net is None or net.startswith(UNCONNECTED):
                continue
            if net in model.judged:
                reached.setdefault(net, (pad, net, None))
                continue
            for rail in model.judged:
                ohms = far.get(rail, {}).get(net)
                if ohms is not None and ohms <= model.max_ohm:
                    reached.setdefault(rail, (pad, net, ohms))
        if len(reached) < 2:
            continue
        # the paths are worked out only for a part that fails
        rails = {
            rail: f"pad {pad} on {net}"
            + ("" if ohms is None else ", " + _how(model, net, rail, ohms))
            for rail, (pad, net, ohms) in reached.items()
        }
        found[ref] = rails
        part = nl.parts[ref]
        where = "; ".join(f"{rail}: {how}" for rail, how in rails.items())
        first = sorted(nl.functions(ref), key=natkey)
        example = f'{{"{ref}": [("{first[0]}", "{first[-1]}")]}}' if first else ""
        report.problems.append(
            f"{ref} ({part['lib']}:{part['part']}) reaches {len(rails)} judged nets "
            f"({where}) and {model.why[ref]}, so a power path through it is not seen: "
            f"declare what it passes in {PARTS_KEY} (in pad to out pad, such as "
            f'{example}), or {{"{ref}": []}} if it passes no power'
        )
    return report


def read_rows(table: Mapping[str, Any]) -> list[tuple[str, str, list[str]]]:
    """Return the paths rows as ``(from, to, [refs])``."""
    return [(x, y, list(refs)) for x, y, refs in table["paths"]]
