"""Semiconductor voltage: FETs, diodes and clamps stay inside their voltage ratings.

``SEMI_VRATED`` is ``{mpn: row}``. A row is one of four kinds:

* a FET: ``{"vds": V, "vgs": V}``; ``|V(D) - V(S)|`` and ``|V(G) - V(S)|`` are held
  under ``derate`` (at most and by default 0.8) times each rating;
* a diode: ``{"vrrm": V}``; its reverse voltage ``V(K) - V(A)`` is held under
  ``derate`` (at most and by default 0.8) times VRRM. A forward drop is not judged;
* a TVS: ``{"vrwm": V}``; its reverse voltage is held under ``derate`` (at most and by
  default 1.0, because a TVS is specified at its standoff voltage) times VRWM;
* a Zener used as a clamp: ``{"vz": V}``; the part is recorded as a clamp and its
  reverse voltage is not held to a rating, but because the DC model has no breakdown,
  a solve that shows more than ``vz`` (plus ``CLAMP_TOLERANCE``) across it is not the
  board's, and fails, saying what to do.

Every row may give ``pins`` (``{"g", "d", "s"}`` for a FET row, ``{"a", "k"}`` for the
others, to pad numbers) when the symbol's pin names do not read as one part, or
``elements``, a list of ``{"pins": {...}}``, for a part with two or more diodes or FETs
in it (a dual diode, a dual FET), each judged on its own against the row's rating.

Which parts are judged. Any of these:

* a part whose pins all read as one FET or one diode: every pad that is on a net (not
  KiCad's ``unconnected-(...)`` one) has a pin function, and the functions are exactly
  G, D and S (or GATE, DRAIN, SOURCE) or exactly A and K (or ANODE, CATHODE), case
  ignored, each name's pads on one net;
* a part whose pins are all named for one family's terminals but do not read as one
  FET or diode (B, C and E; G1, D1, S1, G2...; A1, A2, K; a name from two families,
  as a jumper's A and B, is not one), and a part from a library whose name starts with
  ``Diode`` or ``Transistor``, or a ``Device`` symbol whose name is ``D``, ``LED`` or
  starts with ``D_``, ``LED_`` or ``Q_``: these are judged when their row gives ``pins``
  or ``elements``, and fail by name when it does not;
* a part whose MPN has a row;
* a part the stress circuit holds a diode or FET element for.

A part listed in the optional ``SEMI_NOT_JUDGED`` (``{ref: reason}``) is left out and
its reason recorded, unless its MPN has a row, in which case it is judged as usual. A
reference there that is not in the design fails by name.

A judged part fails, by name, when it has no MPN or no row (no rating on file); when
its pins cannot be read and its row gives none; when its row is of the other family
than its pins; when its row gives both ``pins`` and ``elements``; when the row's pads
disagree with the pin names (a pad named D given as ``g``); when the row's pads leave
out pads of the part on other nets; when a row pad is on no net; and, scenario by
scenario, as each is solved: when the circuit holds no element of the part on the net
of each judged pad (``dc.build`` models only some symbols); when a judged pad's net is
floating (its 0 V is the solver's leak); when a pad on the nets that set the judged
pads' levels belongs to a part the model leaves out (``faults.left_out``: a connector
bringing in a rail the scenario does not fix); when a FET there has a floating gate;
when a scenario cannot be solved, or none is; and when a voltage is over its limit.

Nothing is read from net names or reference letters. The functions here are pure and
import no pytest, so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pcbkit.check.faults import (
    element_nets,
    fet_states,
    left_out,
    level_nets,
    unclamped,
    undecided_fets,
)
from pcbkit.check.netlist import Netlist, natkey
from pcbkit.check.tables import (
    DictOf,
    Fraction,
    ListOf,
    OneOf,
    Pad,
    Record,
    Ref,
    Shape,
    Text,
    Tuple,
    Volts,
)

DERATE_DEFAULT = 0.8
TVS_DERATE_DEFAULT = 1.0
# A clamp may show this share more than its vz before the solve counts as not clamping:
# a modelled breakdown adds its on-resistance drop, and a Zener's vz has a tolerance.
CLAMP_TOLERANCE = 0.05

# a transistor's pads go with its vds and vgs, a diode's with the other ratings
_FET_PINS = Record(required={"g": Pad(), "d": Pad(), "s": Pad()})
_DIODE_PINS = Record(required={"a": Pad(), "k": Pad()})


def _row(required: dict[str, Shape], pins: Shape, derate: float | None) -> Shape:
    """Return the shape of one row kind: its ratings, derate, pins or elements."""
    optional: dict[str, Shape] = {}
    if derate is not None:
        optional["derate"] = Fraction(hi=derate)
    optional["pins"] = pins
    optional["elements"] = ListOf(Record(required={"pins": pins}))
    return Record(required=required, optional=optional)


TABLES: dict[str, Shape] = {
    "SEMI_VRATED": DictOf(
        Text("mpn"),
        OneOf(
            _row({"vds": Volts(), "vgs": Volts()}, _FET_PINS, DERATE_DEFAULT),
            _row({"vrrm": Volts()}, _DIODE_PINS, DERATE_DEFAULT),
            _row({"vrwm": Volts()}, _DIODE_PINS, TVS_DERATE_DEFAULT),
            _row({"vz": Volts()}, _DIODE_PINS, None),
        ),
    ),
    "SEMI_NOT_JUDGED": DictOf(Ref(), Text("reason")),
    "SEMI_HIGH_Z": ListOf(Tuple(Ref(), Pad())),
}

FET = "fet"
DIODE = "diode"
TVS = "tvs"
CLAMP = "clamp"

# the row kind each rating key makes, and the default share of the rating allowed
ROW_KINDS = {"vds": FET, "vrrm": DIODE, "vrwm": TVS, "vz": CLAMP}
DEFAULT_TARGET = {FET: DERATE_DEFAULT, DIODE: DERATE_DEFAULT, TVS: TVS_DERATE_DEFAULT}
# which family of terminals a kind has
FAMILY = {FET: FET, DIODE: DIODE, TVS: DIODE, CLAMP: DIODE}
# the quantities judged: name, plus terminal, minus terminal, taken as an absolute
QUANTITIES = {
    FET: (("vds", "d", "s", True), ("vgs", "g", "s", True)),
    DIODE: (("vr", "k", "a", False),),
}
RATING_KEY = {
    (FET, "vds"): "vds",
    (FET, "vgs"): "vgs",
    (DIODE, "vr"): "vrrm",
    (TVS, "vr"): "vrwm",
    (CLAMP, "vr"): "vz",
}
LABELS = {"vds": "|Vds|", "vgs": "|Vgs|", "vr": "V(K) - V(A)"}

# Pin functions that read as one terminal of a FET or a diode.
TERMINALS = {
    "G": "g",
    "GATE": "g",
    "D": "d",
    "DRAIN": "d",
    "S": "s",
    "SOURCE": "s",
    "A": "a",
    "ANODE": "a",
    "K": "k",
    "CATHODE": "k",
}
# Numbered names whose letter still says the terminal (G1, D2, S1, K1); A1 and A2 do
# not: a bidirectional part names both its pads A.
_NUMBERED_TERMINALS = ("G", "D", "S", "K")
# Pin functions named like a semiconductor's terminals, with or without a number.
_SEMI_NAME = re.compile(
    r"(G|D|S|A|K|B|C|E|GATE|DRAIN|SOURCE|ANODE|CATHODE|BASE|COLLECTOR|EMITTER)\d*"
)
_LETTER = {
    "GATE": "G",
    "DRAIN": "D",
    "SOURCE": "S",
    "ANODE": "A",
    "CATHODE": "K",
    "BASE": "B",
    "COLLECTOR": "C",
    "EMITTER": "E",
}
_FAMILIES = (frozenset("GDS"), frozenset("AK"), frozenset("BCE"))
_SEMI_LIBRARIES = ("Diode", "Transistor")
_SEMI_DEVICE = re.compile(r"(D|LED)|(D|LED|Q)_.*")

UNCONNECTED = "unconnected-("


@dataclass
class Pins:
    """How a part's pads are read: its kind and ``{terminal: pad}``, or why not."""

    kind: str | None
    pads: dict[str, str] = field(default_factory=dict)
    problem: str | None = None


@dataclass
class Plan:
    """How a part is judged: its kind and elements (``{terminal: pad}``), or why not."""

    kind: str | None
    elements: list[dict[str, str]] = field(default_factory=list)
    problem: str | None = None


@dataclass
class SemiReport:
    """The verdict: the record of each judged part and one sentence per failure."""

    parts: dict[str, dict[str, Any]] = field(default_factory=dict)
    not_judged: dict[str, str] = field(default_factory=dict)
    declared: dict[str, str] = field(default_factory=dict)
    high_z: list[str] = field(default_factory=list)
    fet_gates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    unsolved: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class _Context:
    """Hold what every part is judged against: high-impedance pads, blocking parts."""

    high_z: set[tuple[str, str]] = field(default_factory=set)
    blocking: dict[str, float] = field(default_factory=dict)
    fet_gates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class _Seen:
    """What the scenarios showed of one part: worst readings and what stopped them."""

    worst: dict[tuple[int, str], tuple[float, int]] = field(default_factory=dict)
    either_way: set[tuple[int, str]] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    kinds: set[str] = field(default_factory=set)

    def once(self, kind: str, text: str) -> None:
        """Keep the first problem of each kind: the first scenario that shows it."""
        if kind not in self.kinds:
            self.kinds.add(kind)
            self.problems.append(text)


def wired_pads(nl: Netlist, ref: str) -> dict[str, str]:
    """Return ``{pad: function}`` for the pads of ``ref`` that are on a real net."""
    return {
        pad: func
        for pad, func in nl.functions(ref).items()
        if not str(nl.net(ref, pad)).startswith(UNCONNECTED)
    }


def named_like_a_semiconductor(nl: Netlist, ref: str) -> bool:
    """Return True if every wired pad of ``ref`` is named for one family's terminals.

    The families are a FET's (G, D, S), a diode's (A, K) and a bipolar transistor's
    (B, C, E), the long names counted as their letters and a number allowed after
    each (G1, A2). Names from two families (a jumper's A and B) are not one part's.
    """
    pads = wired_pads(nl, ref)
    if len(pads) < 2:
        return False
    found_letters = set()
    for func in pads.values():
        found = _SEMI_NAME.fullmatch(func.strip().upper())
        if not found:
            return False
        found_letters.add(_LETTER.get(found.group(1), found.group(1)))
    return any(found_letters <= family for family in _FAMILIES)


def from_a_semiconductor_library(nl: Netlist, ref: str) -> bool:
    """Return True for a ``Diode`` or ``Transistor`` library, or a Device D, LED, Q."""
    part = nl.parts[ref]
    lib, name = str(part["lib"]), str(part["part"])
    if lib.startswith(_SEMI_LIBRARIES):
        return True
    return lib == "Device" and bool(_SEMI_DEVICE.fullmatch(name))


def read_pins(nl: Netlist, ref: str) -> Pins:
    """Read a part as one FET or one diode from its pin functions, or say why not."""
    by_terminal: dict[str, list[str]] = {}
    names = []
    readable = True
    for pad, func in sorted(wired_pads(nl, ref).items(), key=lambda kv: natkey(kv[0])):
        names.append(func or "(no name)")
        terminal = TERMINALS.get(func.strip().upper())
        if terminal is None:
            readable = False
        else:
            by_terminal.setdefault(terminal, []).append(pad)
    if not readable:
        by_terminal = {}
    shown = ", ".join(dict.fromkeys(names)) or "none"
    kind = None
    if set(by_terminal) == {"g", "d", "s"}:
        kind = FET
    elif set(by_terminal) == {"a", "k"}:
        kind = DIODE
    if kind is None:
        return Pins(
            None,
            problem=f"its pin names ({shown}) do not read as one FET (G, D, S) or one "
            "diode (A, K)",
        )
    for terminal, pads in by_terminal.items():
        nets = {nl.net(ref, p) for p in pads}
        if len(nets) > 1:
            return Pins(
                None,
                problem=f"its pads {', '.join(pads)} are all named "
                f"{terminal.upper()} but are on different nets, so it holds more than "
                "one element",
            )
    return Pins(kind, {t: pads[0] for t, pads in by_terminal.items()})


def row_kind(row: Mapping[str, Any]) -> str:
    """Return the kind of a row: ``fet``, ``diode``, ``tvs`` or ``clamp``.

    Raise ValueError for a row with none of the ratings (the shape refuses one).
    """
    for key, kind in ROW_KINDS.items():
        if key in row:
            return kind
    raise ValueError(f"a SEMI_VRATED row with no rating: {dict(row)!r}")


def mpn_of(nl: Netlist, ref: str) -> str:
    """Return a part's MPN field, or an empty string."""
    return str(nl.parts[ref]["fields"].get("MPN", "")).strip()


def _named_terminal(func: str) -> str | None:
    """Return the terminal a pin name says (``D`` and ``D2`` say ``d``), or None."""
    name = func.strip().upper()
    if name in TERMINALS:
        return TERMINALS[name]
    letter = name.rstrip("0123456789")
    if letter in _NUMBERED_TERMINALS and letter != name:
        return TERMINALS[letter]
    return None


def _row_elements(nl: Netlist, ref: str, row: Mapping[str, Any]) -> Plan:
    """Return the plan from a row's ``pins`` or ``elements``, checked on the part."""
    if "pins" in row:
        elements = [dict(row["pins"])]
    else:
        elements = [dict(e["pins"]) for e in row["elements"]]
    wired = wired_pads(nl, ref)
    pads = [p for e in elements for p in e.values()]
    absent = list(dict.fromkeys(p for p in pads if nl.net(ref, p) is None))
    if absent:
        return Plan(
            None,
            problem=f"its row's pins name pads {', '.join(absent)}, which are on "
            f"no net (its pads are {', '.join(sorted(wired, key=natkey))})",
        )
    names = nl.functions(ref)
    clashes = []
    for element in elements:
        for terminal, pad in element.items():
            said = _named_terminal(names.get(pad, ""))
            if said is not None and said != terminal:
                clashes.append(
                    f"pad {pad} is named {names[pad].strip()} but the row says "
                    f"{terminal}"
                )
    if clashes:
        return Plan(
            None,
            problem="its row's pins disagree with its pin names: "
            + "; ".join(dict.fromkeys(clashes)),
        )
    judged_nets = {nl.net(ref, p) for p in pads}
    others = [p for p in sorted(wired, key=natkey) if nl.net(ref, p) not in judged_nets]
    if others:
        return Plan(
            None,
            problem=f"its row's pins leave out pads {', '.join(others)}, on other "
            'nets: list every diode or FET of the part in its row\'s "elements"',
        )
    return Plan(row_kind(row), elements)


# Device symbols the solver models as parts that break down (Zeners and TVSs).
BREAKDOWN_SYMBOLS = ("Device:D_Zener", "Device:D_TVS")


def breaks_down_by_symbol(nl: Netlist, ref: str) -> bool:
    """Return True for a ``Device`` Zener or TVS: the symbol the solver models it by."""
    return nl.kind(ref).startswith(BREAKDOWN_SYMBOLS)


def plan_part(nl: Netlist, ref: str, row: Mapping[str, Any] | None) -> Plan:
    """Return how ``ref`` is judged: from its row's pads, else from its pin names."""
    if row is not None and "pins" in row and "elements" in row:
        return Plan(None, problem='its row gives both "pins" and "elements": give one')
    if row is not None and ("pins" in row or "elements" in row):
        return _row_elements(nl, ref, row)
    if row is not None and "vrrm" in row and breaks_down_by_symbol(nl, ref):
        return Plan(
            None,
            problem="its symbol says it breaks down, but its row is a vrrm row",
        )
    pins = read_pins(nl, ref)
    if pins.kind is None:
        return Plan(None, problem=pins.problem)
    if row is not None and FAMILY[row_kind(row)] != pins.kind:
        return Plan(
            None,
            problem=f"its pins read as a {pins.kind} but its row is a "
            f"{row_kind(row)} row",
        )
    kind = row_kind(row) if row is not None else pins.kind
    return Plan(kind, [pins.pads])


def _hint(nl: Netlist, ref: str, plan: Plan, row: Mapping[str, Any] | None) -> str:
    """Return what to write when a part cannot be judged: the way out that applies."""
    declare = ", or declare it in SEMI_NOT_JUDGED with the reason"
    problem = str(plan.problem)
    if "says it breaks down" in problem:
        return (
            "write a vz row (a Zener used as a clamp) or a vrwm row (a TVS)" + declare
        )
    if row is not None and ("pins" in row or "elements" in row):
        return "correct the row" + declare
    if "but its row is a" in problem:
        kind = (
            "a FET row (vds, vgs)"
            if "read as a fet" in problem
            else ("a diode row (vrrm, vrwm or vz)")
        )
        return f"write {kind} for it" + declare
    funcs = [f.strip().upper() for f in wired_pads(nl, ref).values()]
    names = {f.rstrip("0123456789") for f in funcs}
    if names == {"B", "C", "E"} or names == {"BASE", "COLLECTOR", "EMITTER"}:
        return (
            'give its SEMI_VRATED row "pins" (a bipolar transistor is written as a FET '
            'row: "vds" its VCEO, "vgs" its VEBO, "pins" {"g": base pad, "d": '
            'collector pad, "s": emitter pad})' + declare
        )
    numbered = any(f != f.rstrip("0123456789") for f in funcs)
    several = "different nets" in problem or (numbered and not names <= {"A"})
    if several or len(funcs) > 3:
        return (
            'list each diode or FET of it in its SEMI_VRATED row\'s "elements"'
            + declare
        )
    return 'give its SEMI_VRATED row "pins"' + declare


def _static_candidates(nl: Netlist, table: Mapping[str, Any]) -> set[str]:
    """Return the parts judged whatever the circuit holds (names, library, a row)."""
    return {
        ref
        for ref in nl.parts
        if named_like_a_semiconductor(nl, ref)
        or from_a_semiconductor_library(nl, ref)
        or (mpn_of(nl, ref) != "" and mpn_of(nl, ref) in table)
    }


def _modelled_now(nl: Netlist, circuit: Any) -> set[str]:
    """Return the parts of the netlist the circuit holds a diode or FET element for."""
    found = {str(ref).split(".", 1)[0] for *_x, ref in circuit.diodes if ref}
    found |= {str(f["ref"]).split(".", 1)[0] for f in circuit.fets if f.get("ref")}
    return found & set(nl.parts)


def _pad_list(
    nl: Netlist, ref: str, plan: Plan, nets_ok: Callable[[Any], bool]
) -> list[str]:
    """Return ``"pad 1 (G) on GATE"`` for each judged pad whose net fails the test."""
    found = []
    for element in plan.elements:
        for terminal, pad in sorted(element.items()):
            net = nl.net(ref, pad)
            if not nets_ok(net):
                found.append(f"pad {pad} ({terminal.upper()}) on {net}")
    return list(dict.fromkeys(found))


def _against_the_circuit(nl: Netlist, ref: str, plan: Plan, circuit: Any) -> str:
    """Return how the plan's elements contradict the circuit's model of the part, or "".

    A diode element whose anode and cathode nets the circuit's own diode for the part
    joins the other way round, or a FET element whose gate is not the circuit's FET
    gate, is a row (or pin names) that says one thing while the model says another.
    """
    diodes = [
        (a, c)
        for a, c, *_x, r in circuit.diodes
        if r
        and str(r).split(".", 1)[0] == ref
        and not str(r).endswith(".body")
        and not str(r).endswith(".breakdown")
    ]
    fets = [f for f in circuit.fets if str(f.get("ref", "")).split(".", 1)[0] == ref]
    for number, element in enumerate(plan.elements, start=1):
        if FAMILY[plan.kind or FET] == DIODE and diodes:
            an, kn = nl.net(ref, element["a"]), nl.net(ref, element["k"])
            if (an, kn) not in diodes and (kn, an) in diodes:
                return (
                    f"its row's element {number} (anode pad {element['a']} on {an}, "
                    f"cathode pad {element['k']} on {kn}) is not how the circuit "
                    f"models it: its diode conducts from {kn} to {an}"
                )
        if FAMILY[plan.kind or FET] == FET and fets:
            g = nl.net(ref, element["g"])
            ds = {nl.net(ref, element["d"]), nl.net(ref, element["s"])}
            if not any(f["g"] == g and {f["d"], f["s"]} == ds for f in fets):
                return (
                    f"its row's element {number} (gate pad {element['g']} on {g}) is "
                    "not how the circuit models it: no FET of it there has that gate, "
                    "drain and source"
                )
    return ""


def _polarity_known(
    nl: Netlist, ref: str, plan: Plan, element: Mapping[str, str], circuit: Any
) -> bool:
    """Return True if the anode is certain, so the reverse voltage alone counts.

    It is certain when the part is not a TVS row, is not held as ``<ref>.open`` and its
    pads are not named A1 and A2 (a bidirectional part), and either both pads' pin names
    read plainly as its anode and cathode (A and K) or the circuit's own diode element
    for the part runs from the element's anode net to its cathode net. Otherwise the
    part is judged on the absolute voltage.
    """
    if plan.kind == TVS:
        return False
    if any(r == f"{ref}.open" for *_x, r in circuit.res):
        return False
    names = nl.functions(ref)
    if any(
        re.fullmatch(r"A\d+", names.get(pad, "").strip().upper())
        for pad in (element["a"], element["k"])
    ):
        return False  # a bidirectional part: no element can say which way round
    if names.get(element["a"], "").strip().upper() in ("A", "ANODE") and names.get(
        element["k"], ""
    ).strip().upper() in ("K", "CATHODE"):
        return True
    an, kn = nl.net(ref, element["a"]), nl.net(ref, element["k"])
    return any(
        (a, c) == (an, kn)
        for a, c, *_x, r in circuit.diodes
        if r and str(r).split(".", 1)[0] == ref and not str(r).endswith(".breakdown")
    )


# A diode element showing this much more than its forward drop conducts more current
# than any part could: only two fixed nets across it can do that.
UNPHYSICAL_EXCESS_V = 1.0


def _unphysical(ref: str, solution: Any) -> str:
    """Return why a diode element of ``ref`` cannot be the board, or "".

    A conducting diode element with far more than its forward drop across it (more
    than ``UNPHYSICAL_EXCESS_V`` above ``vf``) is held between two fixed nets: the
    model forces a current no part carries, so its voltages are not the board's.
    """
    circuit = solution.c
    states = circuit.state.get("diodes", []) if isinstance(circuit.state, dict) else []
    for k, (a, c, vf, _ron, r) in enumerate(circuit.diodes):
        if not r or str(r).split(".", 1)[0] != ref:
            continue
        forward = solution[a] - solution[c]
        on = states[k] if k < len(states) else forward > vf
        if on and forward > vf + UNPHYSICAL_EXCESS_V:
            return (
                f"its diode element {r} conducts with {forward:.2f} V across it, far "
                f"more than its {vf:g} V forward drop, which only a diode between two "
                "fixed nets can show, so the model is not the board; model a "
                "bidirectional part as an open element (circuit.R(net, net, 1e12, "
                f"ref='{ref}.open')), never as circuit.D"
            )
    return ""


def _observe(
    nl: Netlist,
    ref: str,
    plan: Plan,
    index: int,
    solution: Any,
    seen: _Seen,
    context: _Context,
) -> None:
    """Judge one part in one solved scenario, now, before the next is solved."""
    circuit = solution.c
    held = element_nets(circuit).get(ref, set())
    left = _pad_list(nl, ref, plan, lambda n: n in held)
    if left:
        if getattr(circuit, "_built", None) is not None:
            why = (
                "dc.build(extended=True) does not model this symbol (it models the "
                "Device symbols and the two-pad Diode and Power_Protection parts the "
                "docs list)"
            )
        else:
            why = (
                "dc.build does not model this symbol (dc.build(nl, extended=True) "
                "models more)"
            )
        seen.once(
            "model",
            f"{ref} ({nl.kind(ref)}) is not modelled on {', '.join(left)} in "
            f"STRESS_SCENARIOS[{index}]: {why}, so the voltage across it is not the "
            f"board's; add it in circuits.scenario with ref {ref!r} (circuit.D(anode "
            f"net, cathode net, vf, ref={ref!r}) for a diode, a circuit.fets entry for "
            "a FET; a bidirectional part as an open element, circuit.R(net, net, "
            f"1e12, ref='{ref}.open'), never as circuit.D)",
        )
        return
    wrong = _against_the_circuit(nl, ref, plan, circuit)
    if wrong:
        seen.once(
            "direction",
            f"{ref} cannot be judged in STRESS_SCENARIOS[{index}]: {wrong}; correct "
            "the row's pins",
        )
        return
    unphysical = _unphysical(ref, solution)
    if unphysical:
        seen.once(
            "unphysical",
            f"{ref} cannot be judged in STRESS_SCENARIOS[{index}]: {unphysical}",
        )
        return
    floating = _pad_list(nl, ref, plan, lambda n: not solution.floating(n))
    if floating:
        seen.once(
            "floating",
            f"{ref} cannot be judged in STRESS_SCENARIOS[{index}]: "
            f"{', '.join(floating)} {'is' if len(floating) == 1 else 'are'} floating "
            "there (its 0 V is the solver's leak); fix the net that drives it in that "
            "scenario, or give it a pull",
        )
        return
    nets = {str(nl.net(ref, p)) for e in plan.elements for p in e.values()}
    lnets_list = sorted(
        {n for net in nets for n in level_nets(circuit, net)}, key=natkey
    )
    lnets = set(lnets_list)
    missing = left_out(nl, circuit, lnets_list, context.high_z)
    if missing:
        seen.once(
            "left out",
            f"{ref} cannot be judged in STRESS_SCENARIOS[{index}]: the DC model "
            f"leaves out {', '.join(missing)}, on the nets that set its pads' levels; "
            "fix those nets in the scenario, model what drives them with its "
            "reference, or, for a pad that draws and sources nothing at DC, list it in "
            "SEMI_HIGH_Z",
        )
        return
    guessed = undecided_fets(circuit, solution, None, lnets_list)
    guessed += unclamped(
        circuit, solution, lnets_list, skip={ref}, blocking=context.blocking
    )
    if guessed:
        seen.once(
            "gate",
            f"{ref} cannot be judged in STRESS_SCENARIOS[{index}]: "
            + "; ".join(guessed),
        )
        return
    for fet, state in fet_states(circuit, solution, lnets | nets).items():
        record = {"scenario": index, **state}
        if record not in context.fet_gates.setdefault(fet, []):
            context.fet_gates[fet].append(record)
    family = FAMILY[plan.kind or FET]
    for number, element in enumerate(plan.elements):
        for name, plus, minus, absolute in QUANTITIES[family]:
            v = float(
                solution[nl.net(ref, element[plus])]
                - solution[nl.net(ref, element[minus])]
            )
            key = (number, name)
            if not absolute and not _polarity_known(nl, ref, plan, element, circuit):
                absolute = True
                seen.either_way.add(key)
            v = abs(v) if absolute else v
            if key not in seen.worst or v > seen.worst[key][0]:
                seen.worst[key] = (v, index)


def _verdict(
    nl: Netlist, ref: str, plan: Plan, row: Mapping[str, Any] | None, seen: _Seen
) -> tuple[dict[str, Any], list[str]]:
    """Return a judged part's record and problems, from what the scenarios showed."""
    kind = plan.kind or FET
    mpn = mpn_of(nl, ref)
    target = DEFAULT_TARGET.get(kind)
    if row is not None and "derate" in row:
        target = row["derate"]
    entry: dict[str, Any] = {"kind": kind, "mpn": mpn, "target": target}
    if kind == CLAMP and row is not None:
        entry["vz"] = row["vz"]
    problems: list[str] = []
    if row is None:
        problems.append(
            f"no voltage rating on file for {ref} "
            + (f"({mpn}): add a SEMI_VRATED row" if mpn else "(no MPN)")
        )
    problems += seen.problems
    several = row is not None and "elements" in row
    records = []
    for number in range(len(plan.elements)):
        who = f"{ref} element {number + 1}" if several else ref
        quantities: dict[str, Any] = {}
        for name, _plus, _minus, _abs in QUANTITIES[FAMILY[kind]]:
            v, index = seen.worst.get((number, name), (None, None))
            rated = row.get(RATING_KEY[(kind, name)]) if row is not None else None
            shown = None if v is None else round(v, 3)
            either = (number, name) in seen.either_way
            label = "|V(K) - V(A)|" if either else LABELS[name]
            polarity = {}
            if FAMILY[kind] == DIODE:
                shown_polarity = "either way" if either else "reverse"
                polarity = {"polarity": None if v is None else shown_polarity}
            if kind == CLAMP:
                limit = None if rated is None else rated * (1 + CLAMP_TOLERANCE)
                quantities[name] = {
                    "v": shown,
                    "vz": rated,
                    "limit_v": None if limit is None else round(limit, 4),
                    "scenario": index,
                    **polarity,
                }
                if v is not None and limit is not None and v > limit:
                    problems.append(
                        f"{who} is a clamp (vz {rated:g} V) but the model shows "
                        f"{v:.2f} V across it in STRESS_SCENARIOS[{index}]: the DC "
                        "model has no breakdown, so this solve is not the board's; fix "
                        "the clamped net at its clamped voltage in that scenario, or "
                        "model the breakdown as a diode from cathode to anode with vf "
                        f"{rated:g} V (ref '{ref}.breakdown')"
                    )
                continue
            limit = None if rated is None or target is None else target * rated
            quantities[name] = {
                "v": shown,
                "rated": rated,
                "limit_v": None if limit is None else round(limit, 4),
                "scenario": index,
                **polarity,
            }
            if v is not None and limit is not None and v > limit:
                problems.append(
                    f"{who} {label} reaches {v:.2f} V in STRESS_SCENARIOS"
                    f"[{index}], over {target:g} of its {rated:g} V rating "
                    f"({limit:.2f} V)"
                )
        records.append(quantities)
    if several:
        entry["elements"] = records
    else:
        entry.update(records[0])
    return entry, problems


def blocking_ratings(nl: Netlist, table: Mapping[str, Any]) -> dict[str, float]:
    """Return ``{ref: VRRM}`` for each part whose row says it blocks (kind ``vrrm``)."""
    found = {}
    for ref in nl.parts:
        mpn = mpn_of(nl, ref)
        row = table.get(mpn) if mpn else None
        if row is not None and "vrrm" in row and not breaks_down_by_symbol(nl, ref):
            found[ref] = float(row["vrrm"])
    return found


def judge(
    nl: Netlist,
    table: Mapping[str, Any],
    attempts: Iterable[tuple[int, Any, Exception | None]],
    declared: Mapping[str, str] | None = None,
    high_z: Iterable[tuple[str, str]] | None = None,
) -> SemiReport:
    """Judge every semiconductor over the stress scenarios; return the record.

    ``attempts`` is what ``stress.stress_attempts`` yields: ``(index, solution or
    None, error or None)`` per ``STRESS_SCENARIOS`` row; each solution is judged as it
    arrives (its circuit is ``solution.c``), so a project whose scenarios share one
    circuit is judged on each one's own state. ``declared`` is ``SEMI_NOT_JUDGED`` and
    ``high_z`` is ``SEMI_HIGH_Z``.
    """
    report = SemiReport()
    declared = dict(declared or {})

    def row_of(ref: str) -> Mapping[str, Any] | None:
        mpn = mpn_of(nl, ref)
        return table.get(mpn) if mpn else None

    for ref in sorted(declared, key=natkey):
        if ref not in nl.parts:
            report.problems.append(
                f"SEMI_NOT_JUDGED names {ref}, which is not in the design"
            )
    context = _Context(blocking=blocking_ratings(nl, table))
    for ref, pad in high_z or ():
        where = f"SEMI_HIGH_Z names {ref} pad {pad}"
        if ref not in nl.parts:
            report.problems.append(f"{where}: {ref} is not in the design")
        elif nl.net(ref, pad) is None:
            report.problems.append(f"{where}: {ref} has no pad {pad} on a net")
        else:
            context.high_z.add((ref, str(pad)))
            report.high_z.append(f"{ref} pad {pad}")
    skipped = {r for r in declared if r in nl.parts and row_of(r) is None}
    report.declared = {r: declared[r] for r in sorted(skipped, key=natkey)}
    static = _static_candidates(nl, table) - skipped
    plans: dict[str, Plan] = {}
    seen: dict[str, _Seen] = {}
    solved: list[int] = []
    for index, solution, error in attempts:
        if error is not None or solution is None:
            report.unsolved.append(index)
            report.problems.append(
                f"STRESS_SCENARIOS[{index}] could not be solved: "
                f"{type(error).__name__}: {error}"
            )
            continue
        now = _modelled_now(nl, solution.c) - skipped
        for ref in sorted(static | now, key=natkey):
            if ref not in plans:
                plans[ref] = plan_part(nl, ref, row_of(ref))
                seen[ref] = _Seen()
            if plans[ref].kind is not None:
                _observe(nl, ref, plans[ref], index, solution, seen[ref], context)
        solved.append(index)
    for ref in static - set(plans):
        plans[ref] = plan_part(nl, ref, row_of(ref))
        seen[ref] = _Seen()
    report.fet_gates = context.fet_gates
    judged = sorted(plans, key=natkey)
    if judged and not solved:
        report.problems.append(
            f"no STRESS_SCENARIOS row was solved, so {', '.join(judged)} "
            "could not be judged: give the operating points that stress them hardest"
        )
    for ref in judged:
        plan, row = plans[ref], row_of(ref)
        if plan.kind is None:
            why = f"{ref} ({nl.kind(ref)}) cannot be judged: {plan.problem}"
            why += f"; {_hint(nl, ref, plan, row)}"
            report.not_judged[ref] = why
            report.problems.append(why)
            continue
        entry, problems = _verdict(nl, ref, plan, row, seen[ref])
        report.parts[ref] = entry
        report.problems.extend(problems)
    return report
