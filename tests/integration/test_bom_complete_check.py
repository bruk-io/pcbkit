"""``test_bom_complete`` run the way ``pcbkit check`` runs it, on tiny projects.

No KiCad needed: the check reads the exported BOM CSV (written here by the real
``write_csv``), not the board.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from pcbkit.check import runner
from pcbkit.fab import bom
from tests.board_files import TOML, write_file
from tests.fab_files import FAB_NAME, bom_line

CHECKS = TOML + '\n[checks]\ngroups = ["outputs"]\n'


def project(root: Path, *lines: bom.BomLine) -> Path:
    """Write a project whose out/fab holds a BOM of ``lines``; return its root."""
    write_file(root / "pcbkit.toml", CHECKS)
    folder = root / "out" / "fab"
    folder.mkdir(parents=True)
    bom.write_csv(folder / f"{FAB_NAME}_BOM.csv", lines)
    return root


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    """Run only test_bom_complete on the project at ``root``, as pcbkit check would."""
    args = runner.command(root, "test_bom_complete", ["-q", "--no-header"])
    return subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=300)


def test_a_line_with_no_part_number_fails_the_check_and_is_named(
    tmp_path: Path,
) -> None:
    done = run_check(
        project(
            tmp_path,
            bom_line(1, 2, "R1,R2", mpn="RC0603FR-0710KL"),
            bom_line(2, 1, "J1", mpn=""),
        )
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "J1 (v): '' has no manufacturer part number" in done.stdout
    assert "R1,R2" not in done.stdout.split("cannot order:")[1]


def test_the_resistor_stand_in_and_a_description_both_fail(tmp_path: Path) -> None:
    stand_in = dataclasses.replace(
        bom_line(1, 1, "R1", mpn=bom.stand_in_mpn("4k7")), value="4k7"
    )
    described = bom_line(2, 1, "J1", mpn="pin header 1x3 male")
    done = run_check(project(tmp_path, stand_in, described))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "R1 (4k7): '0603 4k7 1%' is the stand-in part number" in done.stdout
    assert "J1 (v): 'pin header 1x3 male' has a space in it" in done.stdout


def test_a_complete_bom_passes(tmp_path: Path) -> None:
    done = run_check(
        project(
            tmp_path,
            bom_line(1, 2, "R1,R2", mpn="RC0603FR-0710KL"),
            bom_line(2, 1, "J1", mpn="B2B-XH-A"),
        )
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout
