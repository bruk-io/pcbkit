"""Unit tests that keep docs/project-interface.md in step with the code it describes."""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from pcbkit import design, libs, project
from pcbkit.project import REQUIRED, SCHEMA, parse_config

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "project-interface.md"

# What the "Type" column says for each kind of rule.
TYPE_WORDS = {
    "text": "string",
    "name": "string",
    "whole": "integer",
    "number": "number",
    "choice": "string",
    "groups": "list of strings",
    "boxes": "list of boxes",
}

HOOKS = [
    "NETCLASSES",
    "design_rules",
    "prerouted",
    "keepouts",
    "gnd_links",
    "zones",
    "solid_pad_refs",
]
HELPERS = ["pad", "ppos", "track", "via", "keepout", "zone", "rect", "clear_spot"]


def doc_text() -> str:
    """Return the text of docs/project-interface.md."""
    return DOC_PATH.read_text(encoding="utf-8")


def toml_text(value: Any) -> str:
    """Return how a default is written in the docs: ``"pcbway"``, ``5.0``, ``[]``."""
    if isinstance(value, tuple):
        return json.dumps(list(value))
    return json.dumps(value) if isinstance(value, str) else repr(value)


def schema_rows() -> dict[str, list[str]]:
    """Return the schema table in the docs: ``section.key`` to its four cells."""
    found: dict[str, list[str]] = {}
    for line in section_text("pcbkit.toml").splitlines():
        match = re.match(r"\| `(\w+\.\w+)` \|", line)
        if match:
            found[match.group(1)] = [
                cell.strip() for cell in line.strip("|").split("|")
            ]
    return found


def section_text(heading: str) -> str:
    """Return the text under ``heading``, up to the next heading of the same level."""
    lines = doc_text().splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if re.fullmatch(rf"#+ {re.escape(heading)}", line)
    )
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if re.match(rf"#{{1,{level}}} ", lines[i])
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def code_blocks(language: str, heading: str | None = None) -> list[str]:
    """Return the fenced code blocks of one language, in order.

    Give a ``heading`` to read only the blocks under it; the sections of the doc that
    came later have python blocks of their own.
    """
    text = doc_text() if heading is None else section_text(heading)
    return re.findall(rf"```{language}\n(.*?)\n```", text, flags=re.DOTALL)


def test_the_schema_table_lists_exactly_the_schema_keys() -> None:
    """Leave out no key, and keep no row for a key that was removed."""
    wanted = {f"{section}.{key}" for section, keys in SCHEMA.items() for key in keys}
    assert set(schema_rows()) == wanted


@pytest.mark.parametrize(
    ("section", "key"),
    [(section, key) for section, keys in SCHEMA.items() for key in keys],
)
def test_each_schema_row_states_the_right_type_and_default(
    section: str, key: str
) -> None:
    """Document a key's type, and its default or "required"."""
    rule = SCHEMA[section][key]
    cells = schema_rows()[f"{section}.{key}"]
    assert cells[1] == TYPE_WORDS[rule.kind], (section, key)
    if rule.default is REQUIRED:
        assert cells[2] == "required", (section, key)
    else:
        assert cells[2] == f"`{toml_text(rule.default)}`", (section, key)


def test_the_example_pcbkit_toml_is_valid_and_complete() -> None:
    """Parse the example in the docs, and make sure it shows every key."""
    example = tomllib.loads(code_blocks("toml")[0])
    config = parse_config(example, source="docs/project-interface.md")
    assert config.board.stem == "my_board"
    shown = {f"{table}.{key}" for table, keys in example.items() for key in keys}
    assert shown == set(schema_rows())


def test_the_docs_list_every_group_profile_and_generated_folder() -> None:
    """Name each value the schema accepts and each folder pcbkit regenerates."""
    text = doc_text()
    for group in project.CHECK_GROUPS:
        assert f"`{group}`" in text, group
    for profile in project.FAB_PROFILES:
        assert f'`"{profile}"`' in text, profile
    for folder in project.GENERATED_DIRS:
        assert f"{folder}/" in text, folder


def test_the_docs_describe_every_routing_hook_and_helper() -> None:
    """Cover each hook in the table and each helper in the api listing."""
    text = doc_text()
    for hook in HOOKS:
        assert re.search(rf"^\| `{hook}`", text, flags=re.MULTILINE), hook
    api_listing = code_blocks("python", "The `api` argument")[0]
    for helper in HELPERS:
        assert re.search(rf"^{helper}\(", api_listing, flags=re.MULTILINE), helper


def test_the_example_routing_py_runs_against_the_documented_helpers() -> None:
    """Execute the example and call it with helpers shaped as the docs say."""
    namespace: dict[str, Any] = {}
    exec(code_blocks("python", "A minimal routing.py")[-1], namespace)
    assert len(namespace["NETCLASSES"]["Wide"]) == 5
    assert namespace["solid_pad_refs"] == {"CN1"}

    calls: list[tuple[str, tuple[Any, ...]]] = []

    class Api:
        @staticmethod
        def ppos(board: Any, ref: str, num: int) -> tuple[float, float]:
            calls.append(("ppos", (ref, num)))
            return (10.0, 5.0) if ref == "CN1" else (20.0, 15.0)

        @staticmethod
        def track(board: Any, pts: list[Any], width: float, netname: str) -> None:
            calls.append(("track", (pts, width, netname)))

    namespace["prerouted"](object(), Api)
    assert calls[-1] == (
        "track",
        ([(10.0, 5.0), (20.0, 5.0), (20.0, 15.0)], 1.0, "VIN"),
    )


def test_the_interface_doc_uses_the_generic_example_and_plain_hyphens() -> None:
    """Show the my-board example, and keep to plain hyphens (no em dashes)."""
    text = doc_text()
    assert "my-board" in text
    assert "\u2014" not in text


# --- design.py, footprints.py and what `pcbkit sch` writes -------------------------


def table_rows(heading: str) -> list[list[str]]:
    """Return each row of the tables under ``heading`` as cells, headers left out."""
    rows: list[list[str]] = []
    for line in section_text(heading).splitlines():
        if not line.startswith("|"):
            continue
        if set(line) <= set("|- :"):  # the divider under a header: drop that header
            rows.pop()
        else:
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def unquote(cell: str) -> str:
    """Return a table cell without its code backticks."""
    return cell.strip("`")


def written_form(fn: Any) -> str:
    """Return ``name(arg, arg="default", ...)`` for a function, as the docs write it."""
    args = []
    for name, param in inspect.signature(fn).parameters.items():
        if param.default is inspect.Parameter.empty:
            args.append(name)
        else:
            default = param.default
            text = json.dumps(default) if isinstance(default, str) else repr(default)
            args.append(f"{name}={text}")
    return f"{fn.__name__}({', '.join(args)})"


def test_the_documented_calls_are_the_real_signatures() -> None:
    """Write each call as the code defines it, defaults included."""
    listing = code_blocks("python", "The calls")[0].splitlines()
    assert listing == [
        written_form(getattr(design, n)) for n in ("part", "R", "C", "LED")
    ]


def test_the_footprint_table_lists_every_default_entry_and_nothing_else() -> None:
    """Document each FP entry with its footprint, exactly."""
    documented = {
        unquote(key): unquote(value)
        for key, value in table_rows("The calls")
        if re.fullmatch(r"`[A-Z0-9]+`", key)
    }
    assert documented == design.DEFAULT_FP


def test_the_example_design_loads_and_means_what_the_docs_say(tmp_path: Path) -> None:
    """Run the design.py in the docs through load_design."""
    path = tmp_path / "design.py"
    path.write_text(code_blocks("python", "design.py")[0], encoding="utf-8")
    loaded = design.load_design(path)
    assert [p["ref"] for p in loaded.parts] == ["J1", "R1", "D1"]
    assert loaded.block_order == ("Supply", "Indicator")
    assert loaded.notes == ("My board: 3V3 in, one LED.", "The LED draws about 5 mA.")
    assert loaded.parts[1]["mpn"] == "0603 330 1%"  # R's documented default
    assert loaded.parts[0]["fp"] == design.DEFAULT_FP["HDR2"]


def test_the_names_table_lists_every_name_load_design_reads() -> None:
    """Document BLOCK_ORDER, NOTES, BLOCK_WIDTHS, BLOCK_TITLES, COMPANY and COMMENT."""
    names = {unquote(row[0]) for row in table_rows("Names design.py can define")}
    assert names == set(design._READ_HERE)


def test_the_hooks_table_names_each_footprints_py_hook() -> None:
    """Document LIB, symbols() and footprints()."""
    names = {unquote(row[0]) for row in table_rows("footprints.py and footprints/")[:3]}
    assert names == {"LIB", "symbols()", "footprints()"}


def test_the_example_footprints_py_builds_the_libraries_the_docs_describe(
    tmp_path: Path,
) -> None:
    """Run the footprints.py in the docs through write_project_libs."""
    (tmp_path / "pcbkit.toml").write_text(
        '[board]\nstem = "my_board"\ntitle = "My Board"\nrev = "A"\n'
        'fab_name = "My_Board_revA"\n',
        encoding="utf-8",
    )
    source = code_blocks("python", "footprints.py and footprints/")[0]
    (tmp_path / "footprints.py").write_text(source, encoding="utf-8")
    saved = list(sys.path), dict(sys.modules)
    try:
        made = libs.write_project_libs(project.load_project(tmp_path))
    finally:
        sys.path[:] = saved[0]
        for name in set(sys.modules) - set(saved[1]):
            del sys.modules[name]
    assert made.nickname == "my_board"
    assert (tmp_path / "kicad" / "my_board.kicad_sym").is_file()
    assert (tmp_path / "kicad" / "my_board.pretty" / "Pad.kicad_mod").is_file()


def test_the_files_table_lists_what_pcbkit_sch_writes() -> None:
    """Name each file that `pcbkit sch` leaves in kicad/."""
    listed = {
        unquote(cell)
        for row in table_rows("footprints.py and footprints/")[3:]
        for cell in row[0].split(", ")
    }
    assert listed == {
        "<stem>.kicad_sch",
        "<LIB>.kicad_sym",
        "<LIB>.pretty/",
        "sym-lib-table",
        "fp-lib-table",
        "<stem>.kicad_pro",
        "erc.rpt",
        "<stem>.net",
    }
