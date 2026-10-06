"""Unit tests that keep docs/project-interface.md in step with the code it describes."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from pcbkit import project
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
    for line in doc_text().splitlines():
        match = re.match(r"\| `(\w+\.\w+)` \|", line)
        if match:
            found[match.group(1)] = [
                cell.strip() for cell in line.strip("|").split("|")
            ]
    return found


def code_blocks(language: str) -> list[str]:
    """Return the fenced code blocks of one language, in order."""
    return re.findall(rf"```{language}\n(.*?)\n```", doc_text(), flags=re.DOTALL)


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
    api_listing = code_blocks("python")[0]
    for helper in HELPERS:
        assert re.search(rf"^{helper}\(", api_listing, flags=re.MULTILINE), helper


def test_the_example_routing_py_runs_against_the_documented_helpers() -> None:
    """Execute the example and call it with helpers shaped as the docs say."""
    namespace: dict[str, Any] = {}
    exec(code_blocks("python")[-1], namespace)
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
