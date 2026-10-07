"""Small DC operating-point solver built straight from the netlist.

Resistors (including arrays and the shunt) are linear. LEDs and diodes are piecewise
(Vf + Ron when forward biased, open otherwise). MOSFETs are switches that turn on when
the gate overdrive passes a chosen threshold (pass the worst-case Vth per scenario).
Capacitors are open at DC. ICs, the MCU and off-board devices are represented by
whatever sources and loads a scenario adds.

``build`` knows the stock KiCad symbols it names (``Device:R``, ``Device:LED``, three
FETs); a board that uses other parts adds them to the circuit itself in its own
``scenario()`` (see ``Circuit.R``, ``Circuit.D``, ``Circuit.fets``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from pcbkit.check.netlist import Netlist, parse_value

LED_VF = {"red": 2.0, "yellow": 2.0, "green": 2.9, "blue": 3.0, "white": 3.0}


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

    # ---- building
    def R(self, a: str | None, b: str | None, r: float, ref: str = "") -> None:
        """Add a resistor between nets ``a`` and ``b`` (ignored if either is open)."""
        if a and b and a != b:
            self.res.append((a, b, float(r), ref))

    def D(
        self, a: str | None, c: str | None, vf: float, ron: float = 1.0, ref: str = ""
    ) -> None:
        """Add a diode from anode ``a`` to cathode ``c``: Vf plus Ron when on."""
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
        """Return True if no conducting path joins ``net`` to a driven net."""
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
        seen: set[str] = set()
        stack = [net]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            if n in self.c.fixed:
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


def build(nl: Netlist, nfet_vth: float = 2.0, pfet_vth: float = -2.0) -> Circuit:
    """Return a circuit with every passive and discrete part of the netlist."""
    c = Circuit()
    for ref, p in nl.parts.items():
        kind = nl.kind(ref)

        def pn(pin: int, ref: str = ref) -> str | None:
            return nl.net(ref, pin)

        if kind == "Device:R":
            c.R(pn(1), pn(2), parse_value(p["value"]), ref)
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
    return c
