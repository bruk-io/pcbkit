#!/usr/bin/env python3
"""Say whether a board project's fab files and checks are current, before an order.

An order is paid for, and a file made before the last change to the board would be paid
for too. Run this from the project folder (or anywhere inside it). It answers from the
files alone: it needs no pcbkit, no KiCad and no network, only a ``python3``.

It reports a problem when

* the Gerber zip, the BOM or the centroid is missing from ``out/fab/`` (or ``fab/``, the
  older place), or older than any source of the board: ``pcbkit.toml``, every Python
  module of the project except the ones that only the checks read, ``footprints/`` and
  ``golden/``;
* ``out/checks/results.json`` is missing, older than any of those sources or than the
  checks' own inputs (``specs.py``, ``circuits.py``, ``checks/``), or records a failed
  check or a non-zero exit;
* the results are incomplete. ``pcbkit check -k EXPR`` writes a results file with only
  the checks it ran, and an exit status of 0 when those passed, so the file is read for
  what it lacks: a check group the project switched on that has no result at all, and a
  check of the project's own (``checks/test_*.py``, read as Python and never run) that
  has none.

What it cannot see: a ``-k`` run that leaves a result in every group and for every
project check (``-k "not drc"``, say) looks complete, because the results file does not
say what was deselected. Recording the expression, or the count of deselected checks, is
pcbkit's to do (pcbkit/check/plugin.py), and this script should then refuse such a run.
Nor does it look for a check the module does not spell out as ``def test...`` (a
``unittest.TestCase`` class, a function made while the module loads).

Exit codes: 0 every file is current, the checks passed and none of the above is
missing, 1 at least one problem (each is printed, with what to run), 2 no board
project was found.

Standard library only; it runs under whichever ``python3`` is on PATH, 3.9 included.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

MARKER = "pcbkit.toml"
# Python modules that only the checks read: changing one cannot change a fab file.
# mutants.py is not an input of a check run either, only of `pcbkit mutants`.
CHECK_ONLY = ("specs.py", "circuits.py", "mutants.py")
CHECK_INPUTS = ("specs.py", "circuits.py")
# Folders that are never sources of the board: generated, or the user's own notes.
SKIP_DIRS = ("checks", "archive", "kicad", "out", "fab", "__pycache__")
# The three files an order needs, by the suffix finalize gives them.
FAB_FILES = ("_gerbers.zip", "_BOM.csv", "_centroid.csv")
# What a line about missing results says to do, and how many checks it names.
PARTIAL = "the last run was partial (pcbkit check -k?): run pcbkit check with no -k"
SHOWN = 3


def find_project(start: Path) -> Path | None:
    """Return the nearest folder at or above ``start`` that holds a pcbkit.toml."""
    for folder in (start, *start.parents):
        if (folder / MARKER).is_file():
            return folder
    return None


def source_files(root: Path) -> list[Path]:
    """Return every file whose change can change the board or the files made from it."""
    found = [root / MARKER]
    for folder, names, files in os.walk(root):
        here = Path(folder)
        names[:] = [n for n in names if not n.startswith(".") and n not in SKIP_DIRS]
        relative = here.relative_to(root).parts
        if relative[:1] in (("footprints",), ("golden",)):
            found += [here / name for name in files]  # every file counts there
            continue
        for name in files:
            check_only = not relative and name in CHECK_ONLY
            if name.endswith(".py") and not check_only:
                found.append(here / name)
    return [p for p in found if p.is_file()]


def check_inputs(root: Path) -> list[Path]:
    """Return what a check run reads beyond the board's sources."""
    found = [root / name for name in CHECK_INPUTS]
    found += sorted((root / "checks").rglob("*.py"))
    return [p for p in found if p.is_file()]


def newest(paths: list[Path]) -> Path:
    """Return the path with the latest modification time."""
    return max(paths, key=lambda p: p.stat().st_mtime)


def fab_folder(root: Path) -> Path:
    """Return ``out/fab``, or ``fab`` where only the older place exists."""
    return root / "out" / "fab" if (root / "out" / "fab").is_dir() else root / "fab"


def check_modules(root: Path) -> list[Path]:
    """Return the project's check modules: ``test_*.py`` below ``checks/``, in order.

    That is all ``pcbkit check`` collects there (its pytest.ini says ``python_files =
    test_*.py``): pytest does not look inside hidden folders, and it passes over a link
    that leads nowhere. A folder that is a link is not entered.
    """
    found = []
    for folder, names, files in os.walk(root / "checks"):
        names[:] = sorted(
            n for n in names if not n.startswith(".") and n != "__pycache__"
        )
        found += [
            Path(folder) / name
            for name in sorted(files)
            if name.startswith("test_")
            and name.endswith(".py")
            and (Path(folder) / name).is_file()
        ]
    return found


def is_fixture(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if ``func`` is a fixture: ``@fixture`` or ``@pytest.fixture``."""
    for decorator in func.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else getattr(target, "id", "")
        )
        if name == "fixture":
            return True
    return False


def collected(node: ast.AST, prefix: str = "") -> list[str]:
    """Return what pytest collects below ``node``, as ``name`` or ``Class::name``.

    A function named ``test...`` at the top of a module or inside a class named
    ``Test...``, in an ``if`` or ``try`` too. A function inside a function is not one,
    and neither is a fixture that happens to be called ``test_...``.
    """
    found: list[str] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name.startswith("test") and not is_fixture(child):
                found.append(prefix + child.name)
        elif isinstance(child, ast.ClassDef):
            if child.name.startswith("Test"):
                found += collected(child, f"{prefix}{child.name}::")
        else:
            found += collected(child, prefix)
    return found


def result_prefix(path: Path, root: Path) -> str:
    """Return the file part of the results key of a check in ``path``.

    The check plugin follows links, then writes the path of the module below the
    project, or only the name of the file for a module that really lives outside it.
    """
    real = path.resolve()
    try:
        return real.relative_to(root.resolve()).as_posix()
    except ValueError:
        return real.name


def project_checks(root: Path) -> tuple[list[str], list[str]]:
    """Return the id of each check of the project's own, and a line per bad module.

    An id is the key of the results file without the ``[param]`` pytest adds to a
    parametrised check: ``checks/test_x.py::test_y``, or
    ``checks/test_x.py::TestZ::test_y`` for a method. The modules are parsed and never
    run.
    """
    ids: list[str] = []
    unreadable: list[str] = []
    for path in check_modules(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, SyntaxError) as err:
            unreadable.append(
                f"UNREADABLE: {path.relative_to(root).as_posix()}: cannot be read as "
                f"Python ({type(err).__name__}): fix it, then run pcbkit check"
            )
            continue
        prefix = result_prefix(path, root)
        ids += [f"{prefix}::{name}" for name in collected(tree)]
    return list(dict.fromkeys(ids)), unreadable


def has_result(check: str, keys: list[str]) -> bool:
    """Return True if a results key is ``check``, or ``check[...]`` for a parameter."""
    return any(key == check or key.startswith(check + "[") for key in keys)


def listed(names: list[str]) -> str:
    """Return ``names`` as one phrase: the first few, then how many more."""
    if len(names) <= SHOWN:
        return ", ".join(names)
    return f"{', '.join(names[:SHOWN])} and {len(names) - SHOWN} more"


def parse_results(text: str) -> tuple[int, int, list[str], list[str], set[str]]:
    """Return what the gate reads from a results file.

    That is the number of failed and errored checks, the exit status, the check groups
    the project switched on, the id of each check that has a result, and the groups
    those checks belong to. Raise ValueError, KeyError, TypeError or AttributeError for
    text that is not a results file as the check plugin writes it, which has always the
    ``groups`` and a ``checks`` table with a ``group`` for every entry.
    """
    data = json.loads(text)
    counts = data["counts"]
    bad = counts.get("failed", 0) + counts.get("error", 0)
    groups = data["groups"]
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise TypeError("groups is not a list of names")
    entries = data["checks"]
    ran = {entry["group"] for entry in entries.values()}
    return bad, data["exit_status"], groups, list(entries), ran


def incomplete(
    root: Path, groups: list[str], keys: list[str], ran: set[str]
) -> list[str]:
    """Return the lines for check groups and for project checks that have no result.

    ``keys`` are the ids of the checks that have a result and ``ran`` the groups of
    those; ``groups`` are the check groups the project switched on.
    """
    found = []
    missing = [group for group in groups if group not in ran]
    if missing:
        noun = "group" if len(missing) == 1 else "groups"
        found.append(
            f"INCOMPLETE: out/checks/results.json has no result for check {noun} "
            f"{', '.join(missing)}: {PARTIAL}"
        )
    expected, unreadable = project_checks(root)
    found += unreadable
    absent = [check for check in expected if not has_result(check, keys)]
    if absent:
        found.append(
            f"INCOMPLETE: out/checks/results.json has no result for {listed(absent)} "
            f"({len(absent)} of {len(expected)} project checks): {PARTIAL}"
        )
    return found


def problems(root: Path) -> list[str]:
    """Return one line per reason the order files or the checks are not current."""
    found = []
    latest = newest(source_files(root))
    when = latest.stat().st_mtime
    where = latest.relative_to(root)
    latest_check = newest([latest, *check_inputs(root)])
    when_checks = latest_check.stat().st_mtime
    where_checks = latest_check.relative_to(root)
    folder = fab_folder(root)
    for suffix in FAB_FILES:
        files = sorted(folder.glob(f"*{suffix}")) if folder.is_dir() else []
        if len(files) != 1:
            found.append(
                f"MISSING: {folder.relative_to(root)}/ has {len(files)} files "
                f"ending {suffix}, and an order needs exactly one: run pcbkit finalize"
            )
        elif files[0].stat().st_mtime < when:
            found.append(
                f"STALE: {files[0].relative_to(root)} is older than {where}: "
                "run pcbkit build, route, promote and finalize"
            )
    results = root / "out" / "checks" / "results.json"
    if not results.is_file():
        found.append("MISSING: out/checks/results.json: run pcbkit check")
        return found
    if results.stat().st_mtime < when_checks:
        found.append(
            f"STALE: the checks were run before {where_checks} changed: "
            "run pcbkit check"
        )
    try:
        bad, exit_status, groups, keys, ran = parse_results(
            results.read_text(encoding="utf-8")
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        found.append("UNREADABLE: out/checks/results.json: run pcbkit check again")
        return found
    if bad or exit_status != 0:
        found.append(
            f"FAILED: the last pcbkit check run did not pass ({bad} failed or "
            f"errored checks, exit status {exit_status}): read "
            "out/checks/results.json and fix the board"
        )
    found += partial(results.read_text(encoding="utf-8"))
    return found + incomplete(root, groups, keys, ran)


def partial(text: str) -> list[str]:
    """Return a line if the results say their run was cut short by -k, -m or a deselect.

    pcbkit writes a ``selection`` block whose ``complete`` is false after such a run.
    Results from a pcbkit too old to write it are judged by ``incomplete`` alone.
    """
    selection = json.loads(text).get("selection")
    if not isinstance(selection, dict) or selection.get("complete") is not False:
        return []
    keyword = selection.get("keyword") or ""
    how = f" by -k {keyword!r}" if keyword else ""
    left = selection.get("deselected", 0)
    return [
        f"PARTIAL: the last pcbkit check run was cut short{how} ({left} checks left "
        "out): run pcbkit check with no -k before ordering"
    ]


def main() -> int:
    """Print what is wrong with the project's order files; return the exit code."""
    root = find_project(Path.cwd().resolve())
    if root is None:
        print("no pcbkit.toml here or above: run this inside a board project")
        return 2
    found = problems(root)
    for line in found:
        print(line)
    if not found:
        print(f"OK: the fab files and checks of {root.name} are newer than its sources")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
