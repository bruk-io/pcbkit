"""The pytest plugin behind ``pcbkit check``: fixtures, group switching and results.

Load it by name, never by entry point, so it only runs where a board's checks are
meant to:

    pytest -p pcbkit.check.plugin --pcbkit-project DIR \\
        --pyargs pcbkit.check.builtin DIR/checks

(``pcbkit check`` builds that command line; see ``pcbkit.check.runner``.) A plain
``pytest`` run, pcbkit's own tests included, does not load it and writes nothing.

The project comes from ``--pcbkit-project DIR`` (a folder with a ``pcbkit.toml``, or one
inside it); without the option it is the nearest one above the directory pytest was
started in. The plugin puts the project folder on ``sys.path``, so a check can
``import specs`` or ``import circuits``.

Fixtures (session scope unless noted):

* ``project``: the ``pcbkit.project.Project``.
* ``specs``, ``circuits``, ``layout``: the project's ``specs.py``, ``circuits.py`` and
  ``layout.py``. A name that is not defined, or a file that is missing, fails the check
  that asks for it with a message that names it; ``specs.get(name, default)`` and
  ``name in specs`` ask without failing.
* ``nl``: the ``Netlist`` of a fresh kicad-cli export of the schematic.
* ``board``: the pcbnew board, loaded from ``kicad/<stem>.kicad_pcb``.
* ``record`` (per test): ``record(key, value)`` keeps a number for the results file.
* ``out_dir``: ``<project>/out/checks``, where results and plots are written.

Results. When the run ends, ``out/checks/results.json`` holds one entry per check, keyed
by a stable id: ``pcbkit.check.builtin.test_x::name[param]`` for a built-in check and
``checks/test_x.py::name[param]`` (relative to the project) for the project's own.
Each entry has the ``outcome`` (passed, failed, skipped or error), the check's ``group``
("project" for the project's own), the skip ``reason`` or the failure ``message``, and
the ``numbers`` the check recorded. ``pcbkit report`` turns the file into a report.

Groups. A built-in check module belongs to a group (``BUILTIN_GROUPS``); the modules of
a group the project has not switched on in ``[checks] groups`` are not collected at all,
so they are neither run nor counted as skipped.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pcbkit.project import (
    Project,
    ProjectError,
    import_optional_project_module,
    load_project,
)

OPTION = "--pcbkit-project"
RESULTS_DIR = "checks"
RESULTS_FILE = "results.json"
RESULTS_FORMAT = 1

# The built-in check modules and the group each belongs to.
BUILTIN_GROUPS = {
    "test_kicad": "kicad",
    "test_outputs": "outputs",
    "test_fab": "fab",
    "test_copper": "copper",
    "test_circuit": "circuit",
    "test_esp32s3": "esp32s3",
}
BUILTIN_PACKAGE = "pcbkit.check.builtin"

STATE = pytest.StashKey[Any]()


class MissingSpec(AttributeError):
    """A project module or a name in it that a check needs is not there."""


@dataclass(frozen=True)
class MissingParam:
    """Stands in for the parameters of a check whose spec is missing.

    A check that is parametrised from the project's data has nothing to run over when
    that data is missing; it gets one parameter set of these, and fails with the
    message instead of vanishing from the run.
    """

    message: str

    def __repr__(self) -> str:
        """Return a short form, so it reads well in a test id."""
        return "missing-spec"


class ProjectModule:
    """A module of the project (specs.py and so on) whose absent names fail by name.

    ``module.NAME`` returns the project's value, or raises ``MissingSpec`` (an
    AttributeError, so ``hasattr`` and ``getattr(module, "NAME", default)`` work)
    saying which file and name are missing. The file is imported the first time a name
    is asked for; an error inside it propagates as an ordinary traceback.
    """

    def __init__(self, root: Path, name: str) -> None:
        """Remember where the module would be; import nothing yet."""
        self._root = Path(root)
        self._name = name
        self._module: ModuleType | None = None
        self._loaded = False

    @property
    def path(self) -> Path:
        """Return where the module's file is, or would be."""
        return self._root / f"{self._name}.py"

    def _load(self) -> ModuleType | None:
        """Import the module on first use; None if the file does not exist."""
        if not self._loaded:
            self._module = import_optional_project_module(self._root, self._name)
            self._loaded = True
        return self._module

    def __getattr__(self, attr: str) -> Any:
        """Return the project's ``attr``, or raise MissingSpec naming it."""
        if attr.startswith("__"):
            raise AttributeError(attr)
        module = self._load()
        if module is None:
            raise MissingSpec(
                f"{self._name}.{attr} is needed, but {self.path} does not exist: "
                "create it (docs/project-interface.md says what each check group reads)"
            )
        try:
            return getattr(module, attr)
        except AttributeError:
            raise MissingSpec(
                f"{self._name}.{attr} is not defined in {self.path}: add it "
                "(docs/project-interface.md says what each check group reads)"
            ) from None

    def get(self, attr: str, default: Any = None) -> Any:
        """Return the project's ``attr``, or ``default`` if it is not defined."""
        try:
            return getattr(self, attr)
        except MissingSpec:
            return default

    def __contains__(self, attr: str) -> bool:
        """Return True if the project defines ``attr``."""
        return self.get(attr, _ABSENT) is not _ABSENT


_ABSENT = object()


@dataclass
class State:
    """What the plugin keeps for one pytest run (it lives on ``config.stash``)."""

    project: Project
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    modules: dict[str, ProjectModule] = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    def module(self, name: str) -> ProjectModule:
        """Return the project's module ``name``, shared by every user of it."""
        if name not in self.modules:
            self.modules[name] = ProjectModule(self.project.root, name)
        return self.modules[name]


def state_of(config: pytest.Config) -> State:
    """Return the plugin's state; raise a UsageError if the plugin is not active."""
    state = config.stash.get(STATE, None)
    if state is None:
        raise pytest.UsageError(
            "the pcbkit check plugin is not active: run the checks with `pcbkit check`"
        )
    return state


def builtin_dir() -> Path:
    """Return the folder of the built-in check modules."""
    import pcbkit.check.builtin as builtin

    return Path(builtin.__file__).resolve().parent


def results_dir(project: Project) -> Path:
    """Return the folder the results, the report and the plots go in."""
    return project.out_dir / RESULTS_DIR


# --- options and set-up -----------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that names the board project."""
    group = parser.getgroup("pcbkit", "pcbkit board checks")
    group.addoption(
        OPTION,
        dest="pcbkit_project",
        metavar="DIR",
        default=None,
        help="the board project to check (a folder with a pcbkit.toml); "
        "default: the nearest one above the current directory",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Find the project and put its folder on sys.path."""
    where = config.getoption("pcbkit_project")
    start = Path(where) if where else Path(config.invocation_params.dir)
    try:
        project = load_project(start)
    except ProjectError as err:
        raise pytest.UsageError(err.format_message()) from None
    config.stash[STATE] = State(project)
    root = str(project.root)
    if root not in sys.path:
        sys.path.insert(0, root)


def pytest_report_header(config: pytest.Config) -> list[str]:
    """Say which board and which check groups this run is about."""
    project = state_of(config).project
    board = project.config.board
    groups = ", ".join(project.config.checks.groups) or "none"
    return [
        f"pcbkit: {board.title} rev {board.rev} ({project.root})",
        f"pcbkit check groups: {groups}",
    ]


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Leave out the built-in modules of a check group that is not switched on."""
    state = config.stash.get(STATE, None)
    if state is None or collection_path.suffix != ".py":
        return None
    if collection_path.resolve().parent != builtin_dir():
        return None
    group = BUILTIN_GROUPS.get(collection_path.stem)
    if group is not None and group not in state.project.config.checks.groups:
        return True
    return None


# --- fixtures ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def project(pytestconfig: pytest.Config) -> Project:
    """Return the board project being checked."""
    return state_of(pytestconfig).project


@pytest.fixture(scope="session")
def specs(pytestconfig: pytest.Config) -> ProjectModule:
    """Return the project's specs.py: datasheet numbers and design limits."""
    return state_of(pytestconfig).module("specs")


@pytest.fixture(scope="session")
def circuits(pytestconfig: pytest.Config) -> ProjectModule:
    """Return the project's circuits.py: operating scenarios and SPICE decks."""
    return state_of(pytestconfig).module("circuits")


@pytest.fixture(scope="session")
def layout(pytestconfig: pytest.Config) -> ProjectModule:
    """Return the project's layout.py: board size, outline and placement."""
    return state_of(pytestconfig).module("layout")


@pytest.fixture(scope="session")
def out_dir(project: Project) -> Path:
    """Return the folder for results and plots, made if it is not there."""
    folder = results_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.fixture(scope="session")
def nl(project: Project) -> Any:
    """Return the netlist of a fresh export of the project's schematic."""
    from pcbkit.check.netlist import fresh_netlist

    stem = project.config.board.stem
    schematic = project.kicad_dir / f"{stem}.kicad_sch"
    if not schematic.is_file():
        pytest.fail(f"{schematic} not found: run `pcbkit sch` first", pytrace=False)
    return fresh_netlist(schematic, Path(tempfile.mkdtemp(prefix="pcbkit_nl_")))


@pytest.fixture(scope="session")
def board(project: Project) -> Any:
    """Return the project's board, loaded with pcbnew."""
    try:
        import pcbnew
    except ImportError:
        pytest.fail(
            "pcbnew isn't importable here: run the checks in the project's .venv "
            "(made by `pcbkit setup`)",
            pytrace=False,
        )
    pcb = project.kicad_dir / f"{project.config.board.stem}.kicad_pcb"
    if not pcb.is_file():
        pytest.fail(f"{pcb} not found: build and route the board first", pytrace=False)
    return pcbnew.LoadBoard(str(pcb))


@pytest.fixture
def record(request: pytest.FixtureRequest) -> Callable[[str, Any], None]:
    """Return ``record(key, value)``: numbers for the results file and the report."""
    state = state_of(request.config)
    key = check_id(request.node, state.project.root)

    def _record(name: str, value: Any) -> None:
        entry = state.results.setdefault(key, _entry(request.node, state.project.root))
        entry["numbers"][name] = value

    return _record


@pytest.fixture(autouse=True)
def _missing_spec_guard(request: pytest.FixtureRequest) -> None:
    """Fail a check that was parametrised from data the project does not have."""
    callspec = getattr(request.node, "callspec", None)
    if callspec is None:
        return
    for value in callspec.params.values():
        if isinstance(value, MissingParam):
            pytest.fail(value.message, pytrace=False)


# --- parametrising from the project's data ----------------------------------------


def spec_params(
    metafunc: pytest.Metafunc,
    argnames: str,
    values: Callable[[ProjectModule], Iterable[Any]],
    ids: Callable[[Any], str] | None = None,
) -> None:
    """Parametrise ``argnames`` of a check from the project's data.

    ``values(specs)`` returns the parameter values (tuples when ``argnames`` has
    several names). ``ids(value)`` names each parameter set; without it pytest's own ids
    are used. When the project lacks the data, the check gets one parameter set whose
    run fails naming what is missing, so it cannot vanish from the report. Call it from
    a module-level ``pytest_generate_tests``; it does nothing for a test that does not
    take ``argnames``.
    """
    names = [n.strip() for n in argnames.split(",")]
    if not all(n in metafunc.fixturenames for n in names):
        return
    specs = state_of(metafunc.config).module("specs")
    try:
        found = list(values(specs))
    except MissingSpec as err:
        missing = MissingParam(str(err))
        metafunc.parametrize(
            argnames, [pytest.param(*([missing] * len(names)), id="missing-spec")]
        )
        return
    metafunc.parametrize(
        argnames, found, ids=[ids(v) for v in found] if ids is not None else None
    )


# --- results ----------------------------------------------------------------------


def check_id(item: pytest.Item, root: Path) -> str:
    """Return a check's stable id, the same wherever pytest was started.

    Built-in checks are ``pcbkit.check.builtin.<module>::<name>``; the project's own are
    ``<path relative to the project>::<name>``. Either way the part after ``::`` is
    pytest's own (function name and parameter ids).
    """
    path = Path(str(item.path)).resolve()
    _, _, name = item.nodeid.partition("::")
    if path.parent == builtin_dir():
        return f"{BUILTIN_PACKAGE}.{path.stem}::{name}"
    try:
        where = path.relative_to(root.resolve()).as_posix()
    except ValueError:
        where = path.name
    return f"{where}::{name}"


def _entry(item: pytest.Item, root: Path) -> dict[str, Any]:
    """Return a fresh results entry for a check."""
    path = Path(str(item.path)).resolve()
    group = "project"
    if path.parent == builtin_dir():
        group = BUILTIN_GROUPS.get(path.stem, "builtin")
    return {"group": group, "outcome": "", "numbers": {}}


def _skip_reason(report: pytest.TestReport) -> str:
    """Return the reason a skipped check gave, without pytest's "Skipped: " prefix."""
    longrepr = report.longrepr
    text = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
    return text.removeprefix("Skipped: ")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo
) -> Iterator[None]:
    """Note each check's outcome, skip reason or failure message for the results."""
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    state = item.config.stash.get(STATE, None)
    if state is None:
        return
    key = check_id(item, state.project.root)
    entry = state.results.setdefault(key, _entry(item, state.project.root))
    if report.when == "call":
        entry["outcome"] = report.outcome
    elif report.when == "setup" and report.outcome != "passed":
        entry["outcome"] = "skipped" if report.skipped else "error"
    elif report.when == "teardown" and report.failed:
        entry["outcome"] = "error"
    else:
        return
    if report.skipped:
        entry["reason"] = _skip_reason(report)
    elif report.failed:
        crash = getattr(report.longrepr, "reprcrash", None)
        entry["message"] = crash.message if crash is not None else str(report.longrepr)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write out/checks/results.json, unless nothing ran."""
    config = session.config
    state = config.stash.get(STATE, None)
    if state is None or config.option.collectonly:
        return
    checks = {k: v for k, v in state.results.items() if v["outcome"]}
    if not checks:
        return
    counts: dict[str, int] = {}
    for entry in checks.values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    board = state.project.config.board
    data = {
        "format": RESULTS_FORMAT,
        "board": {"stem": board.stem, "title": board.title, "rev": board.rev},
        "copper_mm": state.project.config.stackup.copper_mm,
        "groups": list(state.project.config.checks.groups),
        "exit_status": int(exitstatus),
        "counts": counts,
        "seconds": round(time.time() - state.started, 1),
        "checks": checks,
    }
    folder = results_dir(state.project)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / RESULTS_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, default=str)
            handle.write("\n")
    except OSError as err:
        sys.stderr.write(f"pcbkit: could not write {folder / RESULTS_FILE}: {err}\n")
