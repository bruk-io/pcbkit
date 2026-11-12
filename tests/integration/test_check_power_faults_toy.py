"""Integration: the ``power`` group's fault-state check, run as ``pcbkit check`` does.

A regulator U2 whose enable (pad 3, net EN) is held low by a 100k pull-down is added to
the generic toy board (``tests/check_toy.py``), and ``FAULT_STATES`` to its specs: in
two conditions (the toy's scenario at 5 V and at 5.5 V, nothing driving EN) the enable
must be off, under 0.4 V. The real command line runs in a child pytest with ``fake_nl``
serving the netlist, so no KiCad is needed. The control passes; each planted mistake
turns the check red, with the message that says why.
"""

from __future__ import annotations

from pathlib import Path

from tests import check_toy
from tests.check_toy import SPECS, Toy
from tests.integration.test_check_plugin import Run, run

CHECK = "test_fault_state_pins"
TABLE = """
FAULT_STATES = {
    "logic_absent": {"scenario": {"vin": 5.0}, "pins": [("U2", 3, (0, 0.4))]},
    "input_high": {"scenario": {"vin": 5.5}, "pins": [("U2", 3, (0, 0.4))]},
}
"""


def board() -> Toy:
    """Return the toy with U2, its enable pulled down by R7."""
    toy = check_toy.good()
    toy.add(
        "U2",
        "LDO",
        "Regulator:LDO",
        {"1": ("VIN", "IN"), "2": ("GND", "GND"), "3": ("EN", "EN")},
    )
    check_toy.resistor(toy, "R7", "100k", "EN", "GND")
    return toy


def go(tmp_path: Path, name: str, toy: Toy, specs: str = SPECS + TABLE) -> Run:
    """Run only this module's checks, with the ``power`` group on."""
    root = check_toy.write_project(tmp_path / name, toy, ["power"], specs=specs)
    return run(root, "test_faults")


def said(entry: dict[str, object]) -> str:
    """Return what a failed check said: its message's first line, without the type."""
    first = str(entry["message"]).splitlines()[0]
    prefix = "AssertionError: "
    assert first.startswith(prefix), first
    return first[len(prefix) :]


def outcomes(result: Run) -> dict[str, str]:
    """Return each check's outcome by its name and parameter."""
    return {k.partition("::")[2]: v["outcome"] for k, v in result.checks.items()}


def test_the_control_passes_and_records_each_pin(tmp_path: Path) -> None:
    """Pass both conditions; record the pin driven at 0 V with its window."""
    result = go(tmp_path, "control", board())
    assert result.done.returncode == 0, result.done.stdout
    assert outcomes(result) == {
        f"{CHECK}[input_high]": "passed",
        f"{CHECK}[logic_absent]": "passed",
    }
    entry = result.entry(f"{CHECK}[logic_absent]")
    assert entry["numbers"] == {"pins": {"U2 pad 3": [0.0, 0, 0.4, "driven"]}}
    assert entry["gate"] == "sch"
    assert entry["group"] == "power"


def test_a_removed_pull_down_fails_as_floating(tmp_path: Path) -> None:
    """The leak reads 0 V, inside the window; the check fails on floating, twice."""
    toy = board()
    del toy.parts["R7"], toy.pins["R7"]
    result = go(tmp_path, "no-pull", toy)
    assert result.done.returncode == 1
    for condition in ("logic_absent", "input_high"):
        entry = result.entry(f"{CHECK}[{condition}]")
        assert entry["outcome"] == "failed"
        assert said(entry) == (
            f"condition {condition}: U2 pad 3 (EN) is floating: nothing the DC model "
            "holds joins EN to a net the scenario fixes, so its 0 V reading is the "
            "solver's leak, not a level (pull it to a net the scenario fixes)"
        )
        assert entry["numbers"]["pins"] == {"U2 pad 3": [0.0, 0, 0.4, "floating"]}


def test_the_pull_moved_to_the_other_rail_fails_outside_the_window(
    tmp_path: Path,
) -> None:
    """R7 to +3V3 holds EN at 3.3 V: outside (0, 0.4)."""
    toy = board()
    toy.move_pin("R7", "2", "+3V3")
    entry = go(tmp_path, "pull-up", toy).entry(f"{CHECK}[logic_absent]")
    assert entry["outcome"] == "failed"
    assert said(entry) == (
        "condition logic_absent: U2 pad 3 (EN) is at 3.300 V, outside 0 to 0.4 V"
    )
    assert entry["numbers"]["pins"] == {"U2 pad 3": [3.3, 0, 0.4, "driven"]}


def test_a_row_naming_a_pad_not_in_the_netlist_fails(tmp_path: Path) -> None:
    """Pad 9 of U2 is not in the netlist: ``no net``, naming the pads it has."""
    specs = SPECS + TABLE.replace('("U2", 3, (0, 0.4))]}', '("U2", 9, (0, 0.4))]}', 1)
    result = go(tmp_path, "bad-pad", board(), specs)
    entry = result.entry(f"{CHECK}[logic_absent]")
    assert entry["outcome"] == "failed"
    assert said(entry) == (
        "condition logic_absent: U2 has no pad 9 on a net in the netlist "
        "(its pads are 1, 2, 3)"
    )
    assert entry["numbers"]["pins"] == {"U2 pad 9": [None, 0, 0.4, "no net"]}
    assert result.outcome(f"{CHECK}[input_high]") == "passed"


def test_a_controller_pin_on_the_enable_fails_until_it_is_listed(
    tmp_path: Path,
) -> None:
    """An absent controller's pin the model leaves out: named; listed, it passes."""
    toy = board()
    toy.add("U3", "LOGIC", "Logic:Gate", {"1": ("EN", "Y"), "2": ("GND", "GND")})
    entry = go(tmp_path, "logic", toy).entry(f"{CHECK}[logic_absent]")
    assert entry["outcome"] == "failed"
    assert "the DC model leaves out U3 pad 1 (Logic:Gate) on EN" in entry["message"]
    assert entry["numbers"]["not modelled"] == {
        "U2 pad 3": ["U3 pad 1 (Logic:Gate) on EN"]
    }
    listed = SPECS + TABLE.replace(
        '("U2", 3, (0, 0.4))]}', '("U2", 3, (0, 0.4)), ("U3", 1, (-0.3, 0.4))]}'
    )
    result = go(tmp_path, "logic-listed", toy, listed)
    assert result.done.returncode == 0, result.done.stdout


def test_a_scenario_that_does_not_converge_fails_by_name(tmp_path: Path) -> None:
    """Say the solve did not converge in that condition, not a traceback."""
    circuits = check_toy.CIRCUITS + (
        "\n\nclass _Stuck:\n"
        "    def solve(self):\n"
        '        raise RuntimeError("DC solve did not converge")\n'
        "\n\n_scenario = scenario\n\n\n"
        "def scenario(nl, vin=5.0, drive=None):\n"
        "    if vin > 5.2:\n"
        "        return _Stuck()\n"
        "    return _scenario(nl, vin, drive)\n"
    )
    root = check_toy.write_project(
        tmp_path / "stuck", board(), ["power"], SPECS + TABLE, circuits
    )
    result = run(root, "test_faults")
    entry = result.entry(f"{CHECK}[input_high]")
    assert entry["outcome"] == "failed"
    assert said(entry) == "the DC solve did not converge in condition input_high"
    assert entry["numbers"]["pins"] == {"U2 pad 3": [None, 0, 0.4, "unsolved"]}
    assert result.outcome(f"{CHECK}[logic_absent]") == "passed"


def test_a_missing_table_fails_by_name(tmp_path: Path) -> None:
    """With ``power`` on and no ``FAULT_STATES``, the check fails naming it."""
    result = go(tmp_path, "missing", board(), SPECS)
    entry = result.entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "specs.FAULT_STATES is not defined" in entry["message"]
