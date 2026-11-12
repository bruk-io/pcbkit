"""Unit tests for pcbkit.citations: is a value's quote on its page, with its numbers?

The pages are short stand-ins for what pdftotext gives for a datasheet: an absolute
maximum table, a pin description that pdftotext joined across a line break, and the
same table with each cell on its own line, as table text often comes out.
"""

from __future__ import annotations

import hashlib

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


def missing(value: str, quote: str, page: str | None = None) -> list[str]:
    """Return the numbers of ``value`` the quote lacks, the quote being on ``page``.

    With no page the quote is its own page, so every token of it counts.
    """
    found = citations._missing(value, quote, quote if page is None else page)
    assert found is not None, f"{quote!r} is not on the page"
    return found


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


@pytest.mark.parametrize(
    "quote, page",
    [
        ("Specification of the regulator", "Speciﬁcation of the regulator"),  # fi
        ("Input voltage 19 V maximum", "Input voltage １９ Ｖ maximum"),  # full-width
        ("Speciﬁcation of the regulator", "Specification of the regulator"),
    ],
)
def test_ligatures_and_full_width_forms_are_the_plain_characters(
    quote: str, page: str
) -> None:
    """Match a ligature or a full-width form to its plain letters (NFKC)."""
    assert citations.quote_on_page(quote, page)
    assert missing("19 V", "Input voltage １９ Ｖ maximum") == []


def test_a_superscript_is_not_a_digit_of_the_number_before_it() -> None:
    """Refuse "106" for "10⁶ cycles", and "mm2" for "mm²", on the quote and the page.

    "10⁶" is one power token, so neither "10" nor "6" alone is a number of it.
    """
    assert missing("106 cycles", "Endurance 10⁶ cycles") == ["106"]
    assert missing("10 cycles", "Endurance 10⁶ cycles") == ["10"]
    assert missing("10^6 cycles", "Endurance 10⁶ cycles") == []
    assert missing("10^-6 A", "Leakage 10⁻⁶ A max") == []
    assert missing("6 A", "Leakage 10⁻⁶ A max") == ["6"]
    assert missing("2", "Pad area 4 mm² each") == ["2"]
    assert citations.quote_on_page("Endurance 10⁶ cycles", "Endurance 10⁶ cycles")
    assert not citations.quote_on_page("Endurance 106 cycles", "Endurance 10⁶ cycles")
    assert not citations.quote_on_page("Pad area 4 mm2 each", "Pad area 4 mm² each")
    assert not citations.quote_on_page("Pad area 4 mm² each", "Pad area 4 mm2 each")


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


def test_a_pdf_with_no_pages_has_no_page_to_cite() -> None:
    """Refuse any page of an empty page list, without saying "pages 1 to 0"."""
    problem = check_citation("6 V", "Enable EN -0.3 6 V", 1, [])
    assert rule(problem) is Rule.PAGE
    assert "no pages" in problem.message  # type: ignore[union-attr]
    assert "1 to 0" not in problem.message  # type: ignore[union-attr]


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
    "value, quote, absent",
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
    value: str, quote: str, absent: list[str]
) -> None:
    """Refuse a value with a number its quote does not have, and name the number."""
    assert missing(value, quote) == absent


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
    assert missing(value, quote) == []


@pytest.mark.parametrize(
    "value, quote, absent",
    [
        ("0x1A", "Register 0x1F.", ["0x1A"]),  # a letter digit differs
        ("0xFE", "Register 0xFF.", ["0xFE"]),
        ("0x70", "Supply current 70 mA", ["0x70"]),  # hex is not decimal
        ("70", "Default address 0x70.", ["70"]),
        ("0x70", "Default address 71h.", ["0x70"]),
    ],
)
def test_hexadecimal_numbers_are_whole_tokens_apart_from_decimals(
    value: str, quote: str, absent: list[str]
) -> None:
    """Refuse a hex value that differs from the quote's after its first letter digit."""
    assert missing(value, quote) == absent


@pytest.mark.parametrize(
    "value, quote, absent",
    [
        ("2 V", "Q1 2N7002 VGS 20 V", ["2"]),  # not the "2" of 2N7002
        ("4 k", "R5 4k7 pull-up", ["4"]),  # 4k7 is not read as 4
        ("3 V", "Output 3V3 regulated", ["3"]),
        ("7002 V", "Q1 2N7002 VGS 20 V", ["7002"]),
    ],
)
def test_digits_of_a_part_number_or_value_code_are_not_numbers(
    value: str, quote: str, absent: list[str]
) -> None:
    """Refuse a number taken from a word with letters between its digits."""
    assert missing(value, quote) == absent
    code = quote.split()[1]
    assert missing(code, quote) == []  # the code itself, whole, is in the quote


@pytest.mark.parametrize(
    "value, quote",
    [
        ("5 V", "pin-5 and a 5V rail"),  # a separate 5V is still a number
        ("20 V", "Q1 2N7002 VGS 20 V"),
        ("10 MHz", "fSW 10MHz"),  # digits then letters only
        ("4.7", "R5 4.7k pull-up"),
    ],
)
def test_digits_followed_by_letters_alone_are_still_numbers(
    value: str, quote: str
) -> None:
    """Keep "5V" and "10MHz" as numbers: only letters between digits make a code."""
    assert missing(value, quote) == []


@pytest.mark.parametrize(
    "value, quote",
    [
        ("1,000 mA", "Output current 1000 mA"),  # thousands separator
        ("-0,3 V", "Input voltage VIN -0.3 19 V"),  # decimal comma
        ("19.0 V", "Input voltage VIN -0.3 19 V"),  # a trailing zero
        ("-1% to +1%", "Tolerance ±1% at 25 °C"),  # a plus-minus form
    ],
)
def test_other_ways_of_writing_a_number_are_refused_not_normalised(
    value: str, quote: str
) -> None:
    """Refuse "1,000" for "1000", "-0,3", "19.0" for "19" and "-1%" for "±1%"."""
    assert missing(value, quote) != []


def test_units_are_not_checked_a_known_blind_spot() -> None:
    """Pin that "1.25 mV" passes against "1.25 V": units are not read, only numbers."""
    assert missing("1.25 mV", "Reference voltage 1.25 V") == []


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
    recorded = hashlib.sha256(pdf).hexdigest()
    assert len(recorded) == 64 and recorded == recorded.lower()
    assert check_pin(recorded, pdf) is None
    problem = check_pin(recorded, b"%PDF-1.4 revision B")
    assert rule(problem) is Rule.PIN
    assert recorded in problem.message  # type: ignore[union-attr]
    assert rule(check_pin("", pdf)) is Rule.PIN
    assert rule(check_pin(recorded.upper(), pdf)) is Rule.PIN


# The reviewer's cases: each passed the old matcher and must be refused.


@pytest.mark.parametrize(
    "value, quote",
    [
        ("1e6", "Write endurance 1e9 cycles"),
        ("1E3", "Write endurance 1E6 cycles"),
        ("2.5e3", "Write endurance 2.5e6 cycles"),  # not the mantissa alone
        ("1.5e3", "Series resistance 1.5 ohm"),
        ("1e-6", "Leakage 1 uA typical, 2e-6 A max"),  # not "1" and "-6"
    ],
)
def test_an_exponent_is_compared_whole(value: str, quote: str) -> None:
    """Refuse an exponent form that differs anywhere, or that only has a mantissa."""
    assert missing(value, quote) == [value]


@pytest.mark.parametrize(
    "value, quote",
    [
        ("4k7", "Pull-up resistor 2k2"),
        ("3V3", "Rails at 1V8 and 5V0"),
        ("12V1", "Supply rail 12 V nominal"),
        ("2N7002", "Transistor 2N7003 only"),
    ],
)
def test_a_value_code_needs_the_same_code(value: str, quote: str) -> None:
    """Refuse a code value against a different code or a plain number."""
    assert missing(value, quote) == [value]


@pytest.mark.parametrize(
    "page, quote",
    [
        ("The I2C address is 70h by default.", "I2C address is 70"),
        ("The default 0x70 is the I2C address.", "70 is the I2C address"),
        ("The register value is 0x1F at reset.", "The register value is 0x1"),
        ("Supply max is 1.5 V.", "Supply max is 1."),
        ("The supply voltage 5,5 V", "The supply voltage 5"),
        ("Write endurance is 1e3 cycles", "Write endurance is 1"),
        ("Write endurance is 1.5e3 cycles", "Write endurance is 1.5"),
        ("Endurance is 10⁶ cycles.", "Endurance is 10"),
        ("The pull-up resistor 4k7 here", "The pull-up resistor 4"),
        ("1V8 is the IO level here", "8 is the IO level here"),
        ("VDD 2.7-3.6 V supply max range", "-3.6 V supply max range"),
        ("Temperature 0-40 to 85 C ambient", "-40 to 85 C ambient"),
    ],
)
def test_a_quote_cannot_cut_a_token_of_the_page(page: str, quote: str) -> None:
    """Refuse a quote that cuts any page token, or starts on a range dash."""
    assert not citations.quote_on_page(quote, page)


def test_the_numbers_come_from_the_page_not_the_quote_spacing() -> None:
    """Refuse "5" for a quote typed "5, 5" where the page has one token "5,5"."""
    page = "Output 5,5 V max"
    assert citations.quote_on_page("Output 5, 5 V max", page)
    assert missing("5 V", "Output 5, 5 V max", page) == ["5"]


@pytest.mark.parametrize("value", ["-0.3 V", "0.3 V"])
def test_a_dash_after_a_letter_backs_neither_reading(value: str) -> None:
    """Refuse both readings of "VDD-0.3": a sign, or VDD minus 0.3."""
    assert missing(value, "Input high VDD-0.3 V min") == [value.split()[0]]


@pytest.mark.parametrize(
    "value, quote",
    [
        ("2.7 to 3.6 V", "Supply 2.7V-3.6V range"),  # a range written with units
        ("3.0 to 5.5 V", "Supply voltage 3.0 – 5.5 V"),  # a spaced en dash
        ("-40 to 125 °C", "Operating temperature −40 to 125 ºC"),
    ],
)
def test_ranges_keep_their_numbers_unsigned(value: str, quote: str) -> None:
    """Accept the numbers of a range: its dash is not a sign of the second number."""
    assert missing(value, quote) == []


def test_hours_read_as_hexadecimal_a_known_blind_spot() -> None:
    """Pin that "12h" is hex, so hours are refused: a retry, never a wrong value."""
    assert missing("12 h", "Data retention 12h minimum") == ["12"]
    assert missing("24h", "Data retention 24 h minimum") == ["24h"]
    assert missing("12h", "Data retention 12h minimum") == []


@pytest.mark.parametrize(
    "value, quote, absent",
    [
        ("9 V", "Input voltage VIN -0.3 19 V", ["9"]),
        ("0.3 V", "Input voltage VIN -0.3 19 V", ["0.3"]),
        ("1.2 V", "Reference voltage 1.25 V", ["1.2"]),
        ("0x1A", "Register at 0x1F", ["0x1A"]),
        ("0x70", "Supply current 70 mA", ["0x70"]),
        ("0x70", "Default address 70h", []),
        ("10^6", "Endurance 10⁶ cycles", []),
        ("10^6", "Endurance 10^9 cycles", ["10^6"]),
        ("10^6", "Endurance 106 cycles", ["10^6"]),
        ("1×10⁶", "Endurance 1×10⁹ cycles", ["10^6"]),
        ("10^-6", "Leakage 10⁻⁹ A max", ["10^-6"]),
        ("2", "Q1 2N7002 VGS 20 V", ["2"]),
        ("40", "Operating −40 to 125 ºC", ["40"]),
        ("1e-6", "Leakage 1e-9 A max", ["1e-6"]),
        ("1e-6", "Leakage 1e-6 A max", []),
        ("-40 to 125", "Operating −40 to 125 ºC", []),
        ("5 V", "pin-5 and a 5V rail", []),
    ],
)
def test_the_behaviour_review_confirmed_still_holds(
    value: str, quote: str, absent: list[str]
) -> None:
    """Keep each case the earlier review confirmed, exactly as it was."""
    assert missing(value, quote) == absent


def test_non_breaking_spaces_are_spaces() -> None:
    """Accept a quote with plain spaces for a page with non-breaking ones."""
    page = "Input voltage VIN –0.3 19 V"
    quote = "Input voltage VIN -0.3 19 V"
    assert check_citation("-0.3 to 19 V", quote, 1, [page]) is None


# Generated cases. A fragment is a list of parts: ("token", text) is one page token,
# never to be cut; ("dash", "-") is a range dash, which a quote may end after but not
# start on; ("free", text) may be cut anywhere.

FRAGMENTS = {
    "decimal": [("token", "1.25")],
    "decimal-unit": [("token", "19"), ("free", "V")],
    "fraction": [("token", ".25")],
    "negative": [("token", "-40")],
    "negative-fraction": [("token", "-0.3")],
    "exponent": [("token", "2.5e6")],
    "exponent-upper": [("token", "1E3")],
    "exponent-signed": [("token", "1.5e-3")],
    "exponent-negative": [("token", "-1e-6")],
    "hex-prefixed": [("token", "0x1F")],
    "hex-suffixed": [("token", "1Fh")],
    "superscript": [("token", "10⁶")],
    "superscript-signed": [("token", "10⁻⁶")],
    "superscript-times": [("token", "1"), ("free", "×"), ("token", "10⁹")],
    "caret": [("token", "10^6")],
    "code-value": [("token", "4k7")],
    "code-rail": [("token", "3V3")],
    "code-part": [("token", "2N7002")],
    "code-name": [("token", "VIN1")],
    "decimal-comma": [("token", "5,5"), ("free", " V")],
    "thousands": [("token", "1,000"), ("free", " mA")],
    "range": [("token", "2.7"), ("dash", "-"), ("token", "3.6")],
    "range-units": [
        ("token", "2.7"),
        ("free", "V"),
        ("dash", "-"),
        ("token", "3.6"),
        ("free", "V"),
    ],
    "range-integers": [("token", "5"), ("dash", "-"), ("token", "40")],
}
LEAD = "The rated level is "
TAIL = " for this part."


def _cuts() -> list[tuple[str, str, str, bool]]:
    """Return (fragment, side, quote, allowed) for every cut of every fragment."""
    cases = []
    for name, parts in FRAGMENTS.items():
        page = LEAD + "".join(text for _, text in parts) + TAIL
        at = len(LEAD)
        no_end: set[int] = set()
        no_start: set[int] = set()
        boundaries = {len(LEAD)}
        for role, text in parts:
            if role == "token":
                no_end.update(range(at + 1, at + len(text)))
            if role == "dash":
                no_start.add(at)
            at += len(text)
            boundaries.add(at)
        for cut in sorted(no_end | boundaries):
            cases.append((name, "right", page[:cut], cut not in no_end))
        for cut in sorted(no_end | no_start | boundaries):
            allowed = cut not in no_end and cut not in no_start
            cases.append((name, "left", page[cut:], allowed))
    return cases


CUTS = _cuts()


@pytest.mark.parametrize(
    "name, side, quote, allowed",
    CUTS,
    ids=[f"{name}-{side}-{len(quote)}" for name, side, quote, _ in CUTS],
)
def test_every_cut_inside_a_token_is_refused(
    name: str, side: str, quote: str, allowed: bool
) -> None:
    """Refuse every quote cut inside a token; accept every cut between tokens."""
    page = LEAD + "".join(text for _, text in FRAGMENTS[name]) + TAIL
    assert citations.quote_on_page(page, page)
    assert citations.quote_on_page(quote, page) is allowed


def _one_off(text: str) -> list[str]:
    """Return ``text`` with each character changed to another, or left out, in turn."""
    digits = ["0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"]
    variants = []
    for at, character in enumerate(text):
        variants.append(text[:at] + text[at + 1 :])
        other = None
        for row in digits:
            if character in row:
                other = row[(row.index(character) + 1) % 10]
        if character.isascii() and character.isalpha():
            base = ord("a") if character.islower() else ord("A")
            other = chr(base + (ord(character) - base + 1) % 26)
        if other is not None:
            variants.append(text[:at] + other + text[at + 1 :])
    return variants


SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
NEAR = [
    (name, text, variant)
    for name, parts in FRAGMENTS.items()
    for role, text in parts
    if role == "token" and name not in ("decimal-comma", "thousands")
    for variant in _one_off(text)
    if any(character.isdigit() for character in variant.translate(SUPERSCRIPT_DIGITS))
]


@pytest.mark.parametrize(
    "name, token, variant",
    NEAR,
    ids=[f"{name}-{variant}" for name, _, variant in NEAR],
)
def test_a_value_one_character_off_a_token_is_refused(
    name: str, token: str, variant: str
) -> None:
    """Refuse a value one character off the page's token; accept the token itself."""
    page = LEAD + "".join(text for _, text in FRAGMENTS[name]) + TAIL
    assert missing(token, page) == []
    assert missing(variant, page) != []


@pytest.mark.parametrize("name", ["decimal-comma", "thousands"])
def test_a_grouped_number_backs_no_value(name: str) -> None:
    """Refuse a value written with a comma even against the same text: not read."""
    page = LEAD + "".join(text for _, text in FRAGMENTS[name]) + TAIL
    token = FRAGMENTS[name][0][1]
    assert missing(token, page) == [token]
    assert missing(token.replace(",", "."), page) != []
    assert missing(token.split(",")[0], page) != []
