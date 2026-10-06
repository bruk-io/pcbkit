"""Unit tests for pcbkit.fab.pcbway: names, notes, designators and the quote values.

The fab files are made up (tests/fab_files.py): a KiCad-shaped job file in a zip and a
BOM written by the real ``write_csv``. The code that reads them back is the real code.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from pcbkit.fab import pcbway
from pcbkit.fab.pcbway import QuoteError
from pcbkit.project import load_project
from tests.board_files import write_project
from tests.fab_files import (
    FAB_NAME,
    bom_line,
    job_file,
    write_fab_outputs,
    write_gerber_zip,
)

# --- names ---------------------------------------------------------------------------


def test_the_file_names_start_with_the_fab_name() -> None:
    names = pcbway.fab_names("Board_revB")
    assert names.bom_csv == "Board_revB_BOM.csv"
    assert names.bom_xlsx == "Board_revB_BOM.xlsx"
    assert names.centroid == "Board_revB_centroid.csv"
    assert names.gerber_zip == "Board_revB_gerbers.zip"


# --- the notes -----------------------------------------------------------------------


def test_notes_of_exactly_the_limit_pass() -> None:
    assert pcbway.NOTES_LIMIT == 600
    assert pcbway.check_notes("x" * 600) == 600


def test_notes_one_character_over_fail_with_the_count() -> None:
    with pytest.raises(QuoteError) as caught:
        pcbway.check_notes("x" * 601, "the notes in notes.txt")
    assert caught.value.message == (
        "the notes in notes.txt are 601 characters; PCBWay's order notes take 600 "
        "(1 too many)"
    )


def test_the_final_newline_an_editor_adds_is_not_counted() -> None:
    assert pcbway.notes_length("x" * 600 + "\n") == 600
    assert pcbway.notes_length("x" * 600 + "\n\n  \n") == 600


def test_a_line_break_counts_once_whether_the_file_has_crlf_or_lf() -> None:
    assert pcbway.notes_length("ab\ncd") == 5
    assert pcbway.notes_length("ab\r\ncd") == 5
    assert pcbway.notes_length("ab\rcd") == 5


def test_characters_are_counted_not_bytes() -> None:
    assert pcbway.check_notes("é" * 600) == 600  # 1200 bytes in UTF-8


def test_a_notes_file_is_read_and_measured(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("1. First.\n2. Second.\n", encoding="utf-8")
    assert pcbway.check_notes_file(path) == len("1. First.\n2. Second.")


def test_a_notes_file_one_over_the_limit_fails(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("x" * 601 + "\n", encoding="utf-8")
    with pytest.raises(QuoteError, match=r"notes\.txt are 601 characters.*1 too many"):
        pcbway.check_notes_file(path)


def test_a_missing_notes_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(QuoteError, match="cannot read the notes file"):
        pcbway.check_notes_file(tmp_path / "nope.txt")


def test_a_notes_file_that_is_not_text_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "notes.bin"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(QuoteError, match="not UTF-8 text"):
        pcbway.check_notes_file(path)


# --- designators ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("refs", "written"),
    [
        ([], ""),
        (["R1"], "R1"),
        (["R1", "R2"], "R1, R2"),  # two in a row stay as two
        (["R1", "R2", "R3"], "R1-R3"),  # three become a range
        (["R3", "R1", "R2"], "R1-R3"),  # in any order
        (["R9", "R10", "R11"], "R9-R11"),  # numbers are numbers
        (["R1", "R2", "R4", "R5", "R6"], "R1, R2, R4-R6"),  # a gap ends a run
        (["C3", "D4", "E5"], "C3, D4, E5"),  # letters never join
        (
            ["H1", "C7", "C8", "P2", "P3", "P4", "P5", "P8", "P9", "Q1", "P1"],
            "C7, C8, H1, P1-P5, P8, P9, Q1",
        ),
        (["TP_A", "TP_B", "TP_C"], "TP_A, TP_B, TP_C"),  # not letters and a number
        (["R01", "R02", "R03"], "R01-R03"),
        (["R1", "R1"], "R1, R1"),  # a repeat is not a run
    ],
)
def test_designators_are_written_as_pairs_and_ranges(
    refs: list[str], written: str
) -> None:
    assert pcbway.compress_designators(refs) == written


# --- reading the Gerber zip ----------------------------------------------------------


def test_the_board_values_are_read_from_the_job_file_and_drill_files(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "g.zip"
    write_gerber_zip(zip_path, plated=(0.3, 0.8), unplated=(3.2,))
    specs = pcbway.read_board_specs(zip_path)
    assert (specs.layers, specs.thickness_mm) == (2, 1.6)
    assert (specs.width_mm, specs.height_mm) == (30.0, 20.0)  # 30.1 less the stroke
    assert specs.copper_mm == (0.035, 0.035)
    assert specs.finish == "HAL lead-free"
    assert specs.min_track_mm == 0.2 and specs.min_space_mm == 0.2
    assert specs.min_hole_mm == 0.3


def test_the_smallest_hole_may_be_an_unplated_one(tmp_path: Path) -> None:
    write_gerber_zip(tmp_path / "g.zip", plated=(0.8,), unplated=(0.6, 3.2))
    assert pcbway.read_board_specs(tmp_path / "g.zip").min_hole_mm == 0.6


def test_track_and_spacing_are_the_smallest_the_rules_say(tmp_path: Path) -> None:
    rules = [
        {"Layers": "Outer", "MinLineWidth": 0.25, "PadToPad": 0.3, "TrackToTrack": 0.2},
        {"Layers": "Inner", "MinLineWidth": 0.15, "TrackToRegion": 0.4},
    ]
    write_gerber_zip(tmp_path / "g.zip", job=job_file(rules=rules))
    specs = pcbway.read_board_specs(tmp_path / "g.zip")
    assert (specs.min_track_mm, specs.min_space_mm) == (0.15, 0.2)


def test_the_outline_stroke_comes_off_the_size_only_when_the_file_has_one(
    tmp_path: Path,
) -> None:
    write_gerber_zip(tmp_path / "a.zip", job=job_file(size=(50.2, 40.2)), stroke=0.2)
    a = pcbway.read_board_specs(tmp_path / "a.zip")
    assert (a.width_mm, a.height_mm) == (50.0, 40.0)
    write_gerber_zip(tmp_path / "b.zip", job=job_file(size=(50.2, 40.2)), stroke=None)
    b = pcbway.read_board_specs(tmp_path / "b.zip")
    assert (b.width_mm, b.height_mm) == (50.2, 40.2)


def test_values_the_files_do_not_carry_are_none(tmp_path: Path) -> None:
    write_gerber_zip(
        tmp_path / "g.zip",
        job=job_file(finish=None, rules=[]),
        plated=(),
        unplated=(),
    )
    specs = pcbway.read_board_specs(tmp_path / "g.zip")
    assert specs.finish == ""
    assert (specs.min_track_mm, specs.min_space_mm, specs.min_hole_mm) == (
        None,
        None,
        None,
    )


def test_the_two_copper_weights_of_a_board_are_both_read(tmp_path: Path) -> None:
    write_gerber_zip(tmp_path / "g.zip", job=job_file(copper=(0.035, 0.07)))
    assert pcbway.read_board_specs(tmp_path / "g.zip").copper_mm == (0.035, 0.07)


def test_a_zip_that_is_not_a_zip_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "g.zip").write_bytes(b"not a zip")
    with pytest.raises(QuoteError, match="cannot read"):
        pcbway.read_board_specs(tmp_path / "g.zip")


def test_a_zip_with_no_job_file_is_an_error(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "g.zip", "w") as archive:
        archive.writestr("a-F_Cu.gbr", "x")
    with pytest.raises(QuoteError, match="no .gbrjob job file"):
        pcbway.read_board_specs(tmp_path / "g.zip")


@pytest.mark.parametrize("content", ["not json", "{}", '{"GeneralSpecs": {}}'])
def test_a_job_file_pcbkit_cannot_read_is_an_error(
    tmp_path: Path, content: str
) -> None:
    with zipfile.ZipFile(tmp_path / "g.zip", "w") as archive:
        archive.writestr("a-job.gbrjob", content)
    with pytest.raises(
        QuoteError, match="job file in g.zip is not one pcbkit can read"
    ):
        pcbway.read_board_specs(tmp_path / "g.zip")


# --- the assembly numbers ------------------------------------------------------------


def lines_with_everything() -> list:
    """Return SMD lines (one a QFN), a BGA and some through-hole lines."""
    return [
        bom_line(1, 3, "C1,C2,C3"),
        bom_line(2, 2, "R1,R2"),
        bom_line(3, 1, "IC1", "VQFN-24-1EP_4x4mm_P0.5mm"),
        bom_line(4, 1, "IC2", "LFBGA-100_8x8mm"),
        bom_line(5, 1, "IC3", "LQFP-48_7x7mm_P0.5mm"),
        bom_line(6, 1, "IC4", "SOT-23"),
        bom_line(7, 2, "A1", "Socket_2x22", through_hole=True),
        bom_line(8, 2, "J1,J2", "Header_1x03", through_hole=True),
        bom_line(9, 1, "J3", "Header_1x04", through_hole=True),
    ]


def test_assembling_everything_counts_every_line_as_unique() -> None:
    a = pcbway.assembly_numbers(lines_with_everything(), self_solder_tht=False)
    assert a.unique_parts == 9
    assert a.smd_placements == 3 + 2 + 1 + 1 + 1 + 1
    assert a.through_hole_parts == 2 + 2 + 1
    assert a.through_hole_refs == ("A1", "J1", "J2", "J3")


def test_soldering_the_through_hole_parts_yourself_leaves_their_lines_out() -> None:
    a = pcbway.assembly_numbers(lines_with_everything(), self_solder_tht=True)
    assert a.unique_parts == 6
    assert a.smd_placements == 9  # unchanged
    assert a.through_hole_parts == 5 and a.self_solder_tht


def test_fine_pitch_parts_are_counted_by_package_family() -> None:
    a = pcbway.assembly_numbers(lines_with_everything(), self_solder_tht=True)
    assert a.fine_pitch_refs == ("IC1", "IC2", "IC3")  # VQFN, LFBGA, LQFP
    assert a.fine_pitch_parts == 3


def test_a_fine_pitch_part_counts_its_quantity() -> None:
    lines = [bom_line(1, 4, "IC1,IC2,IC3,IC4", "QFN-16_3x3mm")]
    a = pcbway.assembly_numbers(lines, self_solder_tht=False)
    assert a.fine_pitch_parts == 4 and a.fine_pitch_refs == ("IC1", "IC2", "IC3", "IC4")


def test_package_family_is_matched_whatever_the_case() -> None:
    lines = [bom_line(1, 1, "U1", "Package_DFN_QFN:qfn-16")]
    assert pcbway.assembly_numbers(lines, False).fine_pitch_parts == 1


def test_a_fine_pitch_footprint_among_hand_soldered_lines_is_not_assembled() -> None:
    lines = [bom_line(1, 1, "U1", "QFN-16", through_hole=True)]
    a = pcbway.assembly_numbers(lines, self_solder_tht=True)
    assert a.fine_pitch_parts == 0 and a.unique_parts == 0


# --- finding the files ---------------------------------------------------------------


def make_project(root: Path) -> None:
    """Write a board project (pcbkit.toml and a design.py) into ``root``."""
    write_project(root, "x = 1\n")


def test_the_fab_files_are_found_in_out_fab(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "out" / "fab", [bom_line(1, 1, "R1")])
    assert pcbway.find_fab_dir(load_project(tmp_path)) == tmp_path / "out" / "fab"


def test_the_fab_folder_is_the_fallback(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "fab", [bom_line(1, 1, "R1")])
    assert pcbway.find_fab_dir(load_project(tmp_path)) == tmp_path / "fab"


def test_out_fab_wins_over_fab(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "fab", [bom_line(1, 1, "R1")])
    write_fab_outputs(tmp_path / "out" / "fab", [bom_line(1, 1, "R1")])
    assert pcbway.find_fab_dir(load_project(tmp_path)) == tmp_path / "out" / "fab"


def test_no_fab_files_says_to_run_finalize_first(tmp_path: Path) -> None:
    make_project(tmp_path)
    with pytest.raises(
        QuoteError, match=r"My_Board_revA_gerbers\.zip.*pcbkit finalize"
    ):
        pcbway.find_fab_dir(load_project(tmp_path))


def test_a_folder_with_only_the_zip_does_not_count(tmp_path: Path) -> None:
    make_project(tmp_path)
    folder = tmp_path / "out" / "fab"
    folder.mkdir(parents=True)
    write_gerber_zip(folder / f"{FAB_NAME}_gerbers.zip")
    with pytest.raises(QuoteError, match="BOM.csv"):
        pcbway.find_fab_dir(load_project(tmp_path))


# --- the quote -----------------------------------------------------------------------


def quote_for(root: Path, **options: object) -> pcbway.Quote:
    """Build the quote for the project at ``root``."""
    return pcbway.build_quote(load_project(root), **options)  # type: ignore[arg-type]


def test_the_assembly_section_is_made_only_when_boards_are_to_be_assembled(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "out" / "fab", lines_with_everything())
    assert quote_for(tmp_path).assembly is None
    assert quote_for(tmp_path, assembled=2).assembly is not None


def test_the_quote_text_for_a_small_board(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "out" / "fab", lines_with_everything())
    quote = quote_for(
        tmp_path, assembled=2, fab_qty=5, self_solder_tht=True, notes_chars=120
    )
    assert pcbway.format_quote(quote) == "\n".join(
        [
            "PCBWay quote values for My_Board_revA (files in out/fab)",
            "",
            "Bare board",
            "  Layers               2",
            "  Board size           30 x 20 mm",
            "  Thickness            1.6 mm",
            "  Copper weight        1 oz (0.035 mm)",
            "  Min track / spacing  0.2 / 0.2 mm (7.9 / 7.9 mil)",
            "  Min hole size        0.3 mm (11.8 mil)",
            '  Surface finish       HASL lead-free (the board says "HAL lead-free")',
            "  Quantity             5",
            "",
            "Assembly, 2 boards (the counts are per board)",
            "  Unique parts         6 (surface-mount lines only)",
            "  SMD placements       9",
            "  BGA/QFP/QFN parts    3: IC1, IC2, IC3",
            "  Through-hole parts   0 for PCBWay (you solder them)",
            "  You solder           5 parts, 4 designators: A1, J1-J3",
            "",
            "Notes: 120 of 600 characters",
        ]
    )


def test_a_quote_without_self_soldering_lists_the_through_hole_parts_plainly(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "out" / "fab", lines_with_everything())
    text = pcbway.format_quote(quote_for(tmp_path, assembled=1))
    assert "Assembly, 1 board (the counts are per board)" in text
    assert "  Unique parts         9\n" in text
    assert "  Through-hole parts   5 parts, 4 designators: A1, J1-J3" in text
    assert "you solder" not in text.lower()
    assert "Quantity             not given (use --fab-qty N)" in text


def test_a_board_with_no_through_hole_parts_says_none(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "out" / "fab", [bom_line(1, 2, "R1,R2")])
    text = pcbway.format_quote(quote_for(tmp_path, assembled=1, self_solder_tht=True))
    assert "  You solder           none" in text


def test_a_finish_the_form_words_the_same_is_not_repeated(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(
        tmp_path / "out" / "fab",
        [bom_line(1, 1, "R1")],
        job=job_file(finish="ENIG"),
    )
    assert (
        "Surface finish       ENIG\n" in pcbway.format_quote(quote_for(tmp_path)) + "\n"
    )


def test_a_board_with_two_copper_weights_shows_both(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(
        tmp_path / "out" / "fab",
        [bom_line(1, 1, "R1")],
        job=job_file(copper=(0.035, 0.07)),
    )
    text = pcbway.format_quote(quote_for(tmp_path))
    assert "1 oz (0.035 mm), 2 oz (0.07 mm)" in text


def test_a_value_the_files_lack_is_said_so_not_made_up(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(
        tmp_path / "out" / "fab",
        [bom_line(1, 1, "R1")],
        job=job_file(finish=None, rules=[]),
        plated=(),
        unplated=(),
    )
    text = pcbway.format_quote(quote_for(tmp_path))
    assert "Min track / spacing  not in the job file" in text
    assert "Min hole size        not in the drill files" in text
    assert "Surface finish       not set in the board" in text


# --- the warnings --------------------------------------------------------------------


def test_copper_that_differs_from_the_stackup_setting_is_a_warning(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)  # [stackup] copper_mm defaults to 0.035
    write_fab_outputs(
        tmp_path / "out" / "fab",
        [bom_line(1, 1, "R1")],
        job=job_file(copper=(0.07, 0.07)),
    )
    quote = quote_for(tmp_path)
    assert len(quote.warnings) == 1
    assert "0.07, 0.07 mm copper but [stackup] copper_mm is 0.035" in quote.warnings[0]
    assert "WARNING: the Gerbers say" in pcbway.format_quote(quote)


def test_matching_copper_is_no_warning(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "out" / "fab", [bom_line(1, 1, "R1")])
    assert quote_for(tmp_path).warnings == ()


def test_fab_files_older_than_the_board_are_a_warning(tmp_path: Path) -> None:
    make_project(tmp_path)
    write_fab_outputs(tmp_path / "out" / "fab", [bom_line(1, 1, "R1")])
    board = tmp_path / "kicad" / "my_board.kicad_pcb"
    board.parent.mkdir()
    board.write_text("(kicad_pcb)", encoding="utf-8")
    zipped = tmp_path / "out" / "fab" / f"{FAB_NAME}_gerbers.zip"
    os.utime(zipped, (1_000_000_000, 1_000_000_000))
    [warning] = quote_for(tmp_path).warnings
    assert warning.startswith(
        f"{FAB_NAME}_gerbers.zip is older than my_board.kicad_pcb"
    )
    assert "pcbkit finalize" in warning


def test_fab_files_newer_than_the_board_are_no_warning(tmp_path: Path) -> None:
    make_project(tmp_path)
    board = tmp_path / "kicad" / "my_board.kicad_pcb"
    board.parent.mkdir()
    board.write_text("(kicad_pcb)", encoding="utf-8")
    os.utime(board, (1_000_000_000, 1_000_000_000))
    write_fab_outputs(tmp_path / "out" / "fab", [bom_line(1, 1, "R1")])
    assert quote_for(tmp_path).warnings == ()
