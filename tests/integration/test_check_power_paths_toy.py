"""Integration: the ``power`` group's power-path checks, run as ``pcbkit check`` does.

The generic toy board (``tests/check_toy.py``) gets a power tree: a USB input and a
battery input (through a reverse-protection P-FET) diode-ORed into ``VIN``, a
regulator from ``VIN`` to ``+3V3``, a ferrite to ``+3V3A`` and a level shifter powered
from ``VIN`` and ``+3V3``. ``POWER_PATHS`` and ``SUPPLY_NETS`` go into its specs. The real command
line runs in a child pytest with ``fake_nl`` serving the netlist, so no KiCad is
needed. The control passes; each planted mistake turns exactly its check red, with the
message that says why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import check_toy
from tests.check_toy import SPECS, Toy
from tests.integration.test_check_plugin import Run, run

MODULE = "test_power_paths"
PATH = "test_power_path"
UNLISTED = "test_no_unlisted_power_path"
MODELLED = "test_power_path_parts_are_modelled"
ROWS = ["VBUS->VIN", "VBAT->VIN", "VIN->+3V3", "+3V3->+3V3A", "+3V3A->+3V3"]
SUPPLIES = '\nSUPPLY_NETS = ["GND", "VBUS", "VBAT", "VIN", "+3V3", "+3V3A"]\n'
TABLE = (
    """
POWER_PATHS = {
    "nets": ["VBUS", "VBAT", "VIN", "+3V3", "+3V3A"],
    "paths": [
        ("VBUS", "VIN", ["D2"]),
        ("VBAT", "VIN", ["D3"]),
        ("VIN", "+3V3", ["U3"]),
        ("+3V3", "+3V3A", ["FB1"]),
        ("+3V3A", "+3V3", ["FB1"]),
    ],
    "parts": {"U3": [(1, 3)], "U4": []},
}
"""
    + SUPPLIES
)


def pfet(toy: Toy, ref: str, gate: str, source: str, drain: str) -> None:
    """Add a P-FET whose pins are named G, S and D (pads 1, 2, 3)."""
    toy.add(
        ref,
        "AO3401A",
        "Transistor_FET:AO3401A",
        {"1": (gate, "G"), "2": (source, "S"), "3": (drain, "D")},
    )


def schottky(toy: Toy, ref: str, anode: str, cathode: str) -> None:
    """Add a two-pad Schottky diode, pins named A and K."""
    toy.add(ref, "SS14", "Device:D_Schottky", {"1": (cathode, "K"), "2": (anode, "A")})


def board() -> Toy:
    """Return the toy with its power tree."""
    toy = check_toy.good()
    toy.add(
        "J1",
        "USB",
        "Connector:USB_B_Micro",
        {"1": ("VBUS", "VBUS"), "5": ("GND", "GND")},
    )
    schottky(toy, "D2", "VBUS", "VIN")
    toy.add(
        "J2",
        "BAT",
        "Connector:Conn_01x02",
        {"1": ("VBAT_IN", "Pin_1"), "2": ("GND", "Pin_2")},
    )
    pfet(toy, "Q1", "GND", "VBAT", "VBAT_IN")
    schottky(toy, "D3", "VBAT", "VIN")
    toy.add(
        "U3",
        "LDO",
        "Regulator:LDO",
        {"1": ("VIN", "IN"), "2": ("GND", "GND"), "3": ("+3V3", "OUT")},
    )
    toy.add(
        "FB1",
        "600R",
        "Device:FerriteBead",
        {"1": ("+3V3", "~"), "2": ("+3V3A", "~")},
    )
    toy.add("C2", "1u", "Device:C", {"1": ("+3V3A", "~"), "2": ("GND", "~")})
    toy.add("C3", "100n", "Device:C", {"1": ("VIN", "~"), "2": ("+3V3", "~")})
    toy.add(
        "U4",
        "SHIFTER",
        "Logic:Shifter",
        {"1": ("VIN", "VCCA"), "2": ("+3V3", "VCCB"), "3": ("GND", "GND")},
    )
    return toy


def go(tmp_path: Path, name: str, toy: Toy, specs: str = SPECS + TABLE) -> Run:
    """Run only this module's checks, with the ``power`` group on."""
    root = check_toy.write_project(tmp_path / name, toy, ["power"], specs=specs)
    return run(root, MODULE)


def outcomes(result: Run) -> dict[str, str]:
    """Return each check's outcome by its name and parameter."""
    return {k.partition("::")[2]: v["outcome"] for k, v in result.checks.items()}


def green() -> dict[str, str]:
    """Return every check of the module passing."""
    found = {f"{PATH}[{row}]": "passed" for row in ROWS}
    found.update({UNLISTED: "passed", MODELLED: "passed"})
    return found


def test_the_control_passes_and_records_every_path(tmp_path: Path) -> None:
    """Pass every check, at the ``sch`` gate, and record what a review reads."""
    result = go(tmp_path, "control", board())
    assert result.done.returncode == 0, result.done.stdout
    assert outcomes(result) == green()
    entry = result.entry(f"{PATH}[VIN->+3V3]")
    assert entry["gate"] == "sch"
    assert entry["group"] == "power"
    assert entry["numbers"]["power path"] == {
        "from": "VIN",
        "to": "+3V3",
        "refs": ["U3"],
        "ohms": 0.0,
        "path": ["VIN", "U3", "+3V3"],
        "without named": None,
    }
    numbers = result.entry(UNLISTED)["numbers"]
    assert numbers["max_ohm"] == 10
    pairs = numbers["power paths"]
    assert list(pairs) == ROWS
    assert all(v["power path"] and v["listed"] for v in pairs.values())
    modelled = result.entry(MODELLED)["numbers"]
    assert modelled == {
        "not modelled": {},
        "declared parts": {"U3": ["1 -> 3 (VIN -> +3V3)"], "U4": []},
    }


def test_plan_1_a_pfet_whose_drain_lands_on_a_rail_is_an_unlisted_path(
    tmp_path: Path,
) -> None:
    """Q1's drain on +3V3: its channel joins VBAT and +3V3 both ways."""
    toy = board()
    toy.move_pin("Q1", "3", "+3V3")
    result = go(tmp_path, "pfet", toy)
    assert outcomes(result) == {**green(), UNLISTED: "failed"}
    message = result.entry(UNLISTED)["message"]
    assert (
        'VBAT -> +3V3 is joined at 0 ohm (VBAT - Q1 - +3V3) and no row of POWER_PATHS["'
        'paths"] declares it, so current can flow from VBAT into +3V3'
    ) in message
    assert "+3V3 -> VBAT is joined at 0 ohm (+3V3 - Q1 - VBAT)" in message
    assert "(Q1 is a FET: its channel counts as conducting both ways" in message
    assert result.done.returncode == 1


def test_plan_1_as_written_a_drain_and_source_swap_is_not_seen(tmp_path: Path) -> None:
    """The plan's FET rule joins drain and source both ways, so a swap is the same."""
    toy = board()
    pfet(toy, "Q1", "GND", "VBAT_IN", "VBAT")
    result = go(tmp_path, "swap", toy)
    assert outcomes(result) == green()


def test_plan_2_a_regulator_output_moved_to_another_net_is_not_found(
    tmp_path: Path,
) -> None:
    """Only the VIN->+3V3 row goes red, naming U3, its pads and its declaration."""
    toy = board()
    toy.move_pin("U3", "3", "VOUT_X")
    result = go(tmp_path, "moved", toy)
    assert outcomes(result) == {**green(), f"{PATH}[VIN->+3V3]": "failed"}
    assert (
        "VIN->+3V3: declared path VIN->+3V3 through U3 not found: current cannot flow "
        "from VIN to +3V3 (U3: pads 1 on VIN, 2 on GND, 3 on VOUT_X; declared to pass "
        "1 -> 3)"
    ) in result.entry(f"{PATH}[VIN->+3V3]")["message"]
    record = result.entry(f"{PATH}[VIN->+3V3]")["numbers"]["power path"]
    assert (record["ohms"], record["path"], record["without named"]) == (
        None,
        None,
        None,
    )


def test_plan_3_an_undeclared_ic_across_two_rails_is_not_modelled(
    tmp_path: Path,
) -> None:
    """Only the modelled check goes red, naming U5, both nets and the ways out."""
    toy = board()
    toy.add(
        "U5",
        "MONITOR",
        "Power_Supervisor:Monitor",
        {"1": ("VIN", "VS"), "2": ("+3V3", "VDD"), "3": ("GND", "GND")},
    )
    result = go(tmp_path, "ic", toy)
    assert outcomes(result) == {**green(), MODELLED: "failed"}
    message = result.entry(MODELLED)["message"]
    assert (
        "U5 (Power_Supervisor:Monitor) reaches 2 judged nets (VIN: pad 1 on VIN; "
        "+3V3: pad 2 on +3V3) and an IC or module (reference letters U, IC or A) is "
        "not walked, so a power path through it is not seen: declare what it passes "
        'in POWER_PATHS["parts"] (in pad to out pad, such as {"U5": [("1", "3")]}), '
        'or {"U5": []} if it passes no power'
    ) in message
    assert result.entry(MODELLED)["numbers"]["not modelled"] == {
        "U5": {"VIN": "pad 1 on VIN", "+3V3": "pad 2 on +3V3"}
    }


def test_a_diode_from_a_rail_back_into_a_source_is_a_back_feed(tmp_path: Path) -> None:
    """A diode from +3V3 into VBUS: only the unlisted check goes red."""
    toy = board()
    schottky(toy, "D5", "+3V3", "VBUS")
    result = go(tmp_path, "backfeed", toy)
    assert outcomes(result) == {**green(), UNLISTED: "failed"}
    assert (
        "+3V3 -> VBUS is joined at 0 ohm (+3V3 - D5 - VBUS)"
        in (result.entry(UNLISTED)["message"])
    )


def test_a_rail_missing_from_supply_nets_fails_all_three_by_name(
    tmp_path: Path,
) -> None:
    """The two tables must agree, or no check is trusted."""
    specs = SPECS + TABLE.replace(SUPPLIES, '\nSUPPLY_NETS = ["GND", "VBUS", "VIN"]\n')
    result = go(tmp_path, "supplies", board(), specs)
    assert set(outcomes(result).values()) == {"failed"}
    for entry in result.checks.values():
        assert (
            'POWER_PATHS["nets"]: VBAT is not in specs.SUPPLY_NETS: every judged rail '
            "is a supply"
        ) in entry["message"]


def test_a_missing_table_fails_by_name(tmp_path: Path) -> None:
    """Fail each check, naming the table and the file."""
    result = go(tmp_path, "missing", board(), SPECS + SUPPLIES)
    assert set(outcomes(result).values()) == {"failed"}
    entry = result.entry(f"{PATH}[missing-spec]")
    assert "specs.POWER_PATHS is not defined in" in entry["message"]
    assert "specs.POWER_PATHS is not defined in" in result.entry(UNLISTED)["message"]


def test_a_malformed_table_fails_by_name_with_its_path(tmp_path: Path) -> None:
    """Report the wrong row's path, with no traceback."""
    specs = (
        SPECS
        + '\nPOWER_PATHS = {"nets": ["VIN"], "paths": [("VIN", ["U3"])]}\n'
        + SUPPLIES
    )
    entry = go(tmp_path, "malformed", board(), specs).entry(f"{PATH}[bad-table]")
    assert entry["outcome"] == "failed"
    assert (
        'specs.POWER_PATHS["paths"][0]: expected (from net, to net, [ref])'
        in (entry["message"])
    )
    assert "Traceback" not in entry["message"]


def test_no_rows_skips_the_path_check_and_runs_the_other_two(tmp_path: Path) -> None:
    """A board with nothing to declare still has its pairs and parts judged."""
    specs = (
        SPECS + '\nPOWER_PATHS = {"nets": ["VIN"], "paths": []}\n'
        '\nSUPPLY_NETS = ["GND", "VIN"]\n'
    )
    result = go(tmp_path, "empty", board(), specs)
    assert result.entry(f"{PATH}[")["outcome"] == "skipped"
    assert result.outcome(UNLISTED) == "passed"
    assert result.outcome(MODELLED) == "passed"


@pytest.mark.parametrize("groups", [[], ["circuit", "ports"]])
def test_a_board_that_has_not_switched_the_group_on_is_untouched(
    tmp_path: Path, groups: list[str]
) -> None:
    """Run no power-path check, and need no POWER_PATHS, unless ``power`` is on."""
    root = check_toy.write_project(tmp_path / "off", board(), groups)
    result = run(root)
    assert not [k for k in result.checks if MODULE in k]
