"""Keep docs/project-interface.md in step with what the built-in checks read.

Every name the built-in check modules and the ESP32-S3 pack read from the project's
``specs.py``, ``circuits.py`` and ``layout.py`` has to be in the checks section of the
doc, so a check cannot start asking for a number the doc never mentions. The names are
found by reading the modules' syntax trees, not by a list kept here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "project-interface.md"
CHECKS = ROOT / "pcbkit" / "check"
SOURCES = sorted((CHECKS / "builtin").glob("test_*.py")) + [
    CHECKS / "packs" / "esp32s3.py"
]
HEADING = "## Checks: specs.py, circuits.py, checks/ and mutants.py"
MODULES = ("specs", "circuits", "layout")


def checks_section() -> str:
    """Return the doc's section on checks, up to the next level-2 heading."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(HEADING)
    end = text.find("\n## ", start + len(HEADING))
    return text[start : end if end != -1 else len(text)]


def names_read(path: Path) -> set[tuple[str, str]]:
    """Return ``(module, name)`` for each project name a source file reads.

    Found as ``specs.NAME``, ``getattr(specs, "NAME", ...)``, ``"NAME" in specs`` and,
    in the pack, ``pick("NAME", ...)`` (which is ``getattr`` on the project's specs).
    """
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in MODULES
        ):
            found.add((node.value.id, node.attr))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            args = node.args
            if (
                node.func.id == "getattr"
                and len(args) >= 2
                and isinstance(args[0], ast.Name)
                and args[0].id in MODULES
                and isinstance(args[1], ast.Constant)
            ):
                found.add((args[0].id, args[1].value))
            if node.func.id == "pick" and args and isinstance(args[0], ast.Constant):
                found.add(("specs", args[0].value))
        elif (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id in MODULES
        ):
            found.add((node.comparators[0].id, node.left.value))
    return found


def test_the_modules_are_found() -> None:
    """Find the six built-in modules and the pack, so the test cannot pass vacuously."""
    assert [p.name for p in SOURCES] == [
        "test_circuit.py",
        "test_copper.py",
        "test_esp32s3.py",
        "test_fab.py",
        "test_kicad.py",
        "test_outputs.py",
        "esp32s3.py",
    ]


def test_the_name_finder_finds_what_it_should(tmp_path: Path) -> None:
    """Find a name in each way a check reads one, and nothing else."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'a = specs.ONE\nb = getattr(specs, "TWO", 0)\nc = "THREE" in specs\n'
        'd = pick("FOUR", 1)\ne = circuits.scenario\nf = layout.W\n'
        'g = other.NOT_THIS\nh = getattr(other, "NOR_THIS")\n',
        encoding="utf-8",
    )
    assert names_read(sample) == {
        ("specs", "ONE"),
        ("specs", "TWO"),
        ("specs", "THREE"),
        ("specs", "FOUR"),
        ("circuits", "scenario"),
        ("layout", "W"),
    }


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_every_name_a_built_in_module_reads_is_in_the_docs(source: Path) -> None:
    """Fail naming each project name the module reads that the doc does not mention."""
    section = checks_section()
    missing = []
    for module, name in sorted(names_read(source)):
        if module == "layout":
            documented = f"{module}.{name}" in section
        else:
            documented = re.search(rf"`[^`]*\b{re.escape(name)}\b[^`]*`", section)
        if not documented:
            missing.append(f"{module}.{name}")
    assert not missing, f"{source.name} reads {missing}: add them to {DOC.name}"
