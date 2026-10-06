"""Unit tests for pcbkit.sch: the schematic generator, ERC parsing and `pcbkit sch`."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import click
import pytest

from pcbkit import sch
from pcbkit.design import Design, DesignError, load_design
from pcbkit.kicad.sexp import find, findall, parse
from pcbkit.project import BoardConfig
from tests.board_files import restored_imports

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
STOCK = GOLDEN / "symbols"
PROJECT_LIBS = {"proj": GOLDEN / "proj.kicad_sym"}
BOARD = BoardConfig(
    stem="golden_board", title="Golden Board", rev="A", fab_name="Golden_Board_revA"
)
DATE = datetime.date(2026, 1, 2)


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep design modules and sys.path entries from outliving a test."""
    with restored_imports():
        yield


@pytest.fixture
def golden() -> Design:
    """Return the design in tests/fixtures/golden."""
    return load_design(GOLDEN / "design.py")


def make(
    design: Design,
    board: BoardConfig = BOARD,
    project_symbols: dict[str, Path] = PROJECT_LIBS,
) -> str:
    """Generate the golden board's schematic with the fixture libraries."""
    return sch.generate(design, board, STOCK, project_symbols, DATE)


# --- the golden schematic ---------------------------------------------------------


def test_the_golden_design_gives_the_golden_schematic(golden: Design) -> None:
    """Match expected.kicad_sch exactly.

    That file was made by the generator this module was moved from, run on the same
    design and libraries, so it pins the placement, the labels and every UUID.
    """
    expected = (GOLDEN / "expected.kicad_sch").read_text(encoding="utf-8")
    assert make(golden) == expected


def test_generating_twice_gives_the_same_text(golden: Design) -> None:
    """Start every schematic's UUID counts afresh, whatever was generated before."""
    assert make(golden) == make(golden)


def test_the_root_sheet_uuid_is_the_one_boards_already_link_to() -> None:
    """Keep the root UUID: a board's footprints refer to schematic items by path."""
    root = str(uuid.uuid5(sch.NAMESPACE, "root"))
    assert root == "eab2f681-17da-52ed-8e62-7ff820634fe2"


def test_a_part_keeps_its_uuid_when_other_parts_change(golden: Design) -> None:
    """Derive a symbol's UUID from its reference alone."""
    fewer = replace(golden, parts=tuple(p for p in golden.parts if p["ref"] != "R2"))
    full = parse(make(golden))
    fewer_tree = parse(make(fewer))

    def symbol_uuid(tree: list, ref: str) -> str:
        for node in findall(tree, "symbol"):
            props = {p[1]: p[2] for p in findall(node, "property")}
            if props.get("Reference") == ref:
                return str(find(node, "uuid")[1])
        raise AssertionError(ref)

    assert symbol_uuid(full, "R1") == symbol_uuid(fewer_tree, "R1")
    assert symbol_uuid(full, "R1") == str(uuid.uuid5(sch.NAMESPACE, "sym:R1#0"))


def test_the_title_block_comes_from_the_board_and_the_design(golden: Design) -> None:
    """Take title and revision from the board, the rest from the design."""
    tree = parse(make(golden, replace(BOARD, title="Another Title", rev="B")))
    block = find(tree, "title_block")
    assert find(block, "title") == ["title", "Another Title"]
    assert find(block, "rev") == ["rev", "B"]
    assert find(block, "date") == ["date", "2026-01-02"]
    assert find(block, "company") == ["company", "Example Co"]
    comment = "Fixture for the schematic generator's golden test"
    assert find(block, "comment")[1:] == ["1", comment]


def test_company_and_comment_are_left_out_when_empty(golden: Design) -> None:
    """Write no company or comment line for a design that sets neither."""
    tree = parse(make(replace(golden, company="", comment="")))
    block = find(tree, "title_block")
    assert find(block, "company") is None
    assert find(block, "comment") is None


def test_the_date_defaults_to_today(golden: Design) -> None:
    """Stamp today's date when none is given."""
    before = datetime.date.today()
    text = sch.generate(golden, BOARD, STOCK, PROJECT_LIBS)
    after = datetime.date.today()
    block = find(parse(text), "title_block")
    assert find(block, "date")[1] in (before.isoformat(), after.isoformat())


def test_the_project_name_in_each_symbol_is_the_stem(golden: Design) -> None:
    """Name the KiCad project in every symbol's instances block."""
    tree = parse(make(golden, replace(BOARD, stem="other_stem")))
    names = {
        find(find(node, "instances"), "project")[1]
        for node in findall(tree, "symbol")
        if find(node, "instances")
    }
    assert names == {"other_stem"}


def test_block_widths_change_the_layout(golden: Design) -> None:
    """Wrap the parts of a block that is made narrower."""
    narrow = replace(golden, block_widths={**golden.block_widths, "Indicator": 40})
    assert make(narrow) != make(golden)


def test_a_block_with_no_width_gets_the_default(golden: Design) -> None:
    """Size a block that BLOCK_WIDTHS leaves out at DEFAULT_BLOCK_W."""
    bare = replace(golden, block_widths={})
    tree = parse(make(bare))
    rects = findall(tree, "rectangle")  # the block outlines; symbols' are nested
    widths = [float(find(r, "end")[1]) - float(find(r, "start")[1]) for r in rects]
    assert widths == [sch.DEFAULT_BLOCK_W] * 2


def test_block_headings_use_the_title_when_there_is_one(golden: Design) -> None:
    """Print BLOCK_TITLES' heading, and the block's name otherwise."""
    tree = parse(make(golden))
    headings = [t[1] for t in findall(tree, "text")]
    assert "Indicator" in headings
    assert "Flags and holes" in headings
    assert "Housekeeping" not in headings


def test_notes_follow_the_blocks_with_the_first_as_a_heading(golden: Design) -> None:
    """Draw the first note larger and bold, the rest smaller."""
    texts = {
        t[1]: find(find(t, "effects"), "font")
        for t in findall(parse(make(golden)), "text")
    }
    heading = texts["Golden board for the schematic generator"]
    assert find(heading, "size") == ["size", "2", "2"]
    assert find(heading, "bold") == ["bold", "yes"]
    second = texts["A second line of notes"]
    assert find(second, "size") == ["size", "1.6", "1.6"]
    assert find(second, "bold") is None


def test_symbols_are_embedded_once_in_the_order_first_used(golden: Design) -> None:
    """List each used symbol once in lib_symbols, named Library:Name."""
    tree = parse(make(golden))
    names = [s[1] for s in findall(find(tree, "lib_symbols"), "symbol")]
    assert names == [
        "Connector_Generic:Conn_01x02",
        "Device:R",
        "Device:R_Variant",
        "Device:LED",
        "proj:Probe",
        "power:PWR_FLAG",
        "Mechanical:MountingHole",
    ]


def test_a_pin_with_no_net_gets_a_no_connect_except_on_flags_and_holes(
    golden: Design,
) -> None:
    """Flag J1's open pin 2 and nothing else."""
    tree = parse(make(golden))
    assert len(findall(tree, "no_connect")) == 1


def test_a_dnp_part_is_marked_and_a_bom_less_part_is_out_of_the_bom(
    golden: Design,
) -> None:
    """Carry dnp and bom through to the symbol instance."""
    tree = parse(make(golden))
    by_ref = {}
    for node in findall(tree, "symbol"):
        if find(node, "lib_id"):
            props = {p[1]: p[2] for p in findall(node, "property")}
            by_ref[props["Reference"]] = node
    assert find(by_ref["D2"], "dnp") == ["dnp", "yes"]
    assert find(by_ref["D1"], "dnp") == ["dnp", "no"]
    assert find(by_ref["H1"], "in_bom") == ["in_bom", "no"]
    assert find(by_ref["J1"], "in_bom") == ["in_bom", "yes"]


# --- the pieces -------------------------------------------------------------------


def test_label_len_grows_with_the_text() -> None:
    """Give a label 1.1 mm a character plus 1.5 mm."""
    assert sch.label_len("") == pytest.approx(1.5)
    assert sch.label_len("GND") == pytest.approx(4.8)
    assert sch.label_len("VBAT_SENSE") == pytest.approx(12.5)


@pytest.mark.parametrize(
    ("value", "snapped"),
    [
        (0.0, 0.0),
        (1.2, 0.0),
        (1.27, 0.0),  # exactly half a grid step: round() goes to the even neighbour
        (1.3, 2.54),
        (3.8, 2.54),
        (3.9, 5.08),
        (-1.3, -2.54),
    ],
)
def test_snap_rounds_to_the_grid(value: float, snapped: float) -> None:
    """Round to the nearest multiple of 2.54 mm."""
    assert sch.snap(value) == pytest.approx(snapped)


def test_snap_takes_another_grid() -> None:
    """Round to a grid that is given."""
    assert sch.snap(7.0, 5.0) == 5.0


def test_font_is_regular_unless_bold() -> None:
    """Add the bold flag only when asked."""
    assert sch.font() == ["font", ["size", 1.27, 1.27]]
    assert sch.font(2.5, True) == ["font", ["size", 2.5, 2.5], ["bold", "yes"]]


def test_uuids_from_a_seed_count_up_and_a_new_factory_starts_again() -> None:
    """Give the same seed a new UUID each time, and each factory its own count."""
    first, second = sch.uid_factory(), sch.uid_factory()
    a0, a1, b0 = first("seed"), first("seed"), first("other")
    assert len({a0, a1, b0}) == 3
    assert a0 == str(uuid.uuid5(sch.NAMESPACE, "seed#0"))
    assert a1 == str(uuid.uuid5(sch.NAMESPACE, "seed#1"))
    assert second("seed") == a0


def test_the_seeds_of_unreferenced_items_are_the_historic_strings() -> None:
    """Keep the three seeds the original generator derived from its line numbers."""
    assert (sch.SEED_BLOCK_BOX, sch.SEED_BLOCK_TITLE, sch.SEED_NOTE) == (
        "auto:210",
        "auto:212",
        "auto:218",
    )


def test_pins_of_a_symbol_are_read_by_number() -> None:
    """Return each pin's position, angle, name and type."""
    libs = sch.SymbolLibs(STOCK, {})
    pins = sch.sym_pins(libs.flat_symbol("Device", "LED"))
    assert pins == {
        "1": {"x": -3.81, "y": 0.0, "ang": 0, "name": "K", "type": "passive"},
        "2": {"x": 3.81, "y": 0.0, "ang": 180, "name": "A", "type": "passive"},
    }


def test_the_body_box_spans_the_graphics_and_the_pins() -> None:
    """Include the pin ends in the bounding box, in library coordinates (y up)."""
    libs = sch.SymbolLibs(STOCK, {})
    assert sch.sym_body_bbox(libs.flat_symbol("Device", "R")) == (
        -1.016,
        -3.81,
        1.016,
        3.81,
    )
    assert sch.sym_body_bbox(libs.flat_symbol("Mechanical", "MountingHole")) == (
        0.0,
        -1.27,
        0.0,
        0.0,
    )


def test_a_symbol_with_nothing_drawn_gets_a_default_box() -> None:
    """Fall back to a 5.08 mm square for a symbol with no graphics and no pins."""
    assert sch.sym_body_bbox(["symbol", "Empty"]) == (-2.54, -2.54, 2.54, 2.54)


def test_a_symbol_that_extends_another_is_flattened() -> None:
    """Resolve `extends`: the child's own properties, the parent's drawing, renamed."""
    libs = sch.SymbolLibs(STOCK, {})
    flat = libs.flat_symbol("Device", "R_Variant")
    assert flat[:2] == ["symbol", "R_Variant"]
    assert find(flat, "extends") is None
    props = {p[1]: p[2] for p in findall(flat, "property")}
    assert props["Value"] == "R_Variant"
    assert props["Description"] == "Resistor, a symbol that extends R"
    assert [p[1] for p in findall(flat, "property")].count("Reference") == 1
    assert [s[1] for s in findall(flat, "symbol")] == ["R_Variant_0_1", "R_Variant_1_1"]
    assert flat[-1] == ["embedded_fonts", "no"]
    assert sch.sym_pins(flat) == sch.sym_pins(libs.flat_symbol("Device", "R"))


def test_flattening_leaves_the_parsed_library_alone() -> None:
    """Hand out copies, so one part's symbol cannot change another's."""
    libs = sch.SymbolLibs(STOCK, {})
    first = libs.flat_symbol("Device", "R")
    first[1] = "Renamed"
    assert libs.flat_symbol("Device", "R")[1] == "R"


def test_a_project_library_wins_over_a_stock_one_of_the_same_name() -> None:
    """Look in the project's own libraries first, as the first generator did."""
    libs = sch.SymbolLibs(STOCK, {"Device": GOLDEN / "proj.kicad_sym"})
    assert libs.path("Device") == GOLDEN / "proj.kicad_sym"
    assert libs.path("power") == STOCK / "power.kicad_sym"
    assert libs.flat_symbol("Device", "Probe")[1] == "Probe"


# --- mistakes in the design -------------------------------------------------------


def test_an_unknown_library_names_the_file_it_looked_for(golden: Design) -> None:
    """Say which library file is missing, and the project's own libraries."""
    with pytest.raises(click.ClickException) as err:
        make(golden, project_symbols={})
    assert "symbol library 'proj' not found" in err.value.message
    assert str(STOCK / "proj.kicad_sym") in err.value.message


def test_an_unknown_symbol_names_the_library_file(golden: Design) -> None:
    """Say which symbol is missing and where it was looked for."""
    bad = replace(golden, parts=(dict(golden.parts[0], sym="Device:Nope"),))
    with pytest.raises(click.ClickException) as err:
        make(replace(bad, block_order=(bad.parts[0]["block"],), block_widths={}))
    assert "symbol Device:Nope not found" in err.value.message


@pytest.mark.parametrize("sym", ["Device", "Device:", ":R", "a:b:c", ""])
def test_a_symbol_that_is_not_library_colon_name_is_an_error(
    golden: Design, sym: str
) -> None:
    """Name the part and what the symbol should look like."""
    bad = replace(golden, parts=(dict(golden.parts[0], sym=sym),))
    with pytest.raises(
        DesignError, match="J1: symbol .* should be written Library:Name"
    ):
        make(replace(bad, block_order=(bad.parts[0]["block"],), block_widths={}))


def test_a_pin_the_symbol_does_not_have_is_an_error(golden: Design) -> None:
    """Name the part, the pin and the pins the symbol has."""
    pins = {"1": "A", "9": "B"}
    bad = replace(golden, parts=(dict(golden.parts[0], pins=pins),))
    with pytest.raises(DesignError, match=r"J1: pin 9 not in symbol .*\['1', '2'\]"):
        make(replace(bad, block_order=(bad.parts[0]["block"],), block_widths={}))


# --- writing the file -------------------------------------------------------------


def test_write_schematic_writes_the_stem_dot_kicad_sch(
    golden: Design, tmp_path: Path
) -> None:
    """Make the folder and write UTF-8 text with Unix line ends."""
    path = sch.write_schematic(
        golden, BOARD, tmp_path / "kicad", STOCK, PROJECT_LIBS, DATE
    )
    assert path == tmp_path / "kicad" / "golden_board.kicad_sch"
    data = path.read_bytes()
    assert b"\r" not in data
    assert data.decode("utf-8") == make(golden)
