"""Integration: the ``ports`` group's port-map checks, run as ``pcbkit check`` does.

A connector is added to the generic toy board (``tests/check_toy.py``) and a port map
to its specs, and the real command line runs in a child pytest with ``fake_nl`` serving
the netlist, so no KiCad is needed. The control passes; each planted mistake turns the
one check that is for it red, with the message that says why, and no other check of
the module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import check_toy
from tests.check_toy import SPECS, Toy
from tests.integration.test_check_plugin import Run, run

PER_PART = "test_port_pins_carry_their_nets"
COMPLETE = "test_every_connector_is_in_the_port_map"

# the port map of the toy: the connector, and two pads of the controller module
PORT_MAP = """
PIN_NETS = {
    "J1": {1: "VIN", 2: "GND", 3: "SDA", 4: "SCL", 5: None},
    "A1": {7: "+3V3", 8: "GND"},
}
"""


def board() -> Toy:
    """Return the toy with a five-pin connector, pad 5 left unwired."""
    toy = check_toy.good()
    toy.add(
        "J1",
        "Header",
        "Connector_Generic:Conn_01x05",
        {
            "1": ("VIN", "Pin_1"),
            "2": ("GND", "Pin_2"),
            "3": ("SDA", "Pin_3"),
            "4": ("SCL", "Pin_4"),
            "5": ("unconnected-(J1-Pad5)", "Pin_5"),
        },
    )
    return toy


def go(tmp_path: Path, name: str, toy: Toy, specs: str = SPECS + PORT_MAP) -> Run:
    """Run only this module's checks, with the ``ports`` group on."""
    root = check_toy.write_project(tmp_path / name, toy, ["ports"], specs=specs)
    return run(root, "test_ports")


def outcomes(result: Run) -> dict[str, str]:
    """Return each check of the module (id after ``::``) with its outcome."""
    return {k.partition("::")[2]: v["outcome"] for k, v in result.checks.items()}


def test_the_control_passes_every_port_map_check_and_records_the_map(
    tmp_path: Path,
) -> None:
    """Pass one check per listed part and the completeness check; record the map."""
    result = go(tmp_path, "control", board())
    assert result.done.returncode == 0, result.done.stdout
    assert outcomes(result) == {
        f"{PER_PART}[A1]": "passed",
        f"{PER_PART}[J1]": "passed",
        COMPLETE: "passed",
    }
    assert result.entry(f"{PER_PART}[J1]")["numbers"] == {
        "pins": {"1": "VIN", "2": "GND", "3": "SDA", "4": "SCL", "5": None}
    }
    port_map = result.entry(COMPLETE)["numbers"]["port map"]
    assert port_map == {
        "J1": {"1": "VIN", "2": "GND", "3": "SDA", "4": "SCL", "5": None}
    }
    assert {e["gate"] for e in result.checks.values()} == {"sch"}


def test_two_nets_swapped_on_a_connector_fail_that_connectors_check_only(
    tmp_path: Path,
) -> None:
    """Fail ``[J1]`` naming both pads, expected and found; the others still pass."""
    toy = board()
    toy.pins["J1"]["1"] = ("GND", "Pin_1")
    toy.pins["J1"]["2"] = ("VIN", "Pin_2")
    result = go(tmp_path, "swapped", toy)
    assert outcomes(result) == {
        f"{PER_PART}[A1]": "passed",
        f"{PER_PART}[J1]": "failed",
        COMPLETE: "passed",
    }
    message = result.entry(f"{PER_PART}[J1]")["message"]
    assert (
        "J1: pad 1: expected VIN, found GND; pad 2: expected GND, found VIN" in message
    )
    # what was found is recorded even though the check failed
    assert result.entry(f"{PER_PART}[J1]")["numbers"]["pins"]["1"] == "GND"
    assert result.done.returncode == 1


def test_a_connector_missing_from_the_map_fails_the_completeness_check_only(
    tmp_path: Path,
) -> None:
    """Name the new connector, and record every connector as found."""
    toy = board()
    toy.add(
        "J2",
        "Debug",
        "Connector_Generic:Conn_01x02",
        {"1": ("SDA", "Pin_1"), "2": ("GND", "Pin_2")},
    )
    result = go(tmp_path, "unlisted", toy)
    assert outcomes(result) == {
        f"{PER_PART}[A1]": "passed",
        f"{PER_PART}[J1]": "passed",
        COMPLETE: "failed",
    }
    entry = result.entry(COMPLETE)
    assert "connectors not in specs.PIN_NETS: ['J2']" in entry["message"]
    assert entry["numbers"]["port map"]["J2"] == {"1": "SDA", "2": "GND"}
    assert set(entry["numbers"]["port map"]) == {"J1", "J2"}


def test_a_wired_connector_pad_the_row_leaves_out_fails_that_connectors_check(
    tmp_path: Path,
) -> None:
    """Fail ``[J1]`` for pad 4, wired to SCL but not listed."""
    specs = SPECS + PORT_MAP.replace(' 4: "SCL",', "")
    assert specs != SPECS + PORT_MAP
    result = go(tmp_path, "short-row", board(), specs)
    assert outcomes(result)[f"{PER_PART}[J1]"] == "failed"
    assert outcomes(result)[COMPLETE] == "passed"
    assert (
        "pad 4: wired to SCL but not listed"
        in result.entry(f"{PER_PART}[J1]")["message"]
    )


def test_a_row_for_a_part_that_is_not_in_the_design_fails_by_name(
    tmp_path: Path,
) -> None:
    """Fail the row of ``J9``, saying the part is not in the design."""
    specs = SPECS + PORT_MAP.replace('"A1":', '"J9": {1: "VIN"},\n    "A1":')
    result = go(tmp_path, "ghost", board(), specs)
    entry = result.entry(f"{PER_PART}[J9]")
    assert outcomes(result)[f"{PER_PART}[J9]"] == "failed"
    assert entry["message"].strip() == (
        "AssertionError: J9 is in specs.PIN_NETS but not in the design"
    )
    assert entry["numbers"] == {"pins": None}
    assert outcomes(result)[f"{PER_PART}[J1]"] == "passed"


def test_a_jumper_and_a_test_point_are_not_connectors(tmp_path: Path) -> None:
    """Pass a correct board that has JP1 and TP1 and does not list them."""
    toy = board()
    toy.add(
        "JP1",
        "Open",
        "Jumper:SolderJumper_2_Open",
        {"1": ("SDA", "A"), "2": ("SCL", "B")},
    )
    toy.add("TP1", "TP", "Connector:TestPoint", {"1": ("SDA", "1")})
    result = go(tmp_path, "jp-tp", toy)
    assert result.done.returncode == 0, result.done.stdout
    assert result.outcome(COMPLETE) == "passed"
    assert set(result.entry(COMPLETE)["numbers"]["port map"]) == {"J1"}


def test_a_net_written_with_a_leading_slash_says_names_carry_none(
    tmp_path: Path,
) -> None:
    """Fail ``/VIN`` against VIN, and say why."""
    specs = SPECS + PORT_MAP.replace('1: "VIN"', '1: "/VIN"')
    entry = go(tmp_path, "slash", board(), specs).entry(f"{PER_PART}[J1]")
    assert entry["outcome"] == "failed"
    assert (
        "pad 1: expected /VIN, found VIN (net names carry no leading slash)"
        in entry["message"]
    )


@pytest.mark.parametrize("net", ['"X"', "None"])
def test_a_pad_the_connector_does_not_have_fails_as_not_a_pad(
    tmp_path: Path, net: str
) -> None:
    """Fail pad 9 of J1, listed with a net or with None."""
    specs = SPECS + PORT_MAP.replace("5: None}", f"5: None, 9: {net}}}")
    entry = go(tmp_path, "no-pad", board(), specs).entry(f"{PER_PART}[J1]")
    assert entry["outcome"] == "failed"
    assert "pad 9: not a pad of J1" in entry["message"]
    assert "9" not in entry["numbers"]["pins"]
    assert entry["numbers"]["not pads"] == ["9"]


def test_a_controller_pad_on_the_wrong_net_fails_its_own_row(tmp_path: Path) -> None:
    """Compare a listed non-connector too, only on the pads listed."""
    toy = board()
    toy.pins["A1"]["8"] = ("+3V3", "GND")
    result = go(tmp_path, "controller", toy)
    assert outcomes(result)[f"{PER_PART}[A1]"] == "failed"
    assert (
        "pad 8: expected GND, found +3V3" in result.entry(f"{PER_PART}[A1]")["message"]
    )


def test_a_pin_net_table_that_is_not_defined_fails_by_name(tmp_path: Path) -> None:
    """Fail once as ``missing-spec`` and once for the completeness check."""
    result = go(tmp_path, "missing", board(), SPECS)
    assert outcomes(result) == {
        f"{PER_PART}[missing-spec]": "failed",
        COMPLETE: "failed",
    }
    for entry in result.checks.values():
        assert "specs.PIN_NETS is not defined in" in entry["message"]


def test_an_empty_table_means_no_connector_and_passes_a_board_without_one(
    tmp_path: Path,
) -> None:
    """Pass the completeness check on ``{}`` when the design has no connector."""
    result = go(tmp_path, "none", check_toy.good(), SPECS + "\nPIN_NETS = {}\n")
    assert result.outcome(COMPLETE) == "passed"
    assert result.entry(COMPLETE)["numbers"]["port map"] == {}
    assert result.done.returncode == 0, result.done.stdout
    # an empty table has no row to run the per-part check over: it skips, saying so
    skipped = result.entry(f"{PER_PART}[NOTSET]")
    assert skipped["outcome"] == "skipped"
    assert "empty parameter set" in skipped["reason"]


def test_an_empty_table_fails_a_board_that_has_a_connector(tmp_path: Path) -> None:
    """Name the connector: ``{}`` is "none here", and there is one."""
    result = go(tmp_path, "denied", board(), SPECS + "\nPIN_NETS = {}\n")
    assert result.outcome(COMPLETE) == "failed"
    assert (
        "connectors not in specs.PIN_NETS: ['J1']" in result.entry(COMPLETE)["message"]
    )


def test_a_malformed_table_fails_by_name_with_its_path(tmp_path: Path) -> None:
    """Report the wrong value's path, with no traceback."""
    specs = SPECS + '\nPIN_NETS = {"J1": {1: 5}}\n'
    result = go(tmp_path, "malformed", board(), specs)
    assert outcomes(result) == {
        f"{PER_PART}[bad-table]": "failed",
        COMPLETE: "failed",
    }
    message = result.entry(COMPLETE)["message"]
    assert 'specs.PIN_NETS["J1"][1]: expected net or None, got 5' in message
    assert "Traceback" not in message


@pytest.mark.parametrize("groups", [[], ["circuit"]])
def test_a_board_that_has_not_switched_the_group_on_is_untouched(
    tmp_path: Path, groups: list[str]
) -> None:
    """Run no port-map check, and need no PIN_NETS, unless ``ports`` is listed."""
    root = check_toy.write_project(tmp_path / "off", board(), groups)
    result = run(root)
    assert not [k for k in result.checks if "test_ports" in k]
    assert "ports" not in result.results["groups"]
