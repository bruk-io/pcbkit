"""Write the validation report from the last check run.

``pcbkit check`` leaves ``out/checks/results.json`` (see ``pcbkit.check.plugin``); this
turns it into ``out/checks/VALIDATION.md``: the counts, then one table per group of
built-in checks and one per file of the project's own, a row per check with its result
and the numbers it recorded. A skipped check shows its reason, a failed one its message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from pcbkit.check.results import (
    BUILTIN_PACKAGE,
    REPORT_FILE,
    RESULTS_FILE,
    results_dir,
)
from pcbkit.project import Project

RESULT_WORDS = {
    "passed": "pass",
    "failed": "**FAIL**",
    "error": "**ERROR**",
    "skipped": "skip",
}


def load_results(project: Project) -> dict[str, Any]:
    """Return the last run's results; raise a ClickException if there are none."""
    path = results_dir(project) / RESULTS_FILE
    if not path.is_file():
        raise click.ClickException(
            f"{path} not found: run `pcbkit check` first (it writes the results)"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise click.ClickException(f"{path} is not readable: {err}") from None
    if not isinstance(data.get("checks"), dict):
        raise click.ClickException(f"{path} has no checks: run `pcbkit check` again")
    return data


def summary_line(data: dict[str, Any]) -> str:
    """Return the run's counts the way pytest prints them: "4 failed, 138 passed"."""
    counts = data.get("counts", {})
    order = ("failed", "error", "passed", "skipped")
    parts = [f"{counts[k]} {k}" for k in order if counts.get(k)]
    seconds = data.get("seconds")
    tail = f" in {seconds:g}s" if seconds is not None else ""
    return (", ".join(parts) or "no checks") + tail


def _section(check_id: str, entry: dict[str, Any]) -> str:
    """Return the heading a check is listed under."""
    where = check_id.partition("::")[0]
    if where.startswith(BUILTIN_PACKAGE + "."):
        return f"built-in: {entry.get('group', where.rsplit('.', 1)[-1])}"
    return where


def _cell(text: object) -> str:
    """Return text that is safe inside a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "/")


def _numbers(entry: dict[str, Any]) -> str:
    """Return a check's recorded numbers as one cell: ``key: value; key: value``."""
    return _cell("; ".join(f"{k}: {v}" for k, v in entry.get("numbers", {}).items()))


def render(data: dict[str, Any]) -> str:
    """Return the report as Markdown."""
    board = data.get("board", {})
    title = board.get("title", "")
    rev = board.get("rev", "")
    lines = ["# Design validation", ""]
    if title:
        lines += [f"{title}, rev {rev}", ""]
    lines += [f"`{summary_line(data)}`", ""]
    sections: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for check_id, entry in data["checks"].items():
        sections.setdefault(_section(check_id, entry), []).append((check_id, entry))
    for heading in sorted(sections, key=lambda h: (not h.startswith("built-in"), h)):
        lines += [f"## {heading}", "", "| Check | Result | Numbers |", "|---|---|---|"]
        for check_id, entry in sections[heading]:
            name = check_id.partition("::")[2]
            result = RESULT_WORDS.get(entry["outcome"], entry["outcome"])
            note = entry.get("reason") or entry.get("message") or ""
            if note:
                result += f": {_cell(note)[:200]}"
            lines.append(f"| {_cell(name)} | {result} | {_numbers(entry)} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_report(project: Project) -> tuple[Path, dict[str, Any]]:
    """Write the report next to the results; return its path and the results."""
    data = load_results(project)
    path = results_dir(project) / REPORT_FILE
    path.write_text(render(data), encoding="utf-8")
    return path, data
