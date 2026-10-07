"""Unit tests for pcbkit.check.runner: the one pytest command behind ``pcbkit check``.

``command`` only builds a list, so its tests compare whole argument lists. ``run``
starts a child pytest, so ``subprocess.run`` is the one thing faked: the fake records
the command line and the keywords it was given and answers with a canned exit code.
Everything else (the schematic check, the command, the exit code) is the real code.
"""

from __future__ import annotations

import configparser
import importlib.util
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import pytest

import pcbkit.check
from pcbkit.check import plugin, results, runner
from pcbkit.project import Project, load_project
from tests.board_files import write_file, write_project

# The ini file the command must name: the one that sits in the pcbkit.check package.
PACKAGE_INI = Path(pcbkit.check.__file__).resolve().with_name("pytest.ini")


@dataclass
class Call:
    """One command the fake subprocess was asked to run."""

    args: list[str]
    kwargs: dict[str, Any]


@dataclass
class FakeSubprocess:
    """Stand in for the ``subprocess`` module: record each run, answer with a code."""

    returncode: int = 0
    calls: list[Call] = field(default_factory=list)

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Record the command and return a finished process with ``returncode``."""
        self.calls.append(Call(list(args), dict(kwargs)))
        return subprocess.CompletedProcess(args, self.returncode)


@pytest.fixture
def fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> FakeSubprocess:
    """Make the runner's ``subprocess`` a recording fake for one test."""
    fake = FakeSubprocess()
    monkeypatch.setattr(runner, "subprocess", fake)
    return fake


@pytest.fixture
def board_project(tmp_path: Path) -> Project:
    """Return a board project in a temporary folder that has no schematic yet."""
    write_project(tmp_path / "board", "")
    return load_project(tmp_path / "board")


def make_schematic(project: Project) -> Path:
    """Write the (empty) schematic file the runner looks for, and return its path."""
    stem = project.config.board.stem
    return write_file(project.kicad_dir / f"{stem}.kicad_sch", "(kicad_sch)\n")


def head(root: Path) -> list[str]:
    """Return the start of every command: interpreter, ini, rootdir, plugin, project."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(PACKAGE_INI),
        "--rootdir",
        str(root),
        "-p",
        "pcbkit.check.plugin",
        "-p",
        "no:cacheprovider",
        "--pcbkit-project",
        str(root),
    ]


# --- command ----------------------------------------------------------------------


def test_command_for_a_project_without_checks_is_exactly_this(tmp_path: Path) -> None:
    """List every argument, in order, when the project has no checks folder."""
    assert runner.command(tmp_path) == [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(PACKAGE_INI),
        "--rootdir",
        str(tmp_path),
        "-p",
        "pcbkit.check.plugin",
        "-p",
        "no:cacheprovider",
        "--pcbkit-project",
        str(tmp_path),
        "--pyargs",
        "pcbkit.check.builtin",
    ]


def test_command_collects_the_projects_checks_folder_last_when_it_exists(
    tmp_path: Path,
) -> None:
    """End with the folder path, after the built-in package."""
    (tmp_path / "checks").mkdir()
    assert runner.command(tmp_path) == [
        *head(tmp_path),
        "--pyargs",
        "pcbkit.check.builtin",
        str(tmp_path / "checks"),
    ]


def test_command_leaves_the_checks_folder_out_when_it_is_only_a_file(
    tmp_path: Path,
) -> None:
    """Collect the folder only: a file called ``checks`` is not one."""
    write_file(tmp_path / "checks", "not a folder\n")
    assert str(tmp_path / "checks") not in runner.command(tmp_path)
    assert runner.command(tmp_path)[-2:] == ["--pyargs", "pcbkit.check.builtin"]


def test_command_adds_k_only_for_a_non_empty_expression(tmp_path: Path) -> None:
    """Pass the expression to ``-k``, right after the project, and nothing for none."""
    assert runner.command(tmp_path, "test_a or test_b") == [
        *head(tmp_path),
        "-k",
        "test_a or test_b",
        "--pyargs",
        "pcbkit.check.builtin",
    ]
    for none in (None, ""):
        assert "-k" not in runner.command(tmp_path, none)


def test_command_puts_extra_arguments_before_the_places_to_collect(
    tmp_path: Path,
) -> None:
    """Keep the order: project, ``-k``, the extras, ``--pyargs``, the folder."""
    (tmp_path / "checks").mkdir()
    expected = [
        *head(tmp_path),
        "-k",
        "test_a",
        "-q",
        "--no-header",
        "--pyargs",
        "pcbkit.check.builtin",
        str(tmp_path / "checks"),
    ]
    assert runner.command(tmp_path, "test_a", ["-q", "--no-header"]) == expected
    assert runner.command(tmp_path, "test_a", ("-q", "--no-header")) == expected


def test_command_names_modules_that_exist_and_the_option_the_plugin_adds(
    tmp_path: Path,
) -> None:
    """Pass only names the plugin and the built-in package can answer to."""
    args = runner.command(tmp_path)
    assert importlib.util.find_spec(args[args.index("-p") + 1]) is not None
    assert importlib.util.find_spec(args[args.index("--pyargs") + 1]) is not None
    assert plugin.OPTION in args


def test_the_package_the_command_collects_is_the_one_the_results_know() -> None:
    """Agree with ``results.BUILTIN_PACKAGE``, which the check ids start with."""
    assert runner.BUILTIN == results.BUILTIN_PACKAGE


# --- the ini file -----------------------------------------------------------------


def test_the_command_names_the_ini_file_that_ships_in_the_package(
    tmp_path: Path,
) -> None:
    """Pass ``-c`` the pytest.ini beside the runner, as an existing file."""
    assert runner.INI == PACKAGE_INI
    assert runner.INI.is_file()
    args = runner.command(tmp_path)
    assert args[args.index("-c") + 1] == str(runner.INI)


def test_the_ini_file_holds_only_a_pytest_section_with_python_files() -> None:
    """Keep every other pytest setting out of the run: only ``python_files``."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(runner.INI, encoding="utf-8")
    assert parser.sections() == ["pytest"]
    assert parser.options("pytest") == ["python_files"]
    assert parser.get("pytest", "python_files") == "test_*.py"


# --- require_pytest ---------------------------------------------------------------


def test_require_pytest_is_silent_where_pytest_imports() -> None:
    """Return nothing and raise nothing when pytest is there."""
    assert runner.require_pytest() is None


def test_require_pytest_names_pytest_when_it_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise a ClickException that names pytest and says how to add it."""
    monkeypatch.setitem(sys.modules, "pytest", None)
    with pytest.raises(click.ClickException) as raised:
        runner.require_pytest()
    message = raised.value.message
    assert "pytest isn't installed" in message
    assert "uv add pytest" in message


# --- run --------------------------------------------------------------------------


def test_run_says_to_make_the_schematic_first_and_starts_nothing(
    board_project: Project, fake_subprocess: FakeSubprocess
) -> None:
    """Name the missing schematic file and `pcbkit sch`, without running pytest."""
    with pytest.raises(click.ClickException) as raised:
        runner.run(board_project)
    message = raised.value.message
    assert str(board_project.kicad_dir / "my_board.kicad_sch") in message
    assert "run `pcbkit sch`" in message
    assert fake_subprocess.calls == []


def test_run_looks_for_the_schematic_of_this_boards_stem(
    board_project: Project, fake_subprocess: FakeSubprocess
) -> None:
    """Refuse when only a schematic of some other name is there."""
    write_file(board_project.kicad_dir / "other.kicad_sch", "(kicad_sch)\n")
    with pytest.raises(click.ClickException, match="my_board.kicad_sch"):
        runner.run(board_project)
    assert fake_subprocess.calls == []


@pytest.mark.parametrize("code", [0, 1, 2, 5])
def test_run_returns_the_exit_code_pytest_gave(
    code: int, board_project: Project, fake_subprocess: FakeSubprocess
) -> None:
    """Hand pytest's exit code back unchanged."""
    make_schematic(board_project)
    fake_subprocess.returncode = code
    assert runner.run(board_project) == code
    assert len(fake_subprocess.calls) == 1


def test_run_starts_pytest_in_the_project_folder_without_capturing_output(
    board_project: Project, fake_subprocess: FakeSubprocess
) -> None:
    """Run the command for this project, with ``-q -rfEs``, from the project root."""
    make_schematic(board_project)
    runner.run(board_project)
    (call,) = fake_subprocess.calls
    root = board_project.root
    assert call.args == [
        *head(root),
        "-q",
        "-rfEs",
        "--pyargs",
        "pcbkit.check.builtin",
    ]
    assert call.kwargs["cwd"] == root
    # Output must reach the terminal, so nothing may capture it.
    assert not {"capture_output", "stdout", "stderr"} & set(call.kwargs)


def test_run_passes_the_expression_and_the_callers_extras_on(
    board_project: Project, fake_subprocess: FakeSubprocess
) -> None:
    """Pass ``-k`` and replace the default extras with the ones given."""
    make_schematic(board_project)
    (board_project.root / "checks").mkdir()
    runner.run(board_project, "test_a or test_b", ["--tb=short"])
    (call,) = fake_subprocess.calls
    root = board_project.root
    assert call.args == [
        *head(root),
        "-k",
        "test_a or test_b",
        "--tb=short",
        "--pyargs",
        "pcbkit.check.builtin",
        str(root / "checks"),
    ]


def test_run_needs_pytest_before_it_looks_at_anything_else(
    board_project: Project,
    fake_subprocess: FakeSubprocess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Say pytest is missing even when the schematic is there, and start nothing."""
    make_schematic(board_project)
    monkeypatch.setitem(sys.modules, "pytest", None)
    with pytest.raises(click.ClickException, match="pytest isn't installed"):
        runner.run(board_project)
    assert fake_subprocess.calls == []
