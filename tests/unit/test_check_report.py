"""Unit tests for pcbkit.report: the validation report made from ``results.json``.

The results here are dicts written in the tests, in the shape ``pcbkit.check.plugin``
leaves (see ``pcbkit.check.results``), so nothing runs a check. ``render`` is pure;
``load_results`` and ``write_report`` read and write files in a temporary board
project.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import click
import pytest

from pcbkit import report
from pcbkit.project import Project, load_project
from tests.board_files import write_project


def entry(outcome: str, **more: Any) -> dict[str, Any]:
    """Return one results entry the way the plugin writes it, for a project check."""
    return {"group": "project", "outcome": outcome, "numbers": {}, **more}


def make_results(checks: dict[str, dict[str, Any]], **more: Any) -> dict[str, Any]:
    """Return a results dict like the plugin's, counted from ``checks``."""
    counts: dict[str, int] = {}
    for item in checks.values():
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    data: dict[str, Any] = {
        "format": 1,
        "board": {"stem": "my_board", "title": "My Board", "rev": "A"},
        "copper_mm": 0.035,
        "groups": ["kicad"],
        "exit_status": 0,
        "counts": counts,
        "seconds": 1.5,
        "checks": checks,
    }
    data.update(more)
    return data


def headings(text: str) -> list[str]:
    """Return the ``##`` headings of a report, in order, without the marks."""
    return [line[3:] for line in text.splitlines() if line.startswith("## ")]


def table(text: str, heading: str) -> list[list[str]]:
    """Return the rows under ``## heading``, each split into its cells like Markdown."""
    lines = text.splitlines()
    start = lines.index(f"## {heading}")
    assert lines[start + 1 : start + 4] == [
        "",
        "| Check | Result | Numbers |",
        "|---|---|---|",
    ]
    rows: list[list[str]] = []
    for line in lines[start + 4 :]:
        if not line:  # a blank line ends the table
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def one_row(check_id: str, item: dict[str, Any]) -> list[str]:
    """Return the cells of the only row of the report for a single check."""
    text = report.render(make_results({check_id: item}))
    (heading,) = headings(text)
    (row,) = table(text, heading)
    return row


@pytest.fixture
def board_project(tmp_path: Path) -> Project:
    """Return a board project in a temporary folder with no results yet."""
    write_project(tmp_path / "board", "")
    return load_project(tmp_path / "board")


def results_path(project: Project) -> Path:
    """Return where ``pcbkit check`` leaves the results of ``project``."""
    return project.root / "out" / "checks" / "results.json"


def write_results(project: Project, content: str | bytes) -> Path:
    """Write ``content`` as the project's results file, making its folder."""
    path = results_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


# --- summary_line -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"counts": {"passed": 138, "failed": 4}, "seconds": 12.3},
            "4 failed, 138 passed in 12.3s",
        ),
        (
            {
                "counts": {"skipped": 2, "passed": 3, "error": 1, "failed": 4},
                "seconds": 2.0,
            },
            "4 failed, 1 error, 3 passed, 2 skipped in 2s",
        ),
        ({"counts": {"passed": 0, "failed": 2}}, "2 failed"),
        ({"counts": {"passed": 1}, "seconds": 0}, "1 passed in 0s"),
        ({"counts": {"passed": 1}}, "1 passed"),
        ({"counts": {}, "seconds": 0.5}, "no checks in 0.5s"),
        ({"counts": {"passed": 0}}, "no checks"),
        ({}, "no checks"),
    ],
    ids=[
        "failed-then-passed",
        "all-four-in-pytests-order",
        "a-zero-count-is-left-out",
        "zero-seconds-are-shown",
        "no-seconds-no-tail",
        "no-counts-but-seconds",
        "only-zero-counts",
        "empty",
    ],
)
def test_summary_line_reads_like_pytests_own(
    data: dict[str, Any], expected: str
) -> None:
    """Print failed, error, passed, skipped, then the time, leaving out zeros."""
    assert report.summary_line(data) == expected


# --- render: the top of the report ------------------------------------------------


def test_render_opens_with_the_title_the_board_and_the_counts() -> None:
    """Put the heading, the board's title and rev, then the counts in code ticks."""
    text = report.render(
        make_results(
            {
                "checks/test_a.py::test_x": entry("passed"),
                "checks/test_a.py::test_y": entry("failed", message="no"),
            },
            seconds=3.5,
        )
    )
    assert text.splitlines()[:5] == [
        "# Design validation",
        "",
        "My Board, rev A",
        "",
        "`1 failed, 1 passed in 3.5s`",
    ]


def test_render_leaves_out_the_board_line_when_the_title_is_empty() -> None:
    """Go straight from the heading to the counts when the results name no board."""
    text = report.render(make_results({}, board={}, counts={"passed": 1}))
    assert text.splitlines()[:4] == [
        "# Design validation",
        "",
        "`1 passed in 1.5s`",
        "",
    ]


def test_render_ends_with_a_newline_and_leaves_the_results_alone() -> None:
    """Return text that ends in a line break, and do not edit what it was given."""
    data = make_results({"checks/test_a.py::test_x": entry("passed")})
    before = copy.deepcopy(data)
    assert report.render(data).endswith("\n")
    assert data == before


# --- render: the sections ---------------------------------------------------------


def test_render_lists_built_in_sections_before_the_projects_own_files() -> None:
    """Order the headings built-in first (by group), then the project files by path."""
    data = make_results(
        {
            # "alpha/..." sorts before "built-in" as plain text, so this shows the
            # rule is "built-in first", not just alphabetical order.
            "alpha/test_first.py::test_a": entry("passed"),
            "checks/test_zeta.py::test_b": entry("passed"),
            "pcbkit.check.builtin.test_kicad::test_c": entry("passed", group="kicad"),
            "checks/test_beta.py::test_d": entry("passed"),
            "pcbkit.check.builtin.test_copper::test_e": entry("passed", group="copper"),
        }
    )
    assert headings(report.render(data)) == [
        "built-in: copper",
        "built-in: kicad",
        "alpha/test_first.py",
        "checks/test_beta.py",
        "checks/test_zeta.py",
    ]


def test_render_falls_back_to_the_module_name_when_a_group_is_missing() -> None:
    """Fall back to the module's name when an entry carries no group."""
    item = entry("passed")
    del item["group"]
    data = make_results({"pcbkit.check.builtin.test_outputs::test_a": item})
    assert headings(report.render(data)) == ["built-in: test_outputs"]


def test_render_puts_every_check_of_a_file_in_one_table_in_the_order_it_ran() -> None:
    """Keep the results' own order inside a table, and the part after ``::`` as name."""
    text = report.render(
        make_results(
            {
                "checks/test_a.py::test_second[x]": entry("passed"),
                "checks/test_b.py::test_other": entry("passed"),
                "checks/test_a.py::test_first": entry("passed"),
                "checks/test_a.py::TestGroup::test_third": entry("passed"),
            }
        )
    )
    assert headings(text) == ["checks/test_a.py", "checks/test_b.py"]
    assert [row[0] for row in table(text, "checks/test_a.py")] == [
        "test_second[x]",
        "test_first",
        "TestGroup::test_third",
    ]
    assert [row[0] for row in table(text, "checks/test_b.py")] == ["test_other"]


def test_render_with_no_checks_has_a_summary_and_no_tables() -> None:
    """Say there were no checks and write no section."""
    text = report.render(make_results({}))
    assert "`no checks in 1.5s`" in text
    assert headings(text) == []


# --- render: the rows -------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "word"),
    [
        ("passed", "pass"),
        ("failed", "**FAIL**"),
        ("error", "**ERROR**"),
        ("skipped", "skip"),
        ("xfailed", "xfailed"),
    ],
)
def test_render_shows_each_outcome_as_its_word(outcome: str, word: str) -> None:
    """Write pass, **FAIL**, **ERROR** and skip; show an unknown outcome as given."""
    assert one_row("checks/test_a.py::test_x", entry(outcome))[1] == word


def test_render_shows_a_skipped_checks_reason() -> None:
    """Follow the skip word with a colon and the reason."""
    item = entry("skipped", reason="needs pcbnew")
    assert one_row("checks/test_a.py::test_x", item)[1] == "skip: needs pcbnew"


@pytest.mark.parametrize(
    ("outcome", "word"), [("failed", "**FAIL**"), ("error", "**ERROR**")]
)
def test_render_shows_a_failed_or_errored_checks_message(
    outcome: str, word: str
) -> None:
    """Follow **FAIL** or **ERROR** with a colon and the message."""
    item = entry(outcome, message="assert 3 == 4")
    assert one_row("checks/test_a.py::test_x", item)[1] == f"{word}: assert 3 == 4"


def test_render_adds_no_colon_to_a_pass() -> None:
    """Show just ``pass`` for a check that passed."""
    assert one_row("checks/test_a.py::test_x", entry("passed"))[1] == "pass"


def test_render_cuts_a_long_note_to_200_characters() -> None:
    """Keep the first 200 characters of a message, after tidying it."""
    item = entry("failed", message="x" * 300)
    assert one_row("checks/test_a.py::test_x", item)[1] == "**FAIL**: " + "x" * 200


def test_render_joins_a_multi_line_note_into_one_line() -> None:
    """Turn every run of spaces, tabs and line breaks into one space."""
    item = entry("failed", message="E   assert 1 == 2\n\n  +  where 1 = f()\t(here)")
    row = one_row("checks/test_a.py::test_x", item)
    assert row[1] == "**FAIL**: E assert 1 == 2 + where 1 = f() (here)"


def test_render_writes_numbers_as_key_colon_value_joined_by_semicolons() -> None:
    """Keep the order the numbers were recorded in, and show falsy values too."""
    item = entry(
        "passed",
        numbers={"volts": 3.3, "count": 4, "zero": 0, "flag": False, "rows": [1, 2]},
    )
    row = one_row("checks/test_a.py::test_x", item)
    assert row[2] == "volts: 3.3; count: 4; zero: 0; flag: False; rows: [1, 2]"


def test_render_shows_one_exact_row() -> None:
    """Lay a row out as ``| name | result | numbers |``."""
    data = make_results(
        {"checks/test_a.py::test_x[VIN]": entry("passed", numbers={"v": 5.0, "n": 4})}
    )
    assert "| test_x[VIN] | pass | v: 5.0; n: 4 |" in report.render(data).splitlines()


def test_render_leaves_the_numbers_cell_empty_when_there_are_none() -> None:
    """Show an empty cell for a check with no numbers, or no ``numbers`` entry."""
    without = entry("passed")
    del without["numbers"]
    data = make_results(
        {
            "checks/test_a.py::test_x": entry("passed", numbers={}),
            "checks/test_a.py::test_y": without,
        }
    )
    lines = report.render(data).splitlines()
    assert "| test_x | pass |  |" in lines
    assert "| test_y | pass |  |" in lines


def test_render_keeps_pipes_and_line_breaks_from_breaking_the_table() -> None:
    """Give every row three cells, whatever the names, notes and numbers hold."""
    data = make_results(
        {
            "checks/test_a.py::test_pipe[a|b]": entry(
                "failed",
                message="left | right\nnext line",
                numbers={"k|ey": "v|al\nue", "n": 1},
            ),
            "checks/test_a.py::test_skip": entry(
                "skipped", reason="needs | pcbnew\n\n"
            ),
            "checks/test_a.py::test_plain": entry("passed"),
        }
    )
    text = report.render(data)
    rows = table(text, "checks/test_a.py")
    assert [len(row) for row in rows] == [3, 3, 3]
    assert rows[0] == [
        "test_pipe[a/b]",
        "**FAIL**: left / right next line",
        "k/ey: v/al ue; n: 1",
    ]
    assert rows[1][1] == "skip: needs / pcbnew"


# --- load_results -----------------------------------------------------------------


def test_load_results_asks_for_a_check_run_when_there_is_no_file(
    board_project: Project,
) -> None:
    """Name the missing file and say to run `pcbkit check` first."""
    with pytest.raises(click.ClickException) as raised:
        report.load_results(board_project)
    message = raised.value.message
    assert str(results_path(board_project)) in message
    assert "not found" in message
    assert "run `pcbkit check` first" in message
    assert raised.value.exit_code == 1


@pytest.mark.parametrize(
    "content",
    ["not json at all", "", '{"checks": {', b"\xff\xfe\x00\x01"],
    ids=["text", "empty", "cut-off", "not-utf-8"],
)
def test_load_results_names_the_file_when_it_cannot_be_read(
    content: str | bytes, board_project: Project
) -> None:
    """Raise a ClickException that names the file and says it is not readable."""
    path = write_results(board_project, content)
    with pytest.raises(click.ClickException) as raised:
        report.load_results(board_project)
    assert str(path) in raised.value.message
    assert "not readable" in raised.value.message


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"counts": {"passed": 1}},
        {"checks": []},
        {"checks": None},
        {"checks": "three"},
    ],
    ids=["empty-object", "no-checks-key", "list", "null", "text"],
)
def test_load_results_asks_for_a_new_run_when_the_file_has_no_checks(
    content: dict[str, Any], board_project: Project
) -> None:
    """Raise a ClickException that names the file and says to run `pcbkit check`."""
    path = write_results(board_project, json.dumps(content))
    with pytest.raises(click.ClickException) as raised:
        report.load_results(board_project)
    assert str(path) in raised.value.message
    assert "has no checks" in raised.value.message
    assert "run `pcbkit check` again" in raised.value.message


@pytest.mark.parametrize("text", ["[]", "[1, 2]", '"checks"', "3", "null"])
def test_load_results_asks_for_a_new_run_when_the_file_is_not_an_object(
    text: str, board_project: Project
) -> None:
    """Raise a ClickException, not an AttributeError, for JSON that is not an object."""
    path = write_results(board_project, text)
    with pytest.raises(click.ClickException) as raised:
        report.load_results(board_project)
    assert str(path) in raised.value.message
    assert "run `pcbkit check` again" in raised.value.message


def test_load_results_returns_what_the_file_holds(board_project: Project) -> None:
    """Return the parsed results, and accept a run that has no checks in it."""
    data = make_results({"checks/test_a.py::test_x": entry("passed")})
    write_results(board_project, json.dumps(data))
    assert report.load_results(board_project) == data
    write_results(board_project, json.dumps({"checks": {}}))
    assert report.load_results(board_project) == {"checks": {}}


# --- write_report -----------------------------------------------------------------


def test_write_report_writes_validation_md_next_to_the_results(
    board_project: Project,
) -> None:
    """Write out/checks/VALIDATION.md, and return its path and the results."""
    data = make_results(
        {
            "pcbkit.check.builtin.test_kicad::test_erc": entry(
                "passed", group="kicad", numbers={"errors": 0}
            ),
            "checks/test_a.py::test_x": entry("failed", message="assert 1 == 2"),
        }
    )
    write_results(board_project, json.dumps(data))
    path, loaded = report.write_report(board_project)
    assert path == board_project.root / "out" / "checks" / "VALIDATION.md"
    assert loaded == data
    text = path.read_text(encoding="utf-8")
    assert text == report.render(data)
    assert headings(text) == ["built-in: kicad", "checks/test_a.py"]
    assert table(text, "built-in: kicad") == [["test_erc", "pass", "errors: 0"]]
    assert table(text, "checks/test_a.py") == [
        ["test_x", "**FAIL**: assert 1 == 2", ""]
    ]


def test_write_report_replaces_an_older_report_and_keeps_the_results(
    board_project: Project,
) -> None:
    """Overwrite the report, and leave results.json exactly as it was."""
    data = make_results({"checks/test_a.py::test_x": entry("passed")})
    path = write_results(board_project, json.dumps(data))
    stale = path.with_name("VALIDATION.md")
    stale.write_text("# an old report\n", encoding="utf-8")
    raw = path.read_bytes()
    report.write_report(board_project)
    assert stale.read_text(encoding="utf-8") == report.render(data)
    assert path.read_bytes() == raw


def test_write_report_writes_nothing_when_there_are_no_results(
    board_project: Project,
) -> None:
    """Raise the same ClickException as load_results and leave no report behind."""
    with pytest.raises(click.ClickException, match="run `pcbkit check` first"):
        report.write_report(board_project)
    assert not (board_project.root / "out" / "checks" / "VALIDATION.md").exists()
