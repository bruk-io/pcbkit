"""Integration: the ``power`` group's enable-link check, run as ``pcbkit check`` does.

Two regulators on a shared enable net are added to the generic toy board
(``tests/check_toy.py``), each behind a 0 ohm link, and ``ENABLE_LINKS`` to its specs.
The real command line runs in a child pytest with ``fake_nl`` serving the netlist, so
no KiCad is needed. The control passes; each planted mistake turns the check red, with
the message that says why.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests import check_toy
from tests.check_toy import SPECS, Toy
from tests.integration.test_check_plugin import Run, run

CHECK = "test_each_regulator_on_a_shared_enable_has_its_own_link"
SUPPLIES = '\nSUPPLY_NETS = ["GND", "VIN", "+3V3"]\n'
TABLE = '\nENABLE_LINKS = {"EN_ALL": [("U2", 3), ("U3", 3)]}\n' + SUPPLIES


def regulator(toy: Toy, ref: str, enable_net: str) -> None:
    """Add a regulator whose enable pin is pad 3."""
    toy.add(
        ref,
        "LDO",
        "Regulator:LDO",
        {"1": ("VIN", "IN"), "2": ("GND", "GND"), "3": (enable_net, "EN")},
    )


def board() -> Toy:
    """Return the toy with two regulators, each behind its own 0 ohm link."""
    toy = check_toy.good()
    regulator(toy, "U2", "EN_U2")
    regulator(toy, "U3", "EN_U3")
    check_toy.resistor(toy, "R7", "0", "EN_U2", "EN_ALL")
    check_toy.resistor(toy, "R8", "0R", "EN_ALL", "EN_U3")
    return toy


def go(tmp_path: Path, name: str, toy: Toy, specs: str = SPECS + TABLE) -> Run:
    """Run only this module's checks, with the ``power`` group on."""
    root = check_toy.write_project(tmp_path / name, toy, ["power"], specs=specs)
    return run(root, "test_enable_links")


def test_the_control_passes_and_records_each_regulators_link(tmp_path: Path) -> None:
    """Pass the check for ``EN_ALL`` and record ``{net: {ref: link ref}}``."""
    result = go(tmp_path, "control", board())
    assert result.done.returncode == 0, result.done.stdout
    assert {k.partition("::")[2]: v["outcome"] for k, v in result.checks.items()} == {
        f"{CHECK}[EN_ALL]": "passed"
    }
    entry = result.entry(CHECK)
    assert entry["numbers"] == {"enable links": {"EN_ALL": {"U2": "R7", "U3": "R8"}}}
    assert entry["gate"] == "sch"
    assert entry["group"] == "power"


def test_both_enables_wired_straight_to_the_shared_net_fail(tmp_path: Path) -> None:
    """Fail naming both regulators; record that neither has a link."""
    toy = board()
    toy.move_pin("U2", "3", "EN_ALL")
    toy.move_pin("U3", "3", "EN_ALL")
    result = go(tmp_path, "straight", toy)
    entry = result.entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "U2 pad 3 is wired straight to EN_ALL: no link of its own; "
        "U3 pad 3 is wired straight to EN_ALL: no link of its own"
    ) in entry["message"]
    assert entry["numbers"]["enable links"] == {"EN_ALL": {"U2": None, "U3": None}}
    assert result.done.returncode == 1


def test_a_zero_ohm_link_replaced_by_ten_kilohms_fails(tmp_path: Path) -> None:
    """Fail naming R7 and its value; the other regulator's link is still recorded."""
    toy = board()
    toy.set_value("R7", "10k")
    entry = go(tmp_path, "ten-k", toy).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL "
        "(R7 (10k) joins them but is not a link: its value is not 0 ohm or a "
        "not-fitted mark)"
    ) in entry["message"]
    assert entry["numbers"]["enable links"] == {"EN_ALL": {"U2": None, "U3": "R8"}}


def test_two_regulators_behind_one_link_fail(tmp_path: Path) -> None:
    """Fail naming both regulators and the link they share."""
    toy = board()
    toy.move_pin("U3", "3", "EN_U2")
    entry = go(tmp_path, "shared", toy).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "U2 and U3 share the link R7: each regulator needs its own"
        in (entry["message"])
    )
    assert entry["numbers"]["enable links"] == {"EN_ALL": {"U2": "R7", "U3": "R7"}}


def test_a_jumper_is_a_link_too(tmp_path: Path) -> None:
    """Pass with a solder jumper in place of the 0 ohm resistor."""
    toy = board()
    del toy.parts["R7"], toy.pins["R7"]
    toy.add(
        "JP1",
        "Open",
        "Jumper:SolderJumper_2_Open",
        {"1": ("EN_U2", "A"), "2": ("EN_ALL", "B")},
    )
    result = go(tmp_path, "jumper", toy)
    assert result.outcome(CHECK) == "passed"
    assert result.entry(CHECK)["numbers"]["enable links"]["EN_ALL"]["U2"] == "JP1"


def plant_zero_ohms_between_enables(toy: Toy) -> None:
    """Short the enables together downstream of their links."""
    check_toy.resistor(toy, "R9", "0", "EN_U2", "EN_U3")


def plant_ten_k_between_enables(toy: Toy) -> None:
    """Bridge the enables with 10k."""
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_U3")


def plant_parallel_resistor(toy: Toy) -> None:
    """Put 10k in parallel with the link."""
    check_toy.resistor(toy, "R9", "10k", "EN_U2", "EN_ALL")


def plant_second_path_through_two_parts(toy: Toy) -> None:
    """Join EN_U2 to EN_ALL through two 0 ohm parts."""
    check_toy.resistor(toy, "R9", "0", "EN_U2", "MID")
    check_toy.resistor(toy, "R10", "0", "MID", "EN_ALL")


@pytest.mark.parametrize(
    ("plant", "path"),
    [
        (plant_zero_ohms_between_enables, "EN_U2 - R9 - EN_U3"),
        (plant_ten_k_between_enables, "EN_U2 - R9 - EN_U3"),
        (plant_parallel_resistor, "EN_U2 - R9 - EN_ALL"),
        (plant_second_path_through_two_parts, "EN_U2 - R9 - MID - R10 - EN_ALL"),
    ],
)
def test_a_second_path_around_a_link_fails_naming_the_path(
    tmp_path: Path, plant: Callable[[Toy], None], path: str
) -> None:
    """Fail each bypass, though every regulator has a link, and name the path."""
    toy = board()
    plant(toy)
    result = go(tmp_path, "bypass", toy)
    entry = result.entry(CHECK)
    assert entry["outcome"] == "failed"
    assert (
        "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches"
        in (entry["message"])
    )
    assert f"({path})" in entry["message"]
    assert entry["numbers"]["enable links"] == {"EN_ALL": {"U2": "R7", "U3": "R8"}}
    assert result.done.returncode == 1


def test_pulls_and_other_link_kinds_on_a_right_design_still_pass(
    tmp_path: Path,
) -> None:
    """Pass with a pull-down and a pull-up on each enable net and odd links."""
    toy = board()
    for n, ref in enumerate(("EN_U2", "EN_U3", "EN_ALL")):
        check_toy.resistor(toy, f"R2{n}", "100k", ref, "GND")
    check_toy.resistor(toy, "R30", "100k", "EN_U2", "VIN")
    check_toy.resistor(toy, "R31", "100k", "EN_U3", "+3V3")
    del toy.parts["R7"], toy.pins["R7"]
    toy.add("R7", "0R0", "Device:R_Small", {"1": ("EN_U2", "~"), "2": ("EN_ALL", "~")})
    toy.parts["R8"] = ("DNP", "Device", "R_US", toy.parts["R8"][3])
    result = go(tmp_path, "right", toy)
    assert result.outcome(CHECK) == "passed", result.entry(CHECK)
    assert result.entry(CHECK)["numbers"]["enable links"] == {
        "EN_ALL": {"U2": "R7", "U3": "R8"}
    }


def test_a_shared_net_that_is_not_in_the_design_fails_by_name(tmp_path: Path) -> None:
    """Name the net the table has and the schematic does not."""
    specs = SPECS + '\nENABLE_LINKS = {"EN_TYPO": [("U2", 3)]}\n' + SUPPLIES
    entry = go(tmp_path, "typo", board(), specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "net EN_TYPO is not in the design" in entry["message"]


def test_a_missing_table_fails_by_name(tmp_path: Path) -> None:
    """Fail once as ``missing-spec``, naming the table and the file."""
    result = go(tmp_path, "missing", board(), SPECS + SUPPLIES)
    entry = result.entry(f"{CHECK}[missing-spec]")
    assert entry["outcome"] == "failed"
    assert "specs.ENABLE_LINKS is not defined in" in entry["message"]


def test_an_empty_table_says_no_enable_is_shared(tmp_path: Path) -> None:
    """Skip, saying there is no row to run over, and exit 0."""
    result = go(tmp_path, "none", board(), SPECS + "\nENABLE_LINKS = {}\n" + SUPPLIES)
    assert result.done.returncode == 0, result.done.stdout
    entry = result.entry(CHECK)
    assert entry["outcome"] == "skipped"
    assert "empty parameter set" in entry["reason"]


def test_a_malformed_table_fails_by_name_with_its_path(tmp_path: Path) -> None:
    """Report the wrong row's path, with no traceback."""
    specs = SPECS + '\nENABLE_LINKS = {"EN_ALL": [("U2",)]}\n' + SUPPLIES
    entry = go(tmp_path, "malformed", board(), specs).entry(f"{CHECK}[bad-table]")
    assert entry["outcome"] == "failed"
    assert (
        'specs.ENABLE_LINKS["EN_ALL"][0]: expected (regulator ref, enable pad)'
        in (entry["message"])
    )
    assert "Traceback" not in entry["message"]


@pytest.mark.parametrize("groups", [[], ["circuit", "ports"]])
def test_a_board_that_has_not_switched_the_group_on_is_untouched(
    tmp_path: Path, groups: list[str]
) -> None:
    """Run no enable-link check, and need no ENABLE_LINKS, unless ``power`` is on."""
    root = check_toy.write_project(tmp_path / "off", board(), groups)
    result = run(root)
    assert not [k for k in result.checks if "test_enable_links" in k]
    assert "power" not in result.results["groups"]


# ---------------------------------------------------- the second review's plants
def plant_bridge_through_vbus_en(toy: Toy) -> None:
    """B1: join the enables through a net named like a supply, not declared one."""
    check_toy.resistor(toy, "R9", "0", "EN_U2", "VBUS_EN")
    check_toy.resistor(toy, "R10", "0", "VBUS_EN", "EN_U3")


def plant_parallel_through_vcc_en_bus(toy: Toy) -> None:
    """B1: a bypass beside the link through a net named like a supply."""
    check_toy.resistor(toy, "R9", "0", "EN_U2", "VCC_EN_BUS")
    check_toy.resistor(toy, "R10", "0", "VCC_EN_BUS", "EN_ALL")


def plant_tie_to_vin(toy: Toy) -> None:
    """S4: tie U2's enable to VIN with a 0 ohm: the link controls nothing."""
    check_toy.resistor(toy, "R9", "0", "EN_U2", "VIN")


def plant_sip_element_across_the_link(toy: Toy) -> None:
    """S3: an R_Pack04_SIP element (pads 1-2) beside the link."""
    pads = {str(n): (f"A{n}", "~") for n in range(3, 9)}
    pads.update({"1": ("EN_U2", "~"), "2": ("EN_ALL", "~")})
    toy.add("RN1", "4x10k", "Device:R_Pack04_SIP", pads)


def plant_three_pad_jumper_link(toy: Toy) -> None:
    """S5: a three-pad jumper in place of R7."""
    del toy.parts["R7"], toy.pins["R7"]
    toy.add(
        "JP1",
        "J",
        "Jumper:SolderJumper_3_Bridged12",
        {"1": ("EN_U2", "A"), "2": ("EN_ALL", "C"), "3": ("GND", "B")},
    )


def plant_undeclared_rail_pull_ups(toy: Toy) -> None:
    """S1, the other way: pull-ups to a rail SUPPLY_NETS does not list."""
    check_toy.resistor(toy, "R20", "100k", "EN_U2", "3V3")
    check_toy.resistor(toy, "R21", "100k", "EN_U3", "3V3")


@pytest.mark.parametrize(
    ("plant", "message"),
    [
        (
            plant_bridge_through_vbus_en,
            "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the "
            "enable net EN_U3 of U3 (EN_U2 - R9 - VBUS_EN - R10 - EN_U3) (if VBUS_EN "
            "is a supply rail, declare it in specs.SUPPLY_NETS; if it is a signal, "
            "this path is real)",
        ),
        (
            plant_parallel_through_vcc_en_bus,
            "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the "
            "shared net EN_ALL (EN_U2 - R9 - VCC_EN_BUS - R10 - EN_ALL)",
        ),
        (
            plant_tie_to_vin,
            "U2 pad 3: R9 (0) ties EN_U2 to VIN, a net in specs.SUPPLY_NETS, so the "
            "link R7 controls nothing",
        ),
        (
            plant_sip_element_across_the_link,
            "U2 pad 3: removing R7 does not isolate it: EN_U2 still reaches the "
            "shared net EN_ALL (EN_U2 - RN1 - EN_ALL)",
        ),
        (
            plant_three_pad_jumper_link,
            "U2 pad 3: no 0 ohm resistor or jumper joins EN_U2 to EN_ALL (EN_U2 - JP1 "
            "- EN_ALL joins them, with no link on it; JP1 has three pads: a link must "
            "have two)",
        ),
        (
            plant_undeclared_rail_pull_ups,
            "(EN_U2 - R20 - 3V3 - R21 - EN_U3) (if 3V3 is a supply rail, declare it "
            "in specs.SUPPLY_NETS; if it is a signal, this path is real)",
        ),
    ],
)
def test_each_planted_mistake_of_the_second_review_fails(
    tmp_path: Path, plant: Callable[[Toy], None], message: str
) -> None:
    """Fail each plant with the message that says what is wrong and where."""
    toy = board()
    plant(toy)
    entry = go(tmp_path, "plant", toy).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert message in entry["message"], entry["message"]


def test_a_bridge_to_a_regulator_of_another_row_fails_both_rows(
    tmp_path: Path,
) -> None:
    """B2: fail EN_ALL and EN_B when R9 joins U2's enable to U4's."""
    toy = board()
    regulator(toy, "U4", "EN_U4")
    regulator(toy, "U5", "EN_U5")
    check_toy.resistor(toy, "R11", "0", "EN_U4", "EN_B")
    check_toy.resistor(toy, "R12", "0", "EN_U5", "EN_B")
    check_toy.resistor(toy, "R9", "0", "EN_U2", "EN_U4")
    table = (
        '\nENABLE_LINKS = {"EN_ALL": [("U2", 3), ("U3", 3)], '
        '"EN_B": [("U4", 3), ("U5", 3)]}\n'
    )
    result = go(tmp_path, "rows", toy, SPECS + table + SUPPLIES)
    assert result.entry(f"{CHECK}[EN_ALL]")["outcome"] == "failed"
    assert "(EN_U2 - R9 - EN_U4)" in result.entry(f"{CHECK}[EN_ALL]")["message"]
    assert result.entry(f"{CHECK}[EN_B]")["outcome"] == "failed"
    assert "(EN_U4 - R9 - EN_U2)" in result.entry(f"{CHECK}[EN_B]")["message"]


def test_right_designs_the_old_rule_failed_now_pass(tmp_path: Path) -> None:
    """S1, S2, L1: pull-ups to a declared 3V3, a link then an RC, a diode-OR."""
    toy = board()
    check_toy.resistor(toy, "R20", "100k", "EN_U2", "3V3")
    check_toy.resistor(toy, "R21", "100k", "EN_U3", "3V3")
    del toy.parts["R7"], toy.pins["R7"]
    check_toy.resistor(toy, "R7", "0", "EN_ALL", "EN_U2D")
    check_toy.resistor(toy, "R9", "10k", "EN_U2D", "EN_U2")
    toy.add("C9", "1u", "Device:C", {"1": ("EN_U2", "~"), "2": ("GND", "~")})
    toy.add("D9", "D", "Device:D", {"1": ("ANY_ON", "K"), "2": ("EN_U2", "A")})
    toy.add("D10", "D", "Device:D", {"1": ("ANY_ON", "K"), "2": ("EN_U3", "A")})
    specs = SPECS + TABLE.replace('"+3V3"]', '"+3V3", "3V3"]')
    result = go(tmp_path, "right2", toy, specs)
    assert result.outcome(CHECK) == "passed", result.entry(CHECK)
    assert result.entry(CHECK)["numbers"]["enable links"] == {
        "EN_ALL": {"U2": "R7", "U3": "R8"}
    }


def test_a_missing_supply_table_fails_by_name(tmp_path: Path) -> None:
    """Fail once as ``missing-spec``, naming SUPPLY_NETS, when only it is missing."""
    result = go(tmp_path, "no-supplies", board(), SPECS + TABLE.replace(SUPPLIES, ""))
    entry = result.entry(f"{CHECK}[missing-spec]")
    assert entry["outcome"] == "failed"
    assert "specs.SUPPLY_NETS is not defined in" in entry["message"]


def test_a_supply_net_that_is_not_in_the_design_fails_by_name(tmp_path: Path) -> None:
    """Name the declared supply the schematic does not have."""
    specs = SPECS + TABLE.replace('"+3V3"]', '"+3V3", "VDD_TYPO"]')
    entry = go(tmp_path, "supply-typo", board(), specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "specs.SUPPLY_NETS: VDD_TYPO is not in the design" in entry["message"]


# ----------------------------------------------------- the third review's plants
def plant_dual_diode_from_u3(toy: Toy) -> None:
    """Blocker: a BAT54C lets EN_U3 drive EN_U2."""
    toy.add(
        "D9",
        "BAT54C",
        "Diode:BAT54C",
        {"1": ("PG_U1", "A1"), "2": ("EN_U3", "A2"), "3": ("EN_U2", "K")},
    )


def plant_dip_switch(toy: Toy) -> None:
    """Blocker: a DIP switch element across the two enables."""
    toy.add(
        "SW1",
        "SW",
        "Switch:SW_DIP_x02",
        {"1": ("EN_U2", "~"), "2": ("S2", "~"), "3": ("S3", "~"), "4": ("EN_U3", "~")},
    )


def plant_custom_array(toy: Toy) -> None:
    """Blocker: a resistor array from a custom library across the enables."""
    pads = {str(n): (f"A{n}", "~") for n in range(2, 8)}
    pads.update({"1": ("EN_U2", "~"), "8": ("EN_U3", "~")})
    toy.add("RN1", "4x10k", "Custom_Resistors:CAY16", pads)


@pytest.mark.parametrize(
    ("plant", "path"),
    [
        (plant_dual_diode_from_u3, "(EN_U2 - D9 - EN_U3)"),
        (plant_dip_switch, "(EN_U2 - SW1 - EN_U3)"),
        (plant_custom_array, "(EN_U2 - RN1 - EN_U3)"),
    ],
)
def test_each_multi_pad_bypass_of_the_third_review_fails(
    tmp_path: Path, plant: Callable[[Toy], None], path: str
) -> None:
    """Fail each part with three or more pads that joins the enables."""
    toy = board()
    plant(toy)
    entry = go(tmp_path, "multi", toy).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert path in entry["message"], entry["message"]


def test_an_open_strap_and_a_diode_or_after_the_link_pass(tmp_path: Path) -> None:
    """Pass an open jumper from EN_U2 to GND and a BAT54C OR behind the link."""
    toy = board()
    toy.add(
        "JP3",
        "Open",
        "Jumper:SolderJumper_2_Open",
        {"1": ("EN_U2", "A"), "2": ("GND", "B")},
    )
    del toy.parts["R8"], toy.pins["R8"]
    check_toy.resistor(toy, "R8", "0", "EN_ALL", "X")
    toy.add(
        "D9",
        "BAT54C",
        "Diode:BAT54C",
        {"1": ("X", "A1"), "2": ("PG_U1", "A2"), "3": ("EN_U3", "K")},
    )
    result = go(tmp_path, "strap-or", toy)
    assert result.outcome(CHECK) == "passed", result.entry(CHECK)


def pull_downs(toy: Toy) -> Toy:
    """Pull both enables down to GND through 100k."""
    check_toy.resistor(toy, "R20", "100k", "EN_U2", "GND")
    check_toy.resistor(toy, "R21", "100k", "EN_U3", "GND")
    return toy


@pytest.mark.parametrize(
    "ground", ['\nGROUND_NET = "GND"\n', "", '\nGROUND_NET = "AGND"\n']
)
def test_ground_net_or_its_default_stops_the_walk(tmp_path: Path, ground: str) -> None:
    """Pass pull-downs to GND when GND is GROUND_NET, or by default, but not listed.

    With ``GROUND_NET = "AGND"`` the walk stops at AGND and crosses GND, so the
    pull-downs join the enables and the check fails.
    """
    toy = pull_downs(board())
    toy.add("U9", "X", "Sensor:X", {"1": ("AGND", "GND")})
    specs = SPECS + TABLE.replace('"GND", ', "") + ground
    entry = go(tmp_path, "ground", toy, specs).entry(CHECK)
    want = "failed" if "AGND" in ground else "passed"
    assert entry["outcome"] == want, entry
