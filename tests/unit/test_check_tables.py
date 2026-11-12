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
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from typing import Any

import pytest

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
    Net,
    Nullable,
    Ohms,
    OneOf,
    Opt,
    Pad,
    Record,
    Ref,
    TableError,
    Text,
    Tuple,
    Volts,
    Window,
)
from tests import check_toy
from tests.unit.test_cli import invoke

ROOT = Path(__file__).resolve().parents[2]


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
        (Bool(), True, 1, "specs.T: expected true or false, got 1"),
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
    assert errors(Pad(), -1) == ["specs.T: expected pad, got -1"]


def test_two_keys_that_read_as_one_pad_are_an_error() -> None:
    """Refuse ``{1: x, "1": y}``: after reading they would be one key."""
    found = errors(DictOf(Pad(), Net()), {1: "A", "1": "B"})
    assert found == ['specs.T["1"]: the key is a duplicate of another after reading']


def test_a_wrong_length_row_is_one_error_for_the_row_at_its_path() -> None:
    """Report the pins row of the plan's example, path and all."""
    shape = tables.PROPOSED["FAULT_STATES"]
    value = {
        "logic_absent": {
            "scenario": {"vin": 5.0},
            "pins": [("U2", 1, (0, 0.4)), ("U2", "EN")],
        }
    }
    assert tables.validate("FAULT_STATES", value, shape) == [
        'specs.FAULT_STATES["logic_absent"]["pins"][1]: expected '
        "(ref, pad, (min V, max V)), got ('U2', 'EN')"
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
    assert tables.describe(tables.PROPOSED["PIN_NETS"]) == "{ref: {pad: net or None}}"
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
                TABLES = {"PIN_NETS": Net()}
            """
        },
    )
    with pytest.raises(ValueError, match="PIN_NETS is declared in both"):
        tables.registry(pkg)


def test_the_real_registry_imports_only_modules_that_declare_tables() -> None:
    """Run on the real package without importing pytest-bound or pcbnew modules."""
    assert tables.engine_modules() == []
    assert set(tables.registry()) == set(tables.EXISTING) | set(tables.PROPOSED)


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


def test_the_good_example_covers_every_proposed_table() -> None:
    """Keep an example per proposed table, so none goes untested."""
    assert set(GOOD_PROPOSED) == set(tables.PROPOSED)


@pytest.mark.parametrize("name", sorted(tables.PROPOSED))
def test_each_proposed_table_accepts_its_example(name: str) -> None:
    """Accept a table written as the shape says."""
    assert tables.validate(name, GOOD_PROPOSED[name], tables.PROPOSED[name]) == []


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


@pytest.mark.parametrize("name", sorted(tables.PROPOSED))
def test_each_proposed_table_refuses_a_wrong_example(name: str) -> None:
    """Refuse one mistake per table, with an error that starts at its path."""
    found = tables.validate(name, BAD_PROPOSED[name], tables.PROPOSED[name])
    assert found
    assert all(line.startswith(f"specs.{name}") for line in found)


def test_the_bad_example_covers_every_proposed_table() -> None:
    """Keep a refused example per table."""
    assert set(BAD_PROPOSED) == set(tables.PROPOSED)


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
        + "\nFAULT_STATES = {'c': {'scenario': {}, 'pins': [('U2', 'EN')]}}\n",
    )
    monkeypatch.chdir(root)
    result = invoke("specs")
    assert result.exit_code == 1
    assert (
        'specs.FAULT_STATES["c"]["pins"][0]: expected '
        "(ref, pad, (min V, max V)), got ('U2', 'EN')"
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
