"""Unit tests for skills/order-pcbway/scripts/preflight.py.

The script answers "are the files I am about to pay for current?" from file times and
the checks' results alone, so each test builds a throwaway board project with chosen
times and runs the script as the order skill does: a child process in the project
folder.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "order-pcbway"
    / "scripts"
    / "preflight.py"
)

# Seconds after the epoch: every source is made at SOURCES, the fab files after them and
# the check results after those, which is the order a normal session leaves.
SOURCES, FABRICATED, CHECKED = 1_000_000, 1_000_100, 1_000_200

# A project's own check module: a plain check, a parametrised one and a class's. A
# results file for it holds one entry per run check, and pytest adds ``[param]`` to the
# id of a parametrised one (and ``Class::`` to a method's), as the plugin keys them.
CHECK_MODULE = """\
import pytest


def test_led() -> None:
    pass


@pytest.mark.parametrize("ref", ["R1", "R2"])
def test_resistor(ref: str) -> None:
    pass


class TestRails:
    def test_input(self) -> None:
        pass
"""
PROJECT_KEYS = [
    "checks/test_led.py::test_led",
    "checks/test_led.py::test_resistor[R1]",
    "checks/test_led.py::test_resistor[R2]",
    "checks/test_led.py::TestRails::test_input",
]
GROUPS = ["circuit", "copper"]
PARTIAL = "the last run was partial (pcbkit check -k?): run pcbkit check with no -k"


def results_file(
    groups: Sequence[str] = GROUPS,
    ran: Sequence[str] | None = None,
    keys: Sequence[str] = PROJECT_KEYS,
    group_outcome: str = "passed",
    **fields: object,
) -> dict[str, object]:
    """Return a results.json as ``pcbkit check`` writes it, with the holes asked for.

    ``groups`` are the check groups the project switched on; ``ran`` the ones that have
    a result (all of them unless told), with that ``group_outcome``; ``keys`` the
    project checks that have one; ``fields`` replace a top-level key.
    """
    checks = {k: {"group": "project", "outcome": "passed"} for k in keys}
    for group in groups if ran is None else ran:
        checks[f"pcbkit.check.builtin.test_{group}::test_one"] = {
            "group": group,
            "outcome": group_outcome,
        }
    data: dict[str, object] = {
        "format": 1,
        "groups": list(groups),
        "exit_status": 0,
        "counts": {"passed": len(checks)},
        "checks": checks,
    }
    data.update(fields)
    return data


GOOD_RESULTS = results_file()


def without(key: str) -> str:
    """Return a good results file as JSON text, lacking the top-level ``key``."""
    return json.dumps({k: v for k, v in GOOD_RESULTS.items() if k != key})


def replaced(**fields: object) -> str:
    """Return a good results file as JSON text, its top-level ``fields`` replaced."""
    return json.dumps({**GOOD_RESULTS, **fields})


def touch(path: Path, when: float, text: str = "x\n") -> Path:
    """Make ``path`` if it is missing, and set its modification time to ``when``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    os.utime(path, (when, when))
    return path


def write_results(project: Path, data: object) -> None:
    """Replace the project's results.json by ``data``, with the checks' time."""
    path = project / "out" / "checks" / "results.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (CHECKED, CHECKED))


def make_project(
    root: Path,
    fab: str = "out/fab",
    results: object = GOOD_RESULTS,
    check_module: str = CHECK_MODULE,
) -> Path:
    """Build a project whose files are current: sources, then fab files, then checks."""
    for name in ("pcbkit.toml", "design.py", "layout.py", "routing.py", "silk.py"):
        touch(root / name, SOURCES)
    touch(root / "blocks" / "power.py", SOURCES)
    touch(root / "footprints" / "part.kicad_mod", SOURCES)
    touch(root / "golden" / "board.ses", SOURCES)
    for name in ("specs.py", "circuits.py"):
        touch(root / name, SOURCES)
    touch(root / "checks" / "test_led.py", SOURCES, check_module)
    for name in ("board_gerbers.zip", "board_BOM.csv", "board_centroid.csv"):
        touch(root / fab / name, FABRICATED)
    if results is not None:
        text = results if isinstance(results, str) else json.dumps(results)
        touch(root / "out" / "checks" / "results.json", CHECKED, text)
    return root


def run(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the script inside ``root``, as the skill does."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a project whose fab files and checks are current."""
    return make_project(tmp_path / "my-board")


def test_a_current_project_is_fine(project: Path) -> None:
    done = run(project)
    assert done.returncode == 0, done.stdout
    assert done.stdout.startswith("OK: ")


def test_a_run_cut_short_by_k_is_refused(tmp_path: Path) -> None:
    """Refuse results whose own selection block says the run was partial."""
    selection = {"keyword": "led", "markexpr": "", "deselected": 7, "complete": False}
    project = make_project(
        tmp_path / "my-board", results=results_file(selection=selection)
    )
    done = run(project)
    assert done.returncode == 1, done.stdout
    assert "PARTIAL: the last pcbkit check run was cut short by -k 'led'" in done.stdout
    assert "(7 checks left out)" in done.stdout


def test_a_complete_selection_is_fine(tmp_path: Path) -> None:
    """Pass results whose selection block says nothing was left out."""
    selection = {"keyword": "", "markexpr": "", "deselected": 0, "complete": True}
    project = make_project(
        tmp_path / "my-board", results=results_file(selection=selection)
    )
    done = run(project)
    assert done.returncode == 0, done.stdout
    assert "PARTIAL" not in done.stdout


def test_it_works_from_a_folder_inside_the_project(project: Path) -> None:
    assert run(project / "checks").returncode == 0


def test_the_older_fab_folder_is_read_when_there_is_no_out_fab(tmp_path: Path) -> None:
    project = make_project(tmp_path / "old-board", fab="fab")
    assert run(project).returncode == 0


# --- a source changed after the files were made ---------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "pcbkit.toml",
        "design.py",
        "layout.py",
        "routing.py",
        "silk.py",
        "bom.py",
        "blocks/power.py",
        # Only the project's own specs.py, circuits.py and mutants.py are read by the
        # checks alone. A module of a sub-folder with one of those names can be
        # imported by design.py, so it is a source of the board.
        "blocks/specs.py",
        "blocks/circuits.py",
        "blocks/mutants.py",
        "footprints/part.kicad_mod",
        # KiCad footprint libraries are folders named *.pretty
        "footprints/mylib.pretty/part.kicad_mod",
        "golden/board.ses",
    ],
)
def test_a_source_changed_after_the_fab_files_makes_them_stale(
    project: Path, source: str
) -> None:
    touch(project / source, CHECKED + 100)
    done = run(project)
    assert done.returncode == 1
    lines = done.stdout.splitlines()
    assert any(line.startswith("STALE: out/fab/board_gerbers.zip") for line in lines)
    assert any(source in line for line in lines), done.stdout
    assert any("the checks were run before" in line for line in lines)


@pytest.mark.parametrize(
    "ignored",
    [
        "archive/rev_a/design.py",
        ".venv/lib/site.py",
        "kicad/board.kicad_pcb",
        "out/docs/schematic.pdf",
        # a .py file in a folder pcbkit writes, or in a cache, is not a source either
        "kicad/helper.py",
        "out/helper.py",
        "fab/helper.py",
        "__pycache__/design.py",
        "blocks/__pycache__/power.py",
        "mutants.py",
        "notes.md",
        "pyproject.toml",
    ],
)
def test_a_file_that_cannot_change_the_board_is_ignored(
    project: Path, ignored: str
) -> None:
    touch(project / ignored, CHECKED + 100)
    done = run(project)
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize("name", ["specs.py", "circuits.py", "checks/test_led.py"])
def test_a_check_input_changed_after_the_checks_ran_makes_only_them_stale(
    project: Path, name: str
) -> None:
    touch(project / name, CHECKED + 100)
    done = run(project)
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        f"STALE: the checks were run before {name} changed: run pcbkit check"
    ]


def test_a_file_made_at_the_same_second_as_the_last_change_is_current(
    project: Path,
) -> None:
    """A fab file and a source with the same time are not told apart: no false alarm."""
    touch(project / "design.py", FABRICATED)
    touch(project / "out" / "checks" / "results.json", FABRICATED)
    done = run(project)
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize("name", ["specs.py", "circuits.py", "checks/test_led.py"])
def test_checks_run_in_the_same_second_as_their_input_are_current(
    project: Path, name: str
) -> None:
    touch(project / name, CHECKED)
    done = run(project)
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize(
    "keep_folder", [True, False], ids=["empty-checks", "no-checks"]
)
def test_a_project_without_specs_or_circuits_or_checks_is_fine(
    project: Path, keep_folder: bool
) -> None:
    """Those files are optional: a board with none of them must not crash the script."""
    for name in ("specs.py", "circuits.py", "checks/test_led.py"):
        (project / name).unlink()
    if not keep_folder:
        (project / "checks").rmdir()
    done = run(project)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_link_that_leads_nowhere_is_not_a_source(project: Path) -> None:
    """An editor's lock file (.#design.py) is a link to a name that does not exist."""
    (project / ".#design.py").symlink_to("nobody@host.123:456")
    (project / "blocks" / ".#power.py").symlink_to("nobody@host.123:456")
    done = run(project)
    assert done.returncode == 0, done.stdout + done.stderr


# --- files that are missing or ambiguous ----------------------------------------------


@pytest.mark.parametrize(
    "name", ["board_gerbers.zip", "board_BOM.csv", "board_centroid.csv"]
)
def test_a_missing_order_file_is_reported(project: Path, name: str) -> None:
    (project / "out" / "fab" / name).unlink()
    done = run(project)
    assert done.returncode == 1
    assert done.stdout.startswith("MISSING: out/fab/ has 0 files ending")


def test_two_zips_are_ambiguous(project: Path) -> None:
    touch(project / "out" / "fab" / "other_gerbers.zip", FABRICATED)
    done = run(project)
    assert done.returncode == 1
    assert "has 2 files ending _gerbers.zip" in done.stdout


def test_no_fab_folder_at_all_is_reported_for_each_file(tmp_path: Path) -> None:
    project = make_project(tmp_path / "board")
    for file in sorted((project / "out" / "fab").iterdir()):
        file.unlink()
    (project / "out" / "fab").rmdir()
    done = run(project)
    assert done.returncode == 1
    assert done.stdout.count("MISSING: fab/ has 0 files") == 3


def test_a_project_that_was_never_checked_is_reported(tmp_path: Path) -> None:
    done = run(make_project(tmp_path / "board", results=None))
    assert done.returncode == 1
    assert done.stdout.strip() == "MISSING: out/checks/results.json: run pcbkit check"


# --- what the check results say -------------------------------------------------------


@pytest.mark.parametrize(
    "results",
    [
        results_file(exit_status=1, counts={"passed": 10, "failed": 2}),
        results_file(exit_status=0, counts={"passed": 10, "failed": 1}),
        results_file(exit_status=0, counts={"passed": 10, "error": 1}),
        results_file(exit_status=0, counts={"passed": 10, "failed": 1, "error": 1}),
        results_file(exit_status=2, counts={"passed": 10}),
    ],
    ids=["failed-and-exit-1", "one-failed", "one-error", "both", "interrupted"],
)
def test_a_failed_check_run_is_reported(tmp_path: Path, results: object) -> None:
    done = run(make_project(tmp_path / "board", results=results))
    assert done.returncode == 1
    assert done.stdout.startswith("FAILED: the last pcbkit check run did not pass")


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"counts": []}',
        '{"counts": {}}',
        '{"exit_status": 0}',
        "null",
        # What the check plugin always writes, and the order gate now reads: the
        # groups switched on, and an entry per check with the group it belongs to.
        without("groups"),
        without("checks"),
        replaced(groups="circuit"),
        replaced(groups=[1]),
        replaced(checks=[]),
        replaced(checks=None),
        replaced(checks={"a": 1}),
        replaced(checks={"a": {"outcome": "passed"}}),
        replaced(checks={"a": {"group": []}}),
    ],
    ids=[
        "garbled",
        "array",
        "counts-array",
        "no-exit-status",
        "no-counts",
        "null",
        "no-groups",
        "no-checks",
        "groups-a-string",
        "groups-a-number",
        "checks-a-list",
        "checks-null",
        "entry-a-number",
        "entry-without-group",
        "entry-group-a-list",
    ],
)
def test_unreadable_results_are_reported_not_trusted(tmp_path: Path, text: str) -> None:
    done = run(make_project(tmp_path / "board", results=text))
    assert done.returncode == 1
    assert done.stdout.startswith("UNREADABLE: out/checks/results.json")


# --- a check run that was cut short ---------------------------------------------------
#
# `pcbkit check -k EXPR` writes results.json with only the checks it ran, and an exit
# status of 0 when those passed. The gate must not read that as a clean board.


def group_line(*names: str) -> str:
    """Return the line for check groups ``names`` that have no result."""
    noun = "group" if len(names) == 1 else "groups"
    return (
        f"INCOMPLETE: out/checks/results.json has no result for check {noun} "
        f"{', '.join(names)}: {PARTIAL}"
    )


def check_line(shown: str, absent: int, total: int) -> str:
    """Return the line for ``absent`` of ``total`` project checks with no result."""
    return (
        f"INCOMPLETE: out/checks/results.json has no result for {shown} "
        f"({absent} of {total} project checks): {PARTIAL}"
    )


def test_check_groups_with_no_result_are_reported(tmp_path: Path) -> None:
    done = run(make_project(tmp_path / "board", results=results_file(ran=[])))
    assert done.returncode == 1
    assert done.stdout.splitlines() == [group_line("circuit", "copper")]


@pytest.mark.parametrize(
    ("ran", "missing"),
    [(["circuit"], "copper"), (["copper"], "circuit")],
    ids=["copper-missing", "circuit-missing"],
)
def test_only_the_check_groups_with_no_result_are_named(
    tmp_path: Path, ran: list[str], missing: str
) -> None:
    results = results_file(ran=ran)
    done = run(make_project(tmp_path / "board", results=results))
    assert done.returncode == 1
    assert done.stdout.splitlines() == [group_line(missing)]


def test_a_check_group_with_only_skipped_results_has_run(tmp_path: Path) -> None:
    """A skip is a result: a pack that does not apply to this board still ran."""
    results = results_file(group_outcome="skipped")
    done = run(make_project(tmp_path / "board", results=results))
    assert done.returncode == 0, done.stdout


def test_a_project_that_switched_on_no_check_group_has_none_to_miss(
    tmp_path: Path,
) -> None:
    done = run(make_project(tmp_path / "board", results=results_file(groups=[])))
    assert done.returncode == 0, done.stdout


def test_a_project_check_with_no_result_is_reported(tmp_path: Path) -> None:
    results = results_file(keys=PROJECT_KEYS[1:])
    done = run(make_project(tmp_path / "board", results=results))
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        check_line("checks/test_led.py::test_led", 1, 3)
    ]


def test_a_parametrised_check_needs_a_result_for_one_of_its_parameters(
    tmp_path: Path,
) -> None:
    keys = [k for k in PROJECT_KEYS if "test_resistor" not in k]
    done = run(make_project(tmp_path / "board", results=results_file(keys=keys)))
    assert done.stdout.splitlines() == [
        check_line("checks/test_led.py::test_resistor", 1, 3)
    ]


def test_a_class_check_needs_a_result_under_its_class(tmp_path: Path) -> None:
    keys = [k for k in PROJECT_KEYS if "TestRails" not in k]
    keys.append("checks/test_led.py::test_input")  # the same name, outside the class
    done = run(make_project(tmp_path / "board", results=results_file(keys=keys)))
    assert done.stdout.splitlines() == [
        check_line("checks/test_led.py::TestRails::test_input", 1, 3)
    ]


def test_a_result_for_a_longer_name_is_not_a_result_for_the_shorter(
    tmp_path: Path,
) -> None:
    """test_led_current ran; test_led did not. A prefix match would call it done."""
    module = (
        "def test_led() -> None:\n    pass\n\n\n"
        "def test_led_current() -> None:\n    pass\n"
    )
    results = results_file(keys=["checks/test_led.py::test_led_current"])
    done = run(make_project(tmp_path / "board", results=results, check_module=module))
    assert done.stdout.splitlines() == [
        check_line("checks/test_led.py::test_led", 1, 2)
    ]


@pytest.mark.parametrize(
    ("count", "shown"),
    [
        (
            3,
            "checks/test_led.py::test_a, checks/test_led.py::test_b, "
            "checks/test_led.py::test_c",
        ),
        (
            4,
            "checks/test_led.py::test_a, checks/test_led.py::test_b, "
            "checks/test_led.py::test_c and 1 more",
        ),
        (
            9,
            "checks/test_led.py::test_a, checks/test_led.py::test_b, "
            "checks/test_led.py::test_c and 6 more",
        ),
    ],
    ids=["three-are-all-named", "four-names-three", "nine-names-three"],
)
def test_the_line_names_the_first_three_missing_checks_and_counts_the_rest(
    tmp_path: Path, count: int, shown: str
) -> None:
    names = "abcdefghi"[:count]
    module = "".join(f"def test_{n}() -> None:\n    pass\n\n\n" for n in names)
    done = run(
        make_project(
            tmp_path / "board", results=results_file(keys=[]), check_module=module
        )
    )
    assert done.stdout.splitlines() == [check_line(shown, count, count)]


@pytest.mark.parametrize(
    ("module", "collected"),
    [
        pytest.param(
            "import pytest\n\n\n"
            "@pytest.fixture\ndef test_data():\n    return 1\n\n\n"
            "@pytest.fixture(scope='session')\ndef test_board():\n    return 1\n\n\n"
            "def test_real(test_data):\n    pass\n",
            ["test_real"],
            id="fixtures-named-test",
        ),
        pytest.param(
            "from pytest import fixture\n\n\n"
            "@fixture\ndef test_data():\n    return 1\n\n\n"
            "def test_real(test_data):\n    pass\n",
            ["test_real"],
            id="fixture-imported-by-name",
        ),
        pytest.param(
            "def test_outer():\n    def test_inner():\n        pass\n",
            ["test_outer"],
            id="a-nested-function-is-not-a-check",
        ),
        pytest.param(
            "def helper():\n    pass\n\n\ndef check_one():\n    pass\n\n\n"
            "def _test_hidden():\n    pass\n\n\ndef test_real():\n    pass\n",
            ["test_real"],
            id="other-names-are-not-checks",
        ),
        pytest.param(
            "class Helpers:\n    def test_x(self):\n        pass\n\n\n"
            "def test_real():\n    pass\n",
            ["test_real"],
            id="a-class-not-named-Test",
        ),
        pytest.param(
            "class TestA:\n    def helper(self):\n        pass\n\n"
            "    def test_m(self):\n        pass\n",
            ["TestA::test_m"],
            id="a-method-not-named-test",
        ),
        pytest.param(
            "def testing():\n    pass\n",
            ["testing"],
            id="pytest-takes-the-prefix-test-not-test_",
        ),
        pytest.param(
            "async def test_a():\n    pass\n",
            ["test_a"],
            id="async",
        ),
        pytest.param(
            "import sys\n\nif sys.platform:\n    def test_c():\n        pass\n",
            ["test_c"],
            id="defined-in-an-if",
        ),
        pytest.param(
            "try:\n    import numpy\n\n    def test_t():\n        pass\n"
            "except ImportError:\n    pass\n",
            ["test_t"],
            id="defined-in-a-try",
        ),
        pytest.param(
            "class TestA:\n    class TestB:\n        def test_x(self):\n"
            "            pass\n",
            ["TestA::TestB::test_x"],
            id="a-class-in-a-class",
        ),
        pytest.param(
            "class TestA:\n    @staticmethod\n    def test_s():\n        pass\n",
            ["TestA::test_s"],
            id="staticmethod",
        ),
        pytest.param(
            "import pytest\n\n\n@pytest.mark.skip(reason='x')\n"
            "def test_s():\n    pass\n",
            ["test_s"],
            id="decorated-by-a-mark",
        ),
        pytest.param(
            "def test_a():\n    pass\n\n\ndef test_a():\n    pass\n",
            ["test_a"],
            id="defined-twice-counts-once",
        ),
    ],
)
def test_the_checks_expected_are_the_ones_pytest_would_collect(
    tmp_path: Path, module: str, collected: list[str]
) -> None:
    done = run(
        make_project(
            tmp_path / "board", results=results_file(keys=[]), check_module=module
        )
    )
    shown = ", ".join(f"checks/test_led.py::{name}" for name in collected)
    assert done.stdout.splitlines() == [
        check_line(shown, len(collected), len(collected))
    ]


def test_a_check_module_in_a_folder_of_checks_counts(tmp_path: Path) -> None:
    project = make_project(tmp_path / "board")
    touch(
        project / "checks" / "sub" / "test_y.py", SOURCES, "def test_z():\n    pass\n"
    )
    done = run(project)
    assert done.stdout.splitlines() == [
        check_line("checks/sub/test_y.py::test_z", 1, 4)
    ]


@pytest.mark.parametrize(
    "name",
    [
        "helpers.py",  # pcbkit collects test_*.py only
        "test_cases.json",  # data that happens to start with test_
        "test_notes.md",
        "led_test.py",  # pytest's own default would take this; pcbkit's does not
        ".hidden/test_h.py",  # pytest does not look in hidden folders
        "__pycache__/test_c.py",
    ],
)
def test_a_file_pytest_would_not_collect_expects_no_result(
    tmp_path: Path, name: str
) -> None:
    project = make_project(tmp_path / "board")
    touch(project / "checks" / name, SOURCES, "def test_x():\n    pass\n")
    done = run(project)
    assert done.returncode == 0, done.stdout


def test_a_test_file_that_is_a_link_to_nowhere_is_passed_over(tmp_path: Path) -> None:
    """pytest skips such a link, so there is no result to look for."""
    project = make_project(tmp_path / "board")
    (project / "checks" / "test_gone.py").symlink_to("nowhere.py")
    done = run(project)
    assert done.returncode == 0, done.stdout + done.stderr


def linked_project(tmp_path: Path, target: Path, key: str) -> Path:
    """Return a project whose checks/test_link.py is a link to ``target``.

    The results hold a result for ``key`` besides the project's own, as the plugin would
    write them.
    """
    project = make_project(tmp_path / "board")
    (project / "checks" / "test_link.py").symlink_to(target)
    write_results(project, results_file(keys=[*PROJECT_KEYS, key]))
    return project


def test_a_check_module_that_is_a_link_into_the_project_is_keyed_by_where_it_is(
    tmp_path: Path,
) -> None:
    """The plugin follows the link: the results name the file below the project."""
    touch(tmp_path / "board" / "lib" / "test_a.py", SOURCES, "def test_x(): ...\n")
    project = linked_project(
        tmp_path, Path("../lib/test_a.py"), "lib/test_a.py::test_x"
    )
    assert run(project).returncode == 0, run(project).stdout


def test_a_result_under_the_name_of_the_link_does_not_count(tmp_path: Path) -> None:
    touch(tmp_path / "board" / "lib" / "test_a.py", SOURCES, "def test_x(): ...\n")
    project = linked_project(
        tmp_path, Path("../lib/test_a.py"), "checks/test_link.py::test_x"
    )
    assert run(project).stdout.splitlines() == [
        check_line("lib/test_a.py::test_x", 1, 4)
    ]


def test_a_check_module_that_is_a_link_out_of_the_project_is_keyed_by_its_name(
    tmp_path: Path,
) -> None:
    """The plugin has no project path for it: the results hold only the file name."""
    shared = touch(
        tmp_path / "shared" / "test_common.py", SOURCES, "def test_c(): ...\n"
    )
    project = linked_project(tmp_path, shared, "test_common.py::test_c")
    assert run(project).returncode == 0, run(project).stdout


def test_a_result_under_a_project_path_does_not_count_for_a_module_outside(
    tmp_path: Path,
) -> None:
    shared = touch(
        tmp_path / "shared" / "test_common.py", SOURCES, "def test_c(): ...\n"
    )
    project = linked_project(tmp_path, shared, "checks/test_link.py::test_c")
    assert run(project).stdout.splitlines() == [
        check_line("test_common.py::test_c", 1, 4)
    ]


def load_script() -> ModuleType:
    """Return the preflight script as a module, to call one function of it."""
    spec = importlib.util.spec_from_file_location("order_preflight", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_check_module_that_cannot_be_opened_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file the user may not read fails the same way as one that is not Python.

    Only the open is faked: whether a mode-000 file is unreadable depends on who runs
    the test (root reads it), so the refusal is injected, and the script's own handling
    of it is what runs.
    """
    project = make_project(tmp_path / "board")
    read_text = Path.read_text

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "test_led.py":
            raise PermissionError(13, "Permission denied")
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    ids, lines = load_script().project_checks(project)
    assert ids == []
    assert lines == [
        "UNREADABLE: checks/test_led.py: cannot be read as Python (PermissionError): "
        "fix it, then run pcbkit check"
    ]


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"def (:\n", "SyntaxError"),
        (b"\xff\xfe\x00 not utf-8", "UnicodeDecodeError"),
    ],
    ids=["syntax-error", "not-utf-8"],
)
def test_a_check_module_that_cannot_be_read_is_reported_not_a_traceback(
    tmp_path: Path, content: bytes, error: str
) -> None:
    project = make_project(tmp_path / "board")
    (project / "checks" / "test_bad.py").write_bytes(content)
    os.utime(project / "checks" / "test_bad.py", (SOURCES, SOURCES))
    done = run(project)
    assert done.returncode == 1
    assert done.stderr == ""
    assert done.stdout.splitlines() == [
        f"UNREADABLE: checks/test_bad.py: cannot be read as Python ({error}): "
        "fix it, then run pcbkit check"
    ]


def test_a_failed_run_that_is_also_partial_says_both_failed_first(
    tmp_path: Path,
) -> None:
    results = results_file(ran=[], exit_status=1, counts={"failed": 1})
    done = run(make_project(tmp_path / "board", results=results))
    lines = done.stdout.splitlines()
    assert lines[0].startswith("FAILED: the last pcbkit check run did not pass")
    assert lines[1:] == [group_line("circuit", "copper")]


def test_stale_results_that_are_also_partial_say_both(tmp_path: Path) -> None:
    project = make_project(tmp_path / "board", results=results_file(ran=[]))
    touch(project / "specs.py", CHECKED + 100)
    done = run(project)
    assert done.stdout.splitlines() == [
        "STALE: the checks were run before specs.py changed: run pcbkit check",
        group_line("circuit", "copper"),
    ]


def test_a_project_with_no_check_module_has_no_check_to_miss(tmp_path: Path) -> None:
    project = make_project(tmp_path / "board", results=results_file(keys=[]))
    (project / "checks" / "test_led.py").unlink()
    done = run(project)
    assert done.returncode == 0, done.stdout


# --- not a project --------------------------------------------------------------------


def test_outside_a_project_it_says_so_and_exits_2(tmp_path: Path) -> None:
    done = run(tmp_path)
    assert done.returncode == 2
    assert "no pcbkit.toml here or above" in done.stdout


def test_the_script_imports_only_the_standard_library() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "ast", "json", "os", "sys", "pathlib"}, imported
