"""Unit tests for pcbkit.citations: is a value's quote on its page, with its numbers?

The pages are short stand-ins for what pdftotext gives for a datasheet: an absolute
maximum table, a pin description that pdftotext joined across a line break, and the
same table with each cell on its own line, as table text often comes out.
"""

from __future__ import annotations

import pytest

from pcbkit import citations
from pcbkit.citations import Problem, Rule, check_citation, check_pin

RATINGS = (
    "Absolute Maximum Ratings\n"
    "Input voltage VIN –0.3 19 V\n"
    "Enable EN –0.3 6 V\n"
    "Operating junction temperature −40 to 125 ºC\n"
)
# What pdftotext's default mode makes of "regu-" / "lator" and "pull-" / "up".
PROSE = (
    "The EN pin has an internal pull-up current source of 1.2 µA. If EN is\n"
    "left floating, the converter is enabled and the output regulator starts "
    "switching. Keep the pullup resistor short. Default address 0x70.\n"
)
# The same with -layout: the broken words stay broken.
LAYOUT = (
    "the output regu-\nlator starts switching. Keep the pull-\nup resistor short.\n"
)
CELLS = "Input voltage VIN\n–0.3\n19\nV\n"
PAGES = [RATINGS, PROSE, LAYOUT, CELLS, "   \n"]


def rule(problem: Problem | None) -> Rule | None:
    """Return the rule a problem broke, or None for no problem."""
    return problem.rule if problem else None


def test_a_quote_with_other_dashes_and_spacing_is_on_its_page() -> None:
    """Accept an ASCII hyphen for an en dash and spacing the PDF does not have."""
    assert (
        check_citation("-0.3 to 19 V", "Input voltage VIN -0.3  19V", 1, PAGES) is None
    )


@pytest.mark.parametrize(
    "quote, page",
    [
        ("internal pull-up current source of 1.2 μA", 2),  # mu for micro
        ("temperature -40 to 125 °C", 1),  # degree for ordinal, - for minus
        ("source of­ 1.2 µA.​ If EN", 2),  # soft hyphen, zero width
    ],
)
def test_look_alike_characters_are_one_character(quote: str, page: int) -> None:
    """Fold micro and mu, the degree look-alikes, minus signs and invisible marks."""
    assert citations.quote_on_page(quote, PAGES[page - 1])


def test_case_is_kept_because_ma_is_not_milliamps() -> None:
    """Refuse a quote whose only difference is case: mA and MA differ."""
    assert citations.quote_on_page("current source of 1.2 μA", PROSE)
    assert not citations.quote_on_page("current source of 1.2 μa", PROSE)


@pytest.mark.parametrize(
    "quote",
    [
        "the output regulator starts switching",
        "the output regu-lator starts switching",
        "Keep the pull-up resistor short.",
        "Keep the pullup resistor short.",
        "regulator starts switching. Keep the pull-up resistor",
    ],
)
@pytest.mark.parametrize("page", [2, 3], ids=["joined", "layout"])
def test_words_broken_at_a_line_end_match_however_they_are_copied(
    quote: str, page: int
) -> None:
    """Match broken words as pdftotext joins them, as -layout keeps them, and mixed."""
    assert citations.quote_on_page(quote, PAGES[page - 1])


def test_a_quote_never_joins_two_numbers_of_the_page() -> None:
    """Refuse "-0.36 V" for a page with "-0.3 6 V": spacing between digits counts."""
    assert not citations.quote_on_page("Enable EN -0.36 V", RATINGS)
    assert not citations.quote_on_page("Input voltage VIN -0.3 1 9 V", RATINGS)


@pytest.mark.parametrize(
    "quote, page",
    [
        # Ends inside "19".
        ("Input voltage VIN -0.3 1", "Input voltage VIN –0.3 19 V\nEnable EN –0.3 6 V"),
        # Starts after the sign of "-0.3": the sign would be lost.
        ("0.3 19 V Enable EN", "Input voltage VIN –0.3 19 V\nEnable EN –0.3 6 V"),
        # Ends inside "1.25".
        ("Reference voltage 1.2", "Reference voltage 1.25 V\nFeedback pin"),
        # Starts inside "19".
        ("9 V Enable EN -0.3 6", "Input voltage VIN 19 V\nEnable EN –0.3 6 V"),
        # Starts after a decimal point.
        (".25 V Feedback pin now", "Reference voltage 1.25 V\nFeedback pin now"),
        # Ends before the fraction of a number.
        ("Reference voltage 1", "Reference voltage 1.25 V\nFeedback pin now"),
    ],
)
def test_a_quote_cannot_start_or_end_inside_a_number_of_the_page(
    quote: str, page: str
) -> None:
    """Refuse a quote cut out of the middle of a number, which can lose a sign."""
    assert not citations.quote_on_page(quote, page)


@pytest.mark.parametrize(
    "quote, value, page",
    [
        ("Input voltage VIN -0.3 1", "-0.3 to 1 V", "Input voltage VIN –0.3 19 V"),
        ("0.3 19 V Enable EN", "0.3 to 19 V", "VIN –0.3 19 V Enable EN –0.3 6 V"),
        ("Reference voltage 1.2", "1.2 V", "Reference voltage 1.25 V"),
        ("9 V Enable EN -0.3 6", "9 V", "VIN 19 V Enable EN –0.3 6 V"),
    ],
)
def test_check_citation_refuses_a_value_the_cut_quote_would_have_passed(
    quote: str, value: str, page: str
) -> None:
    """Refuse the four cut quotes whose numbers all pass the number rule."""
    problem = check_citation(value, quote, 1, [page])
    assert rule(problem) is Rule.QUOTE


def test_a_quote_at_a_bad_boundary_and_a_good_one_is_accepted_on_the_good_one() -> None:
    """Accept a quote that also occurs where it is not cut out of a number."""
    page = "Reference voltage 1.25 V\nReference voltage 1.2 V typical"
    assert citations.quote_on_page("Reference voltage 1.2", page)
    assert not citations.quote_on_page(
        "Reference voltage 1.2", "Reference voltage 1.25 V"
    )


def test_a_quote_may_start_after_a_range_dash_or_end_before_a_space_number() -> None:
    """Keep a quote that starts after a range "3.0-5.5" dash, or ends before " 6"."""
    assert citations.quote_on_page(
        "5.5 V Supply current", "Supply 3.0-5.5 V Supply current"
    )
    assert citations.quote_on_page("Enable EN -0.3", RATINGS)


def test_table_cells_on_their_own_lines_match_a_row() -> None:
    """Match a table row whose cells pdftotext put on separate lines."""
    assert (
        check_citation("-0.3 to 19 V", "Input voltage VIN -0.3 19 V", 4, PAGES) is None
    )


@pytest.mark.parametrize("page", [0, -1, 6])
def test_a_page_outside_the_pdf_is_refused(page: int) -> None:
    """Refuse page 0, a negative page and one past the end, naming the range."""
    problem = check_citation("6 V", "Enable EN -0.3 6 V", page, PAGES)
    assert rule(problem) is Rule.PAGE
    assert "pages 1 to 5" in problem.message  # type: ignore[union-attr]


def test_a_short_quote_is_refused_even_when_it_is_on_the_page() -> None:
    """Refuse "-0.3 6 V": it is on the page, but too short to be evidence."""
    assert citations.quote_on_page("-0.3 6 V", RATINGS)
    assert rule(check_citation("6 V", "-0.3 6 V", 1, PAGES)) is Rule.SHORT


def test_a_quote_on_another_page_says_which() -> None:
    """Refuse the right quote with the wrong page, and name the right one."""
    problem = check_citation("-0.3 to 6 V", "Enable EN -0.3 6 V", 2, PAGES)
    assert rule(problem) is Rule.QUOTE
    assert "it is on page 1" in problem.message  # type: ignore[union-attr]


def test_a_quote_on_no_page_is_refused() -> None:
    """Refuse a quote that reads "pull-down" where the page says "pull-up"."""
    quote = "internal pull-down current source of 1.2 μA"
    problem = check_citation("1.2 μA", quote, 2, PAGES)
    assert rule(problem) is Rule.QUOTE
    assert "on no page" in problem.message  # type: ignore[union-attr]


def test_a_quote_cited_to_a_page_without_text_says_so() -> None:
    """Say that a cited page has no text, as for a figure or a scan."""
    problem = check_citation("7 V", "Enable EN -0.3 7 V", 5, PAGES)
    assert rule(problem) is Rule.QUOTE
    assert "page 5 has no text" in problem.message  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "value, quote, missing",
    [
        # A right quote with a wrong number: the failure the rule exists for.
        ("-0.3 to 17 V", "Input voltage VIN -0.3 19 V", ["17"]),
        # "9" is in "19" as text, but is not a number of the quote.
        ("-0.3 to 9 V", "Input voltage VIN -0.3 19 V", ["9"]),
        # The minus sign is part of the number.
        ("40 to 125 °C", "temperature −40 to 125 ºC", ["40"]),
        # A number made from two numbers of the quote is neither.
        ("-0.36 V", "Enable EN -0.3 6 V", ["-0.36"]),
        # Digits inside a name are not a number.
        ("1 V", "Rating of pin VIN1 is 5 V", ["1"]),
    ],
)
def test_a_number_of_the_value_must_be_a_number_of_the_quote(
    value: str, quote: str, missing: list[str]
) -> None:
    """Refuse a value with a number its quote does not have, and name the number."""
    assert citations.missing_numbers(value, quote) == missing


@pytest.mark.parametrize(
    "value, quote",
    [
        ("-0.3 to 19 V", "Input voltage VIN –0.3 19 V"),
        ("−0.3 V to +19 V", "Input voltage VIN -0.3 19 V"),
        ("3.0 to 5.5 V", "Supply voltage 3.0-5.5 V"),  # a range, not -5.5
        ("4.5 V to 17 V", "4.5-V to 17-V Input"),
        ("0x70", "Default address 0x70."),
        ("0x70", "Default address 70h."),
        ("0x7f", "Mask 0x7F"),  # hex digits are not case-sensitive
        ("10 MHz", "fSW TPS54331 10 MHz"),  # digits inside a name are not numbers
    ],
)
def test_numbers_match_whatever_dash_sign_or_prefix_they_are_written_with(
    value: str, quote: str
) -> None:
    """Accept the same numbers written with other dashes, a plus sign or 0x."""
    assert citations.missing_numbers(value, quote) == []


@pytest.mark.parametrize(
    "value, quote, missing",
    [
        ("0x1A", "Register 0x1F.", ["0x1a"]),  # a letter digit differs
        ("0xFE", "Register 0xFF.", ["0xfe"]),
        ("0x70", "Supply current 70 mA", ["0x70"]),  # hex is not decimal
        ("70", "Default address 0x70.", ["70"]),
        ("0x70", "Default address 71h.", ["0x70"]),
    ],
)
def test_hexadecimal_numbers_are_whole_tokens_apart_from_decimals(
    value: str, quote: str, missing: list[str]
) -> None:
    """Refuse a hex value that differs from the quote's after its first letter digit."""
    assert citations.missing_numbers(value, quote) == missing


def test_check_citation_refuses_a_wrong_number() -> None:
    """Refuse a cited value whose number its quote does not have, naming it."""
    problem = check_citation("-0.3 to 17 V", "Input voltage VIN -0.3 19 V", 1, PAGES)
    assert rule(problem) is Rule.NUMBER
    assert "has 17," in problem.message  # type: ignore[union-attr]


def test_a_derived_value_skips_the_numbers_but_not_the_quote() -> None:
    """Let a worked-out value through on its numbers, but still check its quote."""
    quote = "Default address 0x70."
    assert rule(check_citation("0x38 (8-bit)", quote, 2, PAGES)) is Rule.NUMBER
    assert check_citation("0x38 (8-bit)", quote, 2, PAGES, derived=True) is None
    assert rule(check_citation("0x38", quote, 1, PAGES, derived=True)) is Rule.QUOTE


def test_the_pdf_must_be_the_one_the_values_were_read_from() -> None:
    """Accept the recorded PDF; refuse another file, or no record of one."""
    pdf = b"%PDF-1.4 revision A"
    recorded = citations.sha256_of(pdf)
    assert len(recorded) == 64 and recorded == recorded.lower()
    assert check_pin(recorded, pdf) is None
    problem = check_pin(recorded, b"%PDF-1.4 revision B")
    assert rule(problem) is Rule.PIN
    assert recorded in problem.message  # type: ignore[union-attr]
    assert rule(check_pin("", pdf)) is Rule.PIN
    assert rule(check_pin(recorded.upper(), pdf)) is Rule.PIN
