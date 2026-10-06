"""Unit tests for pcbkit.design: the DSL, its defaults, and loading a design.py."""

from __future__ import annotations

import dataclasses
import inspect
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pcbkit import design
from pcbkit.design import (
    FP,
    LED,
    C,
    Design,
    DesignError,
    R,
    load_design,
    part,
)
from pcbkit.project import ProjectError
from tests.board_files import restored_imports, write_file, write_project

# The call shape every existing design.py relies on (copied from the source the DSL
# was moved from): positional order matters because boards pass mfr, mpn, desc and
# block without keywords.
SIGNATURES = {
    "part": [
        "ref",
        "sym",
        "value",
        "fp",
        "pins",
        "mfr",
        "mpn",
        "desc",
        "block",
        "dnp",
        "bom",
    ],
    "R": ["ref", "value", "a", "b", "block", "mpn", "mfr", "fp", "desc"],
    "C": ["ref", "value", "a", "b", "block", "size", "mpn", "mfr", "desc"],
    "LED": ["ref", "color", "anode", "cathode", "block", "mpn", "mfr"],
}


@pytest.fixture(autouse=True)
def fresh_registry() -> Iterator[None]:
    """Give every test an empty registry, the default FP and untouched imports."""
    design.reset()
    with restored_imports():
        yield
    design.reset()


def parts_by_ref() -> dict[str, dict[str, Any]]:
    """Return the live registry keyed by reference."""
    return {p["ref"]: p for p in design.PARTS}


# --- the DSL ----------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_the_call_shape_is_the_one_boards_already_use(name: str) -> None:
    """Keep each helper's parameter names and order."""
    got = list(inspect.signature(getattr(design, name)).parameters)
    assert got == SIGNATURES[name]


def test_part_records_every_field_with_todays_defaults() -> None:
    """Record a bare part with empty text fields, not DNP, and in the BOM."""
    part("J1", "Connector_Generic:Conn_01x02", "Hdr", "Lib:Fp", {"1": "A", "2": None})
    assert design.PARTS == [
        {
            "ref": "J1",
            "sym": "Connector_Generic:Conn_01x02",
            "value": "Hdr",
            "fp": "Lib:Fp",
            "pins": {"1": "A", "2": None},
            "mfr": "",
            "mpn": "",
            "desc": "",
            "block": "",
            "dnp": False,
            "bom": True,
        }
    ]
    assert list(design.PARTS[0]) == [
        "ref",
        "sym",
        "value",
        "fp",
        "pins",
        "mfr",
        "mpn",
        "desc",
        "block",
        "dnp",
        "bom",
    ]


def test_part_takes_mfr_mpn_desc_and_block_positionally() -> None:
    """Accept the positional call style of the existing designs."""
    part("U1", "Lib:Sym", "V", "Lib:Fp", {}, "Acme", "AC-1", "A chip", "Block")
    got = design.PARTS[0]
    assert (got["mfr"], got["mpn"], got["desc"], got["block"]) == (
        "Acme",
        "AC-1",
        "A chip",
        "Block",
    )


def test_parts_keep_the_order_they_were_declared_in() -> None:
    """List the parts in call order."""
    for ref in ("R3", "R1", "R2"):
        R(ref, "1k", "A", "B", "Blk")
    assert [p["ref"] for p in design.PARTS] == ["R3", "R1", "R2"]


def test_resistor_defaults() -> None:
    """Make a 0603 1 % resistor with a generic MPN and description."""
    R("R1", "10k", "VIN", "SENSE", "Blk")
    got = parts_by_ref()["R1"]
    assert got["sym"] == "Device:R"
    assert got["fp"] == "Resistor_SMD:R_0603_1608Metric"
    assert got["pins"] == {"1": "VIN", "2": "SENSE"}
    assert got["mpn"] == "0603 10k 1%"
    assert got["desc"] == "Resistor 10k 0603 1%"
    assert (got["mfr"], got["block"], got["dnp"], got["bom"]) == (
        "",
        "Blk",
        False,
        True,
    )


def test_resistor_overrides_win_over_the_defaults() -> None:
    """Take a given footprint, MPN, manufacturer and description as they are."""
    R(
        "R2",
        "47m",
        "A",
        "B",
        "Blk",
        mpn="WSL2512R0470FEA",
        mfr="Vishay",
        fp="Resistor_SMD:R_2512_6332Metric",
        desc="Damping resistor",
    )
    got = parts_by_ref()["R2"]
    assert got["fp"] == "Resistor_SMD:R_2512_6332Metric"
    assert (got["mpn"], got["mfr"], got["desc"]) == (
        "WSL2512R0470FEA",
        "Vishay",
        "Damping resistor",
    )


def test_capacitor_defaults_and_size_in_the_description() -> None:
    """Make a 0603 capacitor with no MPN, described by value and size."""
    C("C1", "100n", "VDD", "GND", "Blk")
    got = parts_by_ref()["C1"]
    assert got["sym"] == "Device:C"
    assert got["fp"] == "Capacitor_SMD:C_0603_1608Metric"
    assert got["pins"] == {"1": "VDD", "2": "GND"}
    assert got["mpn"] == ""
    assert got["desc"] == "Ceramic capacitor 100n 0603"


def test_capacitor_size_picks_the_footprint_and_the_description() -> None:
    """Use the FP entry named by ``size``, and the size's digits in the text."""
    C("C2", "10u 25V", "VIN", "GND", "Blk", size="C1206", mpn="X", mfr="Y")
    got = parts_by_ref()["C2"]
    assert got["fp"] == "Capacitor_SMD:C_1206_3216Metric"
    assert got["desc"] == "Ceramic capacitor 10u 25V 1206"
    assert (got["mpn"], got["mfr"]) == ("X", "Y")


def test_capacitor_with_an_unknown_size_is_a_key_error() -> None:
    """Fail loudly rather than guess a footprint."""
    with pytest.raises(KeyError, match="C9999"):
        C("C3", "1u", "A", "B", "Blk", size="C9999")


def test_led_pin_one_is_the_cathode_and_pin_two_the_anode() -> None:
    """Wire Device:LED as KiCad numbers it: 1 = K, 2 = A."""
    LED("D1", "Red", "ANODE_NET", "CATHODE_NET", "Blk", "LED-1", "Acme")
    got = parts_by_ref()["D1"]
    assert got["sym"] == "Device:LED"
    assert got["value"] == "Red"
    assert got["fp"] == "LED_SMD:LED_0603_1608Metric"
    assert got["pins"] == {"2": "ANODE_NET", "1": "CATHODE_NET"}
    assert (got["mpn"], got["mfr"], got["desc"]) == ("LED-1", "Acme", "LED Red 0603")


def test_the_default_footprints_are_generic_stock_library_names() -> None:
    """Keep to Library:Name entries, and none that belong to one particular board."""
    assert FP["R0603"] == "Resistor_SMD:R_0603_1608Metric"
    assert FP["XH4"] == "Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical"
    assert all(value.count(":") == 1 for value in FP.values())
    assert "HDR3" not in FP
    prefixes = {value.split(":")[0] for value in FP.values()}
    assert prefixes <= {
        "Resistor_SMD",
        "Capacitor_SMD",
        "LED_SMD",
        "Connector_JST",
        "Connector_PinHeader_2.54mm",
        "Connector_Wire",
    }


def test_a_board_can_add_its_own_footprint_to_fp() -> None:
    """Let a helper use an entry the board added."""
    FP["MYHDR"] = "myboard:Header"
    part("J1", "Lib:Sym", "V", FP["MYHDR"], {})
    assert design.PARTS[0]["fp"] == "myboard:Header"


# --- load_design: what comes back -------------------------------------------------

BASIC = """\
from pcbkit.design import LED, R, part

B = "Supply"
part("J1", "Connector_Generic:Conn_01x02", "In", "Lib:Fp", {"1": "+3V3", "2": "GND"},
     block=B)
R("R1", "330", "+3V3", "LED_A", B)
LED("D1", "Green", "LED_A", "GND", "Indicator", "GRN", "Acme")
"""


def test_load_design_returns_the_parts_in_order(tmp_path: Path) -> None:
    """Return a Design holding every part, in declaration order."""
    loaded = load_design(write_project(tmp_path, BASIC))
    assert isinstance(loaded, Design)
    assert [p["ref"] for p in loaded.parts] == ["J1", "R1", "D1"]
    assert loaded.parts[1]["pins"] == {"1": "+3V3", "2": "LED_A"}


def test_without_block_order_blocks_come_in_first_use_order(tmp_path: Path) -> None:
    """Draw the blocks in the order the parts first mention them."""
    loaded = load_design(write_project(tmp_path, BASIC))
    assert loaded.block_order == ("Supply", "Indicator")


def test_every_optional_name_has_a_neutral_default(tmp_path: Path) -> None:
    """Leave notes, widths, titles, company, comment and constants empty."""
    loaded = load_design(write_project(tmp_path, BASIC))
    assert loaded.notes == ()
    assert dict(loaded.block_widths) == {}
    assert dict(loaded.block_titles) == {}
    assert (loaded.company, loaded.comment) == ("", "")
    assert loaded.constants == {"B": "Supply"}


def test_the_optional_names_are_read_from_the_file(tmp_path: Path) -> None:
    """Take BLOCK_ORDER, NOTES, BLOCK_WIDTHS, BLOCK_TITLES, COMPANY and COMMENT."""
    source = (
        BASIC
        + """
BLOCK_ORDER = ["Indicator", "Supply"]
NOTES = ["Heading", "A second line"]
BLOCK_WIDTHS = {"Supply": 100, "Indicator": 87.5}
BLOCK_TITLES = {"Supply": "Supply / headers"}
COMPANY = "Example Co"
COMMENT = "A comment"
"""
    )
    loaded = load_design(write_project(tmp_path, source))
    assert loaded.block_order == ("Indicator", "Supply")
    assert loaded.notes == ("Heading", "A second line")
    assert dict(loaded.block_widths) == {"Supply": 100, "Indicator": 87.5}
    assert dict(loaded.block_titles) == {"Supply": "Supply / headers"}
    assert (loaded.company, loaded.comment) == ("Example Co", "A comment")


def test_other_upper_case_data_is_kept_as_constants(tmp_path: Path) -> None:
    """Keep DEVKIT-style tables; leave out the DSL's own names and lower-case names."""
    source = (
        BASIC
        + """
from pcbkit.design import FP
DEVKIT = {"1": "+3V3", "2": None}
LEGS = ["FL", "FR"]
PROJECT = "my_board"
helper = 5
def HELPER():
    pass
"""
    )
    loaded = load_design(write_project(tmp_path, source))
    assert loaded.constants == {
        "B": "Supply",
        "DEVKIT": {"1": "+3V3", "2": None},
        "LEGS": ["FL", "FR"],
        "PROJECT": "my_board",
    }


def test_the_design_is_plain_data_and_frozen(tmp_path: Path) -> None:
    """Refuse to reassign a field of the returned Design."""
    loaded = load_design(write_project(tmp_path, BASIC))
    with pytest.raises(dataclasses.FrozenInstanceError):
        loaded.notes = ("changed",)


def test_the_design_does_not_share_pin_maps_with_the_file(tmp_path: Path) -> None:
    """Copy the parts, so editing a constant afterwards cannot change a part."""
    source = """\
from pcbkit.design import part
TABLE = {"1": "NET_A", "2": "NET_B"}
part("A1", "Lib:Sym", "V", "Lib:Fp", TABLE, block="Blk")
"""
    loaded = load_design(write_project(tmp_path, source))
    loaded.constants["TABLE"]["1"] = "CHANGED"
    assert loaded.parts[0]["pins"]["1"] == "NET_A"


# --- load_design: state does not leak ---------------------------------------------


def test_the_registry_is_empty_after_a_load(tmp_path: Path) -> None:
    """Leave nothing in the live registry for the next board to find."""
    load_design(write_project(tmp_path, BASIC))
    assert design.PARTS == []


def test_two_boards_do_not_see_each_others_parts(tmp_path: Path) -> None:
    """Give each board only its own parts, and keep the first Design intact."""
    one = write_project(
        tmp_path / "one",
        """\
        from pcbkit.design import R
        R("R1", "1k", "A", "B", "One")
        R("R2", "2k", "B", "C", "One")
        """,
    )
    two = write_project(
        tmp_path / "two",
        """\
        from pcbkit.design import part
        part("J9", "Lib:Sym", "V", "Lib:Fp", {}, block="Two")
        """,
    )
    first = load_design(one)
    second = load_design(two)
    assert [p["ref"] for p in second.parts] == ["J9"]
    assert second.block_order == ("Two",)
    assert [p["ref"] for p in first.parts] == ["R1", "R2"]
    assert first.block_order == ("One",)


def test_loading_the_same_board_twice_gives_the_same_design(tmp_path: Path) -> None:
    """Run the file again each time, not once and then from the module cache."""
    path = write_project(tmp_path, BASIC)
    assert load_design(path) == load_design(path)
    assert len(load_design(path).parts) == 3


def test_footprints_a_board_adds_do_not_reach_the_next_board(tmp_path: Path) -> None:
    """Reset FP to the generic entries before each file runs."""
    adds = write_project(
        tmp_path / "adds",
        """\
        from pcbkit.design import FP, C
        FP["BIG"] = "myboard:Big"
        C("C1", "1u", "A", "B", "Blk", size="BIG")
        """,
    )
    uses = write_project(
        tmp_path / "uses",
        """\
        from pcbkit.design import C
        C("C1", "1u", "A", "B", "Blk", size="BIG")
        """,
    )
    assert load_design(adds).parts[0]["fp"] == "myboard:Big"
    assert "BIG" not in FP
    with pytest.raises(KeyError, match="BIG"):
        load_design(uses)


def test_a_failed_load_leaves_a_clean_registry(tmp_path: Path) -> None:
    """Clear the half-built registry and FP when the file raises."""
    bad = write_project(
        tmp_path,
        """\
        from pcbkit.design import FP, R
        FP["X"] = "a:b"
        R("R1", "1k", "A", "B", "Blk")
        raise RuntimeError("boom")
        """,
    )
    with pytest.raises(RuntimeError, match="boom"):
        load_design(bad)
    assert design.PARTS == []
    assert "X" not in FP


def test_sys_modules_is_left_as_it_was_found(tmp_path: Path) -> None:
    """Drop the design module and the project modules it imported, and nothing else."""
    write_file(
        tmp_path / "power.py",
        """\
        from pcbkit.design import R
        R("R1", "1k", "A", "B", "Power")
        """,
    )
    path = write_project(tmp_path, "import power\nimport colorsys\n")
    load_design(path)
    assert "design" not in sys.modules
    assert "power" not in sys.modules
    assert "colorsys" in sys.modules  # not the project's, so not dropped


def test_a_module_that_declares_parts_is_declared_again_on_the_next_load(
    tmp_path: Path,
) -> None:
    """Re-import a sibling block module, so its parts are not lost the second time."""
    write_file(
        tmp_path / "power.py",
        """\
        from pcbkit.design import R
        R("R1", "1k", "A", "B", "Power")
        """,
    )
    path = write_project(
        tmp_path,
        """\
        import power
        from pcbkit.design import R
        R("R2", "2k", "B", "C", "Power")
        """,
    )
    assert [p["ref"] for p in load_design(path).parts] == ["R1", "R2"]
    assert [p["ref"] for p in load_design(path).parts] == ["R1", "R2"]


def test_a_design_module_registered_earlier_is_put_back(tmp_path: Path) -> None:
    """Restore a module that was already registered under the name ``design``."""
    marker = object()
    sys.modules["design"] = marker
    load_design(write_project(tmp_path, BASIC))
    assert sys.modules["design"] is marker


def test_the_path_may_be_relative_and_the_file_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve the path so a relative one works from any directory."""
    write_project(tmp_path, BASIC)
    monkeypatch.chdir(tmp_path)
    assert len(load_design(Path("design.py")).parts) == 3


# --- load_design: mistakes are reported, not guessed ------------------------------


def error_for(tmp_path: Path, source: str) -> str:
    """Load a design.py that should be rejected and return the message."""
    with pytest.raises(DesignError) as err:
        load_design(write_project(tmp_path, source))
    return err.value.message


def test_a_missing_design_py_names_the_file(tmp_path: Path) -> None:
    """Say which file is missing."""
    with pytest.raises(ProjectError) as err:
        load_design(tmp_path / "design.py")
    assert str(tmp_path.resolve() / "design.py") in err.value.message


def test_a_design_with_no_parts_is_an_error(tmp_path: Path) -> None:
    """Refuse an empty design, which is nearly always a wrong file or a missed call."""
    message = error_for(tmp_path, "X = 1\n")
    assert "defines no parts" in message


def test_a_reference_used_twice_is_an_error(tmp_path: Path) -> None:
    """Name the duplicated reference."""
    message = error_for(
        tmp_path,
        'from pcbkit.design import R\nR("R1", "1k", "A", "B", "X")\n'
        'R("R1", "2k", "A", "B", "X")\n',
    )
    assert "reference R1 is used twice" in message


def test_a_part_without_a_block_is_an_error(tmp_path: Path) -> None:
    """Say which part has no block."""
    message = error_for(
        tmp_path,
        'from pcbkit.design import part\npart("A1", "L:S", "V", "L:F", {})\n',
    )
    assert "part A1 has no block" in message


def test_a_block_missing_from_block_order_is_an_error(tmp_path: Path) -> None:
    """Name the part and the block."""
    message = error_for(tmp_path, BASIC + 'BLOCK_ORDER = ["Supply"]\n')
    assert "part D1 is in block 'Indicator', which is not in BLOCK_ORDER" in message


def test_a_block_order_entry_without_parts_is_an_error(tmp_path: Path) -> None:
    """Name the block that has no parts."""
    message = error_for(
        tmp_path, BASIC + 'BLOCK_ORDER = ["Supply", "Indicator", "X"]\n'
    )
    assert "BLOCK_ORDER lists 'X', which has no parts" in message


def test_a_block_listed_twice_is_an_error(tmp_path: Path) -> None:
    """Refuse a BLOCK_ORDER that repeats a block."""
    message = error_for(
        tmp_path, BASIC + 'BLOCK_ORDER = ["Supply", "Indicator", "Supply"]\n'
    )
    assert "lists a block twice" in message


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('BLOCK_WIDTHS = {"Suply": 100}', "names the block 'Suply'"),
        ('BLOCK_WIDTHS = {"Supply": 0}', "should be a number > 0"),
        ('BLOCK_WIDTHS = {"Supply": True}', "should be a number > 0"),
        ('BLOCK_WIDTHS = {"Supply": "wide"}', "should be a number > 0"),
        ("BLOCK_WIDTHS = [100]", "should be a dict of block name to a number > 0"),
        ('BLOCK_TITLES = {"Supply": 3}', "should be a string"),
        ('BLOCK_TITLES = {"Nope": "x"}', "names the block 'Nope'"),
        ("NOTES = 'one string'", "NOTES in design.py should be a list of strings"),
        ("NOTES = ['ok', 3]", "NOTES in design.py should be a list of strings"),
        ("BLOCK_ORDER = 'Supply'", "BLOCK_ORDER in design.py should be a list"),
        ("COMPANY = 5", "COMPANY in design.py should be a string"),
        ("COMMENT = ['x']", "COMMENT in design.py should be a string"),
    ],
)
def test_a_badly_typed_optional_name_is_an_error(
    tmp_path: Path, line: str, expected: str
) -> None:
    """Say which name is wrong and why; a typo never silently takes the default."""
    assert expected in error_for(tmp_path, BASIC + line + "\n")


def test_an_error_inside_design_py_reaches_the_user_untouched(tmp_path: Path) -> None:
    """Let the project's own exception through, not a wrapped copy."""
    with pytest.raises(NameError, match="nope"):
        load_design(write_project(tmp_path, "nope\n"))
