"""Integration: the ``power`` group's enable-link check, run as ``pcbkit check`` does.

Two regulators on a shared enable net are added to the generic toy board
(``tests/check_toy.py``), each behind a 0 ohm link, and ``ENABLE_LINKS`` to its specs.
The real command line runs in a child pytest with ``fake_nl`` serving the netlist, so
no KiCad is needed. The control passes; each planted mistake turns the check red, with
the message that says why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import check_toy
from tests.check_toy import SPECS, Toy
from tests.integration.test_check_plugin import Run, run

CHECK = "test_each_regulator_on_a_shared_enable_has_its_own_link"
TABLE = '\nENABLE_LINKS = {"EN_ALL": [("U2", 3), ("U3", 3)]}\n'


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
        "(R7 (10k) joins them but is not a link)"
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


def test_a_shared_net_that_is_not_in_the_design_fails_by_name(tmp_path: Path) -> None:
    """Name the net the table has and the schematic does not."""
    specs = SPECS + '\nENABLE_LINKS = {"EN_TYPO": [("U2", 3)]}\n'
    entry = go(tmp_path, "typo", board(), specs).entry(CHECK)
    assert entry["outcome"] == "failed"
    assert "net EN_TYPO is not in the design" in entry["message"]


def test_a_missing_table_fails_by_name(tmp_path: Path) -> None:
    """Fail once as ``missing-spec``, naming the table and the file."""
    result = go(tmp_path, "missing", board(), SPECS)
    entry = result.entry(f"{CHECK}[missing-spec]")
    assert entry["outcome"] == "failed"
    assert "specs.ENABLE_LINKS is not defined in" in entry["message"]


def test_an_empty_table_says_no_enable_is_shared(tmp_path: Path) -> None:
    """Skip, saying there is no row to run over, and exit 0."""
    result = go(tmp_path, "none", board(), SPECS + "\nENABLE_LINKS = {}\n")
    assert result.done.returncode == 0, result.done.stdout
    entry = result.entry(CHECK)
    assert entry["outcome"] == "skipped"
    assert "empty parameter set" in entry["reason"]


def test_a_malformed_table_fails_by_name_with_its_path(tmp_path: Path) -> None:
    """Report the wrong row's path, with no traceback."""
    specs = SPECS + '\nENABLE_LINKS = {"EN_ALL": [("U2",)]}\n'
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
