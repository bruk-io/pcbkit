"""Unit tests for the ERC and DRC report parsers, on real KiCad 10.0.6 report text.

The fixtures in tests/fixtures/reports were written by kicad-cli itself, on small
generic boards and schematics (nets /NET_A, /NET_B and /GND; parts R1 and R2), so the
parsers are tested on what KiCad really prints. The tests count each report's
"[category]" lines with a regex that shares nothing with the parser, the way route.sh
once did with grep, and compare the two.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from pcbkit.kicad.cli import (
    DrcReport,
    ErcReport,
    ReportError,
    parse_drc,
    parse_erc,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reports"
DRC_FILES = sorted(FIXTURES.glob("drc_*.rpt"))
ERC_FILES = sorted(FIXTURES.glob("erc_*.rpt"))


def text_of(name: str) -> str:
    """Return the text of one fixture report."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def drc_of(name: str) -> DrcReport:
    """Return one fixture parsed as a DRC report."""
    return parse_drc(text_of(name))


def erc_of(name: str) -> ErcReport:
    """Return one fixture parsed as an ERC report."""
    return parse_erc(text_of(name))


def entry_lines(text: str) -> Counter[str]:
    """Count the "[category]" lines of a report, the way route.sh's grep did."""
    return Counter(re.findall(r"^\[(\w+)\]", text, flags=re.MULTILINE))


def test_the_fixtures_are_all_there() -> None:
    """Guard the parametrised tests below against an empty glob."""
    assert len(DRC_FILES) >= 6
    assert len(ERC_FILES) >= 4


# --- DRC ---------------------------------------------------------------------------


def test_a_clean_drc_report_reads_as_three_zeros_under_the_printed_names() -> None:
    report = drc_of("drc_clean.rpt")
    assert report.counts == {
        "DRC violations": 0,
        "unconnected pads": 0,
        "Footprint errors": 0,
    }
    assert report.board == "my_board.kicad_pcb"
    assert report.clean
    assert report.violations == ()
    assert (report.drc_violations, report.unconnected, report.footprint_errors) == (
        0,
        0,
        0,
    )


def test_the_ignored_checks_list_is_not_read_as_entries() -> None:
    """Keep the "- Footprint has no courtyard defined" lines out of the entries."""
    text = text_of("drc_clean.rpt")
    assert "Footprint has no courtyard defined" in text
    assert drc_of("drc_clean.rpt").violations == ()


@pytest.mark.parametrize("path", DRC_FILES, ids=lambda p: p.name)
def test_every_drc_report_agrees_with_an_independent_count_of_its_entries(
    path: Path,
) -> None:
    text = path.read_text(encoding="utf-8")
    report = parse_drc(text)
    assert report.categories == dict(entry_lines(text))
    assert len(report.violations) == sum(report.counts.values())
    assert [v.category for v in report.violations] == re.findall(
        r"^\[(\w+)\]", text, flags=re.MULTILINE
    )
    assert all(len(v.positions) == len(v.items) >= 1 for v in report.violations)


def test_a_drc_entry_carries_its_rule_severity_section_positions_and_nets() -> None:
    report = drc_of("drc_unconnected_and_dangling.rpt")
    assert report.counts == {
        "DRC violations": 4,
        "unconnected pads": 4,
        "Footprint errors": 0,
    }
    assert report.categories == {
        "copper_edge_clearance": 1,
        "track_dangling": 3,
        "unconnected_items": 4,
    }
    edge, dangling = report.violations[0], report.violations[1]
    assert edge.category == "copper_edge_clearance"
    assert edge.message.startswith("Board edge clearance violation (")
    assert (edge.severity, edge.rule) == ("error", "board setup constraints edge")
    assert edge.section == "DRC violations"
    assert edge.positions == ((50.0, 50.0), (52.0, 50.1))
    assert edge.items == (
        "Segment on Edge.Cuts",
        "Track [/NET_A] on F.Cu, length 8.0000 mm",
    )
    assert edge.nets == ("/NET_A",)
    assert (dangling.severity, dangling.rule) == ("warning", "Local override")


def test_unconnected_entries_sit_in_their_own_section_and_list_a_net_once() -> None:
    report = drc_of("drc_unconnected_and_dangling.rpt")
    unconnected = [v for v in report.violations if v.category == "unconnected_items"]
    assert len(unconnected) == report.unconnected == 4
    assert {v.section for v in unconnected} == {"unconnected pads"}
    # Pad 1 of R1 and pad 1 of R2 are both on /NET_A: one net, not two.
    pads = [v for v in unconnected if "Pad 1 [/NET_A] of R2 on F.Cu" in v.items]
    assert len(pads) == 1
    assert pads[0].nets == ("/NET_A",)
    assert pads[0].positions == ((57.175, 58.0), (69.175, 58.0))


def test_footprint_errors_sit_under_their_heading_and_may_name_no_net() -> None:
    report = drc_of("drc_footprint_errors.rpt")
    assert report.footprint_errors == 6
    assert report.categories["footprint_symbol_mismatch"] == 2
    assert report.categories["net_conflict"] == 4
    mismatch = next(
        v for v in report.violations if v.category == "footprint_symbol_mismatch"
    )
    assert mismatch.section == "Footprint errors"
    assert mismatch.items == ("Footprint R1",)
    assert mismatch.nets == ()
    conflict = next(v for v in report.violations if v.category == "net_conflict")
    assert conflict.nets == ("/NET_A",)
    assert {v.section for v in report.violations} == {
        "DRC violations",
        "unconnected pads",
        "Footprint errors",
    }


def test_a_clearance_entry_names_both_nets_in_the_order_printed() -> None:
    report = drc_of("drc_clearance_and_holes.rpt")
    clearance = next(v for v in report.violations if v.category == "clearance")
    assert "actual 0.1000 mm" in clearance.message
    assert clearance.nets == ("/NET_B", "/NET_A")
    assert clearance.positions == ((54.0, 54.35), (54.0, 54.0))
    assert not report.clean


def test_a_pad_with_no_net_adds_no_net_name() -> None:
    """Skip the "<no net>" KiCad prints for a pad that belongs to no net."""
    report = drc_of("drc_pad_without_net.rpt")
    clearance = next(v for v in report.violations if v.category == "clearance")
    assert clearance.items[1] == "Pad 1 [<no net>] of R3 on F.Cu"
    assert clearance.nets == ("/NET_A",)


@pytest.mark.parametrize(
    "name",
    [
        "drc_unconnected_and_dangling_mils.rpt",
        "drc_unconnected_and_dangling_inches.rpt",
    ],
)
def test_positions_come_out_in_millimetres_whatever_unit_the_report_used(
    name: str,
) -> None:
    in_mm = drc_of("drc_unconnected_and_dangling.rpt")
    other = drc_of(name)
    assert other.counts == in_mm.counts
    assert other.categories == in_mm.categories
    for a, b in zip(in_mm.violations, other.violations):
        for (ax, ay), (bx, by) in zip(a.positions, b.positions):
            # The reports round to 0.01 mil and 0.0001 in (about 3 um).
            assert (ax, ay) == pytest.approx((bx, by), abs=0.005)
        assert a.nets == b.nets


# --- ERC ---------------------------------------------------------------------------


def test_a_clean_erc_report_keeps_its_summary_text_exactly_as_printed() -> None:
    report = erc_of("erc_clean.rpt")
    assert report.summary == "ERC messages: 0  Errors 0  Warnings 0"
    assert (report.messages, report.errors, report.warnings) == (0, 0, 0)
    assert report.clean
    assert report.violations == ()


@pytest.mark.parametrize("path", ERC_FILES, ids=lambda p: p.name)
def test_every_erc_report_agrees_with_an_independent_count_of_its_entries(
    path: Path,
) -> None:
    text = path.read_text(encoding="utf-8")
    report = parse_erc(text)
    assert report.categories == dict(entry_lines(text))
    assert len(report.violations) == report.messages == report.errors + report.warnings


def test_an_erc_entry_carries_severity_sheet_position_and_no_net() -> None:
    report = erc_of("erc_errors_and_warnings.rpt")
    assert report.summary == "ERC messages: 7  Errors 5  Warnings 2"
    assert (report.errors, report.warnings) == (5, 2)
    assert not report.clean
    assert report.categories == {
        "pin_not_connected": 4,
        "label_dangling": 1,
        "unconnected_wire_endpoint": 1,
        "no_connect_dangling": 1,
    }
    first = report.violations[0]
    assert first.message == "Pin not connected"
    assert (first.severity, first.rule, first.section) == ("error", "", "/")
    assert first.positions == ((100.33, 76.2),)
    assert first.items == ("Symbol R1 Pin 1 [Passive, Line]",)
    # "[Passive, Line]" is a pin's electrical type, not a net.
    assert first.nets == ()
    severities = {v.category: v.severity for v in report.violations}
    assert severities["unconnected_wire_endpoint"] == "warning"
    assert severities["no_connect_dangling"] == "warning"
    assert severities["label_dangling"] == "error"


def test_erc_positions_come_out_in_millimetres_from_mils() -> None:
    in_mm = erc_of("erc_errors.rpt")
    in_mils = erc_of("erc_errors_mils.rpt")
    assert in_mils.summary == in_mm.summary
    for a, b in zip(in_mm.violations, in_mils.violations):
        assert a.positions[0] == pytest.approx(b.positions[0], abs=0.005)


# --- a report that does not add up is an error, not a clean one -----------------


def test_an_empty_drc_report_is_an_error_not_a_clean_one() -> None:
    with pytest.raises(ReportError, match="Found N DRC violations"):
        parse_drc("")


def test_a_truncated_drc_report_is_an_error() -> None:
    text = text_of("drc_unconnected_and_dangling.rpt")
    cut = text[: text.index("** Found 4 unconnected pads **")]
    with pytest.raises(ReportError, match="unconnected pads"):
        parse_drc(cut)


def test_a_drc_heading_that_disagrees_with_its_entries_is_an_error() -> None:
    text = text_of("drc_unconnected_and_dangling.rpt")
    said_three = text.replace(
        "** Found 4 DRC violations **", "** Found 3 DRC violations **"
    )
    with pytest.raises(ReportError, match="says 3 DRC violations but lists 4"):
        parse_drc(said_three)


def test_a_drc_entry_missing_from_the_list_is_an_error() -> None:
    text = text_of("drc_unconnected_and_dangling.rpt")
    start = text.index("[track_dangling]")
    end = text.index("[track_dangling]", start + 1)
    without_one = text[:start] + text[end:]
    with pytest.raises(ReportError, match="says 4 DRC violations but lists 3"):
        parse_drc(without_one)


def test_an_entry_before_any_heading_is_an_error() -> None:
    text = text_of("drc_unconnected_and_dangling.rpt")
    stray = "[clearance]: Clearance violation\n    Local override; error\n"
    with pytest.raises(ReportError, match="before any heading"):
        parse_drc(stray + text)


def test_an_empty_erc_report_is_an_error_not_a_clean_one() -> None:
    with pytest.raises(ReportError, match="ERC messages"):
        parse_erc("")
    with pytest.raises(ReportError, match="ERC messages"):
        parse_erc("ERC report (2026-10-06T00:00:00, Encoding UTF8)\n\n***** Sheet /\n")


def test_erc_totals_that_disagree_with_the_entries_are_an_error() -> None:
    text = text_of("erc_errors_and_warnings.rpt")
    said_four = text.replace("Errors 5  Warnings 2", "Errors 4  Warnings 2")
    with pytest.raises(ReportError, match="says 4 errors but lists 5"):
        parse_erc(said_four)
    said_clean = text_of("erc_errors.rpt").replace("Errors 4", "Errors 0")
    with pytest.raises(ReportError, match="says 0 errors but lists 4"):
        parse_erc(said_clean)
