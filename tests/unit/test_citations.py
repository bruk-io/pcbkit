"""Unit tests for pcbkit.citations: is a value's quote on its page, with its numbers?

The pages are short stand-ins for what pdftotext gives for a datasheet: an absolute
maximum table, a pin description that pdftotext joined across a line break, and the
same table with each cell on its own line, as table text often comes out.

The module refuses by default: a number backs a value only when it and everything
touching it are inside a small grammar. Most tests here pin one rule of that grammar;
the generated tests at the end check the default itself.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

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
# Where every sign is certain: after ":" and "=", which no range or subtraction ends.
CONDITIONS = (
    "Recommended Operating Conditions\n"
    "Input voltage range: –0.3 19 V\n"
    "Ambient temperature, TA = −40 to 125 ºC\n"
)


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
    assert check_citation("19 V", "Input voltage VIN -0.3  19V", 1, PAGES) is None
    quote = "Input voltage range: -0.3  19V"
    assert check_citation("-0.3 to 19 V", quote, 1, [CONDITIONS]) is None


def test_a_negative_number_after_a_symbol_is_refused() -> None:
    """Refuse "-0.3" after "VIN": "VIN –0.3" may be VIN minus 0.3, or a range."""
    problem = check_citation("-0.3 to 19 V", "Input voltage VIN -0.3 19 V", 1, PAGES)
    assert rule(problem) is Rule.NUMBER
    assert "has -0.3," in problem.message  # type: ignore[union-attr]
    assert check_citation("-0.3 V", "Input voltage VIN -0.3 19 V", 4, PAGES)


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


def test_the_ohm_sign_is_omega() -> None:
    """Fold the ohm sign U+2126 to omega U+03A9, in quotes and in units.

    The two look alike, so the cases are written with escapes: the fold table once
    mapped omega to itself and left the ohm sign alone, and no test saw it.
    """
    ohm, omega = "\u2126", "\u03a9"
    page = f"On resistance typical 200 m{ohm} max"
    assert citations.quote_on_page(f"On resistance typical 200 m{omega}", page)
    quote = f"On resistance typical 200 m{omega}"
    assert check_citation(f"200 m{omega}", quote, 1, [page]) is None
    assert check_citation(f"200 m{ohm}", quote, 1, [page]) is None
    assert rule(check_citation(f"200 {omega}", quote, 1, [page])) is Rule.NUMBER


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


def test_a_superscript_backs_nothing() -> None:
    """Refuse every reading of "10⁶": a power, or 10 with footnote 6, is not certain.

    "mm2" is not "mm²" either, on the quote and the page.
    """
    assert missing("106 cycles", "Endurance 10⁶ cycles") == ["106"]
    assert missing("10 cycles", "Endurance 10⁶ cycles") == ["10"]
    assert missing("10^6 cycles", "Endurance 10⁶ cycles") == ["10^6"]
    assert missing("10^-6 A", "Leakage 10⁻⁶ A max") == ["10^-6"]
    assert missing("6 A", "Leakage 10⁻⁶ A max") == ["6"]
    assert missing("2", "Pad area 4 mm² each") == ["2"]
    assert missing("4", "Pad area 4 mm² each") == []
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


def test_a_quote_may_start_after_a_range_dash() -> None:
    """Keep a quote that starts after the dash of a range "3.0-5.5"."""
    assert citations.quote_on_page(
        "5.5 V Supply current", "Supply 3.0-5.5 V Supply current"
    )


@pytest.mark.parametrize(
    "value, quote, page",
    [
        ("1 mA", "Output current 1", "Output current 1 000 mA max"),
        ("000 mA", "000 mA max load", "Output current 1 000 mA max load"),
        ("1000 mA", "Output current 1 000 mA", "Output current 1 000 mA max"),
    ],
)
def test_a_quote_may_stop_at_a_space_between_digits_but_a_group_is_unknown(
    value: str, quote: str, page: str
) -> None:
    """Let a quote stop at a digit space; "1 000" is still read as neither number."""
    assert citations.quote_on_page(quote, page)
    assert rule(check_citation(value, quote, 1, [page])) is Rule.NUMBER


def test_one_cell_of_a_row_may_be_quoted() -> None:
    """Accept a quote of one cell of a row: the dropped space rule refused it."""
    page = "Input voltage range: –0.3 19 V"
    assert check_citation("-0.3 V", "Input voltage range: -0.3", 1, [page]) is None
    assert check_citation("6 V", "19 V Enable EN -0.3 6 V", 1, PAGES) is None


def test_table_cells_on_their_own_lines_match_a_row() -> None:
    """Match a table row whose cells pdftotext put on separate lines."""
    assert check_citation("19 V", "Input voltage VIN -0.3 19 V", 4, PAGES) is None
    cells = "Input voltage range:\n–0.3\n19\nV\n"
    quote = "Input voltage range: -0.3 19 V"
    assert check_citation("-0.3 to 19 V", quote, 1, [cells]) is None


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
        ("-0.3 to 17 V", "Input voltage range: -0.3 19 V", ["17"]),
        # "9" is in "19" as text, but is not a number of the quote.
        ("-0.3 to 9 V", "Input voltage range: -0.3 19 V", ["9"]),
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
        ("-0.3 to 19 V", "Input voltage range: –0.3 19 V"),
        ("−0.3 V to +19 V", "Input voltage range: -0.3 19 V"),
        ("3.0 to 5.5 V", "Supply voltage 3.0-5.5 V"),  # a range, not -5.5
        ("4.5 V to 17 V", "4.5-V to 17-V Input"),
        ("0x70", "Default address 0x70."),
        # A letter digit makes "h" hex. Changed on purpose: this was "7Ah", which
        # may also be 7 ampere-hours, and is now a code (see the "2Ah" cases).
        ("0x7b", "Default address 7Bh."),
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
        # Digits and "h" may be hours: a code, equal only to itself.
        ("0x70", "Default address 70h.", ["0x70"]),
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


@pytest.mark.parametrize(
    "value, quote, accepted",
    [
        # F1 of the fourth review: a value's "±" or "+/-" was dropped.
        ("±5 V", "Supply voltage 5 V max", False),
        ("±12 V", "Supply rail +12 V nominal", False),
        ("+/-5 V", "Supply voltage 5 V max", False),
        ("VIN ±5", "Supply voltage 5 V max", False),
        ("±5 V", "Supply voltage ±5 V max", True),
        ("±5 V", "Supply voltage +/-5 V max", True),
        ("+/-5 V", "Supply voltage ±5 V max", True),
        # A "+" is positive: backed by "+" or no sign, never by "±".
        ("+12 V", "Supply rail +12 V nominal", True),
        ("+12 V", "Supply rail 12 V nominal", True),
        ("+5 V", "Supply voltage ±5 V max", False),
        # No sign: the magnitude of a "±" or "+", a documented blind spot of roles.
        ("5 V", "Supply voltage ±5 V max", True),
        ("12 V", "Supply rail +12 V nominal", True),
    ],
)
def test_a_sign_in_the_value_needs_the_same_sign_on_the_page(
    value: str, quote: str, accepted: bool
) -> None:
    """Refuse a value's "±", "+/-" or "+" unless the page's sign backs it."""
    assert (check_citation(value, quote, 1, [quote]) is None) is accepted


@pytest.mark.parametrize(
    "value, quote",
    [
        # F2 of the fourth review: pin names read as a number and a unit or prefix.
        ("2", "Pin 15 2G enable input"),
        ("2S", "Output enable pin 2G active low"),
        ("1M", "Output enable pin 1G active low"),
        ("1", "Pin 9 1K clear input"),
        ("1S", "Pin 9 1K clear input"),
        ("3V", "Pin 9 1K clear input"),
        ("1M", "Pin 9 1K clear input"),
        ("1", "Pin 4 1S select input"),
        ("1", "Pin 4 1M mode input"),
        ("1", "Pin 4 1H hold input"),
        ("1", "Pin 4 1W write input"),
        ("3", "Pin 7 3H input"),
        ("10 kΩ", "Pull-up resistor 10K to VDD"),
        ("1 MΩ", "Bleed resistor 1M to ground"),
        # Digits and "Ah": hex or ampere-hours, so neither.
        ("0x2A", "Battery capacity 2Ah nominal"),
        ("2 Ah", "Battery capacity 2Ah nominal"),
        ("2", "Battery capacity 2Ah nominal"),
        ("0x12A", "Battery capacity 12Ah nominal"),
        ("0x7a", "Default address 7Ah."),
    ],
)
def test_an_integer_and_a_pin_letter_or_ah_is_a_code(value: str, quote: str) -> None:
    """Refuse a number read out of "1G", "2S", "1K", "1M", "1H", "1W" or "2Ah"."""
    assert rule(check_citation(value, quote, 1, [quote])) is Rule.NUMBER


@pytest.mark.parametrize(
    "value, quote",
    [
        ("2G", "Pin 15 2G enable input"),
        ("10K", "Pull-up resistor 10K to VDD"),
        ("1M", "Bleed resistor 1M to ground"),
        ("2Ah", "Battery capacity 2Ah nominal"),
        ("0x1FA", "Register value 1FAh at reset"),  # hex: "1F" is no number
        ("3 V", "Output rail 3V regulated"),  # "V" stays a unit
        ("100 n", "Bypass capacitor 100n ceramic"),  # lower case stays a prefix
        ("4.7 K", "Pull-up resistor 4.7K to VDD"),  # not an integer: no pin name
    ],
)
def test_a_code_backs_itself_and_other_suffixes_stay_units(
    value: str, quote: str
) -> None:
    """Accept the code written whole, and "3V", "100n", "4.7K" as numbers."""
    assert check_citation(value, quote, 1, [quote]) is None


def test_hex_that_starts_with_a_letter_is_text_only() -> None:
    """Refuse "0xFF" against "FFh", which has no digit; accept "FFh" as text."""
    quote = "Device ID register FFh at reset"
    assert rule(check_citation("0xFF", quote, 1, [quote])) is Rule.NUMBER
    assert rule(check_citation("FFh", quote, 1, [quote])) is Rule.VALUE
    assert check_citation("FFh", quote, 1, [quote], text_only=True) is None


@pytest.mark.parametrize(
    "value, quote",
    [
        # The fourth review's cases: units and prefixes were not compared.
        ("10 Ω", "Pull-up resistor R1 = 10 kΩ"),
        ("100 nF", "Bypass capacitor 100 µF ceramic"),
        ("3.3 V", "Output current 3.3 A max"),
        ("1.25 mV", "Reference voltage 1.25 V"),
        ("1.25 mV", "Reference voltage 1.25V typical"),  # glued on the page
        ("1.25V", "Reference voltage 1.25 mV typical"),  # glued in the value
        ("85 °C", "Shutdown threshold 85% falling"),
        ("85%", "Shutdown threshold 85 °C rising"),
        ("125 °C", "Shutdown threshold 125°F rising"),
        ("45 μA", "Quiescent current IQ  45  mA"),  # across a cell gap
        ("10 kohm", "Pull-up resistor R1 = 10 kΩ"),  # "ohm" and "Ω" are not folded
        ("10 kΩ", "Pull-up resistor R1 = 10 KΩ"),  # nor "k" and "K"
        ("-0.3 to 19 mV", "Input voltage range: –0.3 19 V"),
        ("1.22 mV", "Enable high threshold 0.5 0.9 1.22\n        V"),  # next line
        ("0.9 mV", "Enable high threshold 0.5 0.9 1.22\n        V"),  # its column
    ],
)
def test_a_unit_stated_on_both_sides_must_be_the_same(value: str, quote: str) -> None:
    """Refuse a value whose unit or prefix differs from the one the page states."""
    assert rule(check_citation(value, quote, 1, [quote])) is Rule.NUMBER


@pytest.mark.parametrize(
    "value, quote, page",
    [
        ("10 kΩ", "Pull-up resistor R1 = 10 kΩ", None),
        ("100 µF", "Bypass capacitor 100 μF ceramic", None),  # micro sign and mu
        ("100 uF", "Bypass capacitor 100 μF ceramic", None),  # "u" is micro
        ("10 kΩ", "Pull-up resistor R1 = 10 kΩ", None),  # ohm sign and omega
        ("85%", "Shutdown threshold 85 % falling", None),
        ("125 °C", "Shutdown threshold 125°C rising", None),
        ("45 μA", "Quiescent current IQ  45  μA", None),
        ("1.22 V", "Enable high threshold 0.5 0.9 1.22\n        V", None),
        # Either side states none: as before, not compared. Changed on purpose:
        # this row had "V" on the next line, which is now its unit column.
        ("0.9 mV", "Enable high threshold 0.5 0.9 1.22", None),
        ("1.25", "Reference voltage 1.25 V typical", None),
        ("500 kHz", "Switching frequency kHz 500", None),  # unit before the number
        ("-0.3 to 19 V", "Input voltage range: –0.3 19 V", None),
    ],
)
def test_a_unit_stated_on_one_side_only_is_not_compared(
    value: str, quote: str, page: str | None
) -> None:
    """Accept the same unit however it is spelt, and a unit only one side states."""
    assert check_citation(value, quote, 1, [quote if page is None else page]) is None


# Follow-up 1 of the fifth review: a number with no unit takes its row's unit column,
# a unit standing apart after the last of a run of numbers on the same line.


@pytest.mark.parametrize(
    "value, page",
    [
        ("4.5 A", "Input voltage VIN 4.5 28 V"),
        ("-0.3 mV", "Input voltage VIN    –0.3    32    V"),
        ("80 mA", "Operating quiescent current    80    130    µA"),
        ("5.8 mV", "Threshold VIN = 100 mV 5.8 6.6 7.7 V"),  # after another unit
        ("6.6 mV", "Threshold VIN = 100 mV 5.8 6.6 7.7 V"),
        ("100 V", "Threshold VIN = 100 mV 5.8 6.6 7.7 V"),  # its own unit wins
        ("-17 V", "Reverse threshold VAK    –17    –11    –2    mV"),
        ("456 MHz", "Switching frequency    456    570    684    kHz"),
        ("1.75 kHz", "Clock    456    684    kHz    1.75    2.2    MHz"),  # 2 columns
        ("1 mA", "Supply current, –40°C to 85°C    1    4    μA"),
    ],
)
def test_a_number_takes_its_rows_unit_column(value: str, page: str) -> None:
    """Refuse a unit other than the row's unit column for a number with none."""
    assert rule(check_citation(value, page, 1, [page])) is Rule.NUMBER


@pytest.mark.parametrize(
    "value, page",
    [
        ("4.5 V", "Input voltage VIN 4.5 28 V"),
        ("5.8 V", "Threshold VIN = 100 mV 5.8 6.6 7.7 V"),
        ("100 mV", "Threshold VIN = 100 mV 5.8 6.6 7.7 V"),
        ("-17 mV", "Reverse threshold VAK    –17    –11    –2    mV"),
        ("684 kHz", "Clock    456    684    kHz    1.75    2.2    MHz"),
        ("1.75 MHz", "Clock    456    684    kHz    1.75    2.2    MHz"),
        ("1 μA", "Supply current, –40°C to 85°C    1    4    μA"),
        # A unit glued or joined to its cell is that cell's own, not a column.
        ("73.2 kΩ", "Design    500    5    73.2    10    3.3    22uF × 1"),
        ("3.3 µH", "Design    500    5    73.2    10    3.3    22uF × 1"),
        ("90 %", "Duty ratio    90    93%"),
        ("5 V", "Design    5    6.8-pF    here"),
        # The column does not reach across a line break, a word or another mark.
        ("4.5 mA", "Input voltage VIN 4.5\n28 V"),
        ("4.5 mA", "Input voltage VIN 4.5 to 28 V"),
        ("1.8 µs", "Internal soft-start    ms    1.8"),  # a unit before: none
    ],
)
def test_a_rows_unit_column_backs_its_numbers_and_no_others(
    value: str, page: str
) -> None:
    """Accept the row's unit, a cell's own unit, and units the column cannot reach."""
    assert check_citation(value, page, 1, [page]) is None


def test_a_row_of_cells_in_different_units_is_refused_a_known_cost() -> None:
    """Pin the cost: a spaced unit after mixed cells is taken by all of them."""
    page = "Design    500    5    73.2    10    3.3    22 µF"
    assert rule(check_citation("73.2 kΩ", page, 1, [page])) is Rule.NUMBER
    assert check_citation("73.2", page, 1, [page]) is None


CAPACITANCE = "6.8-pF capacitance is recommended for R2 of 100-kΩ resistance"


@pytest.mark.parametrize(
    "value, quote",
    [
        # H1 of the fifth review: a unit joined by a hyphen was never read.
        ("6.8 nF", CAPACITANCE),
        ("6.8 µF", CAPACITANCE),
        ("100 Ω", CAPACITANCE),
        ("6.8-nF", "6.8-pF capacitance is recommended"),  # the hyphen in the value
        ("6.8-nF", "6.8 pF capacitance is recommended"),
        ("10 Ω", "Use a 10-kΩ pullup resistor"),
        ("100 µF", "Bypass with a 100-nF capacitor"),
        ("4.7 nF", "Place a 4.7-µF ceramic capacitor"),
        ("±20 mV", "60-V VDS(MAX) and ±20-V VGS(MAX)"),
        ("60 A", "Rated 60-V VDS(MAX) here"),
        ("10 µs", "PH (10-ns transient from ground)"),
    ],
)
def test_a_unit_joined_by_a_hyphen_is_compared(value: str, quote: str) -> None:
    """Refuse a value whose unit differs from one the page joins with a hyphen."""
    assert rule(check_citation(value, quote, 1, [quote])) is Rule.NUMBER


@pytest.mark.parametrize(
    "value, quote",
    [
        ("6.8 pF", CAPACITANCE),
        ("100 kΩ", CAPACITANCE),
        ("6.8-pF", "6.8-pF capacitance is recommended"),
        ("6.8-pF", "6.8 pF capacitance is recommended"),
        ("6.8", "6.8-pF capacitance is recommended"),  # no unit in the value
        ("60 V", "Rated 60-V VDS(MAX) here"),
        ("±20 V", "60-V VDS(MAX) and ±20-V VGS(MAX)"),
        ("4.5 V to 17 V", "4.5-V to 17-V Input"),
        ("2.7 V", "Supply voltage 2.7-3.6 V range"),  # a range dash, not a unit
        ("10 ns", "PH (10-ns transient from ground)"),
    ],
)
def test_a_unit_joined_by_a_hyphen_backs_the_same_unit(value: str, quote: str) -> None:
    """Accept the same unit with or without the hyphen, and a range's first end."""
    assert check_citation(value, quote, 1, [quote]) is None


def test_a_quote_may_not_end_between_a_number_and_its_glued_unit() -> None:
    """Refuse a quote that stops at "10" of "10k": "10" against it is not certain."""
    page = "Resistor R1 is 10k pull-up"
    assert not citations.quote_on_page("Resistor R1 is 10", page)
    assert rule(check_citation("10", "Resistor R1 is 10", 1, [page])) is Rule.QUOTE
    assert not citations.quote_on_page("k pull-up to VDD", "R1 10k pull-up to VDD")
    assert citations.quote_on_page("Resistor R1 is 10k", page)
    assert citations.quote_on_page("Supply voltage 19", "Supply voltage 19 V")


def test_check_citation_refuses_a_wrong_number() -> None:
    """Refuse a cited value whose number its quote does not have, naming it."""
    quote = "Input voltage range: -0.3 19 V"
    problem = check_citation("-0.3 to 17 V", quote, 1, [CONDITIONS])
    assert rule(problem) is Rule.NUMBER
    assert "has 17," in problem.message  # type: ignore[union-attr]
    assert check_citation("-0.3 to 19 V", quote, 1, [CONDITIONS]) is None


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
        ("-40 to 125 °C", "Operating temperature, TA = −40 to 125 ºC"),
        ("-40 to 85 °C", "Operating range: −40°C-85°C ambient"),
        ("5 to 10 %", "Duty cycle 5%-10% nominal"),
    ],
)
def test_ranges_keep_their_numbers_unsigned(value: str, quote: str) -> None:
    """Accept the numbers of a range: its dash is not a sign of the second number."""
    assert missing(value, quote) == []


def test_digits_and_h_are_a_code_neither_hours_nor_hex() -> None:
    """Pin that "12h" backs only "12h": not 12, not 12 hours, not 0x12."""
    page = "Data retention 12h minimum"
    assert missing("12 h", page) == ["12"]
    assert missing("24h", "Data retention 24 h minimum") == ["24h"]
    assert missing("12h", page) == []
    assert missing("0x12", page) == ["0x12"]
    assert rule(check_citation("0x12", page, 1, [page])) is Rule.NUMBER


@pytest.mark.parametrize(
    "value, quote, absent",
    [
        ("9 V", "Input voltage VIN -0.3 19 V", ["9"]),
        ("0.3 V", "Input voltage VIN -0.3 19 V", ["0.3"]),
        ("1.2 V", "Reference voltage 1.25 V", ["1.2"]),
        ("0x1A", "Register at 0x1F", ["0x1A"]),
        ("0x70", "Supply current 70 mA", ["0x70"]),
        # Changed on purpose: "70h" may be hours, and a superscript may be a footnote.
        ("0x70", "Default address 70h", ["0x70"]),
        ("10^6", "Endurance 10⁶ cycles", ["10^6"]),
        ("10^6", "Endurance 10^9 cycles", ["10^6"]),
        ("10^6", "Endurance 106 cycles", ["10^6"]),
        ("10^-6", "Leakage 10⁻⁹ A max", ["10^-6"]),
        ("2", "Q1 2N7002 VGS 20 V", ["2"]),
        ("40", "Operating −40 to 125 ºC", ["40"]),
        ("1e-6", "Leakage 1e-9 A max", ["1e-6"]),
        ("1e-6", "Leakage 1e-6 A max", []),
        # Changed on purpose: "Operating" may start a range, so the sign is unknown.
        ("-40 to 125", "Operating −40 to 125 ºC", ["-40"]),
        ("-40 to 125", "Operating at −40 to 125 ºC", []),
        ("5 V", "pin-5 and a 5V rail", []),
    ],
)
def test_the_behaviour_review_confirmed_still_holds(
    value: str, quote: str, absent: list[str]
) -> None:
    """Keep each case the earlier review confirmed, exactly as it was."""
    assert missing(value, quote) == absent


def test_non_breaking_spaces_are_spaces_except_between_digits() -> None:
    """Match plain spaces to no-break ones, but read "0.3 19" as grouped: unknown."""
    page = "Input voltage range:\u00a0–0.3\u00a019\u00a0V"
    quote = "Input voltage range: -0.3 19 V"
    assert citations.quote_on_page(quote, page)
    assert missing("-0.3 to 19 V", quote, page) == ["-0.3", "19"]
    page = "Input voltage range:\u00a0–0.3 19\u00a0V"
    assert check_citation("-0.3 to 19 V", quote, 1, [page]) is None


# Generated cases. A fragment is a list of parts: ("token", text) is one page token,
# never to be cut; ("unit", text) is a unit glued to the token before it, which a quote
# may not cut off or start on; ("dash", "-") is a range dash, which a quote may end
# after but not start on; ("free", text) may be cut anywhere. Changed on purpose: the
# glued units were "free" until a quote was refused an end between "19" and "V".

FRAGMENTS = {
    "decimal": [("token", "1.25")],
    "decimal-unit": [("token", "19"), ("unit", "V")],
    "decimal-prefix": [("token", "10"), ("unit", "k"), ("free", " pull-up")],
    "decimal-prefix-unit": [("token", "4.7"), ("unit", "kΩ")],
    "fraction": [("token", ".25")],
    "negative": [("token", "-40")],
    "negative-fraction": [("token", "-0.3")],
    "exponent": [("token", "2.5e6")],
    "exponent-upper": [("token", "1E3")],
    "exponent-signed": [("token", "1.5e-3")],
    "exponent-negative": [("token", "-1e-6")],
    "hex-prefixed": [("token", "0x1F")],
    "hex-suffixed": [("token", "1Fh")],
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
        ("unit", "V"),
        ("dash", "-"),
        ("token", "3.6"),
        ("unit", "V"),
    ],
    "range-integers": [("token", "5"), ("dash", "-"), ("token", "40")],
    "table-cells": [("token", "-0.3"), ("free", " "), ("token", "19"), ("free", " V")],
}
# Unknown tokens: a quote may not cut them either, but they back no value at all.
UNKNOWN_FRAGMENTS = {
    "superscript": [("token", "10⁶")],
    "superscript-signed": [("token", "10⁻⁶")],
    "superscript-times": [("token", "1"), ("free", "×"), ("token", "10⁹")],
    "space-grouped": [("token", "1"), ("free", " "), ("token", "000"), ("free", " mA")],
    "footnote": [("token", "3.6¹"), ("free", " V")],
}
ALL_FRAGMENTS = {**FRAGMENTS, **UNKNOWN_FRAGMENTS}
LEAD = "The rated level is "
TAIL = " for this part."


def _cuts() -> list[tuple[str, str, str, bool]]:
    """Return (fragment, side, quote, allowed) for every cut of every fragment."""
    cases = []
    for name, parts in ALL_FRAGMENTS.items():
        page = LEAD + "".join(text for _, text in parts) + TAIL
        at = len(LEAD)
        no_end: set[int] = set()
        no_start: set[int] = set()
        boundaries = {len(LEAD)}
        for role, text in parts:
            if role == "token":
                no_end.update(range(at + 1, at + len(text)))
            if role == "unit":
                no_end.update(range(at, at + len(text)))
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
    page = LEAD + "".join(text for _, text in ALL_FRAGMENTS[name]) + TAIL
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


# The third review's cases, end to end through check_citation. Each was accepted
# before the grammar refused by default; each must now be refused, by the rule named.

NBSP, THIN, NARROW = " ", " ", " "
REVIEW_THREE = [
    # H1: a digit-led word with letters outside the unit list is a code, whole.
    ("1E", "Register address 1F (hex)", "Register address 1F (hex) default 00", "N"),
    ("0B", "Reset value 0A for this reg", None, "N"),
    ("7E", "Device ID register 7F", None, "N"),
    ("2A", "Pin 7 2Y Output of gate 2", None, "N"),
    ("1Y", "Pin 1 1A Input of gate 1", None, "N"),
    ("1CLR", "Pin 2 1CLK clock input", None, "N"),
    ("1010", "Address bits 1010b fixed", None, "N"),
    # H2: digits grouped by a space are unknown on both sides.
    ("100 cycles", "Write endurance 100 000 cycles", None, "N"),
    ("1 mA", "Output current 1 000 mA max", None, "N"),
    ("1 mA", f"Output current 1{THIN}000 mA max", None, "N"),
    ("1 mA", f"Output current 1{NARROW}000 mA max", None, "N"),
    ("1 mA", f"Output current 1{NBSP}000 mA max", None, "N"),
    ("000 mA", f"Output current 1{THIN}000 mA max", None, "N"),
    # M1: a sign before a code or hex number is outside the grammar.
    ("-3V3", "Analog rail 3V3 supply", None, "V"),
    ("-0x1F", "Register mask 0x1F", None, "V"),
    ("-70h", "Default address 70h", None, "V"),
    ("-2N7002", "Transistor 2N7002 here", None, "V"),
    # M2: a dash after a number and a space has no certain role.
    ("-3.6 V", "Supply voltage 2.7 –3.6 V", None, "N"),
    ("3.6 V", "Supply voltage 2.7 –3.6 V", None, "N"),
    ("-70 C", "Operating temp 0 –70 °C", None, "N"),
    ("-3.6 V", "Supply voltage 2.7\n–3.6 V", None, "N"),
    # M3: a soft hyphen or a dash look-alike next to a digit.
    ("40 C", "Temperature ­40 to 85 C", None, "N"),
    ("40 C", "Temperature ˗40 to 85 C", None, "N"),
    ("40 C", "Temperature ➖40 to 85 C", None, "N"),
    ("40 C", "Temperature ⸺40 to 85 C", None, "N"),
    ("40 C", "Temperature ⁃40 to 85 C", None, "N"),
    # M4: a flattened power of ten.
    ("1 A", "Leakage 1 x 10-6 A max", None, "N"),
    ("6", "Leakage 1 x 10-6 A max", None, "N"),
    ("10 A", "Leakage 1 x 10-6 A max", None, "N"),
    # Low.
    ("10 cycles", "Endurance 10 ⁶ cycles", None, "N"),
    ("2 VDD", "Threshold ½ VDD typical", None, "N"),
    ("4", "Duty cycle ¾ of period", None, "N"),
    ("102", "Value 10₂ binary here", None, "N"),
    ("12", "Supply rail VEE- 12V here", None, "N"),
    ("1.5", "Value 1.5 E-3 ohm here", None, "N"),
    ("active low", "The RESET pin is active high", None, "V"),
    ("five", "The RESET pin is active high", None, "V"),
    ("", "The RESET pin is active high", None, "V"),
    ("2 V", "Supply VDD(2) 3.6 V max", None, "N"),
    ("1 V", "Supply VDD 3.6(1) V max", None, "N"),
    # Hours read as hex: refused, not pinned as accepted.
    ("0x12", "Data retention 12h minimum", None, "N"),
]
RULES = {"N": Rule.NUMBER, "V": Rule.VALUE}


@pytest.mark.parametrize(
    "value, quote, page, expected",
    REVIEW_THREE,
    ids=[f"{value!r}-{quote[:24]!r}" for value, quote, _, _ in REVIEW_THREE],
)
def test_the_third_review_cases_are_refused(
    value: str, quote: str, page: str | None, expected: str
) -> None:
    """Refuse each value the third review showed accepted, by the rule that owns it."""
    problem = check_citation(value, quote, 1, [quote if page is None else page])
    assert rule(problem) is RULES[expected]


def test_a_superscript_after_a_space_is_on_the_page_only_with_its_space() -> None:
    """Refuse a quote that drops the space before "⁶": spacing between digits counts."""
    page = "Endurance 10 ⁶ cycles"
    assert rule(check_citation("10", "Endurance 10⁶ cycles", 1, [page])) is Rule.QUOTE


def test_every_occurrence_of_the_quote_must_back_the_value() -> None:
    """Refuse "-3.6" when one place says "2.7-3.6" and another "2.7 -3.6".

    The first place backs neither "-3.6" nor (as a range) a sign; the second backs
    nothing. One good place may not launder a bad one.
    """
    quote = "Supply voltage 2.7–3.6 V A"
    page = "Supply voltage 2.7–3.6 V A. Supply voltage 2.7 –3.6 V A"
    assert rule(check_citation("-3.6", quote, 1, [page])) is Rule.NUMBER
    assert rule(check_citation("3.6 V", quote, 1, [page])) is Rule.NUMBER
    assert check_citation("3.6 V", quote, 1, [quote]) is None
    twice = "Default address 0x70. Reset value 0x00. Default address 0x70. Reset"
    assert check_citation("0x70", "Default address 0x70.", 1, [twice]) is None
    other = "Default address 0x70. Reset value 0x00. Default address 0x70h. Reset"
    assert check_citation("0x70", "Default address 0x70", 1, [other]) is None
    message = check_citation("0x71", "Default address 0x70.", 1, [twice])
    assert "every one of the 2 places" in message.message  # type: ignore[union-attr]


def test_the_quote_may_differ_from_the_page_and_the_page_decides() -> None:
    """Judge the page's own numbers inside the quote, not the quote's spelling."""
    page = "Output current 1 000 mA max"
    quote = "Output current 1 000 mA max"
    assert citations.quote_on_page(quote, page)
    assert rule(check_citation("1 mA", quote, 1, [page])) is Rule.NUMBER
    page = "Supply voltage 2.7 –3.6 V"
    assert rule(check_citation("-3.6 V", "Supply voltage 2.7-3.6 V", 1, [page])) is (
        Rule.NUMBER
    )


@pytest.mark.parametrize(
    "value, quote, accepted",
    [
        ("active low", "The RESET pin is active low", True),
        ("active low", "The RESET pin is active high", False),
        ("low", "Inputs allow a low voltage", True),
        ("low", "Inputs allow a high voltage", False),  # "low" inside "allow"
        ("pull-up", "with an internal pullup resistor", True),
        ("RESET", "The reset pin is active low", False),  # case is kept
        ("", "The RESET pin is active low", False),
        ("active †", "The RESET pin is active † low", False),
    ],
)
def test_a_text_value_is_whole_words_of_the_quote(
    value: str, quote: str, accepted: bool
) -> None:
    """Accept a text-only value only as whole words of the quote, in order."""
    problem = check_citation(value, quote, 1, [quote], text_only=True)
    assert (problem is None) is accepted
    if not accepted:
        assert rule(problem) is Rule.VALUE


def test_a_text_value_must_be_in_every_occurrence() -> None:
    """Refuse a text value that one occurrence of the quote has and another lacks."""
    page = "RESET is active low, the pin. RESET is active, the pin low"
    assert citations.quote_on_page("RESET is active", page)
    problem = check_citation("active low", "RESET is active", 1, [page], text_only=True)
    assert rule(problem) is Rule.VALUE


@pytest.mark.parametrize(
    "value",
    ["3.3 V †", "1×10⁶", "½", "19 000", "-4k7", "+0x10"],
)
def test_a_value_outside_the_grammar_is_refused_as_unreadable(value: str) -> None:
    """Refuse a value with any part the grammar cannot read, before comparing it."""
    quote = "Anything at all on this page"
    problem = check_citation(value, quote, 1, [quote])
    assert rule(problem) is Rule.VALUE
    assert "cannot read" in problem.message  # type: ignore[union-attr]


def test_a_derived_value_is_not_read() -> None:
    """Let a derived value through on its quote alone: a person checks it."""
    quote = "Leakage 1 x 10-6 A max"
    assert check_citation("1e-6 A", quote, 1, [quote], derived=True) is None
    assert check_citation("½ VDD", quote, 1, [quote], derived=True) is None


def test_the_short_message_says_how_spaces_count() -> None:
    """Say that each run of white space counts as one character (W3).

    Changed on purpose: before W3 only a space between two digits counted.
    """
    problem = check_citation("6 V", "-0.3 6 V", 1, PAGES)
    assert rule(problem) is Rule.SHORT
    assert "each run of white space between words as one" in problem.message  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "quote, short",
    [
        ("VIN 4.5 28 V", False),  # 12 with its spaces; 9 without
        ("VIN    4.5    28    V", False),  # a run of spaces is one
        ("IQ 45 µA max", False),
        ("VIN 4.5 28 V\n", False),
        ("VIN 4.5 28", True),  # 10
        ("  VIN 4.5 28  ", True),  # the ends count nothing
        ("VIN\u200b 4.5 28", True),  # nor an invisible character
        ("-0.3 6 V", True),
        ("SW –2 32 V", True),  # 10
    ],
)
def test_w3_each_white_space_run_counts_one_toward_the_minimum(
    quote: str, short: bool
) -> None:
    """Count a quote's length with each white-space run as one character."""
    page = "Supply VIN 4.5 28 V max\nVIN    4.5    28    V\nIQ 45 µA max\nSW –2 32 V"
    page += "\nVIN\u200b 4.5 28"
    problem = check_citation("4.5", quote, 1, [page + "\n" + quote])
    assert (rule(problem) is Rule.SHORT) is short


def test_rule_names_say_what_failed() -> None:
    """Name the pin rule "pin", like the others name their check."""
    assert [r.value for r in Rule] == [
        "page",
        "quote-short",
        "quote",
        "value",
        "number",
        "pin",
    ]


# Widenings: each lets a right value through, and its test shows the wrong reading
# next to it is still refused.


@pytest.mark.parametrize(
    "value, quote",
    [
        # After a function word a dash is a sign: "to" ends no range, is not subtracted.
        ("-40 to -5 °C", "Range from −40 to −5 °C"),
        # "+/-" is plus-minus: the number is a magnitude.
        ("0.5 %", "Gain error +/-0.5 % max"),
        # A times sign without 10 after it is no power of ten.
        ("5 x 6 mm", "Package body 5 x 6 mm"),
        # No grouped number has a group after a fraction of one or two digits.
        ("1.5 to 250 mA", "Output current 1.5 250 mA"),
        # A hyphen before a letter is no sign of anything.
        ("4.5 V to 17 V", "4.5-V to 17-V Input"),
    ],
)
def test_widenings_accept_these_right_values(value: str, quote: str) -> None:
    """Accept the right values the widenings were made for."""
    assert check_citation(value, quote, 1, [quote]) is None


@pytest.mark.parametrize(
    "value, quote",
    [
        ("-0.5 %", "Gain error +/-0.5 % max"),
        ("-40", "Range VDD −40 to −5 °C"),
        ("2 mm", "Package body 2 x 10 mm"),  # "x 10" refuses both
        ("1.502 mA", "Output current 1.502 50 mA"),  # a fraction of three: maybe one
        ("50 mA", "Output current 1.502 50 mA"),
    ],
)
def test_widenings_still_refuse_the_wrong_readings(value: str, quote: str) -> None:
    """Refuse the readings next to each widening that may be wrong."""
    assert check_citation(value, quote, 1, [quote]) is not None


@pytest.mark.parametrize(
    "value, quote, page",
    [
        # Follow-up 3 of the fifth review: the quote's own spaces counted.
        ("5", "a b c d e f 5", "abcdef 5"),
        ("4.5 V", "V I N 4 . 5 V", "VIN4.5V supply"),
        ("28 V", "VIN  4.5  28  V", "VIN4.5 28V supply"),
    ],
)
def test_w3_counts_the_page_spacing_not_the_quotes(
    value: str, quote: str, page: str
) -> None:
    """Refuse a quote whose length comes from spaces the page does not have."""
    assert rule(check_citation(value, quote, 1, [page])) is Rule.SHORT


def test_w3_counts_the_shortest_place_the_quote_is() -> None:
    """Refuse when any occurrence of the quote is short on the page."""
    page = "VIN 4.5 28 V max\nVIN4.5 28V max"
    assert rule(check_citation("28 V", "VIN 4.5 28 V", 1, [page])) is Rule.SHORT
    assert check_citation("28 V", "VIN 4.5 28 V", 1, ["VIN 4.5 28 V max"]) is None


# W1: a dash glued to a number, after a symbol or label word and a table-cell gap of
# -layout text (two or more spaces, a tab, or a blank line), is a sign.


@pytest.mark.parametrize(
    "value, page",
    [
        ("-0.3 V", "Input voltage    VIN      –0.3     19     V"),
        ("-2 V", "Switch node SW      –2      32      V"),
        ("-65 °C", "Storage temperature, Tstg      –65      150     °C"),
        ("-40 °C", "Ambient TA\t–40\t125\t°C"),
        ("-0.5 V", "VIL\n\n–0.5 0.8 V"),  # a blank line: a gap in raw text
        ("-0.3 to 19 V", "Input voltage    VIN      –0.3     19     V"),
    ],
)
def test_w1_a_dash_after_a_word_and_a_cell_gap_is_a_sign(value: str, page: str) -> None:
    """Accept a negative number after a symbol in its own table cell."""
    assert check_citation(value, page, 1, [page]) is None


@pytest.mark.parametrize(
    "value, page",
    [
        # A single space: "VIN -0.3" may be VIN minus 0.3.
        ("-0.3 V", "Input voltage VIN –0.3 19 V"),
        ("0.3 V", "Input voltage    VIN      –0.3     19     V"),
        # A single line break, with or without indentation, is no gap.
        ("-0.3 V", "Input voltage VIN\n–0.3 19 V"),
        ("-0.3 V", "Input voltage VIN\n        –0.3 19 V"),
        # A grouping space in the run: no gap.
        ("-0.3 V", "Input voltage VIN \u00a0 –0.3 19 V"),
        ("-0.3 V", "Input voltage VIN  \u00a0–0.3 19 V"),
        ("-0.3 V", "Input voltage VIN\u00a0\u00a0  –0.3 19 V"),
        # After a number (M2 of the third review), a unit, an exponent letter or "x".
        ("-3.6 V", "Supply voltage 2.7     –3.6 V"),
        ("-3.6 V", "Supply voltage 2.7 V     –3.6 V"),
        ("-70 °C", "Operating temp 0     –70 °C"),
        ("-3", "Value level 1.5 E     -3 ohm"),
        ("-6", "Leakage level 1 x     -6 A"),
        # After a unit spelt out or with a suffix (fifth review, follow-up 2).
        ("-3.6", "Supply 2.7 Volts    –3.6 Volts range"),
        ("-3.6", "Supply 2.7 volts    –3.6 volts range"),
        ("-3.6", "Supply 2.7 Vdc    –3.6 Vdc range"),
        ("-3.6", "Supply 2.7 VDC    –3.6 VDC range"),
        ("-3.6", "Supply 2.7 Vac    –3.6 Vac range"),
        ("-3.6", "Supply 2.7 Vpk    –3.6 Vpk range"),
        ("-3.6", "Supply 2.7 mVpk    –3.6 mVpk range"),
        ("-3.6", "Supply 2.7 Vrms    –3.6 Vrms range"),
        ("-1.5", "Load 0.5 Amps    –1.5 Amps range"),
        ("-1.5", "Load 0.5 amps    –1.5 amps range"),
        ("-1.5", "Load 0.5 Amperes    –1.5 Amperes range"),
        ("-2", "Power 1 Watts    –2 Watts range"),
        ("-2", "Value 1 Ohms    –2 Ohms range"),
        ("-2", "Value 1 kohms    –2 kohms range"),
        ("-40", "Ambient 25 degrees    –40 degrees"),
        ("-3", "Delay 1 seconds    –3 seconds"),
        # After ")": not a word, so not widened.
        ("-5 V", "SW (<10ns transient)     –5     35     V"),
        # The dash after a gap but spaced from the number.
        ("-0.3 V", "Input voltage    VIN      –  0.3     19     V"),
    ],
)
def test_w1_still_refuses_a_dash_of_uncertain_role(value: str, page: str) -> None:
    """Refuse a sign after one space, a line break, a number, a unit or a ")"."""
    assert rule(check_citation(value, page, 1, [page])) is Rule.NUMBER


# W2: the three-digit grouping rule does not fire across a cell gap: a thousands
# separator is at most one character wide, never two spaces, a tab or a blank line.


@pytest.mark.parametrize(
    "value, page",
    [
        ("-65 to 150 °C", "Tstg      –65      150     °C"),
        ("0.6 V", "VFB      0.594      0.6      0.606     V"),
        ("150 °C", "Temperature Rising      150     165     °C"),
        ("300 µA", "Charge pump current      162     300     600     µA"),
        ("100 to 200 kHz", "Frequency\t100\t200\tkHz"),
        ("100 cycles", "Endurance 100\n\n000 cycles"),
    ],
)
def test_w2_three_digits_across_a_cell_gap_are_not_a_group(
    value: str, page: str
) -> None:
    """Accept numbers of a table row whose cells hold three-digit numbers."""
    assert check_citation(value, page, 1, [page]) is None


@pytest.mark.parametrize(
    "value, page",
    [
        ("100 cycles", "Write endurance 100 000 cycles"),
        ("100 cycles", "Write endurance 100\n000 cycles"),
        ("100 cycles", "Write endurance 100\n      000 cycles"),
        ("0.6 V", "VFB 0.594 0.6 0.606 V"),
        ("100 cycles", "Write endurance 100 \u2009 000 cycles"),  # grouping space
        ("1 mA", "Output current 1\u00a0 000 mA max"),
    ],
)
def test_w2_still_refuses_a_possible_group(value: str, page: str) -> None:
    """Refuse halves of a possible group: one space, one line break, a thin space."""
    assert rule(check_citation(value, page, 1, [page])) is Rule.NUMBER


# A bullet "•" at the start of a line opens like "(": nothing before it can be a
# range's first end or be subtracted from, and it is not a mark on the number.


@pytest.mark.parametrize(
    "value, page",
    [
        ("60 V", "•    60-V VDS(MAX) rating"),
        ("60 V", "Features\n  • 60-V VDS(MAX) rating"),
        ("60 V", "Features\n•60-V VDS(MAX) rating"),
        ("-40 °C", "Features\n• –40°C to 125°C ambient"),
        ("-40 °C", "Features\n\t•\t–40°C to 125°C ambient"),
    ],
)
def test_a_bullet_at_the_start_of_a_line_is_an_opener(value: str, page: str) -> None:
    """Accept a number after a line-start bullet, and a sign after it."""
    assert check_citation(value, page, 1, [page]) is None


@pytest.mark.parametrize(
    "value, page",
    [
        ("60 V", "Rating x • 60-V VDS(MAX) rating"),  # mid-line: unknown
        ("60 V", "Rating x •60-V VDS(MAX) rating"),
        ("40 °C", "Features\n• –40°C to 125°C ambient"),  # the sign stays
        ("60 V", "Features\n∙ 60-V VDS(MAX) rating"),  # a bullet operator: unknown
        ("-40 °C", "Rating x • –40°C to 125°C ambient"),
    ],
)
def test_a_bullet_inside_a_line_is_still_unknown(value: str, page: str) -> None:
    """Refuse a number next to a bullet inside a line, or another bullet character."""
    assert rule(check_citation(value, page, 1, [page])) is Rule.NUMBER


# Real rows: pdftotext 24.02 -layout text of the absolute maximum ratings, ESD
# ratings, recommended operating conditions, electrical and switching characteristics
# of a public TI datasheet (TPS54331, SLVS839H), copied unchanged.

TPS54331 = (
    (Path(__file__).resolve().parents[1] / "fixtures" / "datasheet_rows")
    .joinpath("tps54331_layout.txt")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "value, quote",
    [
        ("-0.3 V", "VIN –0.3 30 EN –0.3 6"),  # W1: a symbol, a gap, a sign
        ("-0.3 V", "VSENSE –0.3 3"),
        ("-0.6 V", "Output voltage PH –0.6 30 V"),
        ("30 V", "Output voltage PH –0.6 30 V"),
        ("-40 °C", "Operating junction temperature, TJ –40 150 °C"),
        ("150 °C", "Operating junction temperature, TJ –40 150 °C"),
        ("-65 °C", "Storage temperature range, Tstg –65 150 °C"),  # W1 and W2
        ("150 °C", "Storage temperature range, Tstg –65 150 °C"),
        ("-40 °C", "TJ Operating junction temperature –40 150 °C"),
        ("3.5 V", "Operating input voltage on (VIN pin) 3.5 28 V"),
        ("4 μA", "Shutdown supply current EN = 0 V, VIN = 12 V, –40°C to 85°C 1 4 μA"),
        ("190 μA", "non-switching supply current VSENSE = 0.85 V 110 190 μA"),  # W2
        ("0.8 V", "Voltage reference 0.772 0.8 0.828 V"),  # W2 after a fraction
        ("0.772 V", "Voltage reference 0.772 0.8 0.828 V"),
        ("±7 μA", "source and sink current V(COMP) = 1 V, 100-mV overdrive ±7 μA"),
        ("5.8 A", "Current-limit threshold VIN = 12 V 3.5 5.8 A"),
        ("165 °C", "Thermal shutdown 165 °C"),
        ("570 kHz", "Switching frequency VIN = 12 V, 25°C 456 570 684 kHz"),  # W2
        ("130 ns", "Minimum controllable on time VIN = 12 V, 25°C 105 130 ns"),
        ("93%", "Maximum controllable duty ratio(1) BOOT-PH = 6 V 90% 93%"),
        ("200 mΩ", "BOOT-PH = 3 V, VIN = 3.5 V 115 200 mΩ"),  # the ohm sign
        ("2 kV", "JEDEC JS-001, all pins(1) –2 2 kV"),
    ],
)
def test_real_layout_rows_back_their_right_values(value: str, quote: str) -> None:
    """Accept right values quoted from real -layout table rows."""
    assert check_citation(value, quote, 1, [TPS54331]) is None


@pytest.mark.parametrize(
    "value, quote",
    [
        # Wrong values: a changed digit, a dropped or added sign, another unit.
        ("0.3 V", "VIN –0.3 30 EN –0.3 6"),
        ("-0.6 V", "VSENSE –0.3 3"),
        ("40 °C", "Operating junction temperature, TJ –40 150 °C"),
        ("-150 °C", "Storage temperature range, Tstg –65 150 °C"),
        ("5.8 V", "Current-limit threshold VIN = 12 V 3.5 5.8 A"),
        ("-7 μA", "source and sink current V(COMP) = 1 V, 100-mV overdrive ±7 μA"),
        ("0.82 V", "Voltage reference 0.772 0.8 0.828 V"),
        ("165 °F", "Thermal shutdown 165 °C"),
        ("200 Ω", "BOOT-PH = 3 V, VIN = 3.5 V 115 200 mΩ"),
        # Right values the grammar still refuses: the dash has no certain role.
        ("-2 kV", "JEDEC JS-001, all pins(1) –2 2 kV"),  # after ")"
        ("-1 μA", "Input current Enable threshold – 50 mV –1 μA"),  # after a unit
        ("-5 V", "PH (10-ns transient from ground to negative peak) –5"),  # ")"
    ],
)
def test_real_layout_rows_refuse_wrong_and_uncertain_values(
    value: str, quote: str
) -> None:
    """Refuse wrong values from real rows, and the right ones whose sign is unsure."""
    assert rule(check_citation(value, quote, 1, [TPS54331])) is Rule.NUMBER


def test_real_layout_rows_show_the_unit_and_role_blind_spots() -> None:
    """Pin the row unit and the blind spots on real rows: a neighbour's role, no unit.

    Changed on purpose: "570 MHz" was pinned as passing (its unit in another cell)
    until a number took its row's unit column.
    """
    quote = "Switching frequency VIN = 12 V, 25°C 456 570 684 kHz"
    assert rule(check_citation("570 MHz", quote, 1, [TPS54331])) is Rule.NUMBER
    assert check_citation("570 kHz", quote, 1, [TPS54331]) is None
    assert check_citation("684 kHz", quote, 1, [TPS54331]) is None  # max, not typ
    assert rule(check_citation("684 MHz", quote, 1, [TPS54331])) is Rule.NUMBER
    quote = "Voltage reference 0.772 0.8 0.828 V"
    assert rule(check_citation("0.8 mV", quote, 1, [TPS54331])) is Rule.NUMBER
    quote = "Error amplifier DC gain(1) VSENSE = 0.8 V 800 V/V"
    assert check_citation("800 V", quote, 1, [TPS54331]) is None  # "/V" not read


# Blind spots, pinned: each passes, and no text check can tell it from a right value.


@pytest.mark.parametrize(
    "value, quote, page",
    [
        # Roles: a neighbour in the same quote passes.
        ("4.4 V", "Wake up VIN voltage 4.0 4.2 4.4 V", None),
        (
            "-0.3 V",
            "AGND –0.3 0.3 V SW –2 32 V",
            "AGND    –0.3   0.3  V\nSW  –2  32  V",
        ),
        # A cross-reference reads as a range.
        ("10", "see Figure 8-10 for details", None),
        ("8", "see Figure 8-10 for details", None),
        # A power of ten flattened with no mark.
        ("6", "Endurance 10\n6 cycles min", None),
        ("106", "Endurance 106 cycles min", None),
        ("10", "Endurance 10 6 cycles min", None),
        # A footnote digit glued to a number.
        ("3.61", "VDD max is 3.61 V", None),
        # A rail offset in its own cell, read as a sign by W1.
        ("-0.4 V", "Output high VOH VCC    –0.4 V", None),
        # A dash list item after a label and a gap, read as a sign by W1.
        ("-5 V", "Notes    –5 V tolerant inputs", None),
    ],
)
def test_the_documented_blind_spots_pass(
    value: str, quote: str, page: str | None
) -> None:
    """Accept each documented blind spot: a change here must update the docstring."""
    assert check_citation(value, quote, 1, [quote if page is None else page]) is None


# Right values that the grammar still refuses, on purpose. Each is pinned so that a
# change to it is a decision, not an accident. The value must be written as the page
# writes it, or marked derived.


@pytest.mark.parametrize(
    "value, quote",
    [
        ("0x1F", "Register address 1F default"),  # bare hex: also 1 farad
        ("0x70", "Default address 70h here"),  # may be hours
        ("1000 mA", "Output current 1 000 mA"),
        ("1000 mA", "Output current 1,000 mA"),
        ("1.5 V", "Threshold 1,5 V typical"),  # decimal comma
        ("5 V", "Supply voltage 5.0 V typ"),
        ("3.6 V", "Supply VDD 3.6¹ V max"),  # footnote superscript
        ("3 A", "Output current 3A continuous"),  # "3A" is a code
        ("-0.3 V", "Input voltage VIN -0.3 19 V"),  # after a symbol
        ("-40 to 125 °C", "Operating temperature −40 to 125 °C"),  # after a noun
        ("-5.0 V", "Negative rail VEE: −5.5 −5.0 −4.5 V"),  # second of a row
        ("3.0 to 5.5 V", "Supply voltage 3.0 – 5.5 V"),  # spaced dash
        ("2.7 to 3.6 V", "Supply voltage 2.7 V-3.6 V"),  # units and spaces
        ("0x08 to 0x77", "Address range 0x08-0x77 valid"),  # hex range
        ("10 to 20 dB", "Gain range: 10-20 dB here"),  # a range from 10
        ("100 to 200 kHz", "Frequency 100 200 kHz"),  # may be 100200
    ],
)
def test_right_values_the_grammar_still_refuses(value: str, quote: str) -> None:
    """Refuse these right values: each has a reading in which it is wrong."""
    assert check_citation(value, quote, 1, [quote]) is not None
    assert check_citation(value, quote, 1, [quote], derived=True) is None


# Generated: refuse by default. Put a character or construct the grammar has no rule
# for next to a known token, and read every number a careless reader might see in the
# page. None may be accepted, except the exact page word when the insert makes a code.

BASE_TOKENS = ["19", "3.6", "-40", "0x1F", "2.5e6", "10^6", "4k7", ".25", "1.25"]
OUTSIDE = {
    "soft hyphen": "­",
    "modifier minus": "˗",
    "heavy minus": "➖",
    "two-em dash": "⸺",
    "hyphen bullet": "⁃",
    "horizontal bar": "―",
    "small em dash": "﹘",
    "double oblique hyphen": "⸗",
    "zero width space": "​",
    "zero width non-joiner": "‌",
    "zero width joiner": "‍",
    "word joiner": "⁠",
    "byte order mark": "﻿",
    "superscript two": "²",
    "superscript six": "⁶",
    "superscript minus": "⁻",
    "superscript plus": "⁺",
    "superscript one": "¹",
    "subscript two": "₂",
    "subscript minus": "₋",
    "one half": "½",
    "three quarters": "¾",
    "one third": "⅓",
    "fraction slash": "⁄",
    "arabic-indic three": "٣",
    "devanagari three": "३",
    "bengali three": "৩",
    "thai three": "๓",
    "mathematical bold three": "\U0001d7d1",
    "asterisk": "*",
    "dagger": "†",
    "double dagger": "‡",
    "caret": "^",
    "middle dot": "·",
    "dot operator": "⋅",
    "bullet operator": "∙",
    "square root": "√",
    "per mille": "‰",
    "combining acute": "́",
    "ellipsis": "…",
}
GROUPING_SPACES = {
    "no-break space": " ",
    "figure space": " ",
    "thin space": " ",
    "narrow no-break space": " ",
    "en space": " ",
    "hair space": " ",
    "medium mathematical space": " ",
    "ideographic space": "　",
}
LETTER_SUFFIXES = ["Q", "Y", "b", "h", "E", "CLK", "x", "Z", "B", "D", "Fh", "sec"]
SUPERSCRIPT_DIGITS_ALL = str.maketrans(
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺₀₁₂₃₄₅₆₇₈₉₋", "0123456789-+0123456789-"
)
NUMBER_LIKE = re.compile(
    r"[-+]?(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE^][-+]?\d+)?|\.\d+)"
)


def _candidates(page: str, token: str, insert: str) -> set[str]:
    """Return every number a careless reader might take from ``page``."""
    seen = set()
    readings = [
        page,
        unicodedata.normalize("NFKC", page),
        page.translate(SUPERSCRIPT_DIGITS_ALL),
        "".join(c for c in page if c.isprintable() and not unicodedata.combining(c)),
    ]
    for text in readings:
        seen.update(NUMBER_LIKE.findall(text))
    unsigned = token.lstrip("-")
    plain = unicodedata.normalize("NFKC", insert).translate(SUPERSCRIPT_DIGITS_ALL)
    seen.update(
        {token, unsigned, "-" + unsigned, "+" + unsigned, unsigned + plain}
        | {plain + unsigned, token + insert, insert + token, "0x" + unsigned}
    )
    seen.update(c.strip() for c in list(seen))
    return {c for c in seen if any(ch.isdigit() for ch in c)}


def _outside_cases() -> list[tuple[str, str, str, frozenset[str]]]:
    """Return (name, page, token, allowed) for every insert next to every token."""
    cases = []
    for token in BASE_TOKENS:
        for name, insert in OUTSIDE.items():
            for where, middle in [
                ("after", token + insert),
                ("before", insert + token),
                ("spaced-after", token + " " + insert),
                ("spaced-before", insert + " " + token),
                ("between", token + insert + "5"),
            ]:
                page = LEAD + middle + TAIL
                # "19^5" is a power written with a caret: the one insert that makes
                # a word of the grammar, which backs only itself.
                power = insert == "^" and where == "between" and "^" not in token
                allowed = frozenset({middle}) if power else frozenset()
                cases.append((f"{token}-{name}-{where}", page, token + insert, allowed))
        if token[-1].isdigit():
            for name, space in GROUPING_SPACES.items():
                for right in ("5", "000", "25"):
                    page = LEAD + token + space + right + TAIL
                    cases.append((f"{token}-{name}-{right}", page, token, frozenset()))
        for suffix in LETTER_SUFFIXES:
            word = token + suffix
            page = LEAD + word + TAIL
            cases.append((f"{token}-{suffix}", page, token + suffix, frozenset({word})))
    return cases


OUTSIDE_CASES = _outside_cases()


@pytest.mark.parametrize(
    "page, token, allowed",
    [case[1:] for case in OUTSIDE_CASES],
    ids=[case[0] for case in OUTSIDE_CASES],
)
def test_nothing_outside_the_grammar_backs_a_value(
    page: str, token: str, allowed: frozenset[str]
) -> None:
    """Accept no number read next to an unknown construct, but the exact page word."""
    accepted = {
        value
        for value in _candidates(page, token, token)
        if check_citation(value, page, 1, [page]) is None
    }
    assert accepted <= allowed


DASHES = ["-", "−", "‐", "‑", "‒", "–", "—", "﹣", "－"]
# Constructs where a dash's role is not certain, and the numbers that stay certain.
UNCERTAIN_DASHES = [
    ("Input high VDD{d}0.3 V max", set()),
    ("Supply rail 2.7 {d}3.6 V", {"2.7"}),
    ("Supply rail 2.7\n{d}3.6 V", {"2.7"}),
    ("Supply rail 3.0 {d} 5.5 V", {"3.0"}),
    ("Supply rail 2.7{d} 3.6 V", {"2.7"}),
    ("Leakage level 10{d}6 A", set()),
    ("Leakage level 10{d}12 A", set()),
    ("Leakage level 10 {d}6 A", set()),
    ("Leakage level 1 x 10{d}6 A", set()),
    ("Leakage level 1 × 10 {d}6 A", set()),
    ("Leakage level 1x10 {d}6 A", set()),
    ("Input voltage VIN {d}0.3 19 V", {"19"}),
    ("Supply rail VEE{d} 12V here", set()),
    ("Value level 1.5 E{d}3 ohm", set()),
    ("Analog rail 3V3{d}5 V here", {"5"}),
    ("Transistor 2N7002{d}7 here", set()),
    ("Sweep from 5{d}3 V here", set()),
    ("Address range 0x08{d}0x77 here", set()),
    ("Supply rail ½{d}40 V here", set()),
    ("Supply rail 5 {d}{d}3 V here", {"5"}),
]


@pytest.mark.parametrize("dash", DASHES)
@pytest.mark.parametrize("template, certain", UNCERTAIN_DASHES)
def test_a_dash_with_no_certain_role_backs_no_sign(
    template: str, certain: set[str], dash: str
) -> None:
    """Refuse every number a dash of uncertain role touches, signed or not."""
    page = template.format(d=dash)
    candidates = set(
        NUMBER_LIKE.findall(page.translate(str.maketrans(dict.fromkeys(DASHES, "-"))))
    )
    candidates |= {c.lstrip("-+") for c in candidates} | {
        "-" + c.lstrip("-+") for c in candidates
    }
    accepted = {
        value for value in candidates if check_citation(value, page, 1, [page]) is None
    }
    assert accepted <= certain


@pytest.mark.parametrize(
    "value, quote",
    [
        ("2 VDD", "Threshold 1/2 VDD typical"),  # a fraction, not 1 and 2
        ("30 min", "Reset at 12:30 each day"),  # a time
        ("6 mm", "Package body 5×6 mm"),  # glued: one character between digits
        ("3", "Ratio of 1:3 here and"),
    ],
)
def test_one_character_between_two_digits_makes_both_unknown(
    value: str, quote: str
) -> None:
    """Refuse a number that a "/", ":" or "×" glues to another number."""
    assert rule(check_citation(value, quote, 1, [quote])) is Rule.NUMBER


@pytest.mark.parametrize(
    "value, quote",
    [
        ("3.6 V", "Supply ½ 2.7-3.6 V here"),  # the first end is unknown
        ("2.7 V", "Supply 2.7-3.6 * V here"),  # the second end is unknown
    ],
)
def test_a_range_is_as_known_as_its_less_known_end(value: str, quote: str) -> None:
    """Refuse one end of a range when the other end is unknown: the dash then is too."""
    assert rule(check_citation(value, quote, 1, [quote])) is Rule.NUMBER
    clean = quote.replace("½ ", "").replace(" *", "")
    assert check_citation(value, clean, 1, [clean]) is None
