"""``test_bom_complete`` run the way ``pcbkit check`` runs it, on two tiny projects.

No KiCad needed: the check reads design.py and the project's bom.py, not exported files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pcbkit.check import runner
from tests.board_files import TOML, write_file, write_project

CHECKS = TOML + '\n[checks]\ngroups = ["outputs"]\n'

DESIGN = """\
from pcbkit.design import FP, R, part

B = "Power in"
part("J1", "Connector_Generic:Conn_01x02", "CONN", FP["XH2"],
     {{"1": "VIN", "2": "GND"}}, "JST", {mpn!r}, "Power input", B)
R("R1", "10k", "VIN", "GND", B)
"""


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    """Run only test_bom_complete on the project at ``root``, as pcbkit check would."""
    args = runner.command(root, "test_bom_complete", ["-q", "--no-header"])
    return subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=300)


def test_a_part_with_no_part_number_fails_the_check_and_is_named(
    tmp_path: Path,
) -> None:
    write_project(tmp_path, DESIGN.format(mpn=""), CHECKS)
    done = run_check(tmp_path)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "J1 (CONN)" in done.stdout
    assert "R1" not in done.stdout.split("parts the BOM cannot order:")[1]


def test_a_complete_bom_passes(tmp_path: Path) -> None:
    write_project(tmp_path, DESIGN.format(mpn="B2B-XH-A"), CHECKS)
    done = run_check(tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout


def test_a_bom_override_completes_the_part(tmp_path: Path) -> None:
    write_project(tmp_path, DESIGN.format(mpn=""), CHECKS)
    write_file(
        tmp_path / "bom.py",
        """\
        REF_OVERRIDE = {"J1": {"mpn": "B2B-XH-A", "mfr": "JST"}}
        """,
    )
    done = run_check(tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
