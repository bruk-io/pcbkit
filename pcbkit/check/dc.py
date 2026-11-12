"""Small DC operating-point solver built straight from the netlist.

Resistors (including arrays and the shunt) are linear. LEDs and diodes are piecewise
(Vf + Ron when forward biased, open otherwise). MOSFETs are switches that turn on when
the gate overdrive passes a chosen threshold (pass the worst-case Vth per scenario).
Capacitors are open at DC. ICs, the MCU and off-board devices are represented by
whatever sources and loads a scenario adds.

``build`` knows the stock KiCad symbols it names (``Device:R``, ``Device:LED``, three
FETs); a board that uses other parts adds them to the circuit itself in its own
``scenario()`` (see ``Circuit.R``, ``Circuit.D``, ``Circuit.fets``). A resistor whose
value is 0 is modelled as 1 milliohm, and one whose value is a not-fitted mark alone
(``DNP``) is left out.

``build(nl, extended=True)`` is opt-in and also models ``Device:R_Small``, ``R_US`` and
``R_Small_US``; the two-pad ``Device`` diodes ``D``, ``D_Small``, ``D_Schottky_Small``,
``D_Zener``, ``D_Zener_Small``, ``D_TVS`` and ``D_TVS_Small`` by their A and K pins,
forward only (no breakdown; a TVS whose pins are A1 and A2 is held as an open element,
``<ref>.open``); the generic ``Device:Q_NMOS_*`` and ``Q_PMOS_*`` FETs, their gate,
drain and source read from the pin names; and a body diode for every FET
(``<ref>.body``, source to drain for an N-FET, drain to source for a P-FET). In extended
mode a part ``build`` models cannot be added to the circuit again (except one
``<ref>.breakdown`` element for a Zener or TVS, which ``forward_only`` lists because it
is held without breakdown), and no ref can be added twice: ``Circuit.R``, ``Circuit.D``
and ``Circuit.fets`` raise ValueError naming the part, so a scenario that also models a
part by hand is told instead of doubling it. Without ``extended`` nothing about this
changes.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from pcbkit.check.netlist import Netlist, parse_value

LED_VF = {"red": 2.0, "yellow": 2.0, "green": 2.9, "blue": 3.0, "white": 3.0}

# A resistor whose value reads as 0 (an enable link): low enough to be a short, and not
# a division by zero in the solve.
ZERO_OHM = 1e-3
# Values that mean the part is not fitted, written alone (case ignored).
NOT_FITTED = frozenset({"DNP", "DNF", "DNI", "NF", "NP", "NC", "NM", "NL", "NOPOP"})
# What a TVS whose pins are A1 and A2 (bidirectional) is held as: open below breakdown.
OPEN_OHM = 1e12

RESISTORS = ("Device:R",)
EXTENDED_RESISTORS = ("Device:R_Small", "Device:R_US", "Device:R_Small_US")
# two-pad diodes extended mode reads by their A and K pins: forward drop, on-resistance
EXTENDED_DIODES = {
    "Device:D": (0.7, 0.05),
    "Device:D_Small": (0.7, 0.05),
    "Device:D_Schottky_Small": (0.40, 0.05),
    "Device:D_Zener": (0.7, 0.05),
    "Device:D_Zener_Small": (0.7, 0.05),
    "Device:D_TVS": (0.7, 0.05),
    "Device:D_TVS_Small": (0.7, 0.05),
}
# The ref a scenario gives the breakdown it adds to a part extended holds forward only.
BREAKDOWN_SUFFIX = ".breakdown"
BODY_VF = 0.7
BODY_RON = 0.05
GENERIC_FET_RON = 0.05


def _part_of(ref: str) -> str:
    """Return the part an element belongs to: ``Q1`` of ``Q1.body``."""
    return ref.split(".", 1)[0]


class _GuardedFets(list):  # type: ignore[type-arg]
    """``Circuit.fets`` in extended mode: every FET added is checked by the circuit."""

    def __init__(self, circuit: Circuit, items: list[dict[str, Any]]) -> None:
        """Keep the circuit whose guard checks each FET's ref."""
        super().__init__(items)
        self.circuit = circuit

    def append(self, item: dict[str, Any]) -> None:
        """Check the FET's ref, then add it."""
        self.circuit._claim(str(item.get("ref", "")))
        super().append(item)

    def extend(self, items: Any) -> None:
        """Check and add each FET."""
        for item in items:
            self.append(item)

    def insert(self, index: Any, item: dict[str, Any]) -> None:
        """Check the FET's ref, then insert it."""
        self.circuit._claim(str(item.get("ref", "")))
        super().insert(index, item)

    def __deepcopy__(self, memo: dict[int, Any]) -> _GuardedFets:
        """Copy the FETs and the circuit they belong to, without checking them again."""
        twin = _GuardedFets.__new__(_GuardedFets)
        memo[id(self)] = twin
        list.extend(twin, copy.deepcopy(list(self), memo))
        twin.circuit = copy.deepcopy(self.circuit, memo)
        return twin

    def __iadd__(self, items: Any) -> _GuardedFets:
        """Check and add each FET (``fets += [...]``)."""
        self.extend(items)
        return self


class Circuit:
    """A DC circuit: resistors, diodes, FET switches, fixed nets and current sources."""

    def __init__(self) -> None:
        """Start empty: no elements, no fixed nets."""
        self.res: list[tuple[str, str, float, str]] = []  # (a, b, R, ref)
        self.diodes: list[tuple[str, str, float, float, str]] = []  # a, c, Vf, Ron, ref
        self.fets: list[dict[str, Any]] = []  # kind, g, d, s, vth, ron, ref
        self.fixed: dict[str, float] = {}  # net -> V
        self.isrc: list[tuple[str, float]] = []  # (net, amps into net)
        # S to ground on every net so floating nets solve to ~0 V
        self.leak = 1e-9
        self.state: dict[str, list[bool]] = {}
        # extended mode only: the parts build modelled, and the refs added since
        self._built: set[str] | None = None
        self._added: set[str] = set()
        # parts modelled without breakdown (extended Zeners and TVSs): a check that
        # sees reverse voltage across one cannot know whether it conducts
        self.forward_only: set[str] = set()

    def _guard(self) -> None:
        """Start refusing a second model of a part (``build(nl, extended=True)``)."""
        built = {ref for *_x, ref in self.res} | {ref for *_x, ref in self.diodes}
        built |= {str(f.get("ref", "")) for f in self.fets}
        self._built = {_part_of(r) for r in built if r}
        self.fets = _GuardedFets(self, list(self.fets))

    def _claim(self, ref: str) -> None:
        """Raise ValueError if ``ref`` would model a part a second time (extended)."""
        if self._built is None or not ref:
            return
        part = _part_of(ref)
        breakdown = ref == part + BREAKDOWN_SUFFIX and part in self.forward_only
        if part in self._built and not breakdown:
            raise ValueError(
                f"{part} is already modelled by dc.build(extended=True): remove the "
                "hand model from circuits.scenario, or build without extended"
            )
        if ref in self._added:
            raise ValueError(f"{ref} is already modelled in this circuit")
        self._added.add(ref)

    # ---- building
    def R(self, a: str | None, b: str | None, r: float, ref: str = "") -> None:
        """Add a resistor between nets ``a`` and ``b`` (ignored if either is open)."""
        self._claim(ref)
        if a and b and a != b:
            self.res.append((a, b, float(r), ref))

    def D(
        self, a: str | None, c: str | None, vf: float, ron: float = 1.0, ref: str = ""
    ) -> None:
        """Add a diode from anode ``a`` to cathode ``c``: Vf plus Ron when on."""
        self._claim(ref)
        if a and c:
            self.diodes.append((a, c, vf, ron, ref))

    def fix(self, net: str, v: float) -> Circuit:
        """Hold ``net`` at ``v`` volts."""
        self.fixed[net] = float(v)
        return self

    def free(self, net: str) -> Circuit:
        """Stop holding ``net`` at a voltage."""
        self.fixed.pop(net, None)
        return self

    def inject(self, net: str, amps: float) -> None:
        """Push ``amps`` into ``net`` (negative: draw it out)."""
        self.isrc.append((net, amps))

    # ---- solving
    def nets(self) -> list[str]:
        """Return every net the circuit touches, sorted."""
        s = set(self.fixed)
        for a, b, *_ in self.res:
            s |= {a, b}
        for a, c, *_ in self.diodes:
            s |= {a, c}
        for f in self.fets:
            s |= {f["g"], f["d"], f["s"]}
        for n, _ in self.isrc:
            s.add(n)
        s.discard(None)
        return sorted(s)

    def _solve_linear(self, d_on: list[bool], f_on: list[bool]) -> dict[str, float]:
        """Solve the linear circuit for one guess of which diodes and FETs conduct."""
        nets = self.nets()
        unk = [n for n in nets if n not in self.fixed]
        idx = {n: i for i, n in enumerate(unk)}
        size = len(unk)
        G = np.zeros((size, size))
        rhs = np.zeros(size)

        def stamp(a: str, b: str, g: float, vsrc: float = 0.0) -> None:
            # branch current a->b = g*(Va - Vb - vsrc)
            for n, sgn in ((a, 1), (b, -1)):
                if n in idx:
                    i = idx[n]
                    G[i, i] += g
                    other = b if n == a else a
                    if other in idx:
                        G[i, idx[other]] -= g
                    else:
                        rhs[i] += g * self.fixed.get(other, 0.0)
                    rhs[i] += sgn * g * vsrc

        for a, b, r, _ in self.res:
            stamp(a, b, 1.0 / r)
        for k, (a, c, vf, ron, _) in enumerate(self.diodes):
            if d_on[k]:
                stamp(a, c, 1.0 / ron, vf)
        for k, f in enumerate(self.fets):
            if f_on[k]:
                stamp(f["d"], f["s"], 1.0 / f["ron"])
        for n, amps in self.isrc:
            if n in idx:
                rhs[idx[n]] += amps
        for i in range(size):
            G[i, i] += self.leak
        V = np.linalg.solve(G, rhs) if size else []
        out = dict(self.fixed)
        out.update({n: float(V[idx[n]]) for n in unk})
        return out

    def solve(self, max_iter: int = 40) -> Solution:
        """Find the operating point; raise RuntimeError if it does not settle."""
        d_on = [False] * len(self.diodes)
        f_on = [False] * len(self.fets)
        for _ in range(max_iter):
            V = self._solve_linear(d_on, f_on)
            nd = [
                (V[a] - V[c]) > vf * 0.999 if not on else (V[a] - V[c] - vf) >= -1e-9
                for (a, c, vf, _, _), on in zip(self.diodes, d_on)
            ]
            nf = []
            for f in self.fets:
                vgs = V.get(f["g"], 0.0) - V.get(f["s"], 0.0)
                nf.append(vgs > f["vth"] if f["kind"] == "n" else vgs < f["vth"])
            if nd == d_on and nf == f_on:
                self.state = {"diodes": d_on, "fets": f_on}
                return Solution(self, V)
            d_on, f_on = nd, nf
        raise RuntimeError("DC solve did not converge")


class Solution:
    """A solved circuit: net voltages and the state of its diodes and FETs."""

    def __init__(self, circ: Circuit, V: dict[str, float]) -> None:
        """Hold the circuit and its net voltages."""
        self.c = circ
        self.V = V

    def __getitem__(self, net: str) -> float:
        """Return the voltage of ``net`` (0 V for a net the circuit never mentions)."""
        return self.V.get(net, 0.0)

    def vgs(self, ref: str) -> float:
        """Return the gate-source voltage of the FET ``ref``."""
        f = next(f for f in self.c.fets if f["ref"] == ref)
        return self[f["g"]] - self[f["s"]]

    def fet_on(self, ref: str) -> bool:
        """Return True if the FET ``ref`` conducts."""
        k = next(i for i, f in enumerate(self.c.fets) if f["ref"] == ref)
        return self.c.state["fets"][k]

    def diode_current(self, ref: str) -> float:
        """Return the forward current (A) of the diode ``ref``, 0 when it is off."""
        for k, (a, c, vf, ron, r) in enumerate(self.c.diodes):
            if r == ref:
                on = self.c.state["diodes"][k]
                return (self[a] - self[c] - vf) / ron if on else 0.0
        raise KeyError(ref)

    def resistor_power(self) -> dict[str, float]:
        """Return the power (W) in every resistor, by its name."""
        return {ref: (self[a] - self[b]) ** 2 / r for a, b, r, ref in self.c.res}

    def floating(self, net: str) -> bool:
        """Return True if no conducting path joins ``net`` to a driven net.

        A driven net is one the circuit fixes or a current source (``inject``) feeds.
        """
        adj: dict[str, set[str]] = {}

        def link(a: str, b: str) -> None:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

        for a, b, _, _ in self.c.res:
            link(a, b)
        for k, (a, c, *_rest) in enumerate(self.c.diodes):
            if self.c.state["diodes"][k]:
                link(a, c)
        for k, f in enumerate(self.c.fets):
            if self.c.state["fets"][k]:
                link(f["d"], f["s"])
        sources = {n for n, _ in self.c.isrc}
        seen: set[str] = set()
        stack = [net]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            if n in self.c.fixed or n in sources:
                return False
            seen.add(n)
            stack += list(adj.get(n, ()))
        return True

    def resistor_current(self, ref: str) -> float:
        """Return the current (A) from a to b in the resistor ``ref``."""
        a, b, r, _ = next(x for x in self.c.res if x[3] == ref)
        return (self[a] - self[b]) / r


def _fet(
    kind: str,
    g: str | None,
    d: str | None,
    s: str | None,
    vth: float,
    ron: float,
    ref: str,
) -> dict[str, Any]:
    """Return a FET switch: ``kind`` is "n" or "p", ``vth`` its threshold in volts."""
    return {"kind": kind, "g": g, "d": d, "s": s, "vth": vth, "ron": ron, "ref": ref}


def _resistance(value: str) -> float | None:
    """Return a resistor's value in ohms; None for a not-fitted mark, 1 mohm for 0."""
    if value.strip().upper() in NOT_FITTED:
        return None
    r = parse_value(value)
    return ZERO_OHM if r == 0 else r


def _pads_by_name(
    nl: Netlist, ref: str, names: tuple[str, ...]
) -> dict[str, str] | None:
    """Return ``{name: net}`` when each pin name in ``names`` is on exactly one net.

    Every pad of the part must carry one of ``names`` (case ignored); otherwise, or when
    one name's pads are on two nets, return None and the part is not modelled.
    """
    found: dict[str, set[str]] = {}
    for pad, func in nl.functions(ref).items():
        name = func.strip().upper()
        if name not in names:
            return None
        found.setdefault(name, set()).add(str(nl.net(ref, pad)))
    if set(found) != set(names) or any(len(n) != 1 for n in found.values()):
        return None
    return {name: next(iter(nets)) for name, nets in found.items()}


def _body(c: Circuit, kind: str, d: str | None, s: str | None, ref: str) -> None:
    """Add a FET's body diode: source to drain (N), drain to source (P)."""
    if kind == "n":
        c.D(s, d, BODY_VF, BODY_RON, ref + ".body")
    else:
        c.D(d, s, BODY_VF, BODY_RON, ref + ".body")


def build(
    nl: Netlist,
    nfet_vth: float = 2.0,
    pfet_vth: float = -2.0,
    extended: bool = False,
) -> Circuit:
    """Return a circuit with every passive and discrete part of the netlist.

    ``extended`` (opt-in, default False) adds the models the module docstring lists
    and refuses a part modelled twice; without it the circuit is built as it always
    was. Raise ValueError for a resistor value that is neither a value nor a not-fitted
    mark.
    """
    c = Circuit()
    resistors = RESISTORS + (EXTENDED_RESISTORS if extended else ())
    for ref, p in nl.parts.items():
        kind = nl.kind(ref)

        def pn(pin: int, ref: str = ref) -> str | None:
            return nl.net(ref, pin)

        if kind in resistors:
            r = _resistance(p["value"])
            if r is not None:
                c.R(pn(1), pn(2), r, ref)
        elif kind == "Device:R_Pack04":
            r = parse_value(p["value"])
            for a, b in ((1, 8), (2, 7), (3, 6), (4, 5)):
                c.R(pn(a), pn(b), r, f"{ref}.{a}")
        elif kind == "Device:R_Shunt":
            c.R(pn(1), pn(4), parse_value(p["value"]), ref)
            c.R(pn(1), pn(2), 1e-4, ref + ".senseP")
            c.R(pn(4), pn(3), 1e-4, ref + ".senseN")
        elif kind == "Device:LED":
            vf = LED_VF.get(p["value"].lower(), 2.0)
            c.D(pn(2), pn(1), vf, 20.0, ref)
        elif kind == "Device:D_Schottky":
            c.D(pn(2), pn(1), 0.40, 0.05, ref)
        elif kind == "Transistor_FET:Q_PMOS_GDS":
            c.fets.append(_fet("p", pn(1), pn(2), pn(3), pfet_vth, 0.011, ref))
            c.D(pn(2), pn(3), 0.7, 0.05, ref + ".body")  # body diode: drain -> source
        elif kind == "Transistor_FET:AO3401A":
            c.fets.append(_fet("p", pn(1), pn(3), pn(2), -1.3, 0.060, ref))
            c.D(pn(3), pn(2), 0.7, 0.05, ref + ".body")
        elif kind == "Transistor_FET:2N7002":
            c.fets.append(_fet("n", pn(1), pn(3), pn(2), nfet_vth, 5.0, ref))
            if extended:
                _body(c, "n", pn(3), pn(2), ref)
        elif extended:
            _build_extended(nl, c, ref, kind, nfet_vth, pfet_vth)
    if extended:
        c._guard()
    return c


def _build_extended(
    nl: Netlist, c: Circuit, ref: str, kind: str, nfet_vth: float, pfet_vth: float
) -> None:
    """Add the extended model of one part, if ``kind`` has one."""
    if kind in EXTENDED_DIODES:
        vf, ron = EXTENDED_DIODES[kind]
        if kind.startswith(("Device:D_Zener", "Device:D_TVS")):
            c.forward_only.add(ref)
        ak = _pads_by_name(nl, ref, ("A", "K"))
        if ak is not None:
            c.D(ak["A"], ak["K"], vf, ron, ref)
        elif kind.startswith("Device:D_TVS"):
            both = _pads_by_name(nl, ref, ("A1", "A2"))
            if both is not None:
                c.R(both["A1"], both["A2"], OPEN_OHM, ref + ".open")
        return
    for prefix, fet_kind, vth in (
        ("Device:Q_NMOS_", "n", nfet_vth),
        ("Device:Q_PMOS_", "p", pfet_vth),
    ):
        if kind.startswith(prefix):
            gds = _pads_by_name(nl, ref, ("G", "D", "S"))
            if gds is not None:
                g, d, s = gds["G"], gds["D"], gds["S"]
                c.fets.append(_fet(fet_kind, g, d, s, vth, GENERIC_FET_RON, ref))
                _body(c, fet_kind, d, s, ref)
            return
