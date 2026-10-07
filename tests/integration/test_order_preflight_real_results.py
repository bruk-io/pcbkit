"""The order skill's preflight script, run on results the check plugin really wrote.

The unit tests of the script (tests/unit/test_order_preflight.py) build their
results.json by hand, so they only prove the script reads what its author thinks the
plugin writes. These run the checks the way ``pcbkit check`` runs them, in a child
pytest with ``pcbkit.check.plugin``, and then run the script on the file that comes out.
If the plugin ever renames ``groups``, ``checks`` or ``group``, or keys a check
differently, this is where the order gate notices.

No KiCad is needed: with no check group switched on the run is the project's own checks,
and a group that is switched on is only ever deselected by ``-k``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pcbkit.check import runner
from tests.board_files import TOML, write_file

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "order-pcbway"
    / "scripts"
    / "preflight.py"
)

CHECKS = """\
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


def make_project(root: Path, groups: str = "[]") -> Path:
    """Write a board project with three checks of its own, older than its fab files."""
    write_file(root / "pcbkit.toml", TOML + f"\n[checks]\ngroups = {groups}\n")
    write_file(root / "design.py", "x = 1\n")
    write_file(root / "checks" / "test_limits.py", CHECKS)
    now = time.time()
    for name in ("pcbkit.toml", "design.py", "checks/test_limits.py"):
        os.utime(root / name, (now - 1000, now - 1000))
    for name in ("my_board_gerbers.zip", "my_board_BOM.csv", "my_board_centroid.csv"):
        path = write_file(root / "out" / "fab" / name, "x\n")
        os.utime(path, (now - 500, now - 500))
    return root


def run_checks(
    root: Path, expression: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the checks as ``pcbkit check`` does, and return what pytest did."""
    argv = runner.command(root, expression, ["-q", "--no-header"])
    return subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=300)


def check(root: Path, expression: str | None = None) -> None:
    """Run the checks, and require that they all passed."""
    done = run_checks(root, expression)
    assert done.returncode == 0, done.stdout + done.stderr


def preflight(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the order skill's preflight script in the project."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_full_run_of_the_checks_passes_the_gate(tmp_path: Path) -> None:
    root = make_project(tmp_path / "board")
    check(root)
    done = preflight(root)
    assert done.returncode == 0, done.stdout
    assert done.stdout.startswith("OK: ")


def test_a_run_of_one_check_after_a_full_run_no_longer_passes_the_gate(
    tmp_path: Path,
) -> None:
    """The path that made the gate say OK: iterate on one check with -k, then order."""
    root = make_project(tmp_path / "board")
    check(root)
    assert preflight(root).returncode == 0
    check(root, "test_led")
    done = preflight(root)
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        "INCOMPLETE: out/checks/results.json has no result for "
        "checks/test_limits.py::test_resistor, "
        "checks/test_limits.py::TestRails::test_input "
        "(2 of 3 project checks): the last run was partial (pcbkit check -k?): "
        "run pcbkit check with no -k"
    ]


def test_a_run_that_deselected_every_check_group_no_longer_passes_the_gate(
    tmp_path: Path,
) -> None:
    """Every check of the project ran, none of the switched-on groups did."""
    root = make_project(tmp_path / "board", groups='["kicad", "outputs"]')
    check(root, "test_limits")
    done = preflight(root)
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        "INCOMPLETE: out/checks/results.json has no result for check groups kicad, "
        "outputs: the last run was partial (pcbkit check -k?): "
        "run pcbkit check with no -k"
    ]


def test_a_full_run_with_check_groups_on_is_never_read_as_partial(
    tmp_path: Path,
) -> None:
    """The names the plugin writes for the built-in groups are the ones in the config.

    This project has no board, so the checks of both groups fail or error, whatever
    machine this runs on. Each still leaves a result under its group's name, and the
    gate must call the run failed, never partial.
    """
    root = make_project(tmp_path / "board", groups='["kicad", "outputs"]')
    assert run_checks(root).returncode != 0
    written = json.loads((root / "out" / "checks" / "results.json").read_text())
    assert written["groups"] == ["kicad", "outputs"]
    groups = {entry["group"] for entry in written["checks"].values()}
    assert groups == {"project", "kicad", "outputs"}
    lines = preflight(root).stdout.splitlines()
    assert lines and lines[0].startswith("FAILED: ")
    assert not [line for line in lines if line.startswith("INCOMPLETE")], lines


def test_a_check_skipped_with_a_reason_inside_it_has_a_result(tmp_path: Path) -> None:
    """add-check's advice for a check that does not apply: skip it, in the check."""
    root = make_project(tmp_path / "board")
    write_file(
        root / "checks" / "test_optional.py",
        """\
        import pytest


        def test_part_is_fitted() -> None:
            pytest.skip("this board has no such part")
        """,
    )
    os.utime(root / "checks" / "test_optional.py", (time.time() - 1000,) * 2)
    check(root)
    written = json.loads((root / "out" / "checks" / "results.json").read_text())
    entry = written["checks"]["checks/test_optional.py::test_part_is_fitted"]
    assert entry["outcome"] == "skipped"
    assert preflight(root).returncode == 0


def test_a_check_module_skipped_as_a_whole_leaves_no_result_and_is_reported(
    tmp_path: Path,
) -> None:
    """pytest records nothing for a module skipped at its top: its checks did not run.

    The gate calls them missing, which is true. If the plugin ever records such a skip,
    this test is the place to say so.
    """
    root = make_project(tmp_path / "board")
    write_file(
        root / "checks" / "test_whole.py",
        """\
        import pytest

        pytest.skip("this board has no such part", allow_module_level=True)


        def test_part_is_fitted() -> None:
            pass
        """,
    )
    os.utime(root / "checks" / "test_whole.py", (time.time() - 1000,) * 2)
    check(root)
    written = json.loads((root / "out" / "checks" / "results.json").read_text())
    assert not [key for key in written["checks"] if "test_whole" in key]
    done = preflight(root)
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        "INCOMPLETE: out/checks/results.json has no result for "
        "checks/test_whole.py::test_part_is_fitted (1 of 4 project checks): "
        "the last run was partial (pcbkit check -k?): run pcbkit check with no -k"
    ]


@pytest.mark.parametrize(
    ("link", "target"),
    [
        pytest.param("test_in.py", "../lib/test_a.py", id="into-the-project"),
        pytest.param("test_out.py", "../../shared/test_b.py", id="out-of-the-project"),
    ],
)
def test_a_check_module_that_is_a_link_is_keyed_the_way_the_gate_expects(
    tmp_path: Path, link: str, target: str
) -> None:
    root = make_project(tmp_path / "board")
    for path in (root / "lib" / "test_a.py", tmp_path / "shared" / "test_b.py"):
        write_file(path, "def test_c() -> None:\n    pass\n")
        os.utime(path, (time.time() - 1000, time.time() - 1000))
    (root / "checks" / link).symlink_to(target)
    check(root)
    done = preflight(root)
    assert done.returncode == 0, done.stdout
