"""ngspice helpers and fitted MOSFET models.

``run`` runs a netlist in batch mode and reads the ``name = value`` lines of its
``meas`` statements. The two models are VDMOS fits to datasheet numbers (a P-channel
power FET and a small N-channel FET); the three ``*_netlist`` functions build the
datasheet-style test benches that a check runs on a model before it trusts a circuit
result, and a board's own ``circuits.py`` builds the circuit decks that use them.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import click

from pcbkit.kicad import env

SQD50P03 = (
    ".model SQD50P03 VDMOS(pchan Vto=-2.35 Kp=133.8 Rd=5.5m Rs=0.52m Rg=2.85 "
    "Cgdmax=1.6n Cgdmin=0.5n a=0.3 Cgs=3.0n Cjo=0.1n Rb=4m Is=1e-11 N=1.1 "
    "lambda=0.005)"
)
N2N7002BK = (
    ".model N7002 VDMOS(Vto={vto} Kp=0.9 Rd=0.55 Rs=0.15 Rg=5 "
    "Cgdmax=12p Cgdmin=4p a=0.5 Cgs=30p Cjo=6p Rb=0.3)"
)

# Seconds one ngspice run may take.
TIMEOUT = 600.0


def ngspice_path() -> str:
    """Return the path of ngspice, or raise a ClickException saying how to get it."""
    tool = env.find_ngspice()
    if tool is None:
        raise click.ClickException(
            "ngspice not found: install it (brew install ngspice). "
            "`pcbkit doctor` shows what is missing."
        )
    return tool.path


def run(netlist: str, meas_names: list[str]) -> tuple[dict[str, float], str]:
    """Run a netlist in batch mode; return ``({name: float}, output)``.

    The values are the ``name = value`` lines ngspice prints for each of
    ``meas_names``. Raise RuntimeError, with the tail of the output, if any is missing.
    """
    work = Path(tempfile.mkdtemp())
    cir = work / "c.cir"
    cir.write_text(netlist, encoding="utf-8")
    done = subprocess.run(
        [ngspice_path(), "-b", str(cir)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=str(work),
    )
    out = done.stdout + done.stderr
    vals: dict[str, float] = {}
    for name in meas_names:
        found = re.search(
            rf"^\s*{re.escape(name)}\s*=\s*([-+0-9.eE]+)", out, re.M | re.I
        )
        if found:
            vals[name] = float(found.group(1))
    missing = [n for n in meas_names if n not in vals]
    if missing:
        raise RuntimeError(f"ngspice did not report {missing}\n{out[-3000:]}")
    return vals, out


def rds_netlist(vgs: float, i_d: float, model: str = "p") -> str:
    """Return a bench that forces ``i_d`` through the FET at ``vgs`` and prints Rds(on).

    ``model`` is "p" for the P-channel power FET and anything else for the small
    N-channel one.
    """
    if model == "p":
        return f"""* rds
{SQD50P03}
Vg g 0 {-vgs}
Id 0 d {i_d}
M1 d g 0 SQD50P03
.op
.control
run
let r = abs(v(d))/{i_d}
print r
.endc
.end
"""
    return f"""* rds n
{N2N7002BK.format(vto=1.75)}
Vg g 0 {vgs}
Id 0 d {-i_d}
M1 d g 0 N7002
.op
.control
run
let r = abs(v(d))/{i_d}
print r
.endc
.end
"""


def vth_netlist(model: str = "p") -> str:
    """Return a bench with the gate tied to the drain and 250 uA forced through it.

    |Vgs| is then the threshold as datasheets define it.
    """
    if model == "p":
        return f"""* vth
{SQD50P03}
Id d 0 250u
M1 d d 0 SQD50P03
.op
.control
run
let vth = v(d)
print vth
.endc
.end
"""
    return f"""* vth n
{N2N7002BK.format(vto=1.75)}
Id 0 d 250u
M1 d d 0 N7002
.op
.control
run
let vth = v(d)
print vth
.endc
.end
"""


def gate_charge_netlist() -> str:
    """Return a datasheet-style Qg bench: VDD -15 V, ID about -20 A, 1 mA gate."""
    return f"""* gate charge
{SQD50P03}
Vdd src 0 15
Rl d 0 0.75
M1 d g src SQD50P03
Ig g 0 1m
Cg0 g src 1p
.ic v(g)=15
.tran 0.1u 200u uic
.control
run
let vgs = v(g)-v(src)
meas tran tq WHEN vgs=-10 FALL=1
let qg = tq*1m
print qg
.endc
.end
"""
