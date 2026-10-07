"""Unit tests for skills/order-pcbway/scripts/preflight.py.

The script answers "are the files I am about to pay for current?" from file times and
the checks' results alone, so each test builds a throwaway board project with chosen
times and runs the script as the order skill does: a child process in the project
folder.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

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

GOOD_RESULTS = {"exit_status": 0, "counts": {"passed": 12, "skipped": 2}}


def touch(path: Path, when: float, text: str = "x\n") -> Path:
    """Make ``path`` if it is missing, and set its modification time to ``when``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    os.utime(path, (when, when))
    return path


def make_project(
    root: Path, fab: str = "out/fab", results: object = GOOD_RESULTS
) -> Path:
    """Build a project whose files are current: sources, then fab files, then checks."""
    for name in ("pcbkit.toml", "design.py", "layout.py", "routing.py", "silk.py"):
        touch(root / name, SOURCES)
    touch(root / "blocks" / "power.py", SOURCES)
    touch(root / "footprints" / "part.kicad_mod", SOURCES)
    touch(root / "golden" / "board.ses", SOURCES)
    for name in ("specs.py", "circuits.py", "checks/test_led.py"):
        touch(root / name, SOURCES)
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
        "footprints/part.kicad_mod",
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
        {"exit_status": 1, "counts": {"passed": 10, "failed": 2}},
        {"exit_status": 0, "counts": {"passed": 10, "failed": 1}},
        {"exit_status": 0, "counts": {"passed": 10, "error": 1}},
        {"exit_status": 2, "counts": {"passed": 10}},
    ],
    ids=["failed-and-exit-1", "one-failed", "one-error", "interrupted"],
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
    ],
    ids=["garbled", "array", "counts-array", "no-exit-status", "no-counts", "null"],
)
def test_unreadable_results_are_reported_not_trusted(tmp_path: Path, text: str) -> None:
    done = run(make_project(tmp_path / "board", results=text))
    assert done.returncode == 1
    assert done.stdout.startswith("UNREADABLE: out/checks/results.json")


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
    assert imported <= {"__future__", "json", "os", "sys", "pathlib"}, imported
