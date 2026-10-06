"""Unit tests for pcbkit.route.hooks: what routing.py may define, and the guards.

A hook that is missing or the wrong shape is reported by name with the file, before any
routing starts. The docs table of hooks is checked against the names the loader reads.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from pcbkit.project import ProjectError, load_project
from pcbkit.route import hooks
from tests.board_files import TOML, restored_imports, write_file

DOC = Path(__file__).resolve().parents[2] / "docs" / "project-interface.md"

MINIMAL = """\
def prerouted(board, api):
    pass
"""


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep each test's routing.py and layout.py from outliving it."""
    with restored_imports():
        yield


def project(tmp_path: Path, routing: str | None, layout: str | None = None) -> Path:
    """Write a project with the given routing.py (and layout.py); return its root."""
    write_file(tmp_path / "pcbkit.toml", TOML)
    if routing is not None:
        write_file(tmp_path / "routing.py", routing)
    if layout is not None:
        write_file(tmp_path / "layout.py", layout)
    return tmp_path


def load(tmp_path: Path, routing: str | None) -> hooks.Hooks:
    """Return the hooks of a project whose routing.py holds ``routing``."""
    return hooks.load_hooks(load_project(project(tmp_path, routing)))


# --- what loads ---------------------------------------------------------------------


def test_a_routing_py_with_only_prerouted_loads_with_everything_else_empty(
    tmp_path: Path,
) -> None:
    """Fill the optional hooks with None, an empty dict and an empty set."""
    loaded = load(tmp_path, MINIMAL)
    assert callable(loaded.prerouted)
    assert loaded.netclasses == {}
    assert loaded.design_rules is None
    assert loaded.keepouts is None
    assert loaded.gnd_links is None
    assert loaded.zones is None
    assert loaded.solid_pad_refs == frozenset()


def test_every_hook_is_picked_up(tmp_path: Path) -> None:
    """Read each name in the table, and turn the sets and lists into plain data."""
    loaded = load(
        tmp_path,
        MINIMAL
        + """
NETCLASSES = {"Wide": (0.6, 0.25, 0.9, 0.5, ["/VIN", "/VOUT"])}
solid_pad_refs = ["J1", "J2"]


def design_rules(ds):
    pass


def keepouts(board, api):
    pass


def gnd_links(board, api):
    pass


def zones(board, api):
    pass
""",
    )
    assert loaded.netclasses == {"Wide": (0.6, 0.25, 0.9, 0.5, ("/VIN", "/VOUT"))}
    assert loaded.solid_pad_refs == frozenset({"J1", "J2"})
    for hook in (loaded.design_rules, loaded.keepouts, loaded.gnd_links, loaded.zones):
        assert callable(hook)


def test_netclass_sizes_may_be_ints_and_come_back_as_floats(tmp_path: Path) -> None:
    """Accept 1 for 1.0 mm."""
    loaded = load(tmp_path, MINIMAL + 'NETCLASSES = {"A": (1, 1, 1, 1, ["/N"])}\n')
    assert loaded.netclasses["A"] == (1.0, 1.0, 1.0, 1.0, ("/N",))


def test_an_error_inside_the_projects_own_code_is_not_hidden(tmp_path: Path) -> None:
    """Let the user's traceback through: it is theirs to fix."""
    with pytest.raises(ZeroDivisionError):
        load(tmp_path, "x = 1 / 0\n")


# --- the guards, each shown to trip -------------------------------------------------


def test_a_project_without_routing_py_is_an_error(tmp_path: Path) -> None:
    """Name the missing file."""
    with pytest.raises(ProjectError, match=r"routing\.py not found"):
        hooks.load_hooks(load_project(project(tmp_path, None)))


def test_a_routing_py_without_prerouted_is_an_error(tmp_path: Path) -> None:
    """Say prerouted is required, and that it may do nothing."""
    with pytest.raises(ProjectError, match=r"must define prerouted\(board, api\)"):
        load(tmp_path, "NETCLASSES = {}\n")


@pytest.mark.parametrize("name", ["prerouted", "keepouts", "zones", "gnd_links"])
def test_a_hook_that_is_not_a_function_is_an_error(tmp_path: Path, name: str) -> None:
    """Refuse a hook that is a number, naming it."""
    source = MINIMAL + f"{name} = 3\n"
    with pytest.raises(ProjectError, match=f"{name} should be a function"):
        load(tmp_path, source)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('"Wide": (0.6, 0.25, 0.9, 0.5)', r"NETCLASSES\['Wide'\] should be"),
        ('"Wide": (0.6, 0.25, 0.9, 0.5, ["/A"], 1)', r"should be \(track"),
        ('"Wide": 0.6', r"should be \(track"),
        ('"Wide": (0.6, "0.25", 0.9, 0.5, ["/A"])', "four numbers above 0"),
        ('"Wide": (0.6, 0, 0.9, 0.5, ["/A"])', "four numbers above 0"),
        ('"Wide": (0.6, True, 0.9, 0.5, ["/A"])', "four numbers above 0"),
        ('"Wide": (0.6, 0.25, 0.9, 0.5, "/A")', "list of net name patterns"),
        ('"Wide": (0.6, 0.25, 0.9, 0.5, [3])', "list of net name patterns"),
        ('"Wide": (0.6, 0.25, 0.9, 0.5, [""])', "list of net name patterns"),
        ('"": (0.6, 0.25, 0.9, 0.5, ["/A"])', "should be a class name"),
    ],
)
def test_a_malformed_netclass_is_an_error(
    tmp_path: Path, entry: str, message: str
) -> None:
    """Name the class and what is wrong with it."""
    with pytest.raises(ProjectError, match=message):
        load(tmp_path, MINIMAL + "NETCLASSES = {" + entry + "}\n")


def test_netclasses_that_is_not_a_dict_is_an_error(tmp_path: Path) -> None:
    """Refuse a list of classes."""
    with pytest.raises(ProjectError, match="NETCLASSES should be a dict"):
        load(tmp_path, MINIMAL + "NETCLASSES = [1, 2]\n")


@pytest.mark.parametrize("value", ['"J1"', "7", '{"J1": 1}', "[1, 2]", '[""]'])
def test_malformed_solid_pad_refs_is_an_error(tmp_path: Path, value: str) -> None:
    """Refuse a bare string (which would read as a set of letters), and non-refs."""
    with pytest.raises(ProjectError, match="solid_pad_refs"):
        load(tmp_path, MINIMAL + f"solid_pad_refs = {value}\n")


# --- the board's size ---------------------------------------------------------------


def test_the_board_size_is_w_and_h_of_layout_py(tmp_path: Path) -> None:
    """Read the width and height as floats."""
    root = project(tmp_path, MINIMAL, "W, H = 118, 68.5\n")
    assert hooks.board_size(load_project(root)) == (118.0, 68.5)


@pytest.mark.parametrize(
    "layout", ["H = 30\n", "W = 40\n", "W, H = 0, 30\n", 'W, H = "40", 30\n', "x = 1\n"]
)
def test_a_layout_without_a_usable_w_and_h_is_an_error(
    tmp_path: Path, layout: str
) -> None:
    """Say what layout.py must define."""
    root = project(tmp_path, MINIMAL, layout)
    with pytest.raises(ProjectError, match=r"layout\.py must define W and H"):
        hooks.board_size(load_project(root))


def test_a_project_without_layout_py_is_an_error(tmp_path: Path) -> None:
    """Name the missing file."""
    root = project(tmp_path, MINIMAL)
    with pytest.raises(ProjectError, match=r"layout\.py not found"):
        hooks.board_size(load_project(root))


# --- the docs -----------------------------------------------------------------------


def documented_hooks() -> set[str]:
    """Return the names in the first column of the routing.py hooks table."""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("\n## routing.py\n", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `(\w+)` \|", section, flags=re.MULTILINE))


def test_the_docs_table_lists_exactly_the_hooks_the_loader_reads() -> None:
    """Keep docs/project-interface.md and pcbkit.route.hooks in step."""
    assert documented_hooks() == set(hooks.HOOK_NAMES)
