"""Unit tests for pcbkit.sch: the schematic generator, ERC parsing and `pcbkit sch`."""

from __future__ import annotations

import datetime
import shutil
import sys
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import click
import pytest
from click.testing import CliRunner, Result

from pcbkit import sch
from pcbkit.cli import cli
from pcbkit.design import Design, DesignError, load_design
from pcbkit.kicad import env
from pcbkit.kicad.env import Run
from pcbkit.kicad.sexp import find, findall, parse
from pcbkit.libs import ProjectLibs
from pcbkit.project import BoardConfig, Project, load_project
from tests.board_files import TOML, restored_imports, write_file
from tests.fake_machine import FakeMachine

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


# --- reading an ERC report --------------------------------------------------------

CLEAN_REPORT = """\
ERC report (2026-10-06T11:51:26, Encoding UTF8)
Report includes: Errors, Warnings

***** Sheet /

 ** ERC messages: 0  Errors 0  Warnings 0

 ** Ignored checks:
    - Global label only appears once in the schematic
    - Four connection points are joined together
"""

ERROR_REPORT = """\
ERC report (2026-10-06T16:29:10, Encoding UTF8)
Report includes: Errors, Warnings

***** Sheet /
[pin_to_pin]: Pins of type Power output and Power output are connected
    ; error
    @(33.02 mm, 363.22 mm): Symbol #FLG02 Pin 1 [Power output, Line]
    @(15.24 mm, 386.08 mm): Symbol #FLG99 Pin 1 [Power output, Line]
[pin_to_pin]: Pins of type Power output and Power output are connected
    ; error
    @(116.84 mm, 363.22 mm): Symbol #FLG98 Pin 1 [Power output, Line]
    @(15.24 mm, 386.08 mm): Symbol #FLG99 Pin 1 [Power output, Line]

 ** ERC messages: 2  Errors 2  Warnings 0

 ** Ignored checks:
    - Global label only appears once in the schematic
"""

WARNING_REPORT = (
    "ERC report (2026-10-06T16:25:42, Encoding UTF8)\n"
    "Report includes: Errors, Warnings\n"
    "\n"
    "***** Sheet /\n"
    "[footprint_link_issues]: The current configuration does not include "
    "the footprint library 'mylib'\n"
    "    ; warning\n"
    "    @(30.48 mm, 27.94 mm): Symbol J1 [Conn_01x02]\n"
    "\n"
    " ** ERC messages: 1  Errors 0  Warnings 1\n"
)


def test_a_clean_report_has_no_errors_warnings_or_findings() -> None:
    """Read the counts and find nothing to list."""
    assert sch.parse_erc_report(CLEAN_REPORT) == sch.ErcReport(0, 0, ())


def test_the_counts_and_every_finding_are_read_from_an_error_report() -> None:
    """Take the counts from the summary line and each finding with where it is."""
    report = sch.parse_erc_report(ERROR_REPORT)
    assert (report.errors, report.warnings) == (2, 0)
    assert len(report.violations) == 2
    first = report.violations[0]
    assert first.code == "pin_to_pin"
    assert first.severity == "error"
    assert first.message == "Pins of type Power output and Power output are connected"
    assert first.where == (
        "@(33.02 mm, 363.22 mm): Symbol #FLG02 Pin 1 [Power output, Line]",
        "@(15.24 mm, 386.08 mm): Symbol #FLG99 Pin 1 [Power output, Line]",
    )
    assert report.violations[1].where[0].startswith("@(116.84 mm")


def test_a_warning_is_counted_apart_from_errors() -> None:
    """Report a warning as a warning."""
    report = sch.parse_erc_report(WARNING_REPORT)
    assert (report.errors, report.warnings) == (0, 1)
    assert report.violations[0].severity == "warning"
    assert report.violations[0].code == "footprint_link_issues"


def test_a_report_with_no_summary_line_is_an_error() -> None:
    """Refuse a file that is not an ERC report rather than call it clean."""
    with pytest.raises(click.ClickException, match="no 'ERC messages' summary"):
        sch.parse_erc_report("***** Sheet /\n")
    with pytest.raises(click.ClickException, match="no 'ERC messages' summary"):
        sch.parse_erc_report("")


# --- running kicad-cli ------------------------------------------------------------

NETLIST = "(export (version E))\n"


class FakeKicadCli:
    """Stand in for kicad-cli's `sch erc` and `sch export netlist`.

    Each command writes the file kicad-cli would, with the text given, and records the
    arguments and which project files existed at the time it ran. Anything else (the
    `--version` that finds the tool) goes to the fake machine.
    """

    def __init__(self, machine: FakeMachine, erc_text: str = CLEAN_REPORT) -> None:
        self.machine = machine
        self.erc_text = erc_text
        self.returncode = 0
        self.write_files = True
        self.calls: list[list[str]] = []
        self.seen: dict[str, set[str]] = {}

    def __call__(self, args: list[str], timeout: float = 20.0) -> Run:
        """Answer one command the way ``pcbkit.kicad.env._run`` would."""
        words = list(args)
        if words[1:3] == ["sch", "erc"]:
            kind, out, text = "erc", Path(words[words.index("-o") + 1]), self.erc_text
        elif words[1:4] == ["sch", "export", "netlist"]:
            kind, out, text = "netlist", Path(words[words.index("-o") + 1]), NETLIST
        else:
            return self.machine.run(args, timeout)
        self.calls.append(words)
        self.seen[kind] = {p.name for p in out.parent.iterdir()}
        if self.write_files:
            out.write_text(text, encoding="utf-8")
        return Run(self.returncode, "wx assertion noise\n" if self.returncode else "")


def install_kicad(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> FakeKicadCli:
    """Put a kicad-cli and KiCad's symbol libraries on the fake machine."""
    shutil.copytree(STOCK, machine.linux_share / "symbols")
    (machine.linux_share / "footprints").mkdir(parents=True)
    machine.exe(
        machine.usr_bin / "kicad-cli", output="KiCad 10.0.6\n", on_path="kicad-cli"
    )
    fake = FakeKicadCli(machine)
    monkeypatch.setattr(env, "_run", fake)
    return fake


@pytest.fixture
def kicad(machine: FakeMachine, monkeypatch: pytest.MonkeyPatch) -> FakeKicadCli:
    """Return the fake kicad-cli, installed on the fake machine."""
    return install_kicad(machine, monkeypatch)


FOOTPRINTS_PY = f"""\
from pathlib import Path

from pcbkit.kicad.sexp import findall, parse

LIB = "proj"


def symbols():
    text = Path({str(PROJECT_LIBS["proj"])!r}).read_text(encoding="utf-8")
    return findall(parse(text), "symbol")
"""


def make_project(root: Path) -> Project:
    """Make a project of the golden design, with the golden board's own symbol."""
    write_file(root / "pcbkit.toml", TOML)
    shutil.copy(GOLDEN / "design.py", root / "design.py")
    write_file(root / "footprints.py", FOOTPRINTS_PY)
    return load_project(root)


def test_erc_is_run_on_the_schematic_and_the_report_is_read(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Pass kicad-cli the report and schematic paths, and parse what it wrote."""
    kicad.erc_text = ERROR_REPORT
    schematic = tmp_path / "x.kicad_sch"
    report = tmp_path / "erc.rpt"
    result = sch.run_erc("kicad-cli", schematic, report)
    assert kicad.calls == [
        ["kicad-cli", "sch", "erc", "-o", str(report), str(schematic)]
    ]
    assert (result.errors, result.warnings) == (2, 0)


def test_a_stale_report_is_not_mistaken_for_a_new_one(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Delete the old report first, so a failed run cannot pass on yesterday's."""
    report = tmp_path / "erc.rpt"
    report.write_text(CLEAN_REPORT)
    kicad.write_files = False
    with pytest.raises(click.ClickException, match="could not run ERC"):
        sch.run_erc("kicad-cli", tmp_path / "x.kicad_sch", report)


def test_a_failing_erc_run_shows_what_kicad_cli_said(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Report the exit status and kicad-cli's own words."""
    kicad.returncode = 3
    with pytest.raises(click.ClickException, match="wx assertion noise"):
        sch.run_erc("kicad-cli", tmp_path / "x.kicad_sch", tmp_path / "erc.rpt")


def test_the_netlist_is_exported_in_kicad_s_expression_format(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Ask for the kicadsexpr format and write where told."""
    net = tmp_path / "board.net"
    sch.export_netlist("kicad-cli", tmp_path / "x.kicad_sch", net)
    assert kicad.calls == [
        [
            "kicad-cli",
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "-o",
            str(net),
            str(tmp_path / "x.kicad_sch"),
        ]
    ]
    assert net.read_text() == NETLIST


def test_a_netlist_export_that_writes_nothing_is_an_error(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Fail when the file is not there afterwards, whatever the exit status."""
    kicad.write_files = False
    with pytest.raises(click.ClickException, match="could not export the netlist"):
        sch.export_netlist("kicad-cli", tmp_path / "x.kicad_sch", tmp_path / "b.net")


# --- build_schematic --------------------------------------------------------------


def test_build_writes_the_libraries_the_schematic_the_report_and_the_netlist(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Make every file of the sch step in kicad/ and report what was made."""
    result = sch.build_schematic(make_project(tmp_path), DATE)
    out = tmp_path / "kicad"
    assert result.schematic == out / "my_board.kicad_sch"
    assert result.netlist == out / "my_board.net"
    assert result.erc_report == out / "erc.rpt"
    assert result.parts == 9
    assert result.libs.nickname == "proj"
    assert (result.erc.errors, result.erc.warnings) == (0, 0)
    assert sorted(p.name for p in out.iterdir()) == [
        "erc.rpt",
        "fp-lib-table",
        "my_board.kicad_pro",
        "my_board.kicad_sch",
        "my_board.net",
        "proj.kicad_sym",
        "sym-lib-table",
    ]


def test_the_schematic_draws_the_design_with_the_projects_own_symbol(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Find the project library's symbol in the schematic, and nowhere else's."""
    sch.build_schematic(make_project(tmp_path), DATE)
    tree = parse((tmp_path / "kicad" / "my_board.kicad_sch").read_text())
    names = [s[1] for s in findall(find(tree, "lib_symbols"), "symbol")]
    assert "proj:Probe" in names
    assert find(find(tree, "title_block"), "title") == ["title", "My Board"]


def test_erc_sees_the_library_tables_and_project_file_already_written(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Write the tables and project file first: kicad-cli ignores the tables without."""
    sch.build_schematic(make_project(tmp_path), DATE)
    assert {"sym-lib-table", "fp-lib-table", "my_board.kicad_pro"} <= kicad.seen["erc"]
    assert "my_board.kicad_sch" in kicad.seen["erc"]
    assert {"my_board.kicad_sch", "erc.rpt"} <= kicad.seen["netlist"]


def test_erc_errors_do_not_stop_the_netlist(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Export the netlist anyway and let the caller decide what the counts mean."""
    kicad.erc_text = ERROR_REPORT
    result = sch.build_schematic(make_project(tmp_path), DATE)
    assert result.erc.errors == 2
    assert result.netlist.is_file()


def test_a_missing_kicad_cli_is_reported_before_anything_is_written(
    machine: FakeMachine, tmp_path: Path
) -> None:
    """Say so and point at doctor, leaving kicad/ alone."""
    project_ = make_project(tmp_path)
    with pytest.raises(click.ClickException, match="kicad-cli not found.*doctor"):
        sch.build_schematic(project_)
    assert not (tmp_path / "kicad").exists()


def test_missing_kicad_libraries_are_reported(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail with the library message, not a traceback, when KiCad's share is absent."""
    machine.exe(
        machine.usr_bin / "kicad-cli", output="KiCad 10.0.6\n", on_path="kicad-cli"
    )
    with pytest.raises(click.ClickException, match="KiCad libraries not found"):
        sch.build_schematic(make_project(tmp_path))


def test_a_mistake_in_design_py_stops_the_build(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Fail on the design's own error before ERC is run."""
    project_ = make_project(tmp_path)
    (tmp_path / "design.py").write_text("X = 1\n")
    with pytest.raises(DesignError, match="defines no parts"):
        sch.build_schematic(project_)
    assert kicad.calls == []


# --- the command ------------------------------------------------------------------


def invoke_sch(root: Path, monkeypatch: pytest.MonkeyPatch) -> Result:
    """Run `pcbkit sch` with ``root`` as the current directory."""
    monkeypatch.chdir(root)
    return CliRunner().invoke(cli, ["sch"])


def test_a_clean_run_prints_each_step_and_exits_0(
    kicad: FakeKicadCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show libraries, schematic, ERC counts and netlist."""
    make_project(tmp_path)
    result = invoke_sch(tmp_path, monkeypatch)
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "libraries  proj: 1 symbol, 0 footprints",
        f"schematic  kicad/my_board.kicad_sch ({9} parts)",
        "ERC        0 errors, 0 warnings (kicad/erc.rpt)",
        "netlist    kicad/my_board.net",
    ]


def test_errors_are_listed_and_the_exit_code_is_1(
    kicad: FakeKicadCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """List each finding with where it is, still write the netlist, and exit 1."""
    kicad.erc_text = ERROR_REPORT
    make_project(tmp_path)
    result = invoke_sch(tmp_path, monkeypatch)
    assert result.exit_code == 1
    assert "ERC        2 errors, 0 warnings (kicad/erc.rpt)" in result.output
    assert (
        "  error [pin_to_pin] Pins of type Power output and Power output are connected"
        in result.output
    )
    assert "    @(33.02 mm, 363.22 mm): Symbol #FLG02 Pin 1" in result.output
    assert "netlist    kicad/my_board.net" in result.output


def test_warnings_are_listed_but_do_not_fail_the_run(
    kicad: FakeKicadCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 0 with a warning, and still show it."""
    kicad.erc_text = WARNING_REPORT
    make_project(tmp_path)
    result = invoke_sch(tmp_path, monkeypatch)
    assert result.exit_code == 0
    assert "ERC        0 errors, 1 warning (kicad/erc.rpt)" in result.output
    assert "  warning [footprint_link_issues]" in result.output


def test_outside_a_project_the_command_says_what_to_do(
    kicad: FakeKicadCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name the missing pcbkit.toml, with exit code 1."""
    result = invoke_sch(tmp_path, monkeypatch)
    assert result.exit_code == 1
    assert "no pcbkit.toml" in result.output


def test_a_design_mistake_is_a_message_not_a_traceback(
    kicad: FakeKicadCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print the design error with exit code 1."""
    make_project(tmp_path)
    (tmp_path / "design.py").write_text("X = 1\n")
    result = invoke_sch(tmp_path, monkeypatch)
    assert result.exit_code == 1
    assert "defines no parts" in result.output
    assert "Traceback" not in result.output


def test_the_command_does_not_need_pcbnew(
    kicad: FakeKicadCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run in a Python where pcbnew cannot be imported: sch is a tier 1 command."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    make_project(tmp_path)
    assert invoke_sch(tmp_path, monkeypatch).exit_code == 0


# --- the printed result -----------------------------------------------------------


def result_with(
    erc: sch.ErcReport, libs: ProjectLibs | None = None
) -> sch.SchematicResult:
    """Return a SchematicResult for a project rooted at /p, with the given ERC."""
    kicad_dir = Path("/p/kicad")
    return sch.SchematicResult(
        schematic=kicad_dir / "b.kicad_sch",
        netlist=kicad_dir / "b.net",
        erc_report=kicad_dir / "erc.rpt",
        parts=1,
        libs=libs or ProjectLibs("b"),
        erc=erc,
    )


def test_a_project_without_libraries_prints_no_libraries_line() -> None:
    """Leave the libraries line out when there is nothing of the project's own."""
    text = sch.format_result(result_with(sch.ErcReport(0, 0)), Path("/p"))
    assert text.splitlines() == [
        "schematic  kicad/b.kicad_sch (1 part)",
        "ERC        0 errors, 0 warnings (kicad/erc.rpt)",
        "netlist    kicad/b.net",
    ]


def test_counts_use_the_singular_for_one() -> None:
    """Write "1 error, 1 warning"."""
    text = sch.format_result(result_with(sch.ErcReport(1, 1)), Path("/p"))
    assert "ERC        1 error, 1 warning (kicad/erc.rpt)" in text


def test_only_the_first_findings_are_listed() -> None:
    """Stop at the limit and say how many more the report holds."""
    findings = tuple(sch.Violation(f"c{i}", "error", f"m{i}") for i in range(5))
    text = sch.format_result(result_with(sch.ErcReport(5, 0, findings)), Path("/p"), 3)
    assert "[c2] m2" in text
    assert "[c3]" not in text
    assert "  ... and 2 more in kicad/erc.rpt" in text


def test_a_path_outside_the_project_is_shown_in_full() -> None:
    """Fall back to the whole path when it is not under the root."""
    text = sch.format_result(result_with(sch.ErcReport(0, 0)), Path("/elsewhere"))
    assert "schematic  /p/kicad/b.kicad_sch" in text
