"""Unit tests that keep the "Fab outputs" part of docs/project-interface.md true.

The bom.py example is run, and the tables of file names, bom.py names, default resistor
values and quote options are checked against the code they describe.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from pcbkit.design import Part
from pcbkit.fab import bom, docs, pcbway
from tests.board_files import restored_imports, write_file
from tests.unit.test_docs import code_blocks, section_text, table_rows, unquote


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Undo what loading a project's bom.py does to sys.path and sys.modules."""
    with restored_imports():
        yield


def part(ref: str, mpn: str, fp: str, value: str = "v") -> Part:
    """Return a made-up part."""
    return Part(
        ref=ref,
        sym="Conn",
        value=value,
        fp=fp,
        pins={},
        mfr="",
        mpn=mpn,
        desc=f"desc {ref}",
        block="B",
        dnp=False,
        bom=True,
    )


def test_the_example_bom_py_loads_and_does_what_the_docs_say(tmp_path: Path) -> None:
    source = code_blocks("python", "bom.py")[0]
    write_file(tmp_path / "bom.py", source)
    overrides = bom.load_overrides(tmp_path)
    assert overrides.not_in_bom == ("W1 is a wire pad. H1 is a mounting hole.",)

    header = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
    parts = [
        part("J5", "HDR 1x3 male", header, "North"),
        part("J6", "HDR 1x3 male", header, "South"),
        part("BT1", "2x clip", "Lib:Clip_18650"),
    ]
    socket, headers = bom.bom_lines(parts, {"J5", "J6", "BT1"}, overrides)
    # the MPN table: both headers are one line, bought as the real part
    assert (headers.refs, headers.mfr, headers.mpn) == (
        ("J5", "J6"),
        "Acme",
        "AC-HDR-1X3",
    )
    assert headers.desc == "Pin header 1x3 2.54mm vertical"
    assert headers.value == "Header 3-pin"  # the line hook
    # the reference table: one reference, two parts
    assert (socket.refs, socket.mpn, socket.qty) == (("BT1",), "AC-CLIP-18650", 2)
    assert socket.value == "v"  # the hook left it alone


def test_the_bom_py_names_table_lists_exactly_the_names_pcbkit_reads() -> None:
    names = {unquote(row[0]) for row in table_rows("bom.py")}
    assert names == set(bom.OVERRIDE_NAMES)


def test_the_docs_name_every_override_and_hook_field() -> None:
    text = section_text("bom.py")
    for name in (*bom.PART_FIELDS, *bom.LINE_FIELDS):
        assert f"`{name}`" in text, name
    group_fields = (
        "parts",
        "refs",
        "footprint",
        "mfr",
        "mpn",
        "value",
        "desc",
        "qty",
        "through_hole",
    )
    for name in group_fields:
        assert f"`{name}`" in text, name
    assert set(group_fields) == set(bom.Group.__dataclass_fields__) | {"refs"}


def test_the_resistor_values_in_the_docs_are_the_default_table() -> None:
    cells = table_rows("What the BOM lists")
    [values] = [row[0] for row in cells if row[0].startswith("`10`")]
    assert set(re.findall(r"`([^`]+)`", values)) == set(bom.RESISTOR_MPN)


def test_the_file_table_names_every_file_an_export_writes() -> None:
    text = section_text("What is written")
    fab = pcbway.fab_names("<fab_name>")
    for name in (fab.gerber_zip, fab.centroid):
        assert f"`out/fab/{name}`" in text, name
    assert f"`out/fab/{fab.bom_csv}` and `_BOM.xlsx`" in text
    names = docs.doc_names("<fab_name>")
    assert f"`out/docs/{names.schematic}`" in text
    assert f"`out/docs/{names.assembly_pdf}` and `.png`" in text
    assert names.assembly_png == names.assembly_pdf.replace(".pdf", ".png")
    assert f"`out/docs/{names.top_copper}` and `_bottom_copper.pdf`" in text
    assert names.bottom_copper == names.top_copper.replace("top", "bottom")
    for render in (names.render_iso, names.render_top, names.render_bottom):
        suffix = render.replace("<fab_name>", "")
        assert suffix in text.replace("`", ""), render


def test_the_quote_section_names_every_option_and_the_notes_limit() -> None:
    text = section_text("pcbkit quote")
    for option in ("--assembled", "--fab-qty", "--self-solder-tht", "--notes"):
        assert option in text, option
    assert str(pcbway.NOTES_LIMIT) in text
    assert "pcbkit finalize" in text


def test_the_quote_section_explains_each_form_value() -> None:
    first_cells = [row[0] for row in table_rows("pcbkit quote")]
    for value in (
        "Layers, thickness, finish, copper weight",
        "Board size",
        "Min track and spacing",
        "Min hole size",
        "Unique parts",
        "SMD placements",
        "BGA/QFP/QFN parts",
        "Through-hole parts",
    ):
        assert value in first_cells, value
