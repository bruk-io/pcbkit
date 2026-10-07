"""Unit tests for the helpers of pcbkit.check.plugin that need no pytest session.

The plugin itself (its hooks, fixtures and results file) is exercised end to end by
child pytest runs in tests/integration/test_check_plugin.py. Here are the pieces that
can be called directly: ``ProjectModule`` and ``MissingSpec`` (a project's specs.py
and the like), ``MissingParam``, ``check_id`` (given a stand-in for a pytest item),
``_skip_reason`` (given a real ``TestReport``), ``builtin_dir``, and the tables in
``pcbkit.check.results`` that name the built-in check groups.

Only helpers are imported from the plugin, never its fixtures: pytest would register
any fixture it found in a test module's namespace.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Any

import pytest

import pcbkit.check.builtin
import pcbkit.check.packs
from pcbkit.check import results
from pcbkit.check.plugin import (
    MissingParam,
    MissingSpec,
    ProjectModule,
    _skip_reason,
    builtin_dir,
    check_id,
)
from pcbkit.project import CHECK_GROUPS
from tests.board_files import restored_imports, write_file, write_project

SPECS = """\
VIN_MAX = 12.0
NAMES = ["R1", "U1"]
NOTHING = None
_PRIVATE = 7
"""


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Undo the project modules (``specs`` and so on) a test imported by path."""
    with restored_imports():
        yield


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Write a board project (a pcbkit.toml and a design.py) and return its root."""
    write_project(tmp_path / "board", "")
    return tmp_path / "board"


@pytest.fixture
def specs(root: Path) -> ProjectModule:
    """Return the project's specs.py, which defines a few names."""
    write_file(root / "specs.py", SPECS)
    return ProjectModule(root, "specs")


@pytest.fixture
def circuits(root: Path) -> ProjectModule:
    """Return the project's circuits.py, which does not exist."""
    return ProjectModule(root, "circuits")


def read(module: ProjectModule, name: str) -> Any:
    """Return ``module.<name>`` by way of getattr, so the name can be a variable."""
    return getattr(module, name)


# --- ProjectModule: names that are there ------------------------------------------


def test_a_name_the_project_defines_comes_back(specs: ProjectModule) -> None:
    """Return the project's own value, whatever it is, even None."""
    assert specs.VIN_MAX == 12.0
    assert specs.NAMES == ["R1", "U1"]
    assert specs.NOTHING is None


def test_a_name_with_one_leading_underscore_is_the_projects_too(
    specs: ProjectModule,
) -> None:
    """Treat only names that start with two underscores as the language's."""
    assert specs._PRIVATE == 7


def test_the_module_is_imported_on_the_first_name_asked_for(
    root: Path, specs: ProjectModule
) -> None:
    """Import nothing when it is made, and the project's file on first use."""
    assert specs.path == root / "specs.py"
    assert "specs" not in sys.modules
    assert specs.VIN_MAX == 12.0
    assert (
        Path(sys.modules["specs"].__file__).resolve() == (root / "specs.py").resolve()
    )


def test_each_module_name_has_its_own_names(root: Path, specs: ProjectModule) -> None:
    """Keep specs.py and circuits.py apart, though they share a folder."""
    write_file(root / "circuits.py", "GAIN = 3\n")
    circuits = ProjectModule(root, "circuits")
    assert circuits.GAIN == 3
    with pytest.raises(MissingSpec, match=r"circuits\.VIN_MAX"):
        read(circuits, "VIN_MAX")
    with pytest.raises(MissingSpec, match=r"specs\.GAIN"):
        read(specs, "GAIN")


# --- ProjectModule: names that are not there --------------------------------------


def test_a_missing_name_raises_missing_spec_naming_the_name_and_the_file(
    root: Path, specs: ProjectModule
) -> None:
    """Say ``specs.NOPE`` is not defined in the project's specs.py."""
    with pytest.raises(MissingSpec) as raised:
        read(specs, "NOPE")
    assert isinstance(raised.value, AttributeError)
    message = str(raised.value)
    assert "specs.NOPE" in message
    assert str(root / "specs.py") in message
    assert "is not defined in" in message


def test_a_missing_file_raises_missing_spec_saying_it_does_not_exist(
    root: Path, circuits: ProjectModule
) -> None:
    """Say ``circuits.GAIN`` is needed but circuits.py is not there."""
    with pytest.raises(MissingSpec) as raised:
        read(circuits, "GAIN")
    message = str(raised.value)
    assert "circuits.GAIN" in message
    assert str(root / "circuits.py") in message
    assert "does not exist" in message


def test_a_missing_file_has_a_path_to_where_it_would_be(
    root: Path, circuits: ProjectModule
) -> None:
    """Point ``path`` at the file to create."""
    assert circuits.path == root / "circuits.py"
    assert not circuits.path.exists()


def test_missing_spec_is_an_attribute_error() -> None:
    """Make hasattr and getattr's default work on a ProjectModule."""
    assert issubclass(MissingSpec, AttributeError)


def test_hasattr_and_getattr_with_a_default_treat_a_missing_name_as_absent(
    specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Answer False and the default for a missing name or file, not an error."""
    assert hasattr(specs, "VIN_MAX")
    assert not hasattr(specs, "NOPE")
    assert getattr(specs, "NOPE", 7) == 7
    assert getattr(specs, "VIN_MAX", 7) == 12.0
    assert not hasattr(circuits, "GAIN")
    assert getattr(circuits, "GAIN", 7) == 7


@pytest.mark.parametrize(
    ("name", "default", "expected"),
    [
        ("VIN_MAX", None, 12.0),
        ("VIN_MAX", 5, 12.0),
        ("NOPE", None, None),
        ("NOPE", 5, 5),
        ("NOTHING", 5, None),
    ],
    ids=["defined", "defined-with-default", "missing", "missing-with-default", "none"],
)
def test_get_returns_the_value_or_the_default(
    name: str, default: Any, expected: Any, specs: ProjectModule
) -> None:
    """Fall back to the default only for a name that is not defined."""
    assert specs.get(name, default) == expected


def test_get_returns_the_default_when_the_file_is_missing(
    circuits: ProjectModule,
) -> None:
    """Return the default for any name of a module that has no file."""
    assert circuits.get("GAIN") is None
    assert circuits.get("GAIN", 3) == 3


def test_in_asks_whether_the_project_defines_a_name(
    specs: ProjectModule, circuits: ProjectModule
) -> None:
    """Say True for a defined name (even one set to None) and False for the rest."""
    assert "VIN_MAX" in specs
    assert "NOTHING" in specs
    assert "NOPE" not in specs
    assert "GAIN" not in circuits


# --- ProjectModule: names that are not the project's to answer --------------------


def test_dunder_names_raise_a_plain_attribute_error_and_import_nothing(
    specs: ProjectModule,
) -> None:
    """Let tools probe for ``__wrapped__`` and the like without a MissingSpec."""
    with pytest.raises(AttributeError) as raised:
        read(specs, "__wrapped__")
    assert type(raised.value) is AttributeError
    assert str(raised.value) == "__wrapped__"
    assert inspect.unwrap(specs) is specs
    assert "specs" not in sys.modules


# --- ProjectModule: errors in the project's own file ------------------------------


@pytest.mark.parametrize(
    ("body", "error", "text"),
    [
        ("VIN_MAX = 1 / 0\n", ZeroDivisionError, "division"),
        ("raise RuntimeError('specs are broken')\n", RuntimeError, "specs are broken"),
        ("VIN_MAX = (\n", SyntaxError, None),
        ("import os\nVIN_MAX = os.no_such_name\n", AttributeError, "no_such_name"),
    ],
    ids=["zero-division", "raise", "syntax", "attribute-error"],
)
def test_an_error_in_the_projects_file_propagates_as_itself(
    body: str, error: type[Exception], text: str | None, root: Path
) -> None:
    """Show the file's own error, every time, and never relabel it as MissingSpec."""
    write_file(root / "specs.py", body)
    specs = ProjectModule(root, "specs")
    for _ in range(2):
        with pytest.raises(error, match=text) as raised:
            read(specs, "VIN_MAX")
        assert not isinstance(raised.value, MissingSpec)
    with pytest.raises(error):
        specs.get("VIN_MAX", 1)
    with pytest.raises(error):
        assert "VIN_MAX" in specs


# --- MissingParam -----------------------------------------------------------------


def test_missing_param_reads_missing_spec_in_a_test_id() -> None:
    """Show as ``missing-spec`` wherever pytest prints a parameter."""
    param = MissingParam("specs.NAMES is needed")
    assert repr(param) == "missing-spec"
    assert str(param) == "missing-spec"
    assert repr([param]) == "[missing-spec]"


def test_missing_param_keeps_its_message_and_cannot_be_changed() -> None:
    """Hold the message the failing check will show, as a frozen value."""
    param = MissingParam("specs.NAMES is needed")
    assert param.message == "specs.NAMES is needed"
    assert param == MissingParam("specs.NAMES is needed")
    assert param != MissingParam("specs.OTHER is needed")
    with pytest.raises(FrozenInstanceError):
        param.message = "changed"  # type: ignore[misc]


# --- check_id ---------------------------------------------------------------------


@dataclass
class Item:
    """Stand in for a pytest item: ``check_id`` reads only ``path`` and ``nodeid``."""

    path: Path
    nodeid: str


def test_a_built_in_check_is_named_by_its_module() -> None:
    """Give ``pcbkit.check.builtin.<module>::<name>``, whatever the node id says."""
    item = Item(
        builtin_dir() / "test_x.py", "src/pcbkit/check/builtin/test_x.py::name[param]"
    )
    assert (
        check_id(item, Path("/anywhere")) == "pcbkit.check.builtin.test_x::name[param]"
    )


def test_a_project_check_is_named_by_its_path_from_the_project_root(
    root: Path,
) -> None:
    """Give ``checks/test_a.py::name``, with folders and classes kept."""
    top = Item(root / "checks" / "test_a.py", "checks/test_a.py::name")
    assert check_id(top, root) == "checks/test_a.py::name"
    nested = Item(
        root / "checks" / "sub" / "test_b.py",
        "checks/sub/test_b.py::TestGroup::name[p-1]",
    )
    assert check_id(nested, root) == "checks/sub/test_b.py::TestGroup::name[p-1]"


def test_the_id_is_the_same_wherever_pytest_was_started(root: Path) -> None:
    """Ignore the folder part of the node id, which depends on the starting folder."""
    path = root / "checks" / "test_a.py"
    ids = {
        check_id(Item(path, nodeid), root)
        for nodeid in (
            "checks/test_a.py::name[x]",
            "board/checks/test_a.py::name[x]",
            "../board/checks/test_a.py::name[x]",
        )
    }
    assert ids == {"checks/test_a.py::name[x]"}


def test_only_the_first_double_colon_ends_the_path(root: Path) -> None:
    """Keep ``::`` inside a parameter id."""
    item = Item(root / "checks" / "test_a.py", "checks/test_a.py::name[a::b]")
    assert check_id(item, root) == "checks/test_a.py::name[a::b]"


def test_a_check_outside_the_project_is_named_by_its_file_name(
    root: Path, tmp_path: Path
) -> None:
    """Fall back to just ``test_z.py::name``."""
    item = Item(tmp_path / "elsewhere" / "test_z.py", "../elsewhere/test_z.py::name")
    assert check_id(item, root) == "test_z.py::name"


def test_a_look_alike_of_the_built_in_folder_is_not_built_in(
    root: Path, tmp_path: Path
) -> None:
    """Call a file built-in only when it is in the real built-in folder."""
    inside = Item(
        root / "pcbkit" / "check" / "builtin" / "test_x.py", "pcbkit/x/test_x.py::name"
    )
    assert check_id(inside, root) == "pcbkit/check/builtin/test_x.py::name"
    outside = Item(
        tmp_path / "pcbkit" / "check" / "builtin" / "test_x.py", "test_x.py::name"
    )
    assert check_id(outside, root) == "test_x.py::name"


def test_a_project_reached_through_a_symlink_gets_the_same_ids(
    root: Path, tmp_path: Path
) -> None:
    """Resolve both the root and the file, so the link does not change the id."""
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    via_link = Item(link / "checks" / "test_a.py", "checks/test_a.py::name")
    direct = Item(root / "checks" / "test_a.py", "checks/test_a.py::name")
    for item in (via_link, direct):
        for given_root in (link, root):
            assert check_id(item, given_root) == "checks/test_a.py::name"


# --- _skip_reason -----------------------------------------------------------------


def skipped(longrepr: Any) -> pytest.TestReport:
    """Return a real skipped setup report that carries ``longrepr``."""
    return pytest.TestReport(
        nodeid="checks/test_a.py::test_x",
        location=("checks/test_a.py", 3, "test_x"),
        keywords={},
        outcome="skipped",
        longrepr=longrepr,
        when="setup",
    )


@pytest.mark.parametrize(
    ("longrepr", "reason"),
    [
        (("checks/test_a.py", 4, "Skipped: needs pcbnew"), "needs pcbnew"),
        (("checks/test_a.py", 4, "needs pcbnew"), "needs pcbnew"),
        (("checks/test_a.py", 4, "Skipped: Skipped: twice"), "Skipped: twice"),
        (
            ("checks/test_a.py", 4, "unconditional Skipped: inside"),
            "unconditional Skipped: inside",
        ),
        (
            ("checks/test_a.py", 4, "Skipped: needs: pcbnew; or not"),
            "needs: pcbnew; or not",
        ),
        (("checks/test_a.py", 4, "Skipped: "), ""),
        ("Skipped: plain text", "plain text"),
    ],
    ids=[
        "prefix",
        "no-prefix",
        "only-one-prefix",
        "prefix-only-at-the-start",
        "colons-in-the-reason",
        "empty-reason",
        "not-a-tuple",
    ],
)
def test_skip_reason_is_the_reason_without_pytests_prefix(
    longrepr: Any, reason: str
) -> None:
    """Take the third item of the tuple pytest gives, minus ``Skipped: ``."""
    assert _skip_reason(skipped(longrepr)) == reason


# --- builtin_dir and the group tables ---------------------------------------------


def test_builtin_dir_is_the_folder_of_the_built_in_package() -> None:
    """Return the absolute folder that holds the built-in modules."""
    folder = builtin_dir()
    assert folder.is_absolute()
    assert folder.is_dir()
    assert folder == Path(pcbkit.check.builtin.__file__).resolve().parent
    assert (folder / "__init__.py").is_file()
    assert (folder / "test_kicad.py").is_file()
    assert folder.parts[-3:] == ("pcbkit", "check", "builtin")


def test_the_package_name_in_the_ids_is_the_package_builtin_dir_points_at() -> None:
    """Make ``BUILTIN_PACKAGE`` import to the folder ``builtin_dir`` returns."""
    package = importlib.import_module(results.BUILTIN_PACKAGE)
    assert Path(package.__file__).resolve().parent == builtin_dir()


def test_builtin_groups_name_exactly_the_check_modules_in_the_folder() -> None:
    """Map every ``test_*.py`` module of the folder to a group, and nothing else."""
    modules = {path.stem for path in builtin_dir().glob("test_*.py")}
    assert modules  # the folder is not empty, or this test would prove nothing
    assert set(results.BUILTIN_GROUPS) == modules


def test_every_group_a_module_maps_to_is_one_a_project_may_switch_on() -> None:
    """Find each mapped group in ``project.CHECK_GROUPS``."""
    assert set(results.BUILTIN_GROUPS.values()) <= set(CHECK_GROUPS)


def test_every_group_a_project_may_switch_on_has_a_module_or_a_pack() -> None:
    """Leave no name in ``CHECK_GROUPS`` that would switch on nothing."""
    packs = Path(pcbkit.check.packs.__file__).resolve().parent
    pack_names = {path.stem for path in packs.glob("*.py")} - {"__init__"}
    assert pack_names  # there is at least one pack
    known = set(results.BUILTIN_GROUPS.values()) | pack_names
    assert set(CHECK_GROUPS) <= known
