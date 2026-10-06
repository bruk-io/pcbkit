"""Unit tests for pcbkit.project: config validation, root discovery, module import."""

from __future__ import annotations

import copy
import dataclasses
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pcbkit import project
from pcbkit.project import (
    SCHEMA,
    BoardConfig,
    ChecksConfig,
    Config,
    FabConfig,
    Project,
    ProjectError,
    RouteConfig,
    StackupConfig,
    StitchConfig,
    find_root,
    import_optional_project_module,
    import_project_module,
    load_config,
    load_project,
    parse_config,
)

MINIMAL_TOML = """\
[board]
stem = "my_board"
title = "My Board"
rev = "A"
fab_name = "My_Board_revA"
"""

FULL_TOML = """\
[board]
stem = "my_board"
title = "My Board"
rev = "B"
fab_name = "My_Board_revB"

[stackup]
layers = 2
thickness_mm = 2
copper_mm = 0.07

[route]
freerouting_passes = 60
tries = 6
stall_timeout_s = 120
eco_unlock_reach_mm = 12

[stitch]
pitch_mm = 4.0
dense = [[10, 20, 30, 40, 1.5], [0.5, 0.5, 2.5, 2.5, 1]]
gap_limit_mm = 2.5

[fab]
profile = "pcbway"

[checks]
groups = ["kicad", "outputs", "fab", "copper", "circuit", "esp32s3"]
"""


def good() -> dict[str, Any]:
    """Return a fresh, valid config dict (only the required [board] keys)."""
    return {
        "board": {
            "stem": "my_board",
            "title": "My Board",
            "rev": "A",
            "fab_name": "My_Board_revA",
        }
    }


def with_key(section: str, key: str, value: object) -> dict[str, Any]:
    """Return a valid config dict with ``[section] key`` set to ``value``."""
    data = good()
    data.setdefault(section, {})[key] = value
    return data


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Undo whatever a test does to sys.path and sys.modules."""
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    yield
    sys.path[:] = saved_path
    for name in set(sys.modules) - set(saved_modules):
        del sys.modules[name]
    sys.modules.update(saved_modules)


# --- config: good files and defaults ---------------------------------------------


def test_minimal_file_gets_every_documented_default(tmp_path: Path) -> None:
    """Fill every optional key with the default the docs promise."""
    (tmp_path / "pcbkit.toml").write_text(MINIMAL_TOML)
    config = load_config(tmp_path / "pcbkit.toml")
    assert config == Config(
        board=BoardConfig("my_board", "My Board", "A", "My_Board_revA"),
        stackup=StackupConfig(layers=2, thickness_mm=1.6, copper_mm=0.035),
        route=RouteConfig(
            freerouting_passes=40,
            tries=3,
            stall_timeout_s=90,
            eco_unlock_reach_mm=8.0,
        ),
        stitch=StitchConfig(pitch_mm=5.0, dense=(), gap_limit_mm=3.4),
        fab=FabConfig(profile="pcbway"),
        checks=ChecksConfig(groups=("kicad", "outputs")),
    )


def test_full_file_keeps_every_value(tmp_path: Path) -> None:
    """Read every key, turning TOML ints into floats where the schema says float."""
    (tmp_path / "pcbkit.toml").write_text(FULL_TOML)
    config = load_config(tmp_path / "pcbkit.toml")
    assert config.board.rev == "B"
    assert config.stackup == StackupConfig(2, 2.0, 0.07)
    assert isinstance(config.stackup.thickness_mm, float)
    assert config.route == RouteConfig(60, 6, 120, 12.0)
    assert isinstance(config.route.eco_unlock_reach_mm, float)
    assert config.stitch.pitch_mm == 4.0
    assert config.stitch.dense == (
        (10.0, 20.0, 30.0, 40.0, 1.5),
        (0.5, 0.5, 2.5, 2.5, 1.0),
    )
    assert config.stitch.gap_limit_mm == 2.5
    assert config.checks.groups == (
        "kicad",
        "outputs",
        "fab",
        "copper",
        "circuit",
        "esp32s3",
    )


def test_empty_optional_tables_are_fine() -> None:
    """Treat a present-but-empty optional table like an absent one."""
    data = good()
    for section in ("stackup", "route", "stitch", "fab", "checks"):
        data[section] = {}
    assert parse_config(data) == parse_config(good())


def test_config_is_frozen() -> None:
    """Refuse to change a loaded config."""
    config = parse_config(good())
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.board.stem = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.route = config.route


def test_schema_keys_match_the_config_dataclasses() -> None:
    """Keep SCHEMA and the dataclasses that hold its values in step."""
    classes = {
        "board": BoardConfig,
        "stackup": StackupConfig,
        "route": RouteConfig,
        "stitch": StitchConfig,
        "fab": FabConfig,
        "checks": ChecksConfig,
    }
    assert set(classes) == set(SCHEMA)
    for name, cls in classes.items():
        assert {f.name for f in dataclasses.fields(cls)} == set(SCHEMA[name]), name
    assert [f.name for f in dataclasses.fields(Config)] == list(SCHEMA)


# --- config: files that cannot be read -------------------------------------------


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    """Say which pcbkit.toml is missing."""
    with pytest.raises(ProjectError, match="pcbkit.toml not found") as err:
        load_config(tmp_path / "pcbkit.toml")
    assert str(tmp_path) in err.value.message


def test_unreadable_path_is_a_project_error(tmp_path: Path) -> None:
    """Turn an OSError (here: the path is a directory) into a ProjectError."""
    with pytest.raises(ProjectError, match="cannot read"):
        load_config(tmp_path)


def test_bad_toml_syntax_names_the_file(tmp_path: Path) -> None:
    """Report a TOML syntax error with the file and the parser's reason."""
    path = tmp_path / "pcbkit.toml"
    path.write_text('[board\nstem = "x"\n')
    with pytest.raises(ProjectError, match="is not valid TOML") as err:
        load_config(path)
    assert str(path) in err.value.message


def test_non_utf8_file_is_not_valid_toml(tmp_path: Path) -> None:
    """Report undecodable bytes the same way as bad syntax."""
    path = tmp_path / "pcbkit.toml"
    path.write_bytes(b"\xff\xfe[board]\n")
    with pytest.raises(ProjectError, match="is not valid TOML"):
        load_config(path)


# --- config: missing keys and tables ----------------------------------------------


@pytest.mark.parametrize("key", ["stem", "title", "rev", "fab_name"])
def test_each_required_board_key_is_required(key: str) -> None:
    """Name the missing key, in its table."""
    data = good()
    del data["board"][key]
    with pytest.raises(ProjectError) as err:
        parse_config(data)
    assert f"[board] missing required key '{key}'" in err.value.message


def test_missing_board_table_is_one_clear_message() -> None:
    """Report a missing [board] once, listing what it needs, not four key errors."""
    with pytest.raises(ProjectError) as err:
        parse_config({})
    assert "1 problem" in err.value.message
    assert "missing table [board] with stem, title, rev, fab_name" in err.value.message


def test_all_problems_are_reported_together() -> None:
    """List every problem in one error so the user fixes them in one pass."""
    data = good()
    del data["board"]["rev"]
    data["route"] = {"tries": "six", "freerouting_pases": 5}
    with pytest.raises(ProjectError) as err:
        parse_config(data, source="/x/pcbkit.toml")
    message = err.value.message
    assert message.startswith("/x/pcbkit.toml: 3 problems\n")
    assert "[board] missing required key 'rev'" in message
    assert "[route] unknown key 'freerouting_pases'" in message
    assert "[route] tries: expected a whole number >= 1, got 'six' (string)" in message


# --- config: unknown tables and keys ----------------------------------------------


def test_a_misspelt_key_is_an_error_with_a_suggestion() -> None:
    """Refuse a typo instead of silently using the default."""
    with pytest.raises(ProjectError) as err:
        parse_config(with_key("route", "freerouting_pases", 60))
    assert (
        "[route] unknown key 'freerouting_pases' (did you mean 'freerouting_passes'?)"
        in err.value.message
    )


def test_an_unrelated_unknown_key_gets_no_suggestion() -> None:
    """Skip the "did you mean" when nothing is close."""
    with pytest.raises(ProjectError) as err:
        parse_config(with_key("route", "zzz", 1))
    assert "[route] unknown key 'zzz'" in err.value.message
    assert "did you mean" not in err.value.message


def test_a_misspelt_table_is_an_error_with_a_suggestion() -> None:
    """Refuse an unknown table such as [boards]."""
    data = good()
    data["boards"] = {"stem": "x"}
    with pytest.raises(ProjectError) as err:
        parse_config(data)
    assert "unknown table 'boards' (did you mean 'board'?)" in err.value.message


def test_a_key_outside_any_table_is_an_error() -> None:
    """Say that keys belong inside a table when one sits at the top level."""
    data = good()
    data["stem"] = "oops"
    with pytest.raises(ProjectError) as err:
        parse_config(data)
    assert "unknown key 'stem'" in err.value.message
    assert "keys belong inside a table" in err.value.message


def test_a_table_that_is_not_a_table_is_an_error() -> None:
    """Reject ``route = 3`` where a table belongs."""
    data = good()
    data["route"] = 3
    with pytest.raises(ProjectError) as err:
        parse_config(data)
    assert "[route] should be a table, got 3 (integer)" in err.value.message


# --- config: wrong types and values -----------------------------------------------

BAD_VALUES = [
    # (section, key, value, text the message must contain)
    ("board", "stem", 5, "expected a non-empty string, got 5 (integer)"),
    ("board", "title", "   ", "expected a non-empty string"),
    ("board", "rev", 2, "(quote it to make it a string)"),
    ("board", "rev", 0, "(quote it to make it a string)"),
    ("board", "rev", True, "expected a non-empty string, got True (boolean)"),
    ("board", "stem", "has space", "is used in file names"),
    ("board", "stem", ".hidden", "is used in file names"),
    ("board", "fab_name", "a/b", "is used in file names"),
    ("stackup", "layers", True, "expected a whole number >= 1, got True (boolean)"),
    ("stackup", "layers", "2", "expected a whole number >= 1, got '2' (string)"),
    ("stackup", "layers", 2.0, "expected a whole number >= 1, got 2.0 (float)"),
    ("stackup", "layers", 0, "expected a whole number >= 1"),
    ("stackup", "layers", 4, "must be 2, got 4 (pcbkit builds two-layer boards only)"),
    ("stackup", "thickness_mm", "1.6", "expected a number > 0, got '1.6' (string)"),
    ("stackup", "thickness_mm", True, "expected a number > 0, got True (boolean)"),
    ("stackup", "thickness_mm", 0, "expected a number > 0"),
    ("stackup", "copper_mm", -0.035, "expected a number > 0"),
    ("stackup", "copper_mm", float("inf"), "expected a number > 0"),
    ("stackup", "copper_mm", float("nan"), "expected a number > 0"),
    ("stackup", "copper_mm", 10**400, "expected a number > 0"),
    ("route", "tries", 0, "expected a whole number >= 1"),
    ("route", "tries", 2.5, "expected a whole number >= 1, got 2.5 (float)"),
    ("route", "freerouting_passes", -1, "expected a whole number >= 1"),
    ("route", "stall_timeout_s", 1.5, "expected a whole number >= 1"),
    ("route", "eco_unlock_reach_mm", "8", "expected a number > 0"),
    ("stitch", "pitch_mm", 0, "expected a number > 0"),
    ("stitch", "gap_limit_mm", "x", "expected a number > 0"),
    ("stitch", "dense", "box", "expected a list of boxes, got 'box' (string)"),
    ("stitch", "dense", [5], "box 0 should be [x0, y0, x1, y1, pitch]"),
    ("stitch", "dense", [[1, 2, 3]], "box 0 should be [x0, y0, x1, y1, pitch]"),
    ("stitch", "dense", [[1, 2, 3, 4, "x"]], "five finite numbers"),
    ("stitch", "dense", [[1, 2, 3, 4, True]], "five finite numbers"),
    ("stitch", "dense", [[0, 0, 1, 1, 1], [5, 0, 1, 1, 1]], "box 1 needs x0 < x1"),
    ("stitch", "dense", [[0, 0, 1, 1, 0]], "box 0 needs x0 < x1, y0 < y1 and pitch"),
    ("stitch", "dense", [[0, 5, 1, 1, 1]], "box 0 needs x0 < x1, y0 < y1 and pitch"),
    ("stitch", "dense", [[1, 0, 1, 1, 1]], "box 0 needs x0 < x1, y0 < y1 and pitch"),
    ("stitch", "dense", [[0, 1, 1, 1, 1]], "box 0 needs x0 < x1, y0 < y1 and pitch"),
    ("fab", "profile", "jlcpcb", "expected one of 'pcbway', got 'jlcpcb' (string)"),
    ("fab", "profile", 3, "expected one of 'pcbway', got 3 (integer)"),
    ("checks", "groups", "kicad", "expected a list of names from"),
    ("checks", "groups", ["kicad", "nope"], "item 1 should be one of"),
    ("checks", "groups", [1], "item 0 should be one of"),
    ("checks", "groups", ["kicad", "kicad"], "'kicad' is listed twice"),
]


@pytest.mark.parametrize(("section", "key", "value", "expected"), BAD_VALUES)
def test_wrong_types_and_values_are_rejected(
    section: str, key: str, value: object, expected: str
) -> None:
    """Reject a bad value and say what was expected, where, and what was found."""
    with pytest.raises(ProjectError) as err:
        parse_config(with_key(section, key, value))
    assert f"[{section}] {key}: " in err.value.message
    assert expected in err.value.message


def test_every_schema_key_rejects_something() -> None:
    """Make sure BAD_VALUES covers every key in SCHEMA."""
    covered = {(section, key) for section, key, _, _ in BAD_VALUES}
    wanted = {(section, key) for section, keys in SCHEMA.items() for key in keys}
    assert covered == wanted


def test_good_values_are_accepted_at_the_edges() -> None:
    """Accept the smallest valid whole numbers and a TOML int for a float key."""
    data = good()
    data["route"] = {"tries": 1, "freerouting_passes": 1, "stall_timeout_s": 1}
    data["stackup"] = {"thickness_mm": 1, "copper_mm": 1}
    data["checks"] = {"groups": []}
    config = parse_config(data)
    assert config.route.tries == 1
    assert config.stackup.thickness_mm == 1.0
    assert config.checks.groups == ()


def test_parse_config_does_not_change_its_input() -> None:
    """Leave the caller's dict alone."""
    data = with_key("stitch", "dense", [[0, 0, 1, 1, 1]])
    before = copy.deepcopy(data)
    parse_config(data)
    assert data == before


# --- project root discovery -------------------------------------------------------


def make_project(folder: Path) -> Path:
    """Create a minimal board project in ``folder`` and return it."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pcbkit.toml").write_text(MINIMAL_TOML)
    return folder


def test_root_is_the_start_folder_when_it_has_the_file(tmp_path: Path) -> None:
    """Find a pcbkit.toml in the folder itself."""
    make_project(tmp_path)
    assert find_root(tmp_path) == tmp_path.resolve()


def test_root_is_found_from_a_deep_subfolder(tmp_path: Path) -> None:
    """Walk up through parents."""
    make_project(tmp_path)
    deep = tmp_path / "kicad" / "a" / "b"
    deep.mkdir(parents=True)
    assert find_root(deep) == tmp_path.resolve()


def test_nearest_project_wins(tmp_path: Path) -> None:
    """Stop at the closest pcbkit.toml when projects are nested."""
    make_project(tmp_path)
    inner = make_project(tmp_path / "boards" / "inner")
    assert find_root(inner / "kicad") == inner.resolve()


def test_start_may_be_a_file(tmp_path: Path) -> None:
    """Accept a file path and start from its folder."""
    make_project(tmp_path)
    (tmp_path / "layout.py").write_text("W = 1\n")
    assert find_root(tmp_path / "layout.py") == tmp_path.resolve()


def test_start_defaults_to_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the working directory when no start is given."""
    make_project(tmp_path)
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    assert find_root() == tmp_path.resolve()


def test_no_project_is_an_error_that_says_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point at `pcbkit new` when there is no project.

    The config file name is swapped for one that cannot exist anywhere above the
    temporary folder, so the result does not depend on the machine's directory tree.
    """
    monkeypatch.setattr(project, "CONFIG_NAME", "pcbkit-test-no-such-file.toml")
    with pytest.raises(ProjectError) as err:
        find_root(tmp_path)
    assert "no pcbkit-test-no-such-file.toml in" in err.value.message
    assert "pcbkit new" in err.value.message


def test_a_directory_named_like_the_config_is_not_a_project(tmp_path: Path) -> None:
    """Skip a folder called pcbkit.toml and keep walking up to the real file."""
    outer = make_project(tmp_path / "outer")
    inner = outer / "inner"
    (inner / "pcbkit.toml").mkdir(parents=True)
    assert find_root(inner) == outer.resolve()


def test_load_project_returns_root_config_and_directories(tmp_path: Path) -> None:
    """Load the project found above a subfolder."""
    make_project(tmp_path)
    (tmp_path / "kicad").mkdir()
    found = load_project(tmp_path / "kicad")
    assert isinstance(found, Project)
    assert found.root == tmp_path.resolve()
    assert found.config.board.stem == "my_board"
    assert found.kicad_dir == found.root / "kicad"
    assert found.out_dir == found.root / "out"
    assert found.golden_dir == found.root / "golden"
    assert found.fab_dir == found.root / "fab"
    assert found.archive_dir == found.root / "archive"
    generated = {found.kicad_dir, found.out_dir, found.golden_dir, found.fab_dir}
    assert {d.name for d in generated} == set(project.GENERATED_DIRS)


def test_load_project_reports_a_broken_config(tmp_path: Path) -> None:
    """Let the config's own error through, naming the file."""
    (tmp_path / "pcbkit.toml").write_text("[board]\nstem = 5\n")
    with pytest.raises(ProjectError) as err:
        load_project(tmp_path)
    assert str((tmp_path / "pcbkit.toml").resolve()) in err.value.message
    assert "[board] stem: expected a non-empty string" in err.value.message


# --- importing project modules ----------------------------------------------------


def write_module(root: Path, name: str, source: str) -> Path:
    """Write ``<root>/<name>.py`` and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.py"
    path.write_text(source)
    return path


def test_a_module_is_imported_by_file_path(tmp_path: Path) -> None:
    """Import routing.py and use what it defines."""
    write_module(
        tmp_path,
        "routing",
        "NETCLASSES = {'Wide': (0.6, 0.25, 0.9, 0.5, ['/VIN'])}\n"
        "def prerouted(board, api):\n"
        "    return ('routed', board, api)\n",
    )
    routing = import_project_module(tmp_path, "routing")
    assert routing.NETCLASSES["Wide"][0] == 0.6
    assert routing.prerouted(1, 2) == ("routed", 1, 2)
    assert routing.__name__ == "routing"


def test_project_modules_can_import_each_other(tmp_path: Path) -> None:
    """Let routing.py do ``import layout`` and share one layout module."""
    write_module(tmp_path, "layout", "W = 80\nH = 60\n")
    write_module(tmp_path, "routing", "import layout\nWIDTH = layout.W\n")
    routing = import_project_module(tmp_path, "routing")
    assert routing.WIDTH == 80
    assert import_project_module(tmp_path, "layout") is routing.layout


def test_a_second_import_returns_the_same_module(tmp_path: Path) -> None:
    """Run a project module's top level once."""
    write_module(tmp_path, "routing", "STATE = []\n")
    first = import_project_module(tmp_path, "routing")
    first.STATE.append(1)
    second = import_project_module(tmp_path, "routing")
    assert second is first
    assert second.STATE == [1]


def test_the_project_root_goes_on_sys_path_once(tmp_path: Path) -> None:
    """Add the root once, however many modules are imported."""
    write_module(tmp_path, "layout", "W = 1\n")
    write_module(tmp_path, "routing", "W = 2\n")
    import_project_module(tmp_path, "layout")
    import_project_module(tmp_path, "routing")
    assert sys.path.count(str(tmp_path.resolve())) == 1


def test_same_name_in_another_project_replaces_the_module(tmp_path: Path) -> None:
    """Load the right file when two projects have a routing.py."""
    one = write_module(tmp_path / "one", "routing", "VALUE = 1\n").parent
    two = write_module(tmp_path / "two", "routing", "VALUE = 2\n").parent
    assert import_project_module(one, "routing").VALUE == 1
    assert import_project_module(two, "routing").VALUE == 2
    assert import_project_module(one, "routing").VALUE == 1


def test_a_missing_required_module_names_the_file(tmp_path: Path) -> None:
    """Say which file the project lacks and where the contract is documented."""
    with pytest.raises(ProjectError) as err:
        import_project_module(tmp_path, "routing")
    assert str(tmp_path / "routing.py") in err.value.message
    assert "docs/project-interface.md" in err.value.message


def test_a_missing_optional_module_is_none(tmp_path: Path) -> None:
    """Return None, not an error, for an optional module such as footprints."""
    assert import_optional_project_module(tmp_path, "footprints") is None
    assert "footprints" not in sys.modules


@pytest.mark.parametrize("name", ["", "../routing", "a.b", "class", "has space"])
def test_a_bad_module_name_is_a_programming_error(tmp_path: Path, name: str) -> None:
    """Reject names that are not plain identifiers."""
    with pytest.raises(ValueError, match="is not a module name"):
        import_optional_project_module(tmp_path, name)


def test_a_broken_module_raises_its_own_error_and_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    """Let the project's SyntaxError through and unregister the half-loaded module."""
    write_module(tmp_path, "routing", "def broken(:\n")
    with pytest.raises(SyntaxError):
        import_project_module(tmp_path, "routing")
    assert "routing" not in sys.modules


def test_a_failed_reload_restores_the_previous_module(tmp_path: Path) -> None:
    """Put back the module that was loaded before a replacement failed."""
    good_one = write_module(tmp_path / "one", "routing", "VALUE = 1\n").parent
    bad_two = write_module(tmp_path / "two", "routing", "raise RuntimeError('no')\n")
    first = import_project_module(good_one, "routing")
    with pytest.raises(RuntimeError, match="no"):
        import_project_module(bad_two.parent, "routing")
    assert sys.modules["routing"] is first
