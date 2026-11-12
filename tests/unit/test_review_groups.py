"""Unit tests for skills/board-workflow/scripts/groups.py.

A review's ``-k`` selection over a check group that pcbkit.toml leaves off runs nothing
and says nothing. The script owns each gate's groups and selection, names the groups
that are off before the first run, and prints the run for the gate.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from pcbkit.project import CHECK_GROUPS

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "board-workflow"
    / "scripts"
    / "groups.py"
)


def load() -> ModuleType:
    """Import the script as a module, by its path."""
    spec = importlib.util.spec_from_file_location("review_groups", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


groups = load()


def test_groups_that_are_on_pass() -> None:
    assert groups.report(("kicad", "circuit"), ["circuit", "kicad"]) == (
        0,
        "ON: circuit, kicad",
    )


def test_the_default_groups_leave_the_review_groups_off() -> None:
    """The documented default, kicad and outputs, has no circuit, fab or copper."""
    code, line = groups.report(("kicad", "outputs"), ["kicad", "circuit", "copper"])
    assert code == 1
    assert line == (
        "OFF: circuit, copper (pcbkit.toml [checks] groups is kicad, outputs): add "
        "them before the first run, since a group that is off collects no check and "
        "a -k selection over it decides nothing"
    )


def test_an_empty_groups_list_is_named_as_empty() -> None:
    assert "groups is empty" in groups.report((), ["kicad"])[1]


@pytest.mark.parametrize("needed", [["circuits"], []])
def test_a_name_that_is_not_a_group_is_refused(needed: list[str]) -> None:
    code, line = groups.report(("kicad",), needed)
    assert code == 2
    assert line.startswith("UNKNOWN: ")
    assert ", ".join(CHECK_GROUPS) in line


def write_project(
    root: Path, groups_line: str | None, specs: str | None = None
) -> Path:
    """Write a pcbkit.toml with ``groups_line`` under [checks], or no [checks]."""
    root.mkdir()
    text = '[board]\nstem = "my_board"\ntitle = "My Board"\nrev = "A"\n'
    text += 'fab_name = "My_Board_revA"\n'
    if groups_line is not None:
        text += f"\n[checks]\ngroups = {groups_line}\n"
    (root / "pcbkit.toml").write_text(text, encoding="utf-8")
    if specs is not None:
        (root / "specs.py").write_text(specs, encoding="utf-8")
    return root


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the script inside ``root``, as the skills do."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )


ALL = '["kicad", "outputs", "fab", "copper", "circuit", "esp32s3"]'


def test_each_gate_owns_its_groups_and_its_selection() -> None:
    """The set per gate, written out: a skill no longer spells either one."""
    assert {g: v[0] for g, v in groups.GATES.items()} == {
        "sch": ("kicad", "circuit"),
        "place": ("kicad", "circuit", "fab", "copper"),
        "route": ("kicad", "circuit", "fab", "copper"),
        "order": ("kicad", "circuit", "fab", "copper", "outputs"),
    }
    assert {g: v[1] for g, v in groups.GATES.items()} == {
        "sch": "test_circuit or test_esp32s3 or test_erc_clean",
        "place": "test_circuit or test_esp32s3 or test_erc_clean or "
        "test_decoupling_caps_are_close or "
        "test_power_footprints_match_datasheet or "
        "test_filter_caps_sit_by_the_adc_pin",
        "route": "not test_outputs",
        "order": None,
    }


def test_a_gate_prints_its_run_and_the_two_caveats(tmp_path: Path) -> None:
    done = run(write_project(tmp_path / "my-board", ALL), "--gate", "sch")
    assert done.returncode == 0, done.stdout
    lines = done.stdout.splitlines()
    assert lines[0] == "ON: kicad, circuit"
    assert lines[1] == (
        'RUN: .venv/bin/pcbkit check -k "test_circuit or test_esp32s3 or '
        'test_erc_clean"'
    )
    assert lines[2].startswith("NOTE: a group just switched on fails by name each")
    assert lines[3].startswith("NOTE: a -k run is partial")
    assert len(lines) == 4


def test_the_order_gate_runs_every_check_and_says_no_partial_caveat(
    tmp_path: Path,
) -> None:
    done = run(write_project(tmp_path / "my-board", ALL), "--gate", "order")
    lines = done.stdout.splitlines()
    assert lines[:2] == [
        "ON: kicad, circuit, fab, copper, outputs",
        "RUN: .venv/bin/pcbkit check",
    ]
    assert len(lines) == 3 and "-k run is partial" not in done.stdout


def test_a_devkit_board_needs_the_esp32s3_group_at_every_gate(tmp_path: Path) -> None:
    """The group only a DevKit board has: off, a -k over it collects nothing."""
    devkit = write_project(
        tmp_path / "devkit", '["kicad", "circuit"]', specs='DEVKIT_REF = "U1"\n'
    )
    done = run(devkit, "--gate", "sch")
    assert done.returncode == 1
    assert done.stdout.splitlines()[0].startswith("OFF: esp32s3 (pcbkit.toml")
    plain = write_project(tmp_path / "plain", '["kicad", "circuit"]', specs="X = 1\n")
    assert run(plain, "--gate", "sch").stdout.splitlines()[0] == "ON: kicad, circuit"
    on = write_project(
        tmp_path / "on", '["kicad", "circuit", "esp32s3"]', specs='DEVKIT_REF = "U1"\n'
    )
    assert run(on, "--gate", "sch").stdout.splitlines()[0] == (
        "ON: kicad, circuit, esp32s3"
    )


def test_the_default_groups_leave_the_gate_groups_off(tmp_path: Path) -> None:
    done = run(write_project(tmp_path / "my-board", None), "--gate", "place")
    assert done.returncode == 1
    assert done.stdout.splitlines()[0] == (
        "OFF: circuit, fab, copper (pcbkit.toml [checks] groups is kicad, outputs): "
        "add them before the first run, since a group that is off collects no check "
        "and a -k selection over it decides nothing"
    )


@pytest.mark.parametrize(
    "args",
    [[], ["--gate"], ["--gate", "build"], ["kicad", "circuit"], ["--gate", "sch", "x"]],
)
def test_an_unknown_gate_or_a_group_list_is_refused_with_the_usage(
    tmp_path: Path, args: list[str]
) -> None:
    done = run(write_project(tmp_path / "my-board", ALL), *args)
    assert done.returncode == 2
    assert done.stdout == "usage: groups.py --gate {sch|place|route|order}\n"


def test_a_specs_file_that_cannot_be_imported_is_said_not_a_traceback(
    tmp_path: Path,
) -> None:
    root = write_project(tmp_path / "my-board", ALL, specs="raise RuntimeError\n")
    done = run(root, "--gate", "sch")
    assert done.returncode == 2
    assert done.stdout.startswith("specs.py cannot be imported (RuntimeError)")
    assert done.stderr == ""


def test_outside_a_project_it_exits_2(tmp_path: Path) -> None:
    done = run(tmp_path, "--gate", "sch")
    assert done.returncode == 2
    assert done.stdout.startswith("no board project: ")
