"""Unit tests for the ``check``, ``mutants`` and ``report`` commands of pcbkit.cli.

Each command runs through click's CliRunner inside a throwaway board project (the test
changes into it). What is faked is the outside: pcbnew (the ``fake_pcbnew`` fixture,
or ``None`` in ``sys.modules`` where it must be missing) and, for ``check`` and
``mutants``, the one function that would start pytest or plant mistakes, which has its
own unit tests (test_check_runner.py, test_check_mutants.py). ``report`` is the real
code from the results file to the report file.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from pcbkit import mutants
from pcbkit.check import runner
from pcbkit.cli import cli
from tests.board_files import write_project

RESULTS = Path("out") / "checks" / "results.json"
REPORT = Path("out") / "checks" / "VALIDATION.md"

# The commands that need pcbnew, and the function each one hands its work to. ``report``
# only reads the results file, so it runs anywhere.
COMMANDS = [
    ("check", "pcbkit.check.runner.run"),
    ("mutants", "pcbkit.mutants.run_all"),
]


@dataclass
class Recorder:
    """Stand in for a function a command calls: note the arguments, return a value."""

    result: Any = 0
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def __call__(self, *args: Any) -> Any:
        """Record the call and return the canned result."""
        self.calls.append(args)
        return self.result


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a board project in a temporary folder, change into it, return its root."""
    path = tmp_path / "board"
    write_project(path, "")
    monkeypatch.chdir(path)
    return path.resolve()


def invoke(*args: str) -> Result:
    """Run the pcbkit CLI in-process with ``args`` and return click's result."""
    return CliRunner().invoke(cli, list(args))


def assert_exit(result: Result, code: int) -> None:
    """Fail unless the command ended by exiting with ``code``, not by a traceback."""
    assert result.exit_code == code, result.output
    if code:
        assert isinstance(result.exception, SystemExit), repr(result.exception)
    else:
        assert result.exception is None, repr(result.exception)


def assert_error(result: Result, *fragments: str) -> None:
    """Fail unless the command exited 1 printing ``Error:`` and every fragment."""
    assert_exit(result, 1)
    assert result.output.startswith("Error: "), result.output
    for fragment in fragments:
        assert fragment in result.output, result.output


def write_results(root: Path, outcomes: dict[str, str], seconds: float = 3.5) -> Path:
    """Write the results file of a run whose checks ended as ``outcomes`` says."""
    checks: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for check_id, outcome in outcomes.items():
        checks[check_id] = {"group": "project", "outcome": outcome, "numbers": {}}
        counts[outcome] = counts.get(outcome, 0) + 1
    data = {
        "format": 1,
        "board": {"stem": "my_board", "title": "My Board", "rev": "A"},
        "counts": counts,
        "seconds": seconds,
        "checks": checks,
    }
    path = root / RESULTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def listed_commands(help_text: str) -> list[str]:
    """Return the command names under "Commands:" in a --help text, in order."""
    lines = help_text.split("Commands:\n", 1)[1].splitlines()
    return [line.split()[0] for line in lines if line.strip()]


# --- without pcbnew ---------------------------------------------------------------


@pytest.mark.parametrize(("command", "target"), COMMANDS)
@pytest.mark.parametrize("inside_a_project", [True, False], ids=["in-project", "bare"])
def test_a_command_without_pcbnew_says_so_before_anything_else(
    command: str,
    target: str,
    inside_a_project: bool,
    root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print the pcbnew message and exit 1, before looking for a project or working."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    if not inside_a_project:
        (tmp_path / "bare").mkdir()
        monkeypatch.chdir(tmp_path / "bare")
    spy = Recorder()
    monkeypatch.setattr(target, spy)
    result = invoke(command)
    assert_error(result, "pcbnew isn't importable here", "pcbkit setup")
    assert "pcbkit.toml" not in result.output
    assert spy.calls == []


# --- check ------------------------------------------------------------------------


def test_check_passes_the_k_expression_to_the_runner(
    root: Path, fake_pcbnew: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand the project and the ``-k`` text to ``runner.run``."""
    spy = Recorder(0)
    monkeypatch.setattr(runner, "run", spy)
    assert_exit(invoke("check", "-k", "test_a or test_b"), 0)
    ((project, expression),) = spy.calls
    assert project.root == root
    assert expression == "test_a or test_b"


def test_check_without_k_passes_no_expression(
    root: Path, fake_pcbnew: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand the runner ``None`` when no ``-k`` is given."""
    spy = Recorder(0)
    monkeypatch.setattr(runner, "run", spy)
    assert_exit(invoke("check"), 0)
    assert [expression for _, expression in spy.calls] == [None]


@pytest.mark.parametrize("code", [0, 1, 2, 5])
def test_check_exits_with_the_code_the_runner_returned(
    code: int,
    root: Path,
    fake_pcbnew: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit with pytest's own code, whatever it is."""
    monkeypatch.setattr(runner, "run", Recorder(code))
    assert_exit(invoke("check"), code)


@pytest.mark.parametrize("code", [0, 1])
def test_check_says_where_the_results_are_when_the_file_exists(
    code: int,
    root: Path,
    fake_pcbnew: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print ``Results: out/checks/results.json (pcbkit report)``, pass or fail."""
    write_results(root, {"checks/test_a.py::test_x": "passed"})
    monkeypatch.setattr(runner, "run", Recorder(code))
    result = invoke("check")
    assert_exit(result, code)
    assert result.output == f"Results: {RESULTS} (pcbkit report)\n"


def test_check_prints_no_results_line_when_no_file_was_written(
    root: Path, fake_pcbnew: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print nothing of its own when there is no results.json."""
    monkeypatch.setattr(runner, "run", Recorder(2))
    result = invoke("check")
    assert_exit(result, 2)
    assert result.output == ""


def test_check_finds_the_project_from_a_folder_inside_it(
    root: Path, fake_pcbnew: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Walk up to the pcbkit.toml, as every command does."""
    (root / "checks").mkdir()
    monkeypatch.chdir(root / "checks")
    spy = Recorder(0)
    monkeypatch.setattr(runner, "run", spy)
    assert_exit(invoke("check"), 0)
    assert [project.root for project, _ in spy.calls] == [root]


def test_check_outside_a_project_says_so_and_runs_nothing(
    tmp_path: Path, fake_pcbnew: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report the missing pcbkit.toml and exit 1 without calling the runner."""
    (tmp_path / "bare").mkdir()
    monkeypatch.chdir(tmp_path / "bare")
    spy = Recorder(0)
    monkeypatch.setattr(runner, "run", spy)
    assert_error(invoke("check"), "no pcbkit.toml")
    assert spy.calls == []


def test_check_without_a_schematic_says_to_run_pcbkit_sch(
    root: Path, fake_pcbnew: types.ModuleType
) -> None:
    """Show the runner's own refusal (nothing faked) and print no results line."""
    result = invoke("check")
    assert_error(result, "my_board.kicad_sch", "run `pcbkit sch`")
    assert "Results:" not in result.output


# --- mutants ----------------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 1, 2])
def test_mutants_exits_with_the_code_run_all_returned(
    code: int,
    root: Path,
    fake_pcbnew: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand the project to ``run_all`` and exit with what it returns."""
    spy = Recorder(code)
    monkeypatch.setattr(mutants, "run_all", spy)
    assert_exit(invoke("mutants"), code)
    ((project,),) = spy.calls
    assert project.root == root


def test_mutants_without_a_mutants_py_names_the_file(
    root: Path, fake_pcbnew: types.ModuleType
) -> None:
    """Show run_all's own refusal (nothing faked) for a project with no mutants.py."""
    assert_error(invoke("mutants"), str(root / "mutants.py"), "not found")


# --- report -----------------------------------------------------------------------


def test_report_runs_where_pcbnew_cannot_be_imported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the report without pcbnew: it only reads the results file."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    write_results(root, {"checks/test_a.py::test_x": "passed"})
    assert_exit(invoke("report"), 0)
    assert (root / REPORT).is_file()


def test_report_of_a_run_that_ran_no_checks_exits_1_and_says_so(root: Path) -> None:
    """Say nothing ran, rather than report an old run, and exit 1."""
    write_results(root, {})
    result = invoke("report")
    assert_exit(result, 1)
    assert "no checks" in result.output
    assert "ran no checks" in result.output


def test_report_writes_the_report_and_prints_the_summary_and_its_path(
    root: Path, fake_pcbnew: types.ModuleType
) -> None:
    """Print the counts, then ``Report: out/checks/VALIDATION.md``, and write it."""
    write_results(
        root,
        {"checks/test_a.py::test_x": "passed", "checks/test_a.py::test_y": "skipped"},
    )
    result = invoke("report")
    assert_exit(result, 0)
    assert result.output == f"1 passed, 1 skipped in 3.5s\nReport: {REPORT}\n"
    text = (root / REPORT).read_text(encoding="utf-8")
    assert text.startswith("# Design validation\n")
    assert "| test_x | pass |  |" in text.splitlines()


@pytest.mark.parametrize(
    ("outcomes", "code"),
    [
        (["passed", "passed", "skipped"], 0),
        (["passed", "failed"], 1),
        (["passed", "error"], 1),
        (["failed", "error", "passed"], 1),
        (["skipped"], 0),
    ],
    ids=["clean", "a-failure", "an-error", "both", "only-skipped"],
)
def test_report_exits_1_when_a_check_failed_or_errored(
    outcomes: list[str], code: int, root: Path, fake_pcbnew: types.ModuleType
) -> None:
    """Exit 1 for a failed or errored check and 0 otherwise; write the report anyway."""
    write_results(
        root, {f"checks/test_a.py::test_{n}": o for n, o in enumerate(outcomes)}
    )
    result = invoke("report")
    assert_exit(result, code)
    assert f"Report: {REPORT}" in result.output
    assert (root / REPORT).is_file()


def test_report_without_results_says_to_run_pcbkit_check_first(
    root: Path, fake_pcbnew: types.ModuleType
) -> None:
    """Exit 1 with the message, and write no report."""
    result = invoke("report")
    assert_error(result, "run `pcbkit check` first")
    assert not (root / REPORT).exists()


def test_report_with_a_results_file_without_checks_says_to_run_again(
    root: Path, fake_pcbnew: types.ModuleType
) -> None:
    """Exit 1 with the message when results.json holds no ``checks``."""
    (root / RESULTS).parent.mkdir(parents=True)
    (root / RESULTS).write_text('{"counts": {}}', encoding="utf-8")
    assert_error(invoke("report"), "has no checks", "run `pcbkit check` again")
    assert not (root / REPORT).exists()


def test_report_writes_into_the_project_when_run_from_a_folder_inside_it(
    root: Path, fake_pcbnew: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find the results and write the report at the project root, whatever the cwd."""
    write_results(root, {"checks/test_a.py::test_x": "passed"})
    (root / "kicad").mkdir()
    monkeypatch.chdir(root / "kicad")
    assert_exit(invoke("report"), 0)
    assert (root / REPORT).is_file()


# --- help -------------------------------------------------------------------------


def test_help_still_lists_check_mutants_and_report_in_workflow_order() -> None:
    """Keep the three commands in the list, one after the other."""
    result = invoke("--help")
    assert_exit(result, 0)
    commands = listed_commands(result.output)
    at = commands.index("check")
    assert commands[at : at + 3] == ["check", "mutants", "report"]


@pytest.mark.parametrize(
    ("command", "words"),
    [
        ("check", ["-k EXPR", "pytest", "results.json"]),
        ("mutants", ["MUTANTS", "mutants.py", "control"]),
        ("report", ["VALIDATION.md", "results.json"]),
    ],
)
def test_each_commands_help_works_without_pcbnew_or_a_project(
    command: str,
    words: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Show usage and what the command does, where neither pcbnew nor a project is."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    monkeypatch.chdir(tmp_path)
    result = invoke(command, "--help")
    assert_exit(result, 0)
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("Usage: ")
    assert f"{command} [OPTIONS]" in first_line
    for word in words:
        assert word in result.output, word
