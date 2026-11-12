"""Fault states: the level of each listed pin in a condition, such as "logic absent".

``FAULT_STATES`` is ``{condition: {"scenario": {keyword: value}, "pins": [(ref, pad,
(min V, max V))]}}``, pads as the netlist writes them. For each condition the check
builds ``circuits.scenario(nl, **scenario)``, solves it, and judges every listed pin:

* the pad must be on a net in the netlist;
* the net must be *driven*: joined, through the resistors, diodes and FETs the circuit
  holds (diodes and FETs only where they conduct in the solution), to a net the
  scenario fixes. ``dc`` ties every net to ground through 1 nS, so an undriven net
  reads 0.0 V, inside a window such as (0, 0.4): the check never trusts that reading,
  and a floating pin fails whatever its window says;
* nothing that can conduct may be missing from the model on the nets that set the pin's
  level (see below);
* the voltage must be inside the window, and must not depend on that 1 nS leak;

What "missing from the model" means. The nets that set a pin's level are the pin's own
net and every net reached from it through any element of the circuit (every resistor,
diode and FET terminal, conducting or not), stopping at the nets the scenario fixes,
whose level does not depend on anything beyond them. Every pad on those nets must
belong to a part the circuit holds elements for on the nets of all its pads (an element
whose ref is the part's reference or starts with it and a dot, as ``dc.build`` names
pack elements and body diodes), to a capacitor from KiCad's ``Device`` library (open at
DC), or be listed in the same condition's pins (a listed pad is taken to draw no
current: its own level is what is judged). Anything else, an IC pin, a connector, a
diode or FET ``dc.build`` does not model, a resistor symbol it does not know, fails by
name, because the solve could be wrong about it. So a pin left to nothing but parts
``dc`` does not model is floating (nothing joins it to a fixed net) and names those
parts as the reason.

Nothing is read from the names of nets or references. The functions here are pure (a
``Netlist``, a solved circuit and the rows in; the verdict out) and import no pytest,
so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pcbkit.check.netlist import Netlist, natkey
from pcbkit.check.tables import (
    Any_,
    DictOf,
    ListOf,
    Pad,
    Record,
    Ref,
    Shape,
    Text,
    Tuple,
    Volts,
    Window,
)

TABLES: dict[str, Shape] = {
    "FAULT_STATES": DictOf(
        Text("condition"),
        Record(
            required={
                "scenario": DictOf(Text("keyword"), Any_()),
                "pins": ListOf(Tuple(Ref(), Pad(), Window(Volts()))),
            }
        ),
    ),
}

DRIVEN = "driven"
FLOATING = "floating"
NO_NET = "no net"
UNSOLVED = "unsolved"

# KiCad's own capacitor symbols in the Device library: open at DC. A capacitor from
# another library is not known to be one, so it counts as a part the model leaves out.
CAPACITORS = frozenset(
    {
        "C",
        "C_Small",
        "C_US",
        "C_Small_US",
        "C_Polarized",
        "C_Polarized_Small",
        "C_Polarized_US",
        "C_Polarized_Small_US",
        "CP",
        "CP_Small",
        "CP_US",
        "CP_Small_US",
    }
)

# The leak a second solve uses to see whether a reading depends on dc's 1 nS one.
SMALL_LEAK = 1e-12
# A pin whose level moves more than this when the leak is made 1000 times smaller is
# held through about a gigaohm or more: its reading is the leak's, not the design's.
LEAK_MOVE_V = 0.05

# The start of the net KiCad gives a pin that nothing is wired to.
UNCONNECTED = "unconnected-("


Pin = tuple[str, str, tuple[float, float]]


@dataclass
class FaultReport:
    """The verdict on one condition.

    ``pins`` is the record, ``{"U2 pad 5": [V or None, min, max, state]}`` with state
    ``driven``, ``floating``, ``no net`` or ``unsolved``; ``not_modelled`` maps a pin to
    the pads on its nets that the circuit leaves out (``"U1 pad 3"``); ``problems`` is
    one sentence per failure.
    """

    pins: dict[str, list[Any]] = field(default_factory=dict)
    not_modelled: dict[str, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def pin_key(ref: str, pad: str) -> str:
    """Return the record key of a pin: ``U2 pad 5``."""
    return f"{ref} pad {pad}"


def unsolved(pins: Sequence[Pin]) -> dict[str, list[Any]]:
    """Return the record for a condition whose circuit could not be built or solved."""
    return {pin_key(r, p): [None, lo, hi, UNSOLVED] for r, p, (lo, hi) in pins}


def element_refs(circuit: Any) -> set[str]:
    """Return the references of the parts the circuit holds an element for.

    ``dc.build`` names a pack's elements ``RN1.1`` and a FET's body diode ``Q1.body``,
    so the part is the name up to the first dot.
    """
    return set(element_nets(circuit))


def element_nets(circuit: Any) -> dict[str, set[str]]:
    """Return, for each part the circuit holds an element for, the nets they touch.

    A pad of the part is modelled only when its net is one of these: a scenario that
    models one pad of a part says nothing about its others.
    """
    found: dict[str, set[str]] = {}

    def add(name: Any, nets: Iterable[str | None]) -> None:
        if name:
            part = str(name).split(".", 1)[0]
            found.setdefault(part, set()).update(n for n in nets if n)

    for a, b, _r, ref in circuit.res:
        add(ref, (a, b))
    for a, c, _vf, _ron, ref in circuit.diodes:
        add(ref, (a, c))
    for f in circuit.fets:
        add(f["ref"], (f["g"], f["d"], f["s"]))
    return found


def _neighbours(circuit: Any) -> dict[str, set[str]]:
    """Return the nets each net shares an element with, whatever the element's state."""
    adj: dict[str, set[str]] = {}

    def join(nets: Iterable[str | None]) -> None:
        real = [n for n in nets if n]
        for n in real:
            adj.setdefault(n, set()).update(m for m in real if m != n)

    for a, b, *_rest in circuit.res:
        join((a, b))
    for a, c, *_rest in circuit.diodes:
        join((a, c))
    for f in circuit.fets:
        join((f["g"], f["d"], f["s"]))
    return adj


def level_nets(circuit: Any, net: str) -> list[str]:
    """Return the nets that set ``net``'s level: it and those reached through elements.

    The walk goes through every element of the circuit (a resistor, a diode or a FET's
    three terminals, conducting or not) and stops at a net the scenario fixes, which is
    not included. A fixed ``net`` gives an empty list: its level is what the scenario
    says.
    """
    fixed = circuit.fixed
    adj = _neighbours(circuit)
    seen: set[str] = set()
    todo = [net]
    while todo:
        n = todo.pop()
        if n in seen or n in fixed:
            continue
        seen.add(n)
        todo += sorted(adj.get(n, ()))
    return sorted(seen, key=natkey)


def is_open_at_dc(nl: Netlist, ref: str) -> bool:
    """Return True for a capacitor from KiCad's ``Device`` library."""
    part = nl.parts.get(ref)
    return part is not None and part["lib"] == "Device" and part["part"] in CAPACITORS


def left_out(
    nl: Netlist, circuit: Any, nets: Iterable[str], listed: set[tuple[str, str]]
) -> list[str]:
    """Return ``"U1 pad 3 (Module:MCU) on EN"`` for each pad the model leaves out.

    A pad on one of ``nets`` is left out unless the pad is in ``listed`` (the
    condition's pins) or its part is a ``Device`` capacitor or is modelled. A part is
    modelled when the circuit holds elements for it that touch the net of every one of
    its pads: one that is modelled on the pad's net but not on another of its pads (a
    scenario that models one diode of a pair) has the pads it leaves out named instead,
    wherever they are, because current can come in through them.
    """
    held = element_nets(circuit)
    found: list[str] = []

    def name(ref: str, pad: str, net: str) -> str:
        kind = nl.kind(ref) if ref in nl.parts else "not in the design"
        return f"{pin_key(ref, pad)} ({kind}) on {net}"

    for net in nets:
        for ref, pad, _func in nl.nets.get(net, []):
            if (ref, pad) in listed or is_open_at_dc(nl, ref):
                continue
            if ref not in held:
                found.append(name(ref, pad, net))
                continue
            for other in sorted(nl.functions(ref), key=natkey):
                other_net = nl.net(ref, other)
                if other_net is not None and other_net not in held[ref]:
                    found.append(name(ref, other, other_net))
    return list(dict.fromkeys(found))


def low_leak_solution(circuit: Any) -> Any:
    """Solve a copy of the circuit with a 1000 times smaller leak; return the solution.

    The copy is taken so the caller's solution keeps its own diode and FET states.
    """
    twin = copy.deepcopy(circuit)
    twin.leak = SMALL_LEAK
    return twin.solve()


def judge(
    nl: Netlist,
    circuit: Any,
    solved: Any,
    pins: Sequence[Pin],
    recheck: Any = None,
) -> FaultReport:
    """Judge each listed pin of one condition; return the record and the problems.

    ``circuit`` is the condition's circuit and ``solved`` its solution. ``recheck`` is
    the solution of the same circuit with a much smaller leak (``low_leak_solution``),
    or None to leave that test out.
    """
    report = FaultReport()
    listed = {(ref, pad) for ref, pad, _w in pins}
    seen: set[tuple[str, str]] = set()
    for ref, pad, (lo, hi) in pins:
        key = pin_key(ref, pad)
        if (ref, pad) in seen:
            report.problems.append(f"{key} is listed twice")
            continue
        seen.add((ref, pad))
        net = nl.net(ref, pad)
        if ref not in nl.parts:
            report.pins[key] = [None, lo, hi, NO_NET]
            report.problems.append(f"{ref} is not in the design")
            continue
        if net is None:
            have = sorted(nl.functions(ref), key=natkey)
            report.pins[key] = [None, lo, hi, NO_NET]
            report.problems.append(
                f"{ref} has no pad {pad} on a net in the netlist "
                f"(its pads are {', '.join(have) or 'none'})"
            )
            continue
        where = f"{key} ({net})"
        missing = left_out(nl, circuit, level_nets(circuit, net), listed)
        if missing:
            report.not_modelled[key] = missing
        if solved.floating(net):
            report.pins[key] = [_volts(solved[net]), lo, hi, FLOATING]
            why = (
                "it is wired to nothing"
                if net.startswith(UNCONNECTED)
                else f"nothing the DC model holds joins {net} to a net the scenario "
                "fixes, so its 0 V reading is the solver's leak, not a level"
            )
            text = f"{where} is floating: {why}"
            if missing:
                text += "; the parts on its nets the model leaves out: " + ", ".join(
                    missing
                )
                text += " (model what drives the pin in circuits.scenario)"
            else:
                text += " (pull it to a net the scenario fixes)"
            report.problems.append(text)
            continue
        v = solved[net]
        report.pins[key] = [_volts(v), lo, hi, DRIVEN]
        if missing:
            report.problems.append(
                f"{where} cannot be judged: the DC model leaves out "
                + ", ".join(missing)
                + ". Model each in circuits.scenario (an element whose ref is the "
                "part's reference), fix the net it drives, or, for a pad that draws "
                "no current in this condition, list it in this condition's pins"
            )
            continue
        if not lo <= v <= hi:
            report.problems.append(
                f"{where} is at {v:.3f} V, outside {lo:g} to {hi:g} V"
            )
            continue
        if recheck is not None:
            v2 = recheck[net]
            if abs(v2 - v) > LEAK_MOVE_V:
                report.problems.append(
                    f"{where} reads {v:.3f} V only because of the solver's 1 nS leak "
                    f"to ground (it is {v2:.3f} V with a smaller leak): it is held "
                    "through a gigaohm or more; give it a real pull"
                )
    return report


def _volts(v: Any) -> float:
    """Return a voltage as a plain float rounded to the millivolt."""
    return round(float(v), 3)
