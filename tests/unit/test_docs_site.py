"""Unit tests that keep the docs site's pages in step with the code and the conventions.

The strict MkDocs build in CI catches broken links and pages left out of the nav. These
catch what it cannot: a command missing from the CLI reference, which lists each command
by hand, and typography the build does not look at.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pcbkit import cli

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
CLI_PAGE = DOCS / "cli.md"

# One mkdocs-click block per command: the function, then the name the page shows.
BLOCK = re.compile(
    r"^::: mkdocs-click\n"
    r"    :module: pcbkit\.cli\n"
    r"    :command: (?P<function>\w+)\n"
    r"    :prog_name: pcbkit (?P<name>[\w-]+)\n",
    re.MULTILINE,
)

# Every page a reader of the site or the repository sees.
PAGES = sorted(DOCS.glob("*.md")) + [ROOT / "README.md", ROOT / "CHANGELOG.md"]


def documented_commands() -> list[tuple[str, str]]:
    """Return (function, name) for each command block on the CLI page, in page order."""
    return [
        (m["function"], m["name"]) for m in BLOCK.finditer(CLI_PAGE.read_text("utf-8"))
    ]


def test_the_cli_page_documents_every_command_in_workflow_order() -> None:
    """List each command once, in the order `pcbkit --help` lists them."""
    names = [name for _, name in documented_commands()]
    assert names == list(cli.cli.commands)


def test_each_cli_block_names_the_function_of_its_command() -> None:
    """Point each block at the function registered under the name it shows."""
    for function, name in documented_commands():
        assert getattr(cli, function) is cli.cli.commands[name], (function, name)


def test_every_mkdocs_click_block_on_the_cli_page_is_well_formed() -> None:
    """Leave no block the pattern above cannot read, so none is skipped unseen."""
    text = CLI_PAGE.read_text("utf-8")
    assert text.count("::: mkdocs-click") == len(documented_commands())


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_every_page_uses_plain_hyphens(page: Path) -> None:
    """Keep to plain hyphens: no em dash and no en dash, as the conventions ask."""
    text = page.read_text("utf-8")
    found = sorted({char for char in text if char in "—–"})
    assert not found, f"{page.name} has {found}: use a plain hyphen or a colon"
