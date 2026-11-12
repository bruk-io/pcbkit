"""Semiconductor voltage: FETs and diodes stay under a share of their rated voltages.

``SEMI_VRATED`` is ``{mpn: {"vds": V, "vgs": V} or {"vrrm": V}}``, each row with an
optional ``derate`` (the share of the rating allowed, at most and by default 0.8) and
optional ``pins`` (``{"g", "d", "s"}`` with a FET row, ``{"a", "k"}`` with a diode row,
to pad numbers). The check solves every ``STRESS_SCENARIOS`` row and holds, at the
worst of them, ``|V(d) - V(s)|`` under ``derate * vds``, ``|V(g) - V(s)|`` under
``derate * vgs`` and ``|V(k) - V(a)|`` under ``derate * vrrm`` (the voltage either
way: a diode's forward drop is small, and a bidirectional part is held both ways). It
is the capacitor rule's twin.

Which parts are judged. Any of these:

* a part whose pins all read as one FET or one diode: every pad that is on a net (not
  KiCad's ``unconnected-(...)`` one) has a pin function, and the functions are exactly
  G, D and S (or GATE, DRAIN, SOURCE) or exactly A and K (or ANODE, CATHODE), case
  ignored, each name's pads on one net;
* a part whose pins are all named for one family's terminals but do not read as one
  FET or diode (B, C and E; G1, D1, S1, G2...; A1, A2, K; a name from two families,
  as a jumper's A and B, is not one), and a part from a
  library whose name starts with ``Diode`` or ``Transistor``, or a ``Device`` symbol
  whose name is ``D``, ``LED`` or starts with ``D_``, ``LED_`` or ``Q_``: these are
  judged when their row gives ``pins``, and fail by name when it does not;
* a part whose MPN has a row;
* a part the stress circuit holds a diode or FET element for.

A judged part fails, by name, when it has no MPN or no row (no rating on file), when
its pins cannot be read and its row gives none, when its row is of the other kind
than its pins, when the row's ``pins`` leave out pads of the part on other nets (the
row describes one element: a second diode or FET in the same part cannot be judged),
when a judged pad is on no net, when the stress circuit holds no element of the part
on the net of each judged pad (``dc.build`` models only some symbols: the voltage
across a part it leaves out is not what the board would show), when a judged pad's net
is floating in a scenario (its 0 V is the solver's leak), when a scenario cannot be
solved, when no scenario is solved at all (an empty ``STRESS_SCENARIOS`` judges
nothing), and when a voltage is over its limit.

Nothing is read from net names or reference letters. The functions here are pure and
import no pytest, so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pcbkit.check.faults import element_nets
from pcbkit.check.netlist import Netlist, natkey
from pcbkit.check.tables import DictOf, Fraction, OneOf, Pad, Record, Shape, Text, Volts

DERATE_DEFAULT = 0.8

# a transistor's pads go with its vds and vgs, a diode's with its vrrm
_FET_PINS = Record(required={"g": Pad(), "d": Pad(), "s": Pad()})
_DIODE_PINS = Record(required={"a": Pad(), "k": Pad()})

TABLES: dict[str, Shape] = {
    "SEMI_VRATED": DictOf(
        Text("mpn"),
        OneOf(
            Record(
                required={"vds": Volts(), "vgs": Volts()},
                optional={
                    "derate": Fraction(hi=DERATE_DEFAULT),
                    "pins": _FET_PINS,
                },
            ),
            Record(
                required={"vrrm": Volts()},
                optional={
                    "derate": Fraction(hi=DERATE_DEFAULT),
                    "pins": _DIODE_PINS,
                },
            ),
        ),
    ),
}

FET = "fet"
DIODE = "diode"

# The quantities judged, by kind: name, the two pins (plus minus), the rating key.
QUANTITIES = {
    FET: (("vds", "d", "s"), ("vgs", "g", "s")),
    DIODE: (("vr", "k", "a"),),
}
RATING_KEY = {"vds": "vds", "vgs": "vgs", "vr": "vrrm"}

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
    letters = set()
    for func in pads.values():
        found = _SEMI_NAME.fullmatch(func.strip().upper())
        if not found:
            return False
        letters.add(_LETTER.get(found.group(1), found.group(1)))
    return any(letters <= family for family in _FAMILIES)


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
    """Return ``fet`` for a row with ``vds``, ``diode`` for one with ``vrrm``."""
    return FET if "vds" in row else DIODE


def judged_parts(
    nl: Netlist, table: Mapping[str, Any], circuits: Iterable[Any]
) -> list[str]:
    """Return the references the check judges, in natural order (see the module)."""
    modelled: set[str] = set()
    for circuit in circuits:
        modelled |= {_part(ref) for *_rest, ref in circuit.diodes if ref}
        modelled |= {_part(f["ref"]) for f in circuit.fets if f.get("ref")}
    found = []
    for ref in nl.parts:
        if (
            named_like_a_semiconductor(nl, ref)
            or from_a_semiconductor_library(nl, ref)
            or mpn_of(nl, ref) in table
            or ref in modelled
        ):
            found.append(ref)
    return sorted(found, key=natkey)


def _part(name: Any) -> str:
    """Return the part an element belongs to: ``Q1`` of ``Q1.body``."""
    return str(name).split(".", 1)[0]


def mpn_of(nl: Netlist, ref: str) -> str:
    """Return a part's MPN field, or an empty string."""
    return str(nl.parts[ref]["fields"].get("MPN", "")).strip()


def resolve(nl: Netlist, ref: str, row: Mapping[str, Any] | None) -> Pins:
    """Return the pins the check judges ``ref`` on: its row's, else its pin names'."""
    if row is not None and "pins" in row:
        kind = row_kind(row)
        pads = dict(row["pins"])
        wired = wired_pads(nl, ref)
        absent = [p for p in pads.values() if nl.net(ref, p) is None]
        if absent:
            return Pins(
                None,
                problem=f"its row's pins name pads {', '.join(absent)}, which are on "
                f"no net (its pads are {', '.join(sorted(wired, key=natkey))})",
            )
        judged_nets = {nl.net(ref, p) for p in pads.values()}
        others = [
            p for p in sorted(wired, key=natkey) if nl.net(ref, p) not in judged_nets
        ]
        if others:
            return Pins(
                None,
                problem=f"its row's pins leave out pads {', '.join(others)}, on "
                "other nets: a row describes one element, so a part with a second "
                "diode or FET cannot be judged",
            )
        return Pins(kind, pads)
    pins = read_pins(nl, ref)
    if pins.kind is not None and row is not None and row_kind(row) != pins.kind:
        return Pins(
            None,
            problem=f"its pins read as a {pins.kind} but its row is a "
            f"{row_kind(row)} row",
        )
    return pins


@dataclass
class SemiReport:
    """The verdict: the record of each judged part and one sentence per failure."""

    parts: dict[str, dict[str, Any]] = field(default_factory=dict)
    not_judged: dict[str, str] = field(default_factory=dict)
    unsolved: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _hint(nl: Netlist, ref: str) -> str:
    """Return what to write when a part's pins cannot be read."""
    names = {
        f.strip().upper().rstrip("0123456789") for f in wired_pads(nl, ref).values()
    }
    text = 'give its SEMI_VRATED row "pins"'
    if names == {"B", "C", "E"} or names == {"BASE", "COLLECTOR", "EMITTER"}:
        text += (
            ' (a bipolar transistor is written as a FET row: "vds" its VCEO, "vgs" '
            'its VEBO, "pins" {"g": base pad, "d": collector pad, "s": emitter pad})'
        )
    return text


def judge(
    nl: Netlist,
    table: Mapping[str, Any],
    attempts: Iterable[tuple[int, Any, Exception | None]],
) -> SemiReport:
    """Judge every semiconductor over the stress scenarios; return the record.

    ``attempts`` is what ``stress.stress_attempts`` yields: ``(index, solution or
    None, error or None)`` per ``STRESS_SCENARIOS`` row. A solution's circuit is
    ``solution.c``.
    """
    report = SemiReport()
    solved: list[tuple[int, Any]] = []
    for index, solution, error in attempts:
        if error is not None or solution is None:
            report.unsolved.append(index)
            report.problems.append(
                f"STRESS_SCENARIOS[{index}] could not be solved: "
                f"{type(error).__name__}: {error}"
            )
        else:
            solved.append((index, solution))
    judged = judged_parts(nl, table, [s.c for _, s in solved])
    if judged and not solved:
        report.problems.append(
            f"no STRESS_SCENARIOS row was solved, so {', '.join(judged)} "
            "could not be judged: give the operating points that stress them hardest"
        )
    for ref in judged:
        _judge_part(nl, table, ref, solved, report)
    return report


def _judge_part(
    nl: Netlist,
    table: Mapping[str, Any],
    ref: str,
    solved: list[tuple[int, Any]],
    report: SemiReport,
) -> None:
    """Judge one part over the solved scenarios, adding its record and problems."""
    kind_name = nl.kind(ref)
    mpn = mpn_of(nl, ref)
    row = table.get(mpn) if mpn else None
    pins = resolve(nl, ref, row)
    if pins.kind is None:
        why = f"{ref} ({kind_name}) cannot be judged: {pins.problem}"
        if row is None or "pins" not in row:
            why += f"; {_hint(nl, ref)}"
        report.not_judged[ref] = why
        report.problems.append(why)
        return
    nets = {t: nl.net(ref, p) for t, p in pins.pads.items()}
    target = row.get("derate", DERATE_DEFAULT) if row is not None else DERATE_DEFAULT
    entry: dict[str, Any] = {"kind": pins.kind, "mpn": mpn, "target": target}
    problems: list[str] = []
    worst: dict[str, tuple[float, int]] = {}
    for index, solution in solved:
        held = element_nets(solution.c).get(ref, set())
        left = [
            f"pad {pins.pads[t]} ({t.upper()}) on {n}"
            for t, n in sorted(nets.items())
            if n not in held
        ]
        if left:
            problems.append(
                f"{ref} ({kind_name}) is not modelled on {', '.join(left)} in "
                f"STRESS_SCENARIOS[{index}]: dc.build does not model this symbol, so "
                "the voltage across it is not the board's; add it in "
                "circuits.scenario with ref "
                f"{ref!r} (circuit.D(anode net, cathode net, vf, ref={ref!r}) for a "
                "diode, a circuit.fets entry for a FET)"
            )
            break
        floating = [
            f"pad {pins.pads[t]} ({t.upper()}) on {n}"
            for t, n in sorted(nets.items())
            if solution.floating(n)
        ]
        if floating:
            problems.append(
                f"{ref} cannot be judged in STRESS_SCENARIOS[{index}]: "
                f"{', '.join(floating)} {'is' if len(floating) == 1 else 'are'} "
                "floating there (its 0 V is the solver's "
                "leak); fix the net that drives it in that scenario, or give it a "
                "pull"
            )
            continue
        for name, plus, minus in QUANTITIES[pins.kind]:
            v = abs(solution[nets[plus]] - solution[nets[minus]])
            if name not in worst or v > worst[name][0]:
                worst[name] = (v, index)
    for name, _plus, _minus in QUANTITIES[pins.kind]:
        rated = row.get(RATING_KEY[name]) if row is not None else None
        v, index = worst.get(name, (None, None))
        entry[name] = {
            "v": None if v is None else round(float(v), 3),
            "rated": rated,
            "limit_v": None if rated is None else round(target * rated, 4),
            "scenario": index,
        }
        if v is not None and rated is not None and v > target * rated:
            problems.append(
                f"{ref} |{_label(name)}| reaches {v:.2f} V in STRESS_SCENARIOS"
                f"[{index}], over {target:g} of its {rated:g} V rating "
                f"({target * rated:.2f} V)"
            )
    if row is None:
        problems.insert(
            0,
            f"no voltage rating on file for {ref} "
            + (f"({mpn}): add a SEMI_VRATED row" if mpn else "(no MPN)"),
        )
    report.parts[ref] = entry
    report.problems.extend(problems)


def _label(name: str) -> str:
    """Return how a message writes a quantity: ``Vds``, ``Vgs``, ``V(K) - V(A)``."""
    return {"vds": "Vds", "vgs": "Vgs", "vr": "V(K) - V(A)"}[name]
