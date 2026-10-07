"""Unit tests that keep the compare and shots part of docs/project-interface.md true.

The page's example SHOTS is run through the real parser, the sample report is the real
formatter's output for the same numbers, and the defaults and limits it states are the
ones in the code and on the commands.
"""

from __future__ import annotations

import re
from typing import Any

import click

from pcbkit import compare, shots
from pcbkit.cli import cli
from tests.unit.test_docs import code_blocks, doc_text, section_text

SECTION = "Comparing boards and taking shots"


def option_defaults(command: str) -> dict[str, Any]:
    """Return a command's options by name, with their defaults."""
    found = {}
    for param in cli.commands[command].params:
        if isinstance(param, click.Option):
            found[param.opts[0]] = param.default
    return found


# --- pcbkit compare ---------------------------------------------------------------


def test_every_compare_option_is_documented_with_its_default() -> None:
    text = " ".join(section_text(SECTION).split())
    options = option_defaults("compare")
    assert set(options) == {"--json", "--piece-tol", "--fill-tol", "--top"}
    for name in options:
        assert f"`{name}" in text, name
    assert options["--piece-tol"] == compare.PIECE_TOLERANCE_MM2
    assert options["--fill-tol"] == compare.FILL_TOLERANCE_MM2
    assert options["--top"] == compare.DEFAULT_TOP
    assert f"`--piece-tol` (default {compare.PIECE_TOLERANCE_MM2:g})" in text
    assert f"`--fill-tol` (default {compare.FILL_TOLERANCE_MM2:g})" in text
    assert f"`--top` (default {compare.DEFAULT_TOP})" in text


def test_the_docs_state_the_length_tolerance_and_the_three_exit_codes() -> None:
    text = " ".join(section_text(SECTION).split())
    assert f"by more than {compare.LENGTH_TOLERANCE_MM:g} mm" in text
    assert (
        "**0** the boards match, **1** they differ, **2** a file is not a KiCad" in text
    )
    assert compare.CompareError("x").exit_code == 2


def test_the_sample_report_is_what_the_formatter_prints_for_those_numbers() -> None:
    """Rebuild the sample from its numbers and compare it with the page by line."""
    zones = {
        ("", "F.Cu", 6.0): 0.0,
        ("", "F.Cu", 18.0): 0.0,
        ("/GND", "B.Cu", 570.4): 542.9977,
    }
    board = compare.BoardSummary(compare.TrackStats(2, 14.025), 1, zones)
    none = compare.OneSidedCopper
    copper = [
        none("F.Cu", "old", 2.8875, 1, (compare.Piece(2.8875, 15.0, 10.0),)),
        none("F.Cu", "new", 3.13545, 1, (compare.Piece(3.13545, 15.0, 11.0),)),
        none("B.Cu", "old", 0.0, 0, ()),
        none("B.Cu", "new", 0.0, 0, ()),
    ]
    report = compare.build_report(
        "before.kicad_pcb",
        "after.kicad_pcb",
        board,
        board,
        copper,
        compare.Tolerances(),
    )
    sample = next(
        block
        for block in code_blocks("", SECTION)
        if block.startswith("pcbkit compare\n")
    )
    assert compare.format_report(report).splitlines() == sample.splitlines()


# --- pcbkit shots -----------------------------------------------------------------


def test_every_shots_option_is_documented() -> None:
    text = section_text(SECTION)
    assert set(option_defaults("shots")) == {
        "--out",
        "--pcb",
        "--region",
        "--no-render",
    }
    for name in option_defaults("shots"):
        assert name in text, name


def test_the_example_shots_is_valid_and_shows_both_sides_and_the_optional_width() -> (
    None
):
    namespace: dict[str, Any] = {}
    exec(code_blocks("python", "SHOTS in layout.py")[0], namespace)
    regions = shots.parse_regions(namespace["SHOTS"])
    assert {r.side for r in regions.values()} == {"top", "bottom"}
    assert {r.width_px for r in regions.values()} == {shots.REGION_WIDTH_PX, 2400}
    assert all(r.box is not None for r in regions.values())


def test_the_shots_limits_in_the_docs_are_the_ones_in_the_code() -> None:
    text = " ".join(section_text(SECTION).split())
    assert f"from {shots.MIN_WIDTH_PX} to {shots.MAX_WIDTH_PX}" in text
    assert f"The default is {shots.REGION_WIDTH_PX}" in text
    assert f"{shots.BOARD_WIDTH_PX} pixels wide" in text
    assert f"A name starting `{shots.RESERVED_PREFIX}`" in text
    for name in shots.BOARD_SHOTS:
        assert f"`{name}`" in text
    for view in shots.RENDER_VIEWS:
        assert f"render_{view.name}.png" in text


def test_the_layers_in_the_docs_are_the_ones_each_view_shows() -> None:
    text = " ".join(section_text(SECTION).split())
    for layer in shots.SVG_LAYERS["top"]:
        assert f"`{layer}`" in text, layer
    assert "the `B.` equivalents" in text
    assert shots.SVG_LAYERS["bottom"] == tuple(
        re.sub(r"^F\.", "B.", layer) for layer in shots.SVG_LAYERS["top"]
    )


def test_the_docs_say_where_a_boxs_origin_is_and_where_colours_come_from() -> None:
    """Keep the two conditions on a region's picture in the docs: not obvious ones."""
    text = " ".join(section_text(SECTION).split())
    assert "top-left corner of the bounding box of the board outline" in text
    assert "layout (0, 0) when the outline starts there" in text
    assert "colours are those of the PCB editor's colour theme" in text


def test_the_section_is_written_and_uses_plain_hyphens() -> None:
    text = section_text(SECTION)
    assert "To be written" not in text
    assert "—" not in doc_text()
