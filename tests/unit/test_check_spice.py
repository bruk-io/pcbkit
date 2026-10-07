"""Unit tests for pcbkit.check.spice, with an ngspice that is a shell script.

``run`` starts ngspice with ``subprocess``, so the stand-in has to be a real
executable: each test writes a small ``sh`` script that records how it was called (its
arguments, its folder and a copy of the netlist it was given) and prints what the test
says ngspice prints. The module's own code does the rest. No real ngspice is started,
and ``tempfile`` is pointed inside the test's folder so nothing is left behind.

The bench generators (``rds_netlist``, ``vth_netlist`` and ``gate_charge_netlist``)
return text, so they are checked line by line: the model, the stimulus and the
statements that print the result.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click
import pytest

from pcbkit.check import spice
from pcbkit.kicad import env
from tests.fake_machine import FakeMachine

NETLIST = "* demo\nR1 a 0 1k\n.end\n"
# the name SPICE knows the P-channel model by: the word after ".model"
P_MODEL = spice.SQD50P03.split()[1]


@dataclass(frozen=True)
class FakeNgspice:
    """Where the fake ngspice script is and what it leaves behind when it runs."""

    path: Path
    seen: Path  # a copy of the netlist file it was given
    args: Path  # its arguments, one per line
    cwd: Path  # the folder it ran in


@pytest.fixture(autouse=True)
def temporary_files_stay_in_tmp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Make ``tempfile.mkdtemp`` create its folders inside the test's own folder."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    return scratch


def install_ngspice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str = "",
    stderr: str = "",
    before: str = "",
) -> FakeNgspice:
    """Make ``spice.ngspice_path`` return a script that records its run and prints.

    The script writes ``stdout`` and ``stderr`` as given. ``before`` is shell that runs
    before it prints (a test that wants a hung ngspice sleeps there).
    """
    notes = tmp_path / "notes"
    notes.mkdir()
    fake = FakeNgspice(
        tmp_path / "bin" / "ngspice",
        notes / "seen.cir",
        notes / "args.txt",
        notes / "cwd.txt",
    )
    fake.path.parent.mkdir()
    fake.path.write_text(
        "#!/bin/sh\n"
        "for last; do :; done\n"
        f'cp "$last" {shlex.quote(str(fake.seen))}\n'
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(fake.args))}\n"
        f"pwd > {shlex.quote(str(fake.cwd))}\n"
        f"{before}\n"
        f"printf '%s' {shlex.quote(stdout)}\n"
        f"printf '%s' {shlex.quote(stderr)} >&2\n",
        encoding="utf-8",
    )
    fake.path.chmod(0o755)
    monkeypatch.setattr(spice, "ngspice_path", lambda: str(fake.path))
    return fake


# --- run -----------------------------------------------------------------------------


def test_run_returns_the_values_ngspice_prints_and_its_whole_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parse ``name = value`` lines, whatever the padding, and return the output too."""
    printed = (
        "Circuit: demo\n"
        "ipk1                = 1.500000e+01\n"
        "vgs_on = -5.4\n"
        "Total analysis time (seconds) = 0.01\n"
    )
    install_ngspice(tmp_path, monkeypatch, stdout=printed)
    values, output = spice.run(NETLIST, ["ipk1", "vgs_on"])
    assert values == {"ipk1": 15.0, "vgs_on": -5.4}
    assert output == printed


def test_run_returns_standard_output_then_standard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Join the two streams in that order, as one text."""
    install_ngspice(tmp_path, monkeypatch, stdout="out line\n", stderr="err line\n")
    values, output = spice.run(NETLIST, [])
    assert values == {}
    assert output == "out line\nerr line\n"


def test_a_measurement_ngspice_prints_on_standard_error_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read the error stream as well as the output."""
    install_ngspice(tmp_path, monkeypatch, stderr="vgs_on = -5.4\n")
    assert spice.run(NETLIST, ["vgs_on"])[0] == {"vgs_on": -5.4}


def test_names_match_without_regard_to_case_and_keep_the_callers_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find ``IPK1`` for ``ipk1`` and ``vgs_on`` for ``VGS_ON``, keyed as asked."""
    install_ngspice(tmp_path, monkeypatch, stdout="IPK1 = 1.5e+01\nvgs_on = -5.4\n")
    values, _ = spice.run(NETLIST, ["ipk1", "VGS_ON"])
    assert values == {"ipk1": 15.0, "VGS_ON": -5.4}


@pytest.mark.parametrize(
    ("printed", "value"),
    [
        ("1.500000e+01", 15.0),
        ("-5.4", -5.4),
        ("+3", 3.0),
        ("2.5E-09", 2.5e-9),
        (".5", 0.5),
        ("0", 0.0),
        ("1.234500e-05 targ=  2.000000e-05 trig=  0.000000e+00", 1.2345e-05),
    ],
)
def test_the_value_is_the_first_number_after_the_equals_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, printed: str, value: float
) -> None:
    """Read signs and exponents, and ignore the ``targ=`` and ``trig=`` ngspice adds."""
    install_ngspice(tmp_path, monkeypatch, stdout=f"tq                  =  {printed}\n")
    assert spice.run(NETLIST, ["tq"])[0] == {"tq": value}


def test_a_measurement_that_is_not_printed_raises_runtime_error_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """List every missing name, and end the message with the tail of the output."""
    install_ngspice(tmp_path, monkeypatch, stdout="Circuit: demo\nipk1 = 1.5\n")
    with pytest.raises(RuntimeError) as caught:
        spice.run(NETLIST, ["ipk1", "vgs_on", "qg"])
    message = str(caught.value)
    assert message.startswith("ngspice did not report ['vgs_on', 'qg']")
    assert "Circuit: demo" in message


def test_the_error_carries_only_the_tail_of_a_long_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the last 3000 characters, where ngspice says what went wrong."""
    noise = "".join(f"line {n:04d} of ngspice chatter\n" for n in range(400))
    install_ngspice(tmp_path, monkeypatch, stdout="HEAD\n" + noise + "TAIL\n")
    with pytest.raises(RuntimeError) as caught:
        spice.run(NETLIST, ["vgs_on"])
    message = str(caught.value)
    assert "TAIL" in message
    assert "HEAD" not in message
    assert "line 0399" in message
    assert "line 0000" not in message
    assert len(message) < 3200


@pytest.mark.parametrize(
    ("printed", "wanted"),
    [
        ("vgs_on = -5.4\n", "vgs"),  # a longer name is not the shorter one
        ("vgs = -5.4\n", "vgs_on"),  # nor the other way round
        ("result: ipk1 = 3\n", "ipk1"),  # a measurement line starts with its name
        (
            "ipk1 = failed\n",
            "ipk1",
        ),  # ngspice's word for a measurement it could not make
        ("ipk1\n", "ipk1"),  # no value
    ],
)
def test_only_a_measurement_line_for_exactly_that_name_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, printed: str, wanted: str
) -> None:
    """Raise rather than read a value from a line that is not that measurement's."""
    install_ngspice(tmp_path, monkeypatch, stdout=printed)
    with pytest.raises(RuntimeError, match="did not report"):
        spice.run(NETLIST, [wanted])


def test_the_netlist_is_written_to_the_last_argument_and_ngspice_runs_in_batch_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass ``-b`` and the file, give the file the netlist, and run beside it."""
    fake = install_ngspice(tmp_path, monkeypatch, stdout="x = 1\n")
    spice.run(NETLIST, ["x"])
    flag, given = fake.args.read_text(encoding="utf-8").splitlines()
    assert flag == "-b"
    assert Path(given).name == "c.cir"
    assert fake.seen.read_text(encoding="utf-8") == NETLIST
    folder = Path(given).parent
    assert folder.parent.resolve() == (tmp_path / "scratch").resolve()
    assert Path(fake.cwd.read_text(encoding="utf-8").strip()).resolve() == (
        folder.resolve()
    )


def test_each_run_gets_its_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep one run's netlist from being overwritten by the next."""
    fake = install_ngspice(tmp_path, monkeypatch, stdout="x = 1\n")
    spice.run(NETLIST, ["x"])
    first = fake.args.read_text(encoding="utf-8").splitlines()[1]
    spice.run(NETLIST, ["x"])
    second = fake.args.read_text(encoding="utf-8").splitlines()[1]
    assert first != second
    assert Path(first).is_file()
    assert Path(second).is_file()


def test_a_hung_ngspice_is_stopped_at_the_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise subprocess.TimeoutExpired once ``TIMEOUT`` seconds have passed."""
    monkeypatch.setattr(spice, "TIMEOUT", 0.3)
    install_ngspice(tmp_path, monkeypatch, before="exec sleep 30")
    with pytest.raises(subprocess.TimeoutExpired):
        spice.run(NETLIST, ["x"])


# --- ngspice_path --------------------------------------------------------------------


def test_ngspice_path_says_how_to_get_ngspice_when_it_is_not_installed() -> None:
    """Raise a ClickException with the install command: the fake machine has none."""
    with pytest.raises(click.ClickException) as caught:
        spice.ngspice_path()
    message = caught.value.format_message()
    assert "ngspice not found" in message
    assert "brew install ngspice" in message
    assert "pcbkit doctor" in message


def test_ngspice_path_returns_the_ngspice_on_the_path(machine: FakeMachine) -> None:
    """Return the path of the ngspice that the machine finds."""
    exe = machine.exe(machine.usr_bin / "ngspice", "ngspice-45.2", on_path="ngspice")
    assert spice.ngspice_path() == str(exe)


def test_ngspice_path_raises_click_exception_when_find_ngspice_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail with the install hint whenever ``env.find_ngspice`` returns None."""
    monkeypatch.setattr(env, "find_ngspice", lambda: None)
    with pytest.raises(click.ClickException, match="ngspice not found"):
        spice.ngspice_path()


def test_ngspice_path_returns_the_path_of_the_tool_find_ngspice_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the ``path`` of the tool ``env.find_ngspice`` reports."""
    monkeypatch.setattr(
        env, "find_ngspice", lambda: env.Tool("/opt/fake/ngspice", "45")
    )
    assert spice.ngspice_path() == "/opt/fake/ngspice"


def test_run_raises_click_exception_when_ngspice_is_missing() -> None:
    """Fail with the install hint, not with a FileNotFoundError from subprocess."""
    with pytest.raises(click.ClickException, match="ngspice not found"):
        spice.run(NETLIST, ["x"])


# --- the benches ---------------------------------------------------------------------


def in_order(lines: list[str], *wanted: str) -> None:
    """Fail unless every line of ``wanted`` is present, and they come in this order."""
    for line in wanted:
        assert line in lines, f"{line!r} is not in the bench"
    positions = [lines.index(line) for line in wanted]
    assert positions == sorted(positions), f"{wanted} are out of order"


def test_the_two_models_are_a_p_channel_and_an_n_channel_vdmos() -> None:
    """Declare the power FET as ``pchan`` and the small one without it."""
    assert spice.SQD50P03.startswith(f".model {P_MODEL} VDMOS(pchan ")
    assert spice.N2N7002BK.startswith(".model N7002 VDMOS(")
    assert "pchan" not in spice.N2N7002BK


def test_the_p_channel_rds_bench_drives_the_gate_negative_and_prints_rds() -> None:
    """Force 2 A at a gate of -4.5 V and print |V(d)| / 2 as the on-resistance."""
    lines = spice.rds_netlist(4.5, 2.0).splitlines()
    assert lines[0] == "* rds"
    assert spice.SQD50P03 in lines
    assert "Vg g 0 -4.5" in lines
    assert "Id 0 d 2.0" in lines
    assert f"M1 d g 0 {P_MODEL}" in lines
    in_order(
        lines, ".op", ".control", "run", "let r = abs(v(d))/2.0", "print r", ".endc"
    )
    assert lines[-1] == ".end"


def test_the_n_channel_rds_bench_drives_the_gate_positive_and_the_current_out() -> None:
    """Force 2 A the other way at a gate of +4.5 V, on the small N-channel model."""
    lines = spice.rds_netlist(4.5, 2.0, model="n").splitlines()
    assert lines[0] == "* rds n"
    assert spice.N2N7002BK.format(vto=1.75) in lines
    assert "Vg g 0 4.5" in lines
    assert "Id 0 d -2.0" in lines
    assert "M1 d g 0 N7002" in lines
    in_order(
        lines, ".op", ".control", "run", "let r = abs(v(d))/2.0", "print r", ".endc"
    )
    assert lines[-1] == ".end"


def test_any_model_but_p_gets_the_n_channel_bench() -> None:
    """Treat "n", "x" and the empty string alike, as the docstring says."""
    n_bench = spice.rds_netlist(4.5, 2.0, model="n")
    assert spice.rds_netlist(4.5, 2.0, model="x") == n_bench
    assert spice.rds_netlist(4.5, 2.0, model="") == n_bench
    assert spice.rds_netlist(4.5, 2.0, model="p") != n_bench


def test_the_rds_bench_puts_its_arguments_in_the_stimulus_and_the_formula() -> None:
    """Use the caller's gate voltage and drain current, not fixed numbers."""
    lines = spice.rds_netlist(10.0, 5.0).splitlines()
    assert "Vg g 0 -10.0" in lines
    assert "Id 0 d 5.0" in lines
    assert "let r = abs(v(d))/5.0" in lines


def test_the_p_channel_vth_bench_ties_gate_to_drain_and_forces_250_microamps() -> None:
    """Measure |Vgs| at 250 uA with the gate on the drain, as datasheets define Vth."""
    lines = spice.vth_netlist("p").splitlines()
    assert lines[0] == "* vth"
    assert spice.SQD50P03 in lines
    assert "Id d 0 250u" in lines
    assert f"M1 d d 0 {P_MODEL}" in lines
    in_order(lines, ".op", ".control", "run", "let vth = v(d)", "print vth", ".endc")
    assert lines[-1] == ".end"
    assert spice.vth_netlist() == spice.vth_netlist("p")  # "p" is the default


def test_the_n_channel_vth_bench_forces_the_current_into_the_drain() -> None:
    """Flip the current's direction and use the N-channel model."""
    lines = spice.vth_netlist("n").splitlines()
    assert lines[0] == "* vth n"
    assert spice.N2N7002BK.format(vto=1.75) in lines
    assert "Id 0 d 250u" in lines
    assert "M1 d d 0 N7002" in lines
    in_order(lines, ".op", ".control", "run", "let vth = v(d)", "print vth", ".endc")
    assert lines[-1] == ".end"


def test_the_gate_charge_bench_charges_the_gate_with_1_ma_and_prints_the_charge() -> (
    None
):
    """Pull the gate through -10 V of Vgs at 1 mA and report the time times 1 mA."""
    lines = spice.gate_charge_netlist().splitlines()
    assert lines[0] == "* gate charge"
    assert spice.SQD50P03 in lines
    assert "Vdd src 0 15" in lines
    assert "Rl d 0 0.75" in lines
    assert f"M1 d g src {P_MODEL}" in lines
    assert "Ig g 0 1m" in lines
    assert "Cg0 g src 1p" in lines
    assert ".ic v(g)=15" in lines
    in_order(
        lines,
        ".tran 0.1u 200u uic",
        ".control",
        "run",
        "let vgs = v(g)-v(src)",
        "meas tran tq WHEN vgs=-10 FALL=1",
        "let qg = tq*1m",
        "print qg",
        ".endc",
        ".end",
    )
