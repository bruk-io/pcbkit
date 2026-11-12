"""Unit tests for skills/review-sourcing/scripts/stamp.py and its preflight handshake.

The sourcing review judges the BOM and centroid the fab receives; the stamp records
which ones, by sha256, so the order's preflight can refuse files the review never saw.
Each test builds a throwaway project and runs the script as the skill does: a child
process in the project folder.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[2] / "skills"
STAMP = SKILLS / "review-sourcing" / "scripts" / "stamp.py"
PREFLIGHT = SKILLS / "order-pcbway" / "scripts" / "preflight.py"
FAB = ("my_board_gerbers.zip", "my_board_BOM.csv", "my_board_centroid.csv")


def make_project(root: Path, fab: str = "out/fab", review: bool = True) -> Path:
    """Build a project with fab files and, if ``review``, a written sourcing.md."""
    root.mkdir(parents=True)
    (root / "pcbkit.toml").write_text("[board]\n", encoding="utf-8")
    (root / fab).mkdir(parents=True)
    for name in FAB:
        (root / fab / name).write_text(f"{name}\n", encoding="utf-8")
    if review:
        (root / "sourcing.md").write_text("# Sourcing review\n", encoding="utf-8")
    return root


def run(script: Path, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` inside ``root``, as a skill does."""
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )


def digest(path: Path) -> str:
    """Return a file's sha256, worked out here and not by the script under test."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a project whose sourcing review is written and not yet stamped."""
    return make_project(tmp_path / "my-board")


def test_the_stamp_records_the_sha256_of_the_bom_and_the_centroid(
    project: Path,
) -> None:
    done = run(STAMP, project)
    assert done.returncode == 0, done.stdout
    stamp = json.loads((project / "sourcing.json").read_text(encoding="utf-8"))
    fab = project / "out" / "fab"
    assert stamp == {
        "format": 1,
        "review": "sourcing.md",
        "files": {
            "out/fab/my_board_BOM.csv": digest(fab / "my_board_BOM.csv"),
            "out/fab/my_board_centroid.csv": digest(fab / "my_board_centroid.csv"),
        },
    }
    assert done.stdout == (
        "OK: sourcing.json records out/fab/my_board_BOM.csv, "
        "out/fab/my_board_centroid.csv\n"
    )


def test_the_stamp_works_from_a_folder_inside_the_project(project: Path) -> None:
    assert run(STAMP, project / "out").returncode == 0
    assert (project / "sourcing.json").is_file()


def test_the_older_fab_folder_is_stamped_when_there_is_no_out_fab(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path / "old-board", fab="fab")
    assert run(STAMP, project).returncode == 0
    stamp = json.loads((project / "sourcing.json").read_text(encoding="utf-8"))
    assert sorted(stamp["files"]) == [
        "fab/my_board_BOM.csv",
        "fab/my_board_centroid.csv",
    ]


def test_no_stamp_without_a_written_review(tmp_path: Path) -> None:
    project = make_project(tmp_path / "my-board", review=False)
    done = run(STAMP, project)
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        "MISSING: sourcing.md: write the sourcing review first, then stamp the files "
        "it judged"
    ]
    assert not (project / "sourcing.json").exists()


@pytest.mark.parametrize("suffix", ["_BOM.csv", "_centroid.csv"])
def test_no_stamp_when_a_judged_file_is_missing_or_doubled(
    project: Path, suffix: str
) -> None:
    fab = project / "out" / "fab"
    (fab / f"my_board{suffix}").unlink()
    done = run(STAMP, project)
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        f"MISSING: out/fab/ has 0 files ending {suffix}, and the stamp needs exactly "
        "one: run pcbkit finalize, then review the files it writes"
    ]
    (fab / f"a{suffix}").write_text("a\n", encoding="utf-8")
    (fab / f"b{suffix}").write_text("b\n", encoding="utf-8")
    assert "has 2 files ending" in run(STAMP, project).stdout
    assert not (project / "sourcing.json").exists()


def test_outside_a_project_it_says_so_and_exits_2(tmp_path: Path) -> None:
    done = run(STAMP, tmp_path)
    assert done.returncode == 2
    assert "no pcbkit.toml here or above" in done.stdout


def test_the_preflight_accepts_what_the_stamp_wrote_and_refuses_a_later_export(
    project: Path,
) -> None:
    """The two scripts agree on paths and hashes: the handshake the order relies on."""
    assert run(STAMP, project).returncode == 0
    before = run(PREFLIGHT, project).stdout
    assert "UNREVIEWED" not in before, before
    bom = project / "out" / "fab" / "my_board_BOM.csv"
    bom.write_text("a part changed after the review\n", encoding="utf-8")
    after = run(PREFLIGHT, project).stdout
    assert (
        "UNREVIEWED: out/fab/my_board_BOM.csv changed after the sourcing review "
        "judged it" in after
    ), after


def test_the_script_imports_only_the_standard_library() -> None:
    tree = ast.parse(STAMP.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "hashlib", "json", "sys", "pathlib"}, imported
