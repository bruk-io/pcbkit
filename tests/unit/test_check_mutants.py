"""Unit tests for pcbkit.mutants: planting known mistakes in a scratch copy.

Only the outside world is faked: ``subprocess.run`` (the schematic regeneration and
the child pytest, which would need KiCad and a whole board), ``sch.build_schematic`` in
the one test that runs the regeneration snippet, and, for the tests of ``run_all``,
``run_mutant`` itself, so that the report can be checked against verdicts written in
the tests. The fake subprocess records each command line, the keywords it got and what
the scratch folder held at that moment, because the folder is deleted before
``run_mutant`` returns. Copying, editing, the command line, the verdicts and the
clean-up are the real code; ``tempfile`` is only pointed at an empty folder, so the
scratch copies can be seen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import pytest

from pcbkit import mutants, sch
from pcbkit.check import runner
from pcbkit.mutants import Verdict
from pcbkit.project import Project, load_project
from tests.board_files import restored_imports, write_file, write_project

DESIGN = """\
R("R1", "10k", "VIN", "OUT")
R("R2", "10k", "OUT", "GND")
R("R3", "4k7", "EN", "VIN")
"""

PULL_UP = ('R("R3", "4k7", "EN", "VIN")', 'R("R3", "4k7", "EN", "NC_X")')
DOUBLED = ('"10k"', '"20k"')
SPEC = [
    ("pull-up open", [PULL_UP], "test_boot"),
    ("divider doubled", [DOUBLED], "test_divider or test_other"),
]
EXPRESSION = "(test_boot) or (test_divider or test_other)"
CONTROL = "control (no edits)"

# What pytest prints last (-q) for a run where checks fail, and for one where none do.
FAILED_TAIL = "3 failed, 146 deselected in 0.8s"
PASSED_TAIL = "5 passed in 0.2s"


def write_mutants(root: Path, **lists: Any) -> Path:
    """Write a mutants.py with one assignment per keyword, such as ``MUTANTS=[...]``."""
    return write_file(
        root / "mutants.py", "".join(f"{k} = {v!r}\n" for k, v in lists.items())
    )


def files_under(root: Path) -> set[str]:
    """Return the posix path of every file under ``root``, relative to it."""
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    """Return a finished process, as ``subprocess.run`` returns it with text output."""
    return subprocess.CompletedProcess([], code, out, err)


@dataclass
class Call:
    """One command the fake subprocess ran, and what its working folder held."""

    args: list[str]
    kwargs: dict[str, Any]
    cwd: Path
    design: str
    listing: list[str]


class FakeSubprocess:
    """Stand in for the ``subprocess`` module: record each run, answer from a script.

    ``answers`` are used one per call: a finished process is returned, an exception is
    raised. A call past the end of the script fails the test.
    """

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(
        self, *answers: subprocess.CompletedProcess[str] | BaseException
    ) -> None:
        """Keep the script of answers."""
        self.answers = list(answers)
        self.calls: list[Call] = []

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Record the command and the scratch folder, then give the next answer."""
        cwd = Path(kwargs["cwd"])
        self.calls.append(
            Call(
                list(args),
                dict(kwargs),
                cwd,
                (cwd / "design.py").read_text(encoding="utf-8"),
                sorted(os.listdir(cwd)),
            )
        )
        if not self.answers:
            raise AssertionError(f"unexpected extra command: {args}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Undo the project modules (``mutants``) a test imported by path."""
    with restored_imports():
        yield


@pytest.fixture(autouse=True)
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make tempfile use an empty folder, so a test can see the scratch copies."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def board(tmp_path: Path) -> Project:
    """Return a board project: a design, a mutants.py, a check and a board file."""
    root = tmp_path / "board"
    write_project(root, DESIGN)
    write_mutants(root, MUTANTS=SPEC)
    write_file(root / "checks" / "test_boot.py", "def test_boot():\n    pass\n")
    write_file(root / "kicad" / "my_board.kicad_pcb", "(kicad_pcb)\n")
    return load_project(root)


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeSubprocess]:
    """Return a function that makes ``mutants.subprocess`` a fake with those answers."""

    def install(*answers: subprocess.CompletedProcess[str] | BaseException) -> Any:
        """Install a fake that gives ``answers`` in turn, and return it."""
        fake = FakeSubprocess(*answers)
        monkeypatch.setattr(mutants, "subprocess", fake)
        return fake

    return install


# --- load -------------------------------------------------------------------------


def test_load_returns_the_mutants_and_counts_each_parked_list(board: Project) -> None:
    """Read MUTANTS, and count only the lists named ``*_MUTANTS`` beside it."""
    write_mutants(
        board.root,
        MUTANTS=SPEC,
        MODULE_MUTANTS=[("m1", [], "k1"), ("m2", [], "k2"), ("m3", [], "k3")],
        ZEBRA_MUTANTS=[("z", [], "kz")],
        TUPLE_MUTANTS=(("not a list", [], "k"),),
        OTHER=[1, 2],
        LATER_MUTANT=[("one short of the suffix", [], "k")],
    )
    found, parked = mutants.load(board)
    assert found == [
        ("pull-up open", [PULL_UP], "test_boot"),
        ("divider doubled", [DOUBLED], "test_divider or test_other"),
    ]
    assert parked == {"MODULE_MUTANTS": 3, "ZEBRA_MUTANTS": 1}


def test_load_with_nothing_parked_returns_an_empty_count(board: Project) -> None:
    """Return an empty dict when there is no ``*_MUTANTS`` list but MUTANTS."""
    assert mutants.load(board)[1] == {}


@pytest.mark.parametrize(
    "lists",
    [
        {},
        {"OTHER": 1},
        {"MUTANTS": []},
        {"MODULE_MUTANTS": [("parked only", [], "k")]},
    ],
    ids=["empty-file", "other-names-only", "empty-list", "only-a-parked-list"],
)
def test_load_names_the_file_when_there_are_no_mutants(
    lists: dict[str, Any], board: Project
) -> None:
    """Raise a ClickException that names mutants.py and says MUTANTS is missing."""
    write_mutants(board.root, **lists)
    with pytest.raises(click.ClickException) as raised:
        mutants.load(board)
    assert str(board.root / "mutants.py") in raised.value.message
    assert "defines no MUTANTS" in raised.value.message


def test_load_names_the_file_when_the_project_has_no_mutants_py(
    board: Project,
) -> None:
    """Raise a ClickException that names the missing mutants.py."""
    (board.root / "mutants.py").unlink()
    with pytest.raises(click.ClickException) as raised:
        mutants.load(board)
    assert str(board.root / "mutants.py") in raised.value.message
    assert "not found" in raised.value.message


@pytest.mark.parametrize(
    "entry",
    [
        ("bare pair", ("old", "new"), "test_x"),
        ("two characters", ("ab", "cd"), "test_x"),
        ("edits not a list", "R1", "test_x"),
        ("missing the expression", [("old", "new")]),
        ("a number among the texts", [("old", 3)], "test_x"),
        ("an expression that is not text", [("old", "new")], None),
        "just a name",
        42,
    ],
    ids=[
        "bare-pair",
        "two-characters",
        "edits-not-a-list",
        "no-expression",
        "number",
        "no-text-expression",
        "string",
        "number-entry",
    ],
)
def test_load_refuses_a_mutant_that_is_not_name_edits_expression(
    entry: Any, board: Project
) -> None:
    """Say which entry is malformed and what the shape is, before anything runs."""
    write_mutants(board.root, MUTANTS=[entry])
    with pytest.raises(click.ClickException) as raised:
        mutants.load(board)
    assert "MUTANTS entry 0" in raised.value.message
    assert "(name, [(text to find, text to put there), ...]" in raised.value.message


# --- apply_edits ------------------------------------------------------------------


def test_apply_edits_replaces_every_occurrence_and_returns_nothing(
    tmp_path: Path,
) -> None:
    """Change each text wherever it occurs, in every edit given."""
    path = write_file(tmp_path / "design.py", DESIGN)
    assert mutants.apply_edits(path, [DOUBLED, ('"4k7"', '"1k"')]) is None
    assert path.read_text(encoding="utf-8") == (
        'R("R1", "20k", "VIN", "OUT")\nR("R2", "20k", "OUT", "GND")\n'
        'R("R3", "1k", "EN", "VIN")\n'
    )


def test_apply_edits_with_no_edits_leaves_the_text_as_it_was(tmp_path: Path) -> None:
    """Return nothing and keep the file's text, for the control run."""
    path = write_file(tmp_path / "design.py", DESIGN)
    assert mutants.apply_edits(path, []) is None
    assert path.read_text(encoding="utf-8") == DESIGN


def test_apply_edits_says_what_is_missing_and_leaves_the_file_untouched(
    tmp_path: Path,
) -> None:
    """Return a message naming the absent text, and write nothing."""
    path = write_file(tmp_path / "design.py", DESIGN)
    before = path.read_bytes()
    message = mutants.apply_edits(path, [("no such text", "x")])
    assert message == "pattern not found in design.py: no such text"
    assert path.read_bytes() == before


def test_apply_edits_writes_none_of_the_edits_when_a_later_one_is_absent(
    tmp_path: Path,
) -> None:
    """Keep the file as it was, even though the first edit would have matched."""
    path = write_file(tmp_path / "design.py", DESIGN)
    before = path.read_bytes()
    message = mutants.apply_edits(path, [DOUBLED, ("no such text", "x")])
    assert message is not None
    assert path.read_bytes() == before


def test_apply_edits_shortens_a_long_missing_text_in_its_message(
    tmp_path: Path,
) -> None:
    """Show the first 60 characters of the text it could not find."""
    path = write_file(tmp_path / "design.py", DESIGN)
    long_text = "".join(str(n % 10) for n in range(100))
    message = mutants.apply_edits(path, [(long_text, "x")])
    assert message == f"pattern not found in design.py: {long_text[:60]}"


# --- copy_project -----------------------------------------------------------------


def test_copy_project_leaves_out_the_bulky_folders_and_nothing_else(
    board: Project, tmp_path: Path
) -> None:
    """Copy design, checks, board and outputs; skip venvs, git, archive and caches."""
    kept = [
        "docs/archive.txt",
        "fab/bom.csv",
        "golden/route.ses",
        "out/checks/results.json",
        "venv_notes/readme.txt",
    ]
    skipped = [
        ".venv/lib/site.py",
        ".venv-kicad/lib/site.py",
        ".venv3/lib/site.py",
        ".git/config",
        "archive/old/design.py",
        "checks/archive/old.py",
        "__pycache__/design.cpython-39.pyc",
        "checks/__pycache__/test_boot.cpython-39.pyc",
        ".pytest_cache/v/cache/lastfailed",
        ".ruff_cache/0.9/cache",
    ]
    for name in kept + skipped:
        write_file(board.root / name, "x\n")
    before = files_under(board.root)
    dest = tmp_path / "copy"
    dest.mkdir()  # tempfile.mkdtemp makes it first, so it exists
    assert mutants.copy_project(board, dest) == dest
    assert files_under(dest) == before - set(skipped)
    assert set(kept) <= files_under(dest)
    assert {
        "pcbkit.toml",
        "design.py",
        "mutants.py",
        "checks/test_boot.py",
        "kicad/my_board.kicad_pcb",
    } <= files_under(dest)
    assert files_under(board.root) == before  # the project itself is not touched


def test_copy_project_makes_the_destination_when_it_is_not_there(
    board: Project, tmp_path: Path
) -> None:
    """Create the folder to copy into, as well as fill an existing one."""
    dest = tmp_path / "new" / "copy"
    mutants.copy_project(board, dest)
    assert (dest / "design.py").read_text(encoding="utf-8") == DESIGN


# --- control_expression -----------------------------------------------------------


def test_control_expression_ors_each_expression_in_brackets() -> None:
    """Select every check a mutant relies on, keeping each expression's own logic."""
    spec = [
        ("a", [], "test_x"),
        ("b", [], "test_y and not test_z"),
        ("c", [PULL_UP], "test_w"),
    ]
    assert (
        mutants.control_expression(spec)
        == "(test_x) or (test_y and not test_z) or (test_w)"
    )
    assert mutants.control_expression(spec[:1]) == "(test_x)"
    assert mutants.control_expression([]) == ""


# --- Verdict ----------------------------------------------------------------------


def test_verdict_line_is_caught_or_missed_with_the_summary_in_brackets() -> None:
    """Start with CAUGHT or MISSED, two spaces, then the summary in brackets."""
    assert Verdict("m", True, FAILED_TAIL).line() == f"CAUGHT  ({FAILED_TAIL})"
    assert Verdict("m", False, PASSED_TAIL).line() == f"MISSED  ({PASSED_TAIL})"
    error = Verdict("m", False, "SETUP ERROR: x", error=True)
    assert error.line() == "MISSED  (SETUP ERROR: x)"


def test_verdict_is_not_a_setup_error_unless_told_so() -> None:
    """Default ``error`` to False."""
    assert Verdict("m", False, "x").error is False


# --- run_mutant: what runs where --------------------------------------------------


def test_run_mutant_edits_the_scratch_copy_and_never_the_project(
    board: Project, script: Callable[..., Any], scratch_root: Path
) -> None:
    """Show both commands the edited design, while the real design.py stays as it is."""
    fake = script(done(0), done(1, f"F\n{FAILED_TAIL}\n"))
    mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot")
    assert len(fake.calls) == 2
    edited = DESIGN.replace(*PULL_UP)
    assert [c.design for c in fake.calls] == [edited, edited]
    assert (board.root / "design.py").read_text(encoding="utf-8") == DESIGN
    scratch = fake.calls[0].cwd
    assert scratch.parent == scratch_root
    assert scratch != board.root


def test_run_mutant_copies_the_board_but_not_the_venv(
    board: Project, script: Callable[..., Any]
) -> None:
    """Give the checks the project's files, without its virtual environment."""
    write_file(board.root / ".venv" / "lib" / "site.py", "x\n")
    fake = script(done(0), done(0, PASSED_TAIL))
    mutants.run_mutant(board, CONTROL, [], "test_boot")
    listing = fake.calls[0].listing
    assert {"design.py", "kicad", "checks", "pcbkit.toml", "mutants.py"} <= set(listing)
    assert ".venv" not in listing


def test_run_mutant_regenerates_the_schematic_in_the_scratch_copy_first(
    board: Project, script: Callable[..., Any]
) -> None:
    """Run the schematic step, then pytest, both with the scratch copy as the cwd."""
    fake = script(done(0), done(1, FAILED_TAIL))
    mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot")
    regenerate, pytest_run = fake.calls
    assert regenerate.args == [sys.executable, "-c", mutants.REGENERATE]
    assert pytest_run.args[:3] == [sys.executable, "-m", "pytest"]
    assert regenerate.cwd == pytest_run.cwd
    assert regenerate.cwd != board.root


def test_run_mutant_runs_the_checks_command_on_the_scratch_copy(
    board: Project, script: Callable[..., Any]
) -> None:
    """Build the command of `pcbkit check` for the scratch path, with ``-k``."""
    fake = script(done(0), done(1, FAILED_TAIL))
    mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot or test_x")
    scratch = fake.calls[1].cwd
    # The scratch folder is gone by now; put back the checks folder it had while the
    # command was built, so that runner.command sees what the code under test saw.
    assert "checks" in fake.calls[1].listing
    (scratch / "checks").mkdir(parents=True)
    assert fake.calls[1].args == runner.command(
        scratch, "test_boot or test_x", ["-q", "--no-header"]
    )
    args = fake.calls[1].args
    assert args[args.index("--pcbkit-project") + 1] == str(scratch)
    assert args[args.index("--rootdir") + 1] == str(scratch)
    assert args[args.index("-k") + 1] == "test_boot or test_x"
    assert args[-1] == str(scratch / "checks")
    # Nothing may point back at the real project, or every mutant would pass.
    assert not [a for a in args if str(board.root) in a]


def test_run_mutant_captures_text_output_within_the_time_limit(
    board: Project, script: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give both commands the scratch cwd, captured text output and the time limit."""
    monkeypatch.setattr(mutants, "TIMEOUT", 12.5)
    fake = script(done(0), done(1, FAILED_TAIL))
    mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot")
    for call in fake.calls:
        assert set(call.kwargs) == {"cwd", "capture_output", "text", "timeout"}
        assert Path(call.kwargs["cwd"]) == call.cwd
        assert call.kwargs["capture_output"] is True
        assert call.kwargs["text"] is True
        assert call.kwargs["timeout"] == 12.5


def test_the_regenerate_step_builds_the_schematic_of_the_project_it_runs_in(
    board: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the snippet where the project is; ERC errors in its result do not fail it."""
    seen: list[Project] = []

    def build(project: Project) -> Any:
        """Record the project and return a result with ERC errors, as a build may."""
        seen.append(project)
        return types.SimpleNamespace(erc=types.SimpleNamespace(errors=3))

    monkeypatch.setattr(sch, "build_schematic", build)
    monkeypatch.chdir(board.root)
    exec(compile(mutants.REGENERATE, "<regenerate>", "exec"), {})
    assert [p.root for p in seen] == [board.root]


# --- run_mutant: the verdicts -----------------------------------------------------


@pytest.mark.parametrize(
    ("code", "out", "err", "caught", "summary"),
    [
        (1, f"F.F\n= FAILURES =\n\n{FAILED_TAIL}\n", "", True, FAILED_TAIL),
        (0, f"....\n{PASSED_TAIL}\n", "", False, PASSED_TAIL),
        (1, "\n\n3 failed in 0.8s  \n\n", "", True, "3 failed in 0.8s"),
        (5, "no tests ran in 0.01s\n", "", False, "no tests ran in 0.01s"),
        (0, "5 passed, 1 xfailed in 0.2s\n", "", False, "5 passed, 1 xfailed in 0.2s"),
        (4, "", "usage: pytest\nERROR: not found: x\n", False, "ERROR: not found: x"),
        (2, "", "", False, ""),
    ],
    ids=[
        "failed-is-caught",
        "passed-is-missed",
        "blank-lines-around-the-summary",
        "nothing-selected-is-not-a-catch",
        "xfailed-with-exit-0-is-not-a-catch",
        "no-stdout-uses-the-last-stderr-line",
        "no-output-at-all",
    ],
)
def test_run_mutant_judges_the_exit_code_and_the_last_line(
    code: int,
    out: str,
    err: str,
    caught: bool,
    summary: str,
    board: Project,
    script: Callable[..., Any],
) -> None:
    """Call a mutant caught only when pytest exited non-zero with failures."""
    script(done(0), done(code, out, err))
    verdict = mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot")
    assert verdict == Verdict("pull-up open", caught, summary, code=code)
    assert verdict.error is False


def test_run_mutant_reports_a_text_that_is_not_in_the_design_without_running_anything(
    board: Project, script: Callable[..., Any], scratch_root: Path
) -> None:
    """Return a SETUP ERROR that counts as missed, and start no process."""
    fake = script()
    verdict = mutants.run_mutant(board, "stale", [("not in the design", "x")], "k")
    assert verdict == Verdict(
        "stale",
        False,
        "SETUP ERROR: pattern not found in design.py: not in the design",
        error=True,
    )
    assert verdict.line().startswith("MISSED  (SETUP ERROR")
    assert fake.calls == []
    assert list(scratch_root.iterdir()) == []


def test_run_mutant_reports_a_failed_regeneration_and_does_not_run_pytest(
    board: Project, script: Callable[..., Any]
) -> None:
    """Show the last six lines of the error output, and skip the checks."""
    err = "\n".join(f"line {n}\n" for n in range(1, 9))
    fake = script(done(1, "", err))
    verdict = mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot")
    assert verdict == Verdict(
        "pull-up open",
        False,
        "SETUP ERROR: line 3 | line 4 | line 5 | line 6 | line 7 | line 8",
        error=True,
    )
    assert len(fake.calls) == 1


@pytest.mark.parametrize("step", [0, 1], ids=["regenerating", "running-pytest"])
def test_run_mutant_reports_a_timeout_as_a_setup_error(
    step: int,
    board: Project,
    script: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Say the run timed out, with the limit, whichever step took too long."""
    monkeypatch.setattr(mutants, "TIMEOUT", 2.5)
    answers: list[Any] = [done(0)] * step + [subprocess.TimeoutExpired("cmd", 2.5)]
    script(*answers)
    verdict = mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot")
    assert verdict == Verdict(
        "pull-up open", False, "SETUP ERROR: timed out after 2.5 s", error=True
    )


@pytest.mark.parametrize(
    "answers",
    [
        [done(0), done(1, FAILED_TAIL)],
        [done(0), done(0, PASSED_TAIL)],
        [done(1, "", "boom\n")],
        [subprocess.TimeoutExpired("cmd", 1)],
        [done(0), subprocess.TimeoutExpired("cmd", 1)],
    ],
    ids=["caught", "missed", "regenerate-failed", "timeout-1", "timeout-2"],
)
def test_run_mutant_deletes_the_scratch_folder_whatever_happened(
    answers: list[Any],
    board: Project,
    script: Callable[..., Any],
    scratch_root: Path,
) -> None:
    """Leave no scratch copy behind, after a verdict or a setup error."""
    fake = script(*answers)
    mutants.run_mutant(board, "pull-up open", [PULL_UP], "test_boot")
    assert fake.calls  # a scratch copy really was made and used
    assert fake.calls[0].cwd.parent == scratch_root
    assert list(scratch_root.iterdir()) == []


def test_run_mutant_with_no_edits_runs_the_control_on_an_unchanged_design(
    board: Project, script: Callable[..., Any]
) -> None:
    """Run both steps on a copy whose design is the project's own."""
    fake = script(done(0), done(0, PASSED_TAIL))
    verdict = mutants.run_mutant(board, CONTROL, [], EXPRESSION)
    assert verdict == Verdict(CONTROL, False, PASSED_TAIL)
    assert [c.design for c in fake.calls] == [DESIGN, DESIGN]
    assert fake.calls[1].args[fake.calls[1].args.index("-k") + 1] == EXPRESSION


# --- run_all ----------------------------------------------------------------------


@dataclass
class FakeMutantRunner:
    """Stand in for ``run_mutant``: record each call, answer with a canned verdict."""

    verdicts: dict[str, Verdict]
    calls: list[tuple[str, list[Any], str]] = field(default_factory=list)

    def __call__(
        self, project: Project, name: str, edits: Any, expression: str
    ) -> Verdict:
        """Record the call and return the verdict written for ``name``."""
        self.calls.append((name, list(edits), expression))
        return self.verdicts[name]


@pytest.fixture
def mutant_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeMutantRunner]:
    """Return a function that replaces ``run_mutant`` with canned verdicts."""

    def install(*verdicts: Verdict) -> FakeMutantRunner:
        """Install a runner that answers with ``verdicts`` by name, and return it."""
        fake = FakeMutantRunner({v.name: v for v in verdicts})
        monkeypatch.setattr(mutants, "run_mutant", fake)
        return fake

    return install


CLEAN_CONTROL = Verdict(CONTROL, False, PASSED_TAIL)


def test_run_all_with_every_mutant_caught_returns_0_and_says_so(
    board: Project, mutant_runner: Callable[..., FakeMutantRunner]
) -> None:
    """Print the control, a line per mutant, then ``N/N planted mistakes caught``."""
    fake = mutant_runner(
        CLEAN_CONTROL,
        Verdict("pull-up open", True, "1 failed, 4 passed in 0.5s"),
        Verdict("divider doubled", True, "2 failed in 0.4s"),
    )
    lines: list[str] = []
    assert mutants.run_all(board, lines.append) == 0
    assert lines == [
        f"{CONTROL.ljust(62)} PASSES  {PASSED_TAIL}",
        f"{'pull-up open'.ljust(62)} CAUGHT  (1 failed, 4 passed in 0.5s)",
        f"{'divider doubled'.ljust(62)} CAUGHT  (2 failed in 0.4s)",
        "\n2/2 planted mistakes caught",
    ]
    assert fake.calls == [
        (CONTROL, [], EXPRESSION),
        ("pull-up open", [PULL_UP], "test_boot"),
        ("divider doubled", [DOUBLED], "test_divider or test_other"),
    ]


def test_run_all_with_a_missed_mutant_returns_1_and_still_runs_the_rest(
    board: Project, mutant_runner: Callable[..., FakeMutantRunner]
) -> None:
    """Count the miss, show it as MISSED, and go on to the next mutant."""
    fake = mutant_runner(
        CLEAN_CONTROL,
        Verdict("pull-up open", False, PASSED_TAIL),
        Verdict("divider doubled", True, "2 failed in 0.4s"),
    )
    lines: list[str] = []
    assert mutants.run_all(board, lines.append) == 1
    assert lines[1] == f"{'pull-up open'.ljust(62)} MISSED  ({PASSED_TAIL})"
    assert lines[2].startswith("divider doubled")
    assert lines[-1] == "\n1/2 planted mistakes caught"
    assert [c[0] for c in fake.calls] == [CONTROL, "pull-up open", "divider doubled"]


def test_run_all_counts_a_setup_error_as_a_missed_mutant(
    board: Project, mutant_runner: Callable[..., FakeMutantRunner]
) -> None:
    """Return 1 when a mutant could not be planted, and show why."""
    mutant_runner(
        CLEAN_CONTROL,
        Verdict("pull-up open", False, "SETUP ERROR: pattern not found", error=True),
        Verdict("divider doubled", True, "2 failed in 0.4s"),
    )
    lines: list[str] = []
    assert mutants.run_all(board, lines.append) == 1
    assert lines[1] == (
        f"{'pull-up open'.ljust(62)} MISSED  (SETUP ERROR: pattern not found)"
    )
    assert lines[-1] == "\n1/2 planted mistakes caught"


@pytest.mark.parametrize(
    "control",
    [
        Verdict(CONTROL, True, "2 failed, 5 passed in 0.3s", code=1),
        Verdict(CONTROL, False, "SETUP ERROR: boom", error=True),
        Verdict(CONTROL, False, "no tests ran in 0.01s", code=5),
        Verdict(CONTROL, False, "5 passed, 1 error in 0.5s", code=1),
    ],
    ids=["a-check-fails", "setup-error", "nothing-ran", "a-check-errors"],
)
def test_run_all_stops_with_2_when_the_control_does_not_pass(
    control: Verdict,
    board: Project,
    mutant_runner: Callable[..., FakeMutantRunner],
) -> None:
    """Print the control as FAILS, say why, and run no mutant."""
    fake = mutant_runner(control)
    lines: list[str] = []
    assert mutants.run_all(board, lines.append) == 2
    assert lines == [
        f"{CONTROL.ljust(62)} FAILS   {control.summary}",
        "\ncontrol run did not pass cleanly: fix that first",
    ]
    assert fake.calls == [(CONTROL, [], EXPRESSION)]


def test_run_all_reports_parked_lists_in_name_order_and_does_not_run_them(
    board: Project, mutant_runner: Callable[..., FakeMutantRunner]
) -> None:
    """Add a ``N parked in NAME (not run)`` line per list, after the total."""
    write_mutants(
        board.root,
        MUTANTS=SPEC,
        ZEBRA_MUTANTS=[("z", [], "kz")],
        MODULE_MUTANTS=[("m1", [], "k1"), ("m2", [], "k2")],
    )
    fake = mutant_runner(
        CLEAN_CONTROL,
        Verdict("pull-up open", True, "1 failed in 0.1s"),
        Verdict("divider doubled", True, "1 failed in 0.1s"),
    )
    lines: list[str] = []
    assert mutants.run_all(board, lines.append) == 0
    assert lines[-3:] == [
        "\n2/2 planted mistakes caught",
        "2 parked in MODULE_MUTANTS (not run)",
        "1 parked in ZEBRA_MUTANTS (not run)",
    ]
    assert [c[0] for c in fake.calls] == [CONTROL, "pull-up open", "divider doubled"]


def test_run_all_pads_each_name_to_62_columns_and_never_cuts_a_longer_one(
    board: Project, mutant_runner: Callable[..., FakeMutantRunner]
) -> None:
    """Line the verdicts up in column 63, and keep a long name whole."""
    long_name = "a mistake with a name that is much longer than sixty-two columns wide"
    write_mutants(board.root, MUTANTS=[("short", [], "k1"), (long_name, [], "k2")])
    mutant_runner(
        CLEAN_CONTROL,
        Verdict("short", True, "1 failed in 0.1s"),
        Verdict(long_name, True, "1 failed in 0.1s"),
    )
    lines: list[str] = []
    mutants.run_all(board, lines.append)
    assert len(long_name) > 62
    assert lines[0].index("PASSES") == 63
    assert lines[1].index("CAUGHT") == 63
    assert lines[1][:62] == "short".ljust(62)
    assert lines[2] == f"{long_name} CAUGHT  (1 failed in 0.1s)"


def test_run_all_prints_through_click_echo_when_given_no_echo(
    board: Project,
    mutant_runner: Callable[..., FakeMutantRunner],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Write the lines to standard output by default."""
    mutant_runner(
        CLEAN_CONTROL,
        Verdict("pull-up open", True, "1 failed in 0.1s"),
        Verdict("divider doubled", True, "1 failed in 0.1s"),
    )
    assert mutants.run_all(board) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"{CONTROL.ljust(62)} PASSES  {PASSED_TAIL}"
    assert out.endswith("2/2 planted mistakes caught\n")


def test_run_all_needs_pytest_and_runs_nothing_without_it(
    board: Project,
    mutant_runner: Callable[..., FakeMutantRunner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise the missing-pytest message before any run."""
    fake = mutant_runner(CLEAN_CONTROL)
    monkeypatch.setitem(sys.modules, "pytest", None)
    lines: list[str] = []
    with pytest.raises(click.ClickException, match="pytest isn't installed"):
        mutants.run_all(board, lines.append)
    assert fake.calls == []
    assert lines == []


def test_run_all_names_the_design_when_it_is_missing(
    board: Project, mutant_runner: Callable[..., FakeMutantRunner]
) -> None:
    """Raise a ClickException that names design.py, before any run."""
    fake = mutant_runner(CLEAN_CONTROL)
    (board.root / "design.py").unlink()
    with pytest.raises(click.ClickException) as raised:
        mutants.run_all(board, lambda line: None)
    assert raised.value.message == f"{board.root / 'design.py'} not found"
    assert fake.calls == []


def test_run_all_names_mutants_py_when_it_defines_no_mutants(
    board: Project, mutant_runner: Callable[..., FakeMutantRunner]
) -> None:
    """Pass on the ClickException from ``load``, before any run."""
    fake = mutant_runner(CLEAN_CONTROL)
    write_mutants(board.root, OTHER=1)
    with pytest.raises(click.ClickException, match="defines no MUTANTS"):
        mutants.run_all(board, lambda line: None)
    assert fake.calls == []


def test_run_all_end_to_end_over_a_fake_subprocess(
    board: Project, script: Callable[..., Any], scratch_root: Path
) -> None:
    """Run the real run_mutant for each mutant, and report what the processes said."""
    fake = script(
        done(0),  # control: regenerate
        done(0, f"....\n{PASSED_TAIL}\n"),  # control: pytest
        done(0),  # pull-up open: regenerate
        done(1, f"F\n{FAILED_TAIL}\n"),  # pull-up open: pytest
        # stale edit: its text is not in the design, so no process runs for it
    )
    write_mutants(
        board.root,
        MUTANTS=[
            ("pull-up open", [PULL_UP], "test_boot"),
            ("stale edit", [("not in the design", "x")], "test_divider"),
        ],
    )
    lines: list[str] = []
    assert mutants.run_all(board, lines.append) == 1
    assert lines == [
        f"{CONTROL.ljust(62)} PASSES  {PASSED_TAIL}",
        f"{'pull-up open'.ljust(62)} CAUGHT  ({FAILED_TAIL})",
        f"{'stale edit'.ljust(62)} MISSED  "
        "(SETUP ERROR: pattern not found in design.py: not in the design)",
        "\n1/2 planted mistakes caught",
    ]
    pytest_runs = [c for c in fake.calls if c.args[1:3] == ["-m", "pytest"]]
    assert [c.args[c.args.index("-k") + 1] for c in pytest_runs] == [
        "(test_boot) or (test_divider)",
        "test_boot",
    ]
    assert [c.design for c in pytest_runs] == [DESIGN, DESIGN.replace(*PULL_UP)]
    assert list(scratch_root.iterdir()) == []
