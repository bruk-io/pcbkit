"""Unit tests for skills/board-workflow/scripts/groups.py.

A review's ``-k`` selection over a check group that pcbkit.toml leaves off runs nothing
and says nothing. The script names such groups before the first run.
"""

from __future__ import annotations

import importlib.util
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


def write_project(root: Path, groups_line: str | None) -> Path:
    """Write a pcbkit.toml with ``groups_line`` under [checks], or no [checks]."""
    root.mkdir()
    text = '[board]\nstem = "my_board"\ntitle = "My Board"\nrev = "A"\n'
    text += 'fab_name = "My_Board_revA"\n'
    if groups_line is not None:
        text += f"\n[checks]\ngroups = {groups_line}\n"
    (root / "pcbkit.toml").write_text(text, encoding="utf-8")
    return root


def test_main_reads_the_projects_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(write_project(tmp_path / "my-board", '["kicad", "circuit"]'))
    assert groups.main(["circuit", "fab"]) == 1
    assert capsys.readouterr().out.startswith("OFF: fab (pcbkit.toml [checks] groups")


def test_main_uses_the_default_when_the_project_names_no_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(write_project(tmp_path / "my-board", None))
    assert groups.main(["kicad", "outputs"]) == 0
    assert capsys.readouterr().out == "ON: kicad, outputs\n"


def test_main_outside_a_project_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert groups.main(["kicad"]) == 2
    assert capsys.readouterr().out.startswith("no board project: ")
