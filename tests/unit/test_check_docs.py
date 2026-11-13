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
        "test_enable_links.py",
        "test_esp32s3.py",
        "test_fab.py",
        "test_faults.py",
        "test_i2c_expected.py",
        "test_kicad.py",
        "test_outputs.py",
        "test_ports.py",
        "test_semis.py",
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


# --- table shapes ---------------------------------------------------------------

PROPOSED_HEADING = "### Proposed tables (validated, no built-in check yet)"


def proposed_block() -> str:
    """Return the doc's block of proposed tables, up to the next heading."""
    section = checks_section()
    start = section.index(PROPOSED_HEADING)
    end = section.find("\n### ", start + len(PROPOSED_HEADING))
    return section[start : end if end != -1 else len(section)]


def table_rows(text: str) -> dict[str, list[str]]:
    """Return the cells of each ``| `NAME` | ... |`` row of ``text``, by table name."""
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("| `"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows.setdefault(cells[0].strip("`"), cells)
    return rows


# The group whose block holds the row of each table that has its own check.
TABLE_GROUP = {
    "CAP_DERATE": "circuit",
    "PIN_NETS": "ports",
    "ENABLE_LINKS": "power",
    "FAULT_STATES": "power",
    "SEMI_VRATED": "power",
    "SEMI_NOT_JUDGED": "power",
    "SEMI_HIGH_Z": "power",
    "SUPPLY_NETS": "power",
    "I2C_EXPECTED": "bringup",
}


def table_row_names(text: str) -> list[str]:
    """Return the table name of every ``| `NAME` | ... |`` row of ``text``, in order."""
    return [
        line.strip().strip("|").split("|")[0].strip().strip("`")
        for line in text.splitlines()
        if line.startswith("| `")
    ]


def group_block(group: str, section: str | None = None) -> str:
    """Return the block for ``group``: from its ``**`group`**`` paragraph on.

    ``section`` defaults to the doc's checks section.
    """
    section = checks_section() if section is None else section
    found = re.search(rf"^\*\*`{group}`\*\*", section, re.MULTILINE)
    assert found, f"no block for the {group} group"
    after = re.search(r"^\*\*`", section[found.end() :], re.MULTILINE)
    end = found.end() + after.start() if after else len(section)
    return section[found.start() : end]


def test_every_built_table_has_one_row_and_it_is_in_its_groups_block() -> None:
    """Hold a built table to exactly one row outside the proposed block, in its group.

    A table row is found by name; an early correct copy elsewhere in the section used
    to shadow a wrong real row.
    """
    from pcbkit.check import tables

    built = sorted(
        n
        for n in tables.registry()
        if n not in tables.EXISTING and n not in tables.PROPOSED
    )
    assert built == sorted(TABLE_GROUP), "name each built table's group above"
    outside = checks_section().replace(proposed_block(), "")
    names = table_row_names(outside)
    for name in built:
        assert names.count(name) == 1, f"{name}: {names.count(name)} rows, want 1"
        assert name in table_row_names(group_block(TABLE_GROUP[name])), (
            f"{name} is not in the {TABLE_GROUP[name]} block"
        )


def test_the_row_finder_sees_a_duplicate_and_a_row_in_the_wrong_block() -> None:
    """Count two rows of one name, and find a row only in the block it is in."""
    text = "| `A` | x | y |\ntext\n| `B` | x |\n| `A` | q | r |\n"
    assert table_row_names(text) == ["A", "B", "A"]
    section = (
        "**`power`** (opt-in)\n\n| `B` | x | y |\n\n"
        "**`ports`** (opt-in)\n\n| `A` | x | y |\n"
    )
    assert table_row_names(group_block("power", section)) == ["B"]
    assert table_row_names(group_block("ports", section)) == ["A"]
    assert "A" not in table_row_names(group_block("power", section))


def test_every_registered_table_that_is_not_an_existing_one_has_a_generated_row() -> (
    None
):
    """Show ``describe(shape)`` word for word in the Shape cell of each new table.

    A table whose check is not written is in the proposed-tables block, and the block
    holds exactly those. A table that has moved to its engine module has its row in its
    group's block, three cells wide (name, shape, holds), and is no longer in the
    proposed block.
    """
    from pcbkit.check import tables

    new = {n: s for n, s in tables.registry().items() if n not in tables.EXISTING}
    assert new, "no new tables: the test would pass vacuously"
    proposed = table_rows(proposed_block())
    missing = sorted(set(tables.PROPOSED) - set(proposed))
    assert not missing, f"no row in the proposed-tables block for {missing}"
    stale = sorted(set(proposed) - set(tables.PROPOSED))
    assert not stale, f"rows in the proposed-tables block for tables built: {stale}"
    for name, shape in new.items():
        if name in proposed:
            cells = proposed[name]
        else:
            block = table_rows(group_block(TABLE_GROUP[name]))
            assert name in block, f"no row for {name} in the checks section"
            cells = block[name]
        assert len(cells) == 3, f"{name}: a row is name | shape | read by"
        assert cells[1] == f"`{tables.describe(shape)}`", name


def test_every_existing_table_with_a_shape_is_named_in_the_docs() -> None:
    """Keep the shapes of today's tables to names the docs section already lists."""
    from pcbkit.check import tables

    section = checks_section()
    missing = [
        name
        for name in tables.EXISTING
        if not re.search(rf"`[^`]*\b{re.escape(name)}\b[^`]*`", section)
    ]
    assert not missing, f"shapes for tables the docs never mention: {missing}"


def test_the_docs_have_a_table_shapes_paragraph_that_names_the_command() -> None:
    """Explain the shapes and point at ``pcbkit specs``."""
    section = checks_section()
    assert "### Table shapes" in section
    assert "`pcbkit specs`" in section


def test_the_docs_say_what_a_pad_shape_cannot_tell_and_give_a_number() -> None:
    """Say that a pin name passes the shape, and give the example error a number."""
    section = checks_section()
    assert "cannot tell a pin name from a pad label" in section
    assert "the check that reads the table reports a" in section.replace("\n", " ")
    assert "got ('U2', 5)" in section
    assert "'EN')" not in section


def test_troubleshooting_has_a_bad_table_row_beside_missing_spec() -> None:
    """List ``bad-table`` in the table of messages, with a section it links to."""
    text = (ROOT / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    missing = next(i for i, line in enumerate(lines) if "`missing-spec`" in line)
    bad = next(i for i, line in enumerate(lines) if "`bad-table`" in line)
    assert bad == missing + 1
    assert "(#a-table-in-specspy-has-the-wrong-shape)" in lines[bad]
    assert "\n## A table in specs.py has the wrong shape\n" in text
