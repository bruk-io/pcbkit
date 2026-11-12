"""Unit tests for pcbkit.check.tables and the ``pcbkit specs`` command.

Each shape primitive has a value it accepts and a value it refuses, with the exact
message, path included. The registry is shown to find a ``TABLES`` in a scratch module.
The toy board's own ``SPECS`` must validate clean: that is the evidence that the shapes
of the existing tables are as permissive as the checks are. A toy run shows a
``TableError`` raised while parametrising failing that one check by name.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy  # noqa: F401  (loaded before any restored_imports(): a C extension loads once)
import openpyxl  # noqa: F401
import pytest
import scipy  # noqa: F401

from pcbkit.check import runner, tables
from pcbkit.check.results import RESULTS_FILE
from pcbkit.check.tables import (
    Amps,
    Bool,
    Box,
    Callable,
    Choice,
    DictOf,
    Fraction,
    Int,
    ListOf,
    Mm,
    Mm2,
    Net,
    Nullable,
    Ohms,
    OneOf,
    Opt,
    Pad,
    Pairs,
    Record,
    Ref,
    TableError,
    Text,
    Tuple,
    Volts,
    Window,
)
from tests import check_toy
from tests.board_files import restored_imports
from tests.check_group_specs import COPPER_SPECS, FAB_SPECS
from tests.unit.test_cli import invoke

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _undo_imports() -> Iterator[None]:
    """Leave ``sys.modules`` and ``sys.path`` as they were after every test.

    ``pcbkit specs`` imports the project's ``specs.py`` in this process, under the bare
    name ``specs``; left there it fails the next test module that expects none.
    """
    with restored_imports():
        yield


def errors(shape: Any, value: Any, name: str = "T") -> list[str]:
    """Return the validation errors of ``value`` against ``shape``."""
    return tables.validate(name, value, shape)


# --- the primitives: one value each way, exact messages ----------------------------


@pytest.mark.parametrize(
    ("shape", "good", "bad", "message"),
    [
        (Text("label"), "x", "", "specs.T: expected label, got ''"),
        (Net(), "+3V3", 5, "specs.T: expected net, got 5"),
        (Ref(), "U1", "U 1", "specs.T: expected ref, got 'U 1'"),
        (Pad(), "A1", "", "specs.T: expected pad, got ''"),
        (Bool(), True, 1, "specs.T: expected True or False, got 1"),
        (Int("n", lo=0), 3, 3.0, "specs.T: expected n of at least 0, got 3.0"),
        (Int("n", lo=0), 0, -1, "specs.T: expected n of at least 0, got -1"),
        (Volts(), -3.3, "3", "specs.T: expected V, got '3'"),
        (Volts(), 5, True, "specs.T: expected V, got True"),
        (Volts(), 5, float("nan"), "specs.T: expected V, got nan"),
        (Amps(), 0.02, None, "specs.T: expected A, got None"),
        (Ohms(), 0, -1, "specs.T: expected ohm (at least 0), got -1"),
        (
            Mm("mm", nonneg=True),
            1.5,
            -0.1,
            "specs.T: expected mm (at least 0), got -0.1",
        ),
        (Mm(), -4.0, "x", "specs.T: expected mm, got 'x'"),
        (
            Fraction(hi=0.8),
            0.8,
            0.9,
            "specs.T: expected fraction in (0, 0.8], got 0.9",
        ),
        (Fraction(), 1, 0, "specs.T: expected fraction in (0, 1], got 0"),
        (
            Choice("top", "bottom"),
            "top",
            "left",
            'specs.T: expected "top" or "bottom", got \'left\'',
        ),
        (Choice(1, 2), 2, True, "specs.T: expected 1 or 2, got True"),
        (Nullable(Net()), None, 5, "specs.T: expected net or None, got 5"),
        (Callable(), len, 5, "specs.T: expected function, got 5"),
        (
            Window(Volts()),
            (0, 0.4),
            (5, 3),
            "specs.T: expected (min V, max V) with min <= max, got (5, 3)",
        ),
        (Window(Volts()), [1, 2], (1,), "specs.T: expected (min V, max V), got (1,)"),
        (
            Box(Text("name")),
            ("a", 0, 0, 1, 1),
            ("a", 0, 0, 0, 1),
            "specs.T: expected (name, x0, y0, x1, y1) with x0 < x1 and y0 < y1, "
            "got ('a', 0, 0, 0, 1)",
        ),
        (
            Tuple(Ref(), Pad()),
            ("U1", 2),
            ("U1",),
            "specs.T: expected (ref, pad), got ('U1',)",
        ),
        (ListOf(Ref()), ["R1", "R2"], "R1", "specs.T: expected [ref], got 'R1'"),
        (
            DictOf(Net(), Volts()),
            {"GND": 0},
            [("GND", 0)],
            "specs.T: expected {net: V}, got [('GND', 0)]",
        ),
        (
            Record(required={"a": Volts()}),
            {"a": 1},
            [],
            'specs.T: expected {"a": V}, got []',
        ),
        (OneOf(Net(), Callable()), len, 5, "specs.T: expected net or function, got 5"),
    ],
)
def test_each_primitive_accepts_one_value_and_refuses_another(
    shape: Any, good: Any, bad: Any, message: str
) -> None:
    """Give a passing example and a refused one, with the exact message."""
    assert errors(shape, good) == []
    assert errors(shape, bad) == [message]


def test_a_window_that_is_upside_down_is_refused_not_normalised() -> None:
    """Refuse min > max (a window nothing can be inside)."""
    assert errors(Window(Volts()), (3.0, 2.0))
    assert errors(Window(Volts()), (2.0, 2.0)) == []


def test_a_pad_is_an_int_or_a_str_and_reads_as_a_str() -> None:
    """Take pad 1 and pad "1" as the same pad: ``read`` gives the string."""
    shape = DictOf(Pad(), Pad())
    out, found = tables.convert("T", {1: 2, "3": "A4"}, shape)
    assert found == []
    assert out == {"1": "2", "3": "A4"}
    assert errors(Pad(), True) == ["specs.T: expected pad, got True"]
    assert errors(Pad(), "1 2") == ["specs.T: expected pad, got '1 2'"]
    assert errors(Pad(), " 1") == ["specs.T: expected pad, got ' 1'"]
    assert errors(Pad(), -1) == ["specs.T: expected pad, got -1"]


def test_two_keys_that_read_as_one_pad_are_an_error() -> None:
    """Refuse ``{1: x, "1": y}``: after reading they would be one key."""
    found = errors(DictOf(Pad(), Net()), {1: "A", "1": "B"})
    assert found == ['specs.T["1"]: the key is a duplicate of another after reading']


def test_a_wrong_length_row_is_one_error_for_the_row_at_its_path() -> None:
    """Report the pins row of the plan's example, path and all."""
    shape = tables.registry()["FAULT_STATES"]
    value = {
        "logic_absent": {
            "scenario": {"vin": 5.0},
            "pins": [("U2", 1, (0, 0.4)), ("U2", 5)],
        }
    }
    assert tables.validate("FAULT_STATES", value, shape) == [
        'specs.FAULT_STATES["logic_absent"]["pins"][1]: expected '
        "(ref, pad, (min V, max V)), got ('U2', 5)"
    ]


def test_every_error_is_reported_not_only_the_first() -> None:
    """List each wrong row, so one run shows all of a table's problems."""
    shape = ListOf(Tuple(Ref(), Volts()))
    found = errors(shape, [("R1", "x"), ("R2", 1), ("R 3", 2)])
    assert found == [
        "specs.T[0][1]: expected V, got 'x'",
        "specs.T[2][0]: expected ref, got 'R 3'",
    ]


def test_a_record_names_a_missing_and_an_unknown_key() -> None:
    """Say which key is missing, and which is unknown with the ones that are known."""
    shape = Record(required={"a": Volts()}, optional={"b": Volts()})
    assert errors(shape, {}) == ['specs.T: missing key "a"']
    assert errors(shape, {"a": 1, "c": 2}) == [
        'specs.T: unknown key "c" (the keys are "a", "b")'
    ]
    assert errors(Record(required={"a": Volts()}, open=True), {"a": 1, "c": 2}) == []


def test_an_optional_key_with_a_default_is_filled_in() -> None:
    """Read ``max_ohm`` as 10 when the table leaves it out."""
    shape = tables.PROPOSED["POWER_PATHS"]
    bare = {"nets": ["A", "B"], "paths": [], "parts": {}}
    assert tables.convert("POWER_PATHS", bare, shape)[0]["max_ohm"] == 10
    kept = {**bare, "max_ohm": 2.5}
    assert tables.convert("POWER_PATHS", kept, shape)[0]["max_ohm"] == 2.5
    assert errors(Opt(Ohms(), default=10), -1) == [
        "specs.T: expected ohm (at least 0), got -1"
    ]


def test_dict_of_with_required_keys_names_the_missing_one() -> None:
    """Refuse RESISTOR_POWER_W without its ``default``."""
    shape = tables.EXISTING["RESISTOR_POWER_W"]
    assert tables.validate("RESISTOR_POWER_W", {"R1": 0.1}, shape) == [
        'specs.RESISTOR_POWER_W: missing key "default"'
    ]


def test_describe_renders_the_shapes_the_docs_show() -> None:
    """Spell out a few shapes exactly."""
    assert tables.describe(tables.registry()["PIN_NETS"]) == "{ref: {pad: net or None}}"
    assert (
        tables.describe(tables.PROPOSED["REFERENCE_PLANES"])
        == '{net: "top" or "bottom" or ("top" or "bottom", max unreferenced mm)}'
    )
    assert tables.describe(tables.PROPOSED["PROBE_KEEPAWAY"]) == (
        "[(name, x0, y0, x1, y1)]"
    )


# --- read -------------------------------------------------------------------------


def test_read_returns_the_normalised_table_or_raises_table_error() -> None:
    """Give a good table back, and raise with every error for a bad one."""
    specs = types.SimpleNamespace(PIN_NETS={"J1": {1: "VIN", 2: None}})
    assert tables.read(specs, "PIN_NETS") == {"J1": {"1": "VIN", "2": None}}
    bad = types.SimpleNamespace(PIN_NETS={"J1": {1: 5}, "J 2": {}})
    with pytest.raises(TableError) as err:
        tables.read(bad, "PIN_NETS")
    assert err.value.errors == [
        'specs.PIN_NETS["J1"][1]: expected net or None, got 5',
        "specs.PIN_NETS[\"J 2\"]: expected ref, got 'J 2'",
    ]
    assert str(err.value) == "\n".join(err.value.errors)


def test_read_of_a_name_with_no_registered_shape_says_so() -> None:
    """Raise KeyError naming the table."""
    with pytest.raises(KeyError, match="NO_SUCH"):
        tables.read(types.SimpleNamespace(NO_SUCH=1), "NO_SUCH")


def test_read_passes_a_missing_name_through_as_the_project_module_raises_it() -> None:
    """Let ``specs.NAME`` raise (the plugin's MissingSpec) for an undefined table."""
    with pytest.raises(AttributeError):
        tables.read(types.SimpleNamespace(), "PIN_NETS")


# --- the registry -----------------------------------------------------------------


def make_package(
    tmp_path: Path, name: str, modules: dict[str, str]
) -> types.ModuleType:
    """Write a scratch package of modules and import it."""
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for module, text in modules.items():
        (pkg / f"{module}.py").write_text(textwrap.dedent(text), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        import importlib

        return importlib.import_module(name)
    finally:
        sys.path.remove(str(tmp_path))


def test_the_registry_finds_tables_in_a_module_without_a_shared_list(
    tmp_path: Path,
) -> None:
    """Gather ``TABLES`` from the modules of a package that declare it."""
    pkg = make_package(
        tmp_path,
        "scratch_a",
        {
            "mine": """
                from pcbkit.check.tables import Net, DictOf, Volts
                TABLES = {"SCRATCH_TABLE": DictOf(Net(), Volts())}
            """,
            "other": "X = 1\n",
            "boom": "raise RuntimeError('imported a module that declares no TABLES')\n",
        },
    )
    found = tables.registry(pkg)
    assert "SCRATCH_TABLE" in found
    assert set(tables.EXISTING) | set(tables.PROPOSED) <= set(found)
    assert tables.validate("SCRATCH_TABLE", {"GND": "x"}, found["SCRATCH_TABLE"]) == [
        "specs.SCRATCH_TABLE[\"GND\"]: expected V, got 'x'"
    ]


def test_a_table_declared_twice_is_an_error_naming_both_places(
    tmp_path: Path,
) -> None:
    """Refuse a name in EXISTING or PROPOSED that an engine module also declares."""
    pkg = make_package(
        tmp_path,
        "scratch_b",
        {
            "dup": """
                from pcbkit.check.tables import Net
                TABLES = {"GROUND_NET": Net()}
            """
        },
    )
    with pytest.raises(ValueError, match="GROUND_NET is declared in both"):
        tables.registry(pkg)


def declared_in_source() -> set[str]:
    """Return the engine modules that assign ``TABLES`` at module level, by source."""
    folder = Path(tables.__file__).parent
    return {
        f"pcbkit.check.{path.stem}"
        for path in folder.glob("*.py")
        if re.search(r"^TABLES\b", path.read_text(encoding="utf-8"), re.M)
    }


def test_the_real_registry_imports_only_modules_that_declare_tables() -> None:
    """Run on the real package without importing pytest-bound or pcbnew modules.

    The engine modules found are exactly those whose source assigns ``TABLES``, and
    none is a built-in check module or a pack.
    """
    found = tables.engine_modules()
    assert {m.__name__ for m in found} == declared_in_source() != set()
    assert not [
        m for m in found if ".builtin." in m.__name__ or ".packs." in m.__name__
    ]
    expected = set(tables.EXISTING) | set(tables.PROPOSED)
    for module in found:
        expected |= set(module.TABLES)
    assert set(tables.registry()) == expected


def test_no_name_is_in_both_the_existing_and_the_proposed_shapes() -> None:
    """Keep the two dicts apart, so a table has one shape."""
    assert not set(tables.EXISTING) & set(tables.PROPOSED)


def test_every_shape_describes_itself() -> None:
    """Render every registered shape, so none raises or comes out empty."""
    for name, shape in tables.registry().items():
        assert tables.describe(shape), name


# --- the existing tables: as permissive as the checks ------------------------------


def toy_specs() -> types.ModuleType:
    """Return the toy board's specs as a module."""
    module = types.ModuleType("specs")
    exec(check_toy.SPECS, module.__dict__)
    return module


def test_the_toy_boards_specs_validate_clean() -> None:
    """Accept every table of the toy, which the built-in checks run on today."""
    count, found = tables.check_module(toy_specs())
    assert found == []
    assert count >= 18  # the toy defines these many registered tables, none skipped


def test_a_pad_that_refuses_ints_would_not_pass_the_toy() -> None:
    """Show why: the toy's SUPPLY_PINS and DEVKIT_GPIO use int pads."""
    module = toy_specs()
    assert isinstance(module.SUPPLY_PINS[0][1], int)
    assert isinstance(next(iter(module.DEVKIT_GPIO)), int)


def test_the_blinky_templates_specs_validate_clean() -> None:
    """Accept the tables of the template a new board starts from."""
    module = types.ModuleType("specs")
    path = ROOT / "pcbkit" / "templates" / "board" / "specs.py"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    count, found = tables.check_module(module)
    assert found == []
    assert count >= 4


def test_a_table_with_rows_as_lists_and_pads_as_strings_is_accepted() -> None:
    """Accept both spellings, as the checks do."""
    module = types.SimpleNamespace(
        SUPPLY_PINS=[["U1", "1", [2.7, 5.5]]],
        DECOUPLING=[("U1", 3, ["C1", "C2"], 2.0)],
        SUPPLY_LIMITS=[("fits", 5.5, "<=", 6.0), lambda nl: ("f", 1, "<", 2)],
    )
    assert tables.check_module(module) == (3, [])


def test_check_module_skips_a_table_the_module_does_not_define() -> None:
    """Count only what the module has."""
    assert tables.check_module(types.SimpleNamespace(GROUND_NET="GND")) == (1, [])


# --- the proposed tables ----------------------------------------------------------

# Every table that is not an existing one: the proposed ones, and those that have
# moved into the engine module of their check.
NEW_TABLES = {
    n: shape for n, shape in tables.registry().items() if n not in tables.EXISTING
}

GOOD_PROPOSED = {
    "CAP_DERATE": {"CAP-1": 0.5},
    "FAULT_STATES": {"c": {"scenario": {"vin": 0}, "pins": [("U2", 5, (0, 0.4))]}},
    "POWER_PATHS": {
        "nets": ["VIN", "+5V"],
        "paths": [("VIN", "+5V", ["U3"])],
        "parts": {"U3": [(1, 2)]},
    },
    "PIN_NETS": {"J1": {1: "VIN", 2: None}},
    "MATING": {"P1": {"pins": ["VIN", None], "fits": ["J1"], "keyed": False}},
    "NET_LIMITS": {"+3V3": 3.6},
    "SEMI_VRATED": {
        "Q-1": {"vds": 20, "vgs": 8, "derate": 0.7, "pins": {"g": 1, "d": 3, "s": 2}},
        "D-1": {"vrrm": 40},
    },
    "I2C_EXPECTED": {"main": [0x3C, 0x40]},
    "ENABLE_LINKS": {"EN": [("U3", 3)]},
    "SUPPLY_NETS": ["GND", "+3V3"],
    "TEST_POINTS": [("VIN", 10.0)],
    "PROBE_KEEPAWAY": [("antenna", 0, 0, 10, 5)],
    "REFERENCE_PLANES": {"CLK": "top", "SDA": ("bottom", 1.5)},
    "PART_SPACING": [("U1", "U2", 3.0)],
    "POLARITY_MARKS": ["D1"],
    "FIDUCIALS": {"min": 3, "clearance_mm": 2.0},
    "BOTTOM_PARTS": ["U1"],
    "EXCLUSIONS": [("e1", "why", ["R*"], ["VIN"])],
    "PORT_EDGES": {"J1": ("left", 2.0)},
    "CONTACT_RATINGS": {"J1": {"amps": 3.0, "derate": 0.8}},
    "HOT_LOOPS": [
        {"name": "buck", "refs": ["U1"], "pads": [("U1", 2)], "max_mm2": 20.0}
    ],
    "HEIGHT_MAX_MM": 12.0,
}


def test_the_good_example_covers_every_new_table() -> None:
    """Keep an example per proposed or engine table, so none goes untested."""
    assert set(GOOD_PROPOSED) == set(NEW_TABLES)


@pytest.mark.parametrize("name", sorted(NEW_TABLES))
def test_each_new_table_accepts_its_example(name: str) -> None:
    """Accept a table written as the shape says."""
    assert tables.validate(name, GOOD_PROPOSED[name], NEW_TABLES[name]) == []


BAD_PROPOSED = {
    "CAP_DERATE": {"CAP-1": 0.9},
    "FAULT_STATES": {"c": {"scenario": {}, "pins": [], "extra": 1}},
    "POWER_PATHS": {"nets": [], "paths": [], "parts": {}, "max_ohm": -1},
    "PIN_NETS": {"J1": {1: 3}},
    "MATING": {"P1": {"pins": [], "fits": [], "keyed": "no"}},
    "NET_LIMITS": {"+3V3": "3.6"},
    "SEMI_VRATED": {"Q-1": {"vds": 20}},
    "I2C_EXPECTED": {"main": [0x80]},
    "ENABLE_LINKS": {"EN": [("U3",)]},
    "SUPPLY_NETS": ["GND", 3],
    "TEST_POINTS": [("VIN", -1)],
    "PROBE_KEEPAWAY": [("antenna", 10, 0, 0, 5)],
    "REFERENCE_PLANES": {"CLK": "left"},
    "PART_SPACING": [("U1", "U2")],
    "POLARITY_MARKS": "D1",
    "FIDUCIALS": {"min": 3},
    "BOTTOM_PARTS": [1],
    "EXCLUSIONS": [("e1", "why", ["R*"])],
    "PORT_EDGES": {"J1": ("middle", 2.0)},
    "CONTACT_RATINGS": {"J1": {"amps": 0, "derate": 0.8}},
    "HOT_LOOPS": [{"name": "buck"}],
    "HEIGHT_MAX_MM": -2,
}


@pytest.mark.parametrize("name", sorted(NEW_TABLES))
def test_each_new_table_refuses_a_wrong_example(name: str) -> None:
    """Refuse one mistake per table, with an error that starts at its path."""
    found = tables.validate(name, BAD_PROPOSED[name], NEW_TABLES[name])
    assert found
    assert all(line.startswith(f"specs.{name}") for line in found)


def test_the_bad_example_covers_every_new_table() -> None:
    """Keep a refused example per table."""
    assert set(BAD_PROPOSED) == set(NEW_TABLES)


# --- pcbkit specs -----------------------------------------------------------------


def project_with(tmp_path: Path, specs: str | None) -> Path:
    """Write a toy project whose specs.py is ``specs`` (no file when None)."""
    root = check_toy.write_project(tmp_path / "board", check_toy.good(), [])
    if specs is None:
        (root / "specs.py").unlink()
    else:
        (root / "specs.py").write_text(specs, encoding="utf-8")
    return root


def test_specs_on_a_good_project_prints_a_count_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check the toy's own specs (and a proposed table) and find nothing wrong."""
    root = project_with(
        tmp_path, check_toy.SPECS + "\nPIN_NETS = {'J1': {1: 'VIN', 2: None}}\n"
    )
    monkeypatch.chdir(root)
    result = invoke("specs")
    assert result.exit_code == 0, result.output
    assert "no problems" in result.output
    assert "table(s) checked" in result.output


def test_specs_on_a_bad_fault_states_row_prints_its_path_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print the path of the row, and exit 1."""
    root = project_with(
        tmp_path,
        check_toy.SPECS
        + "\nFAULT_STATES = {'c': {'scenario': {}, 'pins': [('U2', 5)]}}\n",
    )
    monkeypatch.chdir(root)
    result = invoke("specs")
    assert result.exit_code == 1
    assert (
        'specs.FAULT_STATES["c"]["pins"][0]: expected '
        "(ref, pad, (min V, max V)), got ('U2', 5)"
    ) in result.output
    assert "1 problem(s) in" in result.output


def test_specs_without_a_specs_file_says_so_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treat a project with no specs.py as having nothing to check."""
    monkeypatch.chdir(project_with(tmp_path, None))
    result = invoke("specs")
    assert result.exit_code == 0
    assert "No specs.py" in result.output


def test_specs_that_cannot_be_imported_exits_1_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a specs.py that raises when imported, not a traceback."""
    monkeypatch.chdir(project_with(tmp_path, "raise ValueError('oops')\n"))
    result = invoke("specs")
    assert result.exit_code == 1
    assert "specs.py could not be read" in result.output
    assert "oops" in result.output


def test_specs_needs_neither_pcbnew_nor_kicad_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run on the fake machine, which has neither."""
    monkeypatch.chdir(project_with(tmp_path, "GROUND_NET = 'GND'\n"))
    assert invoke("specs").exit_code == 0


def test_specs_is_documented_in_the_command_help() -> None:
    """Describe what it does in --help."""
    assert "shape" in invoke("specs", "--help").output


# --- a TableError while parametrising ---------------------------------------------


def test_a_table_error_while_parametrising_fails_that_one_check_by_name(
    tmp_path: Path,
) -> None:
    """Fail ``test_each[bad-table]`` with the paths, and leave the other check alone."""
    root = project_with(
        tmp_path,
        check_toy.SPECS + "\nPIN_NETS = {'J1': {1: 5}}\nGOOD_REFS = ['R1']\n",
    )
    folder = root / "checks"
    folder.mkdir()
    (folder / "test_tables.py").write_text(
        textwrap.dedent(
            """
            from pcbkit.check import tables
            from pcbkit.check.plugin import spec_params

            def pytest_generate_tests(metafunc):
                def refs(specs):
                    return sorted(tables.read(specs, "PIN_NETS"))

                spec_params(metafunc, "ref", refs)
                spec_params(metafunc, "good", lambda specs: sorted(specs.GOOD_REFS))

            def test_each(ref):
                raise AssertionError("must not run: the table is the wrong shape")

            def test_other(good):
                assert good == "R1"
            """
        ),
        encoding="utf-8",
    )
    command = runner.command(root, None, ["-q", "-p", "fake_nl"])
    subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    got = json.loads((root / "out" / "checks" / RESULTS_FILE).read_text("utf-8"))
    entries = {k.split("::", 1)[1]: v for k, v in got["checks"].items()}
    bad = entries["test_each[bad-table]"]
    assert bad["outcome"] == "failed"
    assert 'specs.PIN_NETS["J1"][1]: expected net or None, got 5' in bad["message"]
    assert entries["test_other[R1]"]["outcome"] == "passed"
    assert not [k for k in entries if "missing-spec" in k]


# --- the follow-ups of the slice 2 review -------------------------------------------
#
# Each section says which item of /tmp/wp17/slice2-followups.md it holds. The rule for
# the shapes of the existing tables: exactly as permissive as the check that reads the
# table (never refuse what a check accepts), and refusing what makes that check crash.


class Unprintable:
    """A value whose ``repr`` raises, as a project's own object might."""

    def __repr__(self) -> str:
        """Raise instead of printing."""
        raise RuntimeError("no repr")


GOOD_EXISTING: dict[str, Any] = {
    "PCBWAY": {
        "min_annular": 0.15,
        "min_drill": 0.3,
        "min_plated_slot": 0.5,
        "silk_min_height": 0.8,
        "silk_min_stroke": 0.15,
        "min_track_2oz": 0.2,
        "min_space_2oz": 0.2,
        "extra": "ignored",
    },
    "WIRE_PADS": {"J1": (1, "2")},
    "DECOUPLING": [("U1", 8, ("C1", "C2"), 3.0)],
    "POWER_FOOTPRINTS": [
        {"ref": "Q1", "tab_pad": 2, "tab_net": "VBUS", "smaller": [(1, 2), (3, 2)]},
        {"ref": "J1", "drill_mm": {"1": 1.0}},
        {"ref": "Q2", "tab_pad": 2, "tab_net": None},
    ],
    "I2C_BUSES": {
        "main": ("SDA", "SCL", 400e3, [("DISPLAY", 0x3C, 10), ("PROBE", 0x3D, None)])
    },
    "I2C_TR_MAX": {400e3: 300e-9},
    "I2C_PIN_C": 10e-12,
    "TRACE_C_PER_MM": 0.1e-12,
    "I2C_CABLE_LEN": 0.3,
    "I2C_CABLE_C_PER_M": 100e-12,
    "I2C_PULLUP_NET": "+3V3",
    "I2C_DEVICE_PREFIXES": ["U", "A"],
    "ASSEMBLY_TEXT_EXEMPT": ("J1",),
    "RESERVED_COPPER": [
        ("keepout", "top", 1.5, 3.6, 8.0, 6.8, {"/GND"}),
        ("strip", "bottom", 1.5, 33.6, 58.5, 36.8, ["/GND", "/RTN"]),
    ],
    "SENSE_LOOPS": [
        {
            "name": "shunt",
            "nets": ("SA", "SB"),
            "from_ref": "R1",
            "to_ref": "R2",
            "to_pads": ("1", "2"),
            "max_mm2": 60.0,
        }
    ],
    "COPPER_SEGMENTS": [
        {
            "name": "vin_path",
            "net": "VIN",
            "heating": True,
            "terminals": [("TP2", 1, -0.4, "load"), ("TP1", 1, 1.0, "source")],
        },
        {"name": "rtn", "net": "RTN", "terminals": [("TP5", 1, 1.0), ("TP4", 1, -1.0)]},
    ],
    "COPPER_BUDGETS": {
        "i_cont": 4.0,
        "i_peak": 8.0,
        "segment_drop_max_v": 0.08,
        "max_rise_c": 20.0,
    },
    "SUPPLY_REGULATION": {
        "supply": "vin_path",
        "ground": "rtn_path",
        "label": "the far load",
        "nominal_v": 12.0,
        "max_fraction": 0.01,
    },
    "GROUND_NET": "GND",
    "STITCH_BASIS": {"f_hz": 2.4e9, "er_fr4": 4.5},
    "PINOUT": {"U1": {"1": "VDD", 2: ""}},
    "PIN_ALIASES": {"IN": "VIN", "~": None},
    "ABSENT_REFS": {"U9": "not fitted in this revision"},
    "STRESS_SCENARIOS": [{"vin": 5.0, "drive": {"LED_DRV": 3.3}}, {}],
    "RESISTOR_POWER_W": {"default": 0.1, "R1": 0.25},
    "CAP_VRATED": {"CAP-10U": 10.0, "": 6.3},
    "LED_REFS": ("D1", "panel_led"),
    "LED_SCENARIO": {"vin": 5.0},
    "LED_I_MIN": 0.3e-3,
    "LED_I_MAX": 0.02,
    "SUPPLY_SCENARIO": {"vin": 5.0},
    "SUPPLY_PINS": [("U1", 1, (2.7, 5.5)), ["U2", "A1", [0, 3.6]]],
    "SUPPLY_LIMITS": [("fits", 5.5, "<=", 6.0), lambda nl: ("f", 1, "<", 2)],
    "I2C_IOL": {400e3: 3e-3},
    "I2C_VDD": 3.3,
    "DEVKIT_REF": "A1",
    "DEVKIT_GPIO": {1: 4, "2": 5},
    "ANALOG_NETS": ("SENSE",),
    "STRAP_SCENARIO": {},
    "GPIO_SOURCE_SCENARIO": {"vin": 5.0},
    "GPIO_SCENARIOS": {"5.0-led": {"vin": 5.0}, 2: {}},
    "OPEN_DRAIN_NETS": ["SDA", "SCL"],
    "RAIL_3V3": "+3V3",
    "ESP32_VDD": 3.3,
    "ESP32_VIH": 2.475,
    "ESP32_VIL": 0.825,
    "ESP32_VOH_MIN": 2.64,
    "ESP32_PIN_ABS_MAX": 3.6,
    "ESP32_GPIO_SOURCE_MAX": 0.02,
    "ESP32_INTERNAL_PULL": 45e3,
    "RESERVED": frozenset({35, 36}),
    "UART0": [43, 44],
    "ADC1": range(1, 11),
    "INPUT_ONLY": (),
    "STRAPS": {0: ("high", "pullup"), 45: ["low", "pulldown"]},
}


def test_there_is_a_positive_example_for_every_existing_table() -> None:
    """Keep an example per existing shape, so a new one cannot go without."""
    assert set(GOOD_EXISTING) == set(tables.EXISTING)


@pytest.mark.parametrize("name", sorted(tables.EXISTING))
def test_each_existing_table_accepts_its_example(name: str) -> None:
    """Accept a table written as the check that reads it takes it."""
    assert tables.validate(name, GOOD_EXISTING[name], tables.EXISTING[name]) == []


def test_the_copper_and_fab_group_specs_validate_clean() -> None:
    """Accept the specs the kicad-marked group tests write, without pcbnew.

    The integration tests of the copper and fab groups run these very texts through the
    real checks on a real board, so the shapes are as permissive as the checks are on
    real data. The counts keep the test from passing vacuously.
    """
    for text, count in ((COPPER_SPECS, 5), (FAB_SPECS, 11)):
        module = types.ModuleType("specs")
        exec(text, module.__dict__)
        assert tables.check_module(module) == (count, [])


# --- item 1: a range or a frozenset is a set of GPIO numbers ------------------------


@pytest.mark.parametrize("name", ["ADC1", "RESERVED", "UART0", "INPUT_ONLY"])
def test_a_gpio_set_may_be_any_iterable_that_the_pack_turns_into_a_frozenset(
    name: str,
) -> None:
    """Accept a range, a set and a generator; refuse a str, a dict and a bad number.

    ``packs/esp32s3.py`` reads each of these as ``frozenset(pick(NAME, default))``,
    and its own default for ADC1 is ``frozenset(range(1, 11))``.
    """
    shape = tables.EXISTING[name]
    for value in (range(1, 13), frozenset(range(1, 11)), {4, 5}, (n for n in (1, 2))):
        assert errors(shape, value, name) == [], value
    assert tables.convert(name, range(3), shape)[0] == [0, 1, 2]
    assert errors(shape, "12", name) == [f"specs.{name}: expected [GPIO], got '12'"]
    assert errors(shape, {1: 2}, name) == [
        f"specs.{name}: expected [GPIO], got {{1: 2}}"
    ]
    assert errors(shape, [1, "2"], name) == [f"specs.{name}[1]: expected GPIO, got '2'"]


# --- item 2: tab_net may be None ----------------------------------------------------


def test_a_power_footprint_may_have_tab_net_none() -> None:
    """Accept ``tab_net=None``, as ``test_power_footprints_match_datasheet`` does.

    The check reads ``net = rule.get("tab_net")`` and tests ``net is not None``.
    """
    shape = tables.EXISTING["POWER_FOOTPRINTS"]
    row = {"ref": "Q1", "tab_pad": 2, "tab_net": None}
    assert errors(shape, [row], "POWER_FOOTPRINTS") == []
    assert errors(shape, [{**row, "tab_net": 5}], "POWER_FOOTPRINTS") == [
        'specs.POWER_FOOTPRINTS[0]["tab_net"]: expected net or None, got 5'
    ]


# --- item 3: a list of pairs where the check says dict(...) -------------------------

PAIR_TABLES = {
    "RESISTOR_POWER_W": [("default", 0.1), ("R1", 0.25)],
    "ABSENT_REFS": [("U9", "not fitted")],
    "PIN_ALIASES": [("IN", "VIN"), ("~", None)],
    "STRAPS": [(0, ("high", "pullup")), (45, ("low", "pulldown"))],
    "STITCH_BASIS": [("f_hz", 2.4e9), ("er_fr4", 4.5)],
}


@pytest.mark.parametrize("name", sorted(PAIR_TABLES))
def test_a_table_the_check_reads_through_dict_may_be_a_list_of_pairs(name: str) -> None:
    """Accept the pairs, and read them as the dict the check would make of them."""
    pairs = PAIR_TABLES[name]
    out, found = tables.convert(name, pairs, tables.EXISTING[name])
    assert found == []
    assert out == dict(pairs)
    assert tables.describe(tables.EXISTING[name]) == tables.describe(
        tables.EXISTING[name].inner
    )


def test_pairs_are_still_judged_row_by_row() -> None:
    """Refuse a pair list with a missing required key, a bad value or a 3-row."""
    shape = tables.EXISTING["RESISTOR_POWER_W"]
    assert errors(shape, [("R1", 0.25)], "R") == ['specs.R: missing key "default"']
    assert errors(shape, [("default", "x")], "R") == [
        "specs.R[\"default\"]: expected W, got 'x'"
    ]
    assert errors(shape, [("default", 1, 2)], "R") == [
        "specs.R: expected {ref or default: W}, got [('default', 1, 2)]"
    ]
    basis = tables.EXISTING["STITCH_BASIS"]
    assert errors(basis, [("pitch", 1.0)], "S") == [
        'specs.S: unknown key "pitch" (the keys are "f_hz", "er_fr4")'
    ]
    assert errors(Pairs(DictOf(Net(), Volts())), [("GND", "x")]) == [
        "specs.T[\"GND\"]: expected V, got 'x'"
    ]


# --- item 4: allowed nets of a reserved region may be one string --------------------


def test_allowed_nets_of_a_region_may_be_a_string_or_a_list() -> None:
    """Accept both; the list is the intended form, and a bad list item is named.

    ``intruders`` tests ``what.split()[-1] not in allowed``, which a string answers by
    substring.
    """
    shape = tables.EXISTING["RESERVED_COPPER"]
    row = ("keepout", "top", 0, 0, 5, 5)
    for allowed in ("/GND", ["/GND"], ("/GND",), {"/GND"}, []):
        assert errors(shape, [(*row, allowed)], "R") == [], allowed
    assert errors(shape, [(*row, ["/GND", 5])], "R") == [
        "specs.R[0][6][1]: expected allowed net, got 5"
    ]
    assert errors(shape, [(*row, 5)], "R") == [
        "specs.R[0][6]: expected allowed net or [allowed net], got 5"
    ]


# --- item 5: what the checks accept that the shapes refused -------------------------


def test_heating_may_be_0_or_1_as_the_check_tests_its_truth() -> None:
    """Accept 0 and 1 for ``heating`` and read them as False and True.

    ``test_copper_segment`` does ``segment.get("heating", True)`` and tests it.
    """
    shape = tables.EXISTING["COPPER_SEGMENTS"]
    base = {"name": "s", "net": "VIN", "terminals": [("TP1", 1, 1.0)]}
    for given, want in ((0, False), (1, True), (False, False), (True, True)):
        out, found = tables.convert("S", [{**base, "heating": given}], shape)
        assert found == []
        assert out[0]["heating"] is want
    assert errors(shape, [{**base, "heating": 2}], "S") == [
        'specs.S[0]["heating"]: expected True or False, got 2'
    ]
    assert errors(shape, [{**base, "heating": "no"}], "S") == [
        "specs.S[0][\"heating\"]: expected True or False, got 'no'"
    ]


def test_an_off_board_device_may_have_no_capacitance() -> None:
    """Accept None for the third field of an I2C device, which no check reads."""
    shape = tables.EXISTING["I2C_BUSES"]
    bus = ("SDA", "SCL", 400e3, [("DISPLAY", 0x3C, None)])
    assert errors(shape, {"main": bus}, "B") == []
    assert errors(shape, {"main": ("SDA", "SCL", 400e3, [("D", 0x3C, "10")])}, "B") == [
        "specs.B[\"main\"][3][0][2]: expected pF or None, got '10'"
    ]


def test_the_texts_a_check_copes_with_empty_may_be_empty() -> None:
    """Accept "" for a limit's name, a capacitor with no MPN, and an I2C prefix.

    A capacitor without an MPN looks up ``CAP_VRATED[""]``; ``r[0] in prefixes`` is
    False for an empty prefix; the name of a supply limit only goes into a message.
    """
    limits = tables.EXISTING["SUPPLY_LIMITS"]
    assert errors(limits, [("", 1, "<", 2)], "L") == []
    assert errors(tables.EXISTING["CAP_VRATED"], {"": 6.3}, "C") == []
    prefixes = tables.EXISTING["I2C_DEVICE_PREFIXES"]
    assert errors(prefixes, "", "P") == []
    assert errors(prefixes, ["U", ""], "P") == []
    assert errors(prefixes, ["U", 5], "P") == ["specs.P[1]: expected prefix, got 5"]


# --- item 6: PCBWAY needs all seven numbers -----------------------------------------

PCBWAY_ALL = dict.fromkeys(tables._PCBWAY_KEYS, 0.2)


def test_pcbway_names_the_key_the_fab_checks_would_fail_on() -> None:
    """Require each of the seven keys the fab checks read with ``limits["..."]``."""
    shape = tables.EXISTING["PCBWAY"]
    assert errors(shape, PCBWAY_ALL, "PCBWAY") == []
    assert len(PCBWAY_ALL) == 7
    for key in PCBWAY_ALL:
        short = {k: v for k, v in PCBWAY_ALL.items() if k != key}
        assert errors(shape, short, "PCBWAY") == [f'specs.PCBWAY: missing key "{key}"']
    assert errors(shape, {}, "PCBWAY") == [
        f'specs.PCBWAY: missing key "{key}"' for key in PCBWAY_ALL
    ]


# --- item 7: COPPER_BUDGETS ---------------------------------------------------------

BUDGETS = {"i_cont": 4.0, "i_peak": 8.0, "segment_drop_max_v": 0.08}


def test_copper_budgets_need_the_three_numbers_every_segment_reads() -> None:
    """Require ``i_cont``, ``i_peak`` and ``segment_drop_max_v``; ``max_rise_c`` is not.

    ``max_rise_c`` is read only for a segment that heats, which the table cannot know.
    """
    shape = tables.EXISTING["COPPER_BUDGETS"]
    assert errors(shape, BUDGETS, "B") == []
    assert errors(shape, {**BUDGETS, "max_rise_c": 20}, "B") == []
    for key in BUDGETS:
        short = {k: v for k, v in BUDGETS.items() if k != key}
        assert errors(shape, short, "B") == [f'specs.B: missing key "{key}"']
    assert errors(shape, {}, "B") == [
        f'specs.B: missing key "{key}"' for key in BUDGETS
    ]


@pytest.mark.parametrize("key", ["i_cont", "i_peak"])
@pytest.mark.parametrize("bad", [0, -1.0])
def test_copper_budget_currents_are_above_zero(key: str, bad: float) -> None:
    """Refuse a zero or negative current: the peak drop divides by ``i_cont``."""
    shape = tables.EXISTING["COPPER_BUDGETS"]
    assert errors(shape, {**BUDGETS, key: bad}, "B") == [
        f"specs.B[{json.dumps(key)}]: expected A (above 0), got {bad}"
    ]


# --- item 8: to_pads are texts, as the board's pad numbers are ----------------------


def test_sense_loop_pads_are_texts_because_the_check_compares_them_as_texts() -> None:
    """Refuse ``to_pads=[1]``: ``loop_area`` never matches an int pad and fails."""
    shape = tables.EXISTING["SENSE_LOOPS"]
    row = {
        "name": "shunt",
        "nets": ("SA", "SB"),
        "from_ref": "R1",
        "to_ref": "R2",
        "max_mm2": 60.0,
    }
    assert errors(shape, [{**row, "to_pads": ["1", "2"]}], "S") == []
    assert errors(shape, [{**row, "to_pads": [1]}], "S") == [
        'specs.S[0]["to_pads"][0]: expected pad, got 1'
    ]


class _Pad:
    """A pad of the stub board: a number, a net and a place."""

    def __init__(self, number: str, net: str, x: int) -> None:
        """Keep the three."""
        self.number, self.net, self.x = number, net, x

    def GetNumber(self) -> str:  # noqa: N802
        """Return the pad number as pcbnew does: a str."""
        return self.number

    def GetNetname(self) -> str:  # noqa: N802
        """Return the net, with KiCad's leading slash."""
        return self.net

    def GetX(self) -> int:  # noqa: N802
        """Return the place in internal units."""
        return self.x

    def GetY(self) -> int:  # noqa: N802
        """Return the place in internal units."""
        return 0


class _Footprint:
    """A footprint of the stub board."""

    def __init__(self, ref: str, pads: list[_Pad]) -> None:
        """Keep the reference and the pads."""
        self.ref, self.pads = ref, pads

    def GetReference(self) -> str:  # noqa: N802
        """Return the reference."""
        return self.ref

    def Pads(self) -> list[_Pad]:  # noqa: N802
        """Return the pads."""
        return self.pads


class _Board:
    """A board with two parts on a sense pair, and nothing else."""

    def GetFootprints(self) -> list[_Footprint]:  # noqa: N802
        """Return R1 and R2, each with a pad on both nets of the pair."""
        return [
            _Footprint("R1", [_Pad("1", "/SA", 0), _Pad("2", "/SB", 0)]),
            _Footprint("R2", [_Pad("1", "/SA", 1000000), _Pad("2", "/SB", 1000000)]),
        ]


def test_an_int_pad_in_to_pads_is_a_bare_key_error_in_loop_area(
    fake_pcbnew: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show why: ``p.GetNumber() in to_pads`` compares a str with the table's value."""
    from pcbkit.check import coupling

    monkeypatch.setattr(coupling, "_track_path", lambda board, net, a, z: [a, z])
    board = _Board()
    got = coupling.loop_area(board, ("SA", "SB"), "R1", "R2", ["1", "2"])
    assert got[0] == 0
    with pytest.raises(KeyError):
        coupling.loop_area(board, ("SA", "SB"), "R1", "R2", [1, 2])


# --- item 9: a zero that the check divides by ---------------------------------------


def test_the_numbers_a_check_divides_by_are_above_zero() -> None:
    """Refuse a zero sink current, nominal voltage and stitching frequency."""
    assert errors(tables.EXISTING["I2C_IOL"], {400e3: 0}, "I") == [
        "specs.I[400000.0]: expected A (above 0), got 0"
    ]
    reg = dict(GOOD_EXISTING["SUPPLY_REGULATION"])
    assert errors(
        tables.EXISTING["SUPPLY_REGULATION"], {**reg, "nominal_v": 0}, "R"
    ) == ['specs.R["nominal_v"]: expected V (above 0), got 0']
    for given in ({"f_hz": 0}, [("f_hz", 0)]):
        assert errors(tables.EXISTING["STITCH_BASIS"], given, "S") == [
            'specs.S["f_hz"]: expected Hz (above 0), got 0'
        ]


def test_the_zeros_do_divide_in_the_code_that_reads_them() -> None:
    """Tie the three refusals to the divisions: each is a ZeroDivisionError."""
    from pcbkit.check.stitching import Basis

    with pytest.raises(ZeroDivisionError):
        Basis(5.0, f_hz=0).lambda_mm  # noqa: B018
    assert (3.3 - 0.4) / 3e-3  # test_i2c_single_pullup_and_sink_current, nonzero
    with pytest.raises(ZeroDivisionError):
        (3.3 - 0.4) / 0  # the same line with I2C_IOL[hz] == 0


# --- item 10: an int too large for a float ------------------------------------------


def test_an_int_too_large_for_a_float_is_not_a_finite_number_and_loses_nothing() -> (
    None
):
    """Report it at its path, with the other errors, not an OverflowError."""
    assert tables._number(10**400) is False
    found = errors(Volts(), 10**400)
    assert len(found) == 1
    assert found[0].startswith("specs.T: expected V, got 1000")
    module = types.SimpleNamespace(
        SUPPLY_PINS=[("U1", 1, (0, 10**400))], I2C_VDD="3.3", LED_I_MAX=0.02
    )
    count, found = tables.check_module(module)
    assert count == 3
    assert len(found) == 2
    assert found[0].startswith("specs.I2C_VDD: expected V")
    assert found[1].startswith("specs.SUPPLY_PINS[0][2][1]: expected V, got 1000")


# --- item 12: error lines survive an object that cannot print itself ----------------


def test_a_value_whose_repr_raises_is_reported_and_loses_no_other_error() -> None:
    """Say the repr failed in its place, and go on to the next error."""
    found = errors(Volts(), Unprintable())
    assert found == ["specs.T: expected V, got <Unprintable object whose repr failed>"]
    shape = DictOf(Text("k"), Volts())
    assert errors(shape, {Unprintable(): 1, "b": "x"}) == [
        "specs.T[<Unprintable object whose repr failed>]: expected k, got "
        "<Unprintable object whose repr failed>",
        "specs.T[\"b\"]: expected V, got 'x'",
    ]


def test_one_table_that_cannot_be_checked_hides_no_other_table() -> None:
    """Name the table whose check raised, as a bug in pcbkit, and carry on."""

    class Evil:
        def __iter__(self) -> Iterator[Any]:
            raise RuntimeError("no iterating")

    module = types.SimpleNamespace(ADC1=Evil(), I2C_VDD="x", GROUND_NET="GND")
    count, found = tables.check_module(module)
    assert count == 3
    assert found == [
        "specs.ADC1: pcbkit could not check this table (RuntimeError: no iterating); "
        "this is a bug in pcbkit",
        "specs.I2C_VDD: expected V, got 'x'",
    ]


# --- item 16: OneOf says what is wrong inside the closest alternative ---------------


def test_a_semi_vrated_row_with_one_bad_key_names_that_key() -> None:
    """Report the closest alternative's error, not a description of both."""
    shape = tables.PROPOSED["SEMI_VRATED"]
    assert errors(shape, {"Q-1": {"vds": 20}}, "S") == [
        'specs.S["Q-1"]: missing key "vgs"'
    ]
    assert errors(shape, {"Q-1": {"vds": 20, "vgs": "x"}}, "S") == [
        'specs.S["Q-1"]["vgs"]: expected V, got \'x\''
    ]
    assert errors(shape, {"D-1": {"vrrm": 40, "derate": 0.9}}, "S") == [
        'specs.S["D-1"]["derate"]: expected fraction in (0, 0.8], got 0.9'
    ]
    # a value of neither kind keeps the one line that lists both
    assert errors(shape, {"Q-1": 5}, "S")[0].startswith(
        'specs.S["Q-1"]: expected {"vds": V, "vgs": V'
    )


def test_one_of_picks_the_alternative_with_fewest_errors() -> None:
    """Choose by kind first, then by the number of errors."""
    a = Record(required={"x": Volts(), "y": Volts(), "z": Volts()})
    b = Record(required={"x": Volts(), "w": Volts()})
    shape = OneOf(a, b)
    assert errors(shape, {"x": 1}) == ['specs.T: missing key "w"']
    assert errors(shape, 5) == [
        'specs.T: expected {"x": V, "y": V, "z": V} or {"x": V, "w": V}, got 5'
    ]
    assert errors(OneOf(Text("t"), ListOf(Net())), ["a", 5]) == [
        "specs.T[1]: expected net, got 5"
    ]


def test_a_bad_supply_limit_operator_is_named_inside_the_tuple() -> None:
    """Report the tuple's own error, not "expected (...) or function"."""
    shape = tables.EXISTING["SUPPLY_LIMITS"]
    assert errors(shape, [("fits", 5, "=<", 6)], "L") == [
        'specs.L[0][2]: expected "<" or "<=", got \'=<\''
    ]
    assert errors(shape, [("fits", 5)], "L") == [
        'specs.L[0]: expected (what, value, "<" or "<=", limit) or function, '
        "got ('fits', 5)"
    ]


# --- item 17: the registry ----------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "declared"),
    [
        ("TABLES = {}\n", True),
        ("TABLES: dict = {}\n", True),
        ("TABLES: dict\n", False),
        ("A, TABLES = 1, {}\n", True),
        ("(A, TABLES) = 1, {}\n", True),
        ("[TABLES, *REST] = {}, 1, 2\n", True),
        ("if True:\n    TABLES = {}\n", True),
        ("if False:\n    pass\nelse:\n    TABLES = {}\n", True),
        ("try:\n    TABLES = {}\nexcept Exception:\n    TABLES = {}\n", True),
        ("try:\n    import x\nexcept ImportError:\n    TABLES = {}\n", True),
        ("try:\n    pass\nfinally:\n    TABLES = {}\n", True),
        ("with open('x') as f:\n    TABLES = {}\n", True),
        ("def f():\n    TABLES = {}\n", False),
        ("class C:\n    TABLES = {}\n", False),
        ("X = 1\n", False),
        ("OTHER_TABLES = {}\n", False),
    ],
)
def test_declares_tables_reads_the_source_the_way_python_binds_names(
    tmp_path: Path, source: str, declared: bool
) -> None:
    """Find ``TABLES`` however it is bound at module level, and only then."""
    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    assert tables._declares_tables(str(path)) is declared


def test_a_module_that_only_annotates_tables_does_not_break_the_registry(
    tmp_path: Path,
) -> None:
    """Skip a module with ``TABLES: dict`` and no value; read ones set under ``if``."""
    pkg = make_package(
        tmp_path,
        "scratch_c",
        {
            "bare": "TABLES: dict\n",
            "never": "if False:\n    TABLES = {'NEVER': 1}\n",
            "tuple_target": """
                from pcbkit.check.tables import Net
                ONE, TABLES = 1, {"TUPLE_TABLE": Net()}
            """,
            "guarded": """
                from pcbkit.check.tables import Net
                try:
                    TABLES = {"TRY_TABLE": Net()}
                except Exception:
                    TABLES = {}
            """,
        },
    )
    found = tables.registry(pkg)
    assert {"TUPLE_TABLE", "TRY_TABLE"} <= set(found)
    assert "NEVER" not in found


def test_the_registry_finds_the_engine_modules_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache the scan: ``read`` calls ``registry`` each time and must not re-parse."""
    calls = []

    def counting(package: Any = None) -> list[Any]:
        calls.append(package)
        return []

    tables.clear_cache()
    monkeypatch.setattr(tables, "engine_modules", counting)
    try:
        tables.registry()
        tables.registry()
        specs = types.SimpleNamespace(GROUND_NET="GND")
        tables.read(specs, "GROUND_NET")
        tables.read(specs, "GROUND_NET")
        assert len(calls) == 1
        tables.clear_cache()
        tables.registry()
        assert len(calls) == 2
    finally:
        tables.clear_cache()


# --- item 18: the proposed shapes ---------------------------------------------------


def test_power_paths_parts_are_optional_and_filled_in_when_left_out() -> None:
    """Read ``parts`` as ``{}`` and ``max_ohm`` as 10 when a table leaves them out."""
    specs = types.SimpleNamespace(
        POWER_PATHS={"nets": ["VIN", "+5V"], "paths": [("VIN", "+5V", ["U3"])]}
    )
    first = tables.read(specs, "POWER_PATHS")
    assert first["parts"] == {}
    assert first["max_ohm"] == 10
    first["parts"]["U9"] = []  # a check that edits what it reads edits its own copy
    assert tables.read(specs, "POWER_PATHS")["parts"] == {}


def test_power_paths_max_ohm_is_above_zero() -> None:
    """Refuse 0 and negative: no path would ever count."""
    shape = tables.PROPOSED["POWER_PATHS"]
    base = {"nets": [], "paths": []}
    assert errors(shape, {**base, "max_ohm": 0}, "P") == [
        'specs.P["max_ohm"]: expected ohm (above 0), got 0'
    ]
    assert errors(shape, {**base, "max_ohm": 0.5}, "P") == []


def test_mating_rows_default_to_one_and_read_in() -> None:
    """Fill in ``rows`` so a check reads it without a default of its own."""
    specs = types.SimpleNamespace(
        MATING={"P1": {"pins": ["VIN", None], "fits": ["J1"], "keyed": False}}
    )
    assert tables.read(specs, "MATING")["P1"]["rows"] == 1
    specs.MATING["P1"]["rows"] = 2
    assert tables.read(specs, "MATING")["P1"]["rows"] == 2
    specs.MATING["P1"]["rows"] = 3
    with pytest.raises(TableError):
        tables.read(specs, "MATING")


def test_semi_vrated_pins_go_with_the_rating_they_belong_to() -> None:
    """Accept g/d/s with vds and vgs, a/k with vrrm; refuse them crossed."""
    shape = tables.PROPOSED["SEMI_VRATED"]
    fet = {"vds": 20, "vgs": 8}
    diode = {"vrrm": 40}
    assert errors(shape, {"Q": {**fet, "pins": {"g": 1, "d": 3, "s": 2}}}, "S") == []
    assert errors(shape, {"D": {**diode, "pins": {"a": 1, "k": 2}}}, "S") == []
    assert errors(shape, {"Q": {**fet, "pins": {"a": 1, "k": 2}}}, "S") == [
        'specs.S["Q"]["pins"]: missing key "g"',
        'specs.S["Q"]["pins"]: missing key "d"',
        'specs.S["Q"]["pins"]: missing key "s"',
        'specs.S["Q"]["pins"]: unknown key "a" (the keys are "g", "d", "s")',
        'specs.S["Q"]["pins"]: unknown key "k" (the keys are "g", "d", "s")',
    ]
    assert errors(shape, {"D": {**diode, "pins": {"g": 1, "d": 3, "s": 2}}}, "S") == [
        'specs.S["D"]["pins"]: missing key "a"',
        'specs.S["D"]["pins"]: missing key "k"',
        'specs.S["D"]["pins"]: unknown key "g" (the keys are "a", "k")',
        'specs.S["D"]["pins"]: unknown key "d" (the keys are "a", "k")',
        'specs.S["D"]["pins"]: unknown key "s" (the keys are "a", "k")',
    ]


def test_a_hot_loop_limit_is_an_area() -> None:
    """Use the area type for ``max_mm2``: refuse a negative one, keep the label."""
    assert isinstance(Mm2(), Mm2) and not isinstance(Mm2(), Mm)
    assert errors(Mm2("max mm2", nonneg=True), 20.0) == []
    assert errors(Mm2("max mm2", nonneg=True), -1) == [
        "specs.T: expected max mm2 (at least 0), got -1"
    ]
    hot = tables.PROPOSED["HOT_LOOPS"].item.required["max_mm2"]
    assert isinstance(hot, Mm2)
    assert not isinstance(hot, Mm)


# --- item 19: describe, and the docs of Table shapes --------------------------------


def test_describe_writes_true_and_false_as_python_does() -> None:
    """Say ``True or False``, in a shape and so in the docs rows made from it."""
    assert tables.describe(Bool()) == "True or False"
    assert "True or False" in tables.describe(tables.PROPOSED["MATING"])
    assert "true" not in tables.describe(tables.PROPOSED["MATING"])
    assert (
        tables.describe(Opt(Choice(1, 2), default=1)) == "1 or 2 (optional, default 1)"
    )


# --- item 12: pcbkit specs tells specs.py's faults from pcbkit's own -----------------


def test_a_syntax_error_in_specs_names_the_file_and_the_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Say ``specs.py could not be read`` with the line of the error."""
    monkeypatch.chdir(project_with(tmp_path, "GROUND_NET = 'GND'\nX = (\n"))
    result = invoke("specs")
    assert result.exit_code == 1
    assert "specs.py could not be read" in result.output
    assert "(specs.py line 2)" in result.output or "(specs.py line 3)" in result.output


def test_a_failure_at_a_line_of_specs_names_that_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point at the line of specs.py, though a module it imports raised."""
    root = project_with(tmp_path, "GROUND_NET = 'GND'\n\nimport helper\n\nX = 1\n")
    (root / "helper.py").write_text("\n\nraise ValueError('deep')\n", encoding="utf-8")
    monkeypatch.chdir(root)
    result = invoke("specs")
    assert result.exit_code == 1
    assert "specs.py could not be read: ValueError('deep') (specs.py line 3)" in (
        result.output
    )


def test_specs_that_calls_sys_exit_is_reported_not_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch ``sys.exit(3)``, which ``except Exception`` lets through."""
    monkeypatch.chdir(project_with(tmp_path, "import sys\nsys.exit(3)\n"))
    result = invoke("specs")
    assert result.exit_code == 1, result.output
    assert "specs.py could not be read: it called sys.exit(3)" in result.output
    monkeypatch.chdir(project_with(tmp_path / "again", "import sys\nsys.exit()\n"))
    assert "called sys.exit() while being imported" in invoke("specs").output


def test_a_failure_of_the_validator_is_pcbkits_own_not_specs_pys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Say pcbkit failed, and do not call specs.py unreadable."""

    def broken(package: Any = None) -> dict[str, Any]:
        raise RuntimeError("registry is broken")

    monkeypatch.chdir(project_with(tmp_path, "GROUND_NET = 'GND'\n"))
    monkeypatch.setattr(tables, "registry", broken)
    result = invoke("specs")
    assert result.exit_code == 1
    assert "pcbkit failed while checking the tables" in result.output
    assert "RuntimeError: registry is broken" in result.output
    assert "could not be read" not in result.output


def test_an_exception_that_cannot_print_itself_still_gives_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fall back to the exception's type when its repr raises."""
    specs = (
        "class Odd(Exception):\n"
        "    def __repr__(self):\n"
        "        raise RuntimeError('no repr')\n\n"
        "raise Odd()\n"
    )
    monkeypatch.chdir(project_with(tmp_path, specs))
    result = invoke("specs")
    assert result.exit_code == 1
    assert "specs.py could not be read: Odd (specs.py line 5)" in result.output


def test_a_value_that_cannot_print_itself_loses_no_other_error_in_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print every problem, the unprintable one as a stand-in."""
    specs = (
        "class Odd:\n"
        "    def __repr__(self):\n"
        "        raise RuntimeError('no repr')\n\n"
        "SUPPLY_PINS = [('U1', Odd(), (0, 1))]\n"
        "I2C_VDD = 'x'\n"
    )
    monkeypatch.chdir(project_with(tmp_path, specs))
    result = invoke("specs")
    assert result.exit_code == 1
    assert (
        "specs.SUPPLY_PINS[0][1]: expected pad, got <Odd object whose repr failed>"
        in result.output
    )
    assert "specs.I2C_VDD: expected V, got 'x'" in result.output
    assert "2 problem(s)" in result.output


# --- item 13: a table of the wrong shape read in a check's body ----------------------


def run_toy_project(root: Path) -> dict[str, Any]:
    """Run the project's own checks in a child pytest and return their results."""
    subprocess.run(
        runner.command(root, None, ["-q", "-p", "fake_nl"]),
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    got = json.loads((root / "out" / "checks" / RESULTS_FILE).read_text("utf-8"))
    return {k.split("::", 1)[1]: v for k, v in got["checks"].items()}


def test_a_table_error_in_a_check_body_fails_that_check_with_the_paths_only(
    tmp_path: Path,
) -> None:
    """Fail ``test_reads`` with the paths and no traceback; the other check passes."""
    root = project_with(
        tmp_path, check_toy.SPECS + "\nPIN_NETS = {'J1': {1: 5, 2: 'VIN'}, 'J 2': {}}\n"
    )
    folder = root / "checks"
    folder.mkdir()
    (folder / "test_tables.py").write_text(
        textwrap.dedent(
            """
            from pcbkit.check import tables

            def test_reads(specs):
                tables.read(specs, "PIN_NETS")
                raise AssertionError("must not run: the table is the wrong shape")

            def test_other(specs):
                assert tables.read(specs, "LED_I_MAX") == 0.02
            """
        ),
        encoding="utf-8",
    )
    entries = run_toy_project(root)
    bad = entries["test_reads"]
    assert bad["outcome"] == "failed"
    # pytest.fail(..., pytrace=False) reports as "Failed: <message>"
    assert bad["message"] == (
        'Failed: specs.PIN_NETS["J1"][1]: expected net or None, got 5\n'
        "specs.PIN_NETS[\"J 2\"]: expected ref, got 'J 2'"
    )
    assert "Traceback" not in bad["message"]
    assert "TableError" not in bad["message"]
    assert entries["test_other"]["outcome"] == "passed"
