"""Unit tests for skills/review-sourcing/scripts/bomlines.py.

How many of each line to buy, and which lines have no verified datasheet record, are
decided by this script from the BOM and ``parts/``, not worked out in a conversation.
Each test builds a throwaway project and runs the script as the skill does.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from pcbkit.datasheet_find import record_path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "review-sourcing"
    / "scripts"
    / "bomlines.py"
)
HEADER = [
    "Item",
    "Qty",
    "Designator",
    "Manufacturer",
    "Manufacturer Part Number",
    "Value",
    "Description",
    "Package / Footprint",
    "Type",
]
# (designators, qty per board, MPN, the record's status or None for no record).
LINES = [
    ("U1", 1, "CHIP-1", "VERIFIED"),
    ("U2", 1, "CHIP-2", "CONFIRMED"),
    ("U3", 2, "CHIP-3", "CANDIDATE"),
    ("U4", 1, "CHIP-4", "NOT FOUND"),
    ("C1, C2, C3", 3, "CAP/10u", None),
    ("J1", 1, "", None),
]


def write_project(root: Path) -> Path:
    """Write a project with a BOM of the lines above and a record for most of them."""
    (root / "out" / "fab").mkdir(parents=True)
    (root / "parts").mkdir()
    (root / "pcbkit.toml").write_text("[board]\n", encoding="utf-8")
    with open(root / "out" / "fab" / "B_BOM.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for item, (refs, qty, mpn, _status) in enumerate(LINES, start=1):
            writer.writerow([item, qty, refs, "M", mpn, "v", "d", "pkg", "SMD"])
    for _refs, _qty, mpn, status in LINES:
        if status:
            record = {"format": 1, "mpn": mpn, "status": status}
            path = root / "parts" / record_path(Path("parts"), mpn).name
            path.write_text(json.dumps(record), encoding="utf-8")
    return root


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the script inside ``root``, as the skill does."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_it_prints_the_quantity_to_buy_per_line(tmp_path: Path) -> None:
    done = run(write_project(tmp_path / "my-board"), "--boards", "5", "--spare", "2")
    assert done.returncode == 0, done.stdout
    needed = {r["designators"]: r["needed"] for r in json.loads(done.stdout)["lines"]}
    assert needed == {
        "U1": 1 * 5 + 2,
        "U2": 1 * 5 + 2,
        "U3": 2 * 5 + 2,
        "U4": 1 * 5 + 2,
        "C1, C2, C3": 3 * 5 + 2,
        "J1": 1 * 5 + 2,
    }


def test_the_spare_is_zero_unless_given(tmp_path: Path) -> None:
    done = run(write_project(tmp_path / "my-board"), "--boards", "3")
    lines = json.loads(done.stdout)["lines"]
    assert [row["needed"] for row in lines] == [3, 3, 6, 3, 9, 3]


def test_it_names_each_lines_datasheet_status_and_the_unchecked_items(
    tmp_path: Path,
) -> None:
    done = run(write_project(tmp_path / "my-board"), "--boards", "1")
    data = json.loads(done.stdout)
    assert [row["datasheet"] for row in data["lines"]] == [
        "VERIFIED",
        "CONFIRMED",
        "CANDIDATE",
        "NOT FOUND",
        "NO RECORD",
        "NO MPN",
    ]
    assert data["unchecked"] == ["3", "4", "5", "6"]


def test_a_record_of_another_part_or_a_broken_one_is_not_a_check(
    tmp_path: Path,
) -> None:
    root = write_project(tmp_path / "my-board")
    first = root / "parts" / record_path(Path("parts"), "CHIP-1").name
    first.write_text(json.dumps({"mpn": "OTHER", "status": "VERIFIED"}))
    second = root / "parts" / record_path(Path("parts"), "CHIP-2").name
    second.write_text("not json")
    data = json.loads(run(root, "--boards", "1").stdout)
    assert [row["datasheet"] for row in data["lines"][:2]] == [
        "NO RECORD",
        "UNREADABLE",
    ]
    assert data["unchecked"][:2] == ["1", "2"]


def test_the_record_file_name_is_the_one_pcbkit_writes() -> None:
    spec = importlib.util.spec_from_file_location("review_bomlines", SCRIPT)
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for mpn in ("CHIP-1", "CAP/10u", " odd part #1 ", "", "..."):
        assert module.record_name(mpn) == record_path(Path("parts"), mpn).name


@pytest.mark.parametrize(
    "args",
    [[], ["--boards"], ["--boards", "x"], ["--boards", "2", "--spare", "-1"], ["5"]],
)
def test_bad_arguments_exit_2_with_the_usage(tmp_path: Path, args: list[str]) -> None:
    done = run(write_project(tmp_path / "my-board"), *args)
    assert done.returncode == 2
    assert done.stdout.startswith("usage: bomlines.py --boards N [--spare S]")


def test_without_a_single_bom_it_says_so(tmp_path: Path) -> None:
    root = write_project(tmp_path / "my-board")
    (root / "out" / "fab" / "B_BOM.csv").unlink()
    done = run(root, "--boards", "1")
    assert done.returncode == 1
    assert done.stdout.startswith("0 files ending _BOM.csv in fab/")


def test_outside_a_project_it_exits_2(tmp_path: Path) -> None:
    assert run(tmp_path, "--boards", "1").returncode == 2
