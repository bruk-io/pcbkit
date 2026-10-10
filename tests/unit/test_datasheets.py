"""Unit tests for pcbkit.datasheets.judge: is this datasheet the part's?

The page texts are short stand-ins written to show what the real PDFs showed in the
2026-10-09 measurement (plans/wp14-parts.md), one case per mistake found there.
"""

from __future__ import annotations

import pytest

from pcbkit import datasheets
from pcbkit.datasheets import ConfirmationRefused, Reason, Status, judge


def test_the_whole_part_number_and_the_maker_verify_it() -> None:
    """Verify a datasheet that lists the orderable number and names its maker."""
    pages = [
        "INA226 Current and Power Monitor\nTexas Instruments",
        "Package option addendum\nINA226AIDGSR ACTIVE VSSOP DGS 10 2500",
    ]
    verdict = judge(pages, "INA226AIDGSR", "Texas Instruments")
    assert verdict.status is Status.VERIFIED and verdict.reason is None
    assert (verdict.part_number_found, verdict.part_number_page) == ("ina226aidgsr", 2)
    assert (verdict.manufacturer_name, verdict.manufacturer_page) == (
        "Texas Instruments",
        1,
    )
    assert verdict.next_step == ""


def test_a_missing_reel_code_of_two_characters_is_let_through() -> None:
    """Accept a number whose last two characters (a reel code) are not printed."""
    pages = ["DS3231 RTC. Ordering: DS3231SN# 16 SO. Maxim Integrated"]
    verdict = judge(pages, "DS3231SN#T&R", "Analog Devices")
    assert verdict.status is Status.VERIFIED
    assert verdict.manufacturer_name == "Maxim Integrated"


def test_three_missing_characters_are_too_many_even_for_a_long_number() -> None:
    """Refuse a number missing three characters: past a reel code, it is the package."""
    pages = ["SN74LVC1G17 Buffer. Ordering: SN74LVC1G17DCKR. Texas Instruments"]
    verdict = judge(pages, "SN74LVC1G17DBVR", "Texas Instruments")
    assert verdict.reason is Reason.PART_NUMBER_MISSING
    assert verdict.part_number_found == "sn74lvc1g17d"


def test_the_part_number_is_never_pieced_together_across_words() -> None:
    """Refuse AO3401A from "AO3401 Alpha": the next word is not part of the number."""
    pages = ["AO3401 Alpha & Omega Semiconductor P-Channel MOSFET"]
    verdict = judge(pages, "AO3401A", "Alpha & Omega")
    assert verdict.reason is Reason.PART_NUMBER_MISSING
    assert verdict.part_number_found == "ao3401"


def test_the_slack_never_turns_one_part_into_its_sibling_when_short() -> None:
    """Refuse AO3401 for AO3401A: under eight characters, the whole number must show."""
    pages = ["AO3401 30V P-Channel MOSFET. Alpha & Omega Semiconductor"]
    verdict = judge(pages, "AO3401A", "Alpha & Omega")
    assert verdict.reason is Reason.PART_NUMBER_MISSING
    assert verdict.part_number_found == "ao3401"


@pytest.mark.parametrize(
    "mpn, maker, text",
    [
        # Another maker's through-hole datasheet for the SOD-123 part.
        ("1N5819HW-7-F", "Diodes Incorporated", "1N5817 thru 1N5819 Vishay"),
        # The older BSS138 datasheet for onsemi's own BSS138LT1G.
        ("BSS138LT1G", "onsemi", "BSS138 N-Channel Logic Level FET onsemi"),
        # A dual Schottky datasheet from another maker of the same part type.
        ("BAT54SLT1G", "onsemi", "BAT54S Dual Schottky Diodes Incorporated"),
        # The chip's datasheet for the development board.
        ("ESP32-S3-DEVKITC-1-N8R8", "Espressif", "ESP32-S3 Series Espressif"),
    ],
)
def test_a_sibling_parts_datasheet_is_only_a_candidate(
    mpn: str, maker: str, text: str
) -> None:
    """Refuse a datasheet that has the family but not this part's whole number."""
    verdict = judge([text], mpn, maker)
    assert verdict.status is Status.CANDIDATE
    assert verdict.reason is Reason.PART_NUMBER_MISSING
    assert "sibling" in verdict.next_step


def test_a_word_in_the_text_is_not_the_maker() -> None:
    """Never take "diodes" in a diode datasheet for Diodes Incorporated."""
    pages = ["1N5819HW-7-F Schottky barrier diodes. Vishay General Semiconductor"]
    verdict = judge(pages, "1N5819HW-7-F", "Diodes Incorporated")
    assert verdict.reason is Reason.MANUFACTURER_MISSING
    assert verdict.manufacturer_name is None


def test_a_maker_is_matched_as_whole_words_across_line_breaks() -> None:
    """Find a name split over a line break, and not inside a longer word."""
    assert judge(["PCA9685PW,118\nNXP"], "PCA9685PW,118", "NXP").status is (
        Status.VERIFIED
    )
    assert judge(["PCA9685PW,118 ANXP"], "PCA9685PW,118", "NXP").reason is (
        Reason.MANUFACTURER_MISSING
    )
    verdict = judge(
        ["INA226AIDGSR Texas\n  Instruments"], "INA226AIDGSR", "Texas Instruments"
    )
    assert verdict.manufacturer_name == "Texas Instruments"


def test_a_built_number_asks_for_a_confirmation_not_another_source() -> None:
    """Say a series datasheet needs confirming, rather than calling it a sibling's."""
    pages = ["RC series thick film chip resistors. RC0603 FR-07 10K L. Yageo"]
    verdict = judge(pages, "RC0603FR-0710KL", "Yageo", built=True)
    assert verdict.reason is Reason.PART_NUMBER_BUILT
    assert "confirm" in verdict.next_step.lower()


def test_a_pdf_with_no_text_is_only_a_candidate() -> None:
    """Refuse a PDF with no text to check, such as a scan."""
    verdict = judge(["", "  \n"], "INA226AIDGSR", "Texas Instruments")
    assert (verdict.status, verdict.reason) == (Status.CANDIDATE, Reason.NO_TEXT)


def test_an_unknown_maker_is_looked_for_by_its_own_name() -> None:
    """Use the name given when the table does not know the maker."""
    assert datasheets.maker_names("Acme Parts") == ("Acme Parts",)
    verdict = judge(["ACME-1234567 by Acme Parts"], "ACME-1234567", "Acme Parts")
    assert verdict.status is Status.VERIFIED


def test_a_maker_is_known_by_any_of_its_names() -> None:
    """Find a maker's names from the name a distributor gives, however it is written."""
    assert datasheets.maker_names("ON Semiconductor")[0] == "onsemi"
    assert datasheets.maker_names("Microchip Technology")[0] == "Microchip"
    assert datasheets.maker_names("Diodes Incorporated") == (
        "Diodes Incorporated",
        "Diodes Inc",
    )


def test_every_reason_has_a_next_step() -> None:
    """Give a next step for every reason, so the tool always says what to do."""
    assert set(datasheets.NEXT) == set(Reason)
    assert all(step for step in datasheets.NEXT.values())


def test_no_makers_name_is_an_ordinary_word_alone() -> None:
    """Keep single ordinary words out of the names: they match any datasheet."""
    ordinary = {"diodes", "semiconductor", "instruments", "devices", "power", "ti"}
    names = [name for names in datasheets.MAKERS for name in names]
    assert not [name for name in names if name.lower() in ordinary]


def test_an_empty_maker_never_verifies() -> None:
    """Call the maker unknown when none is given: an empty name would match anywhere."""
    pages = ["Some text. \n\nINA226AIDGSR in a table.\n"]
    for nothing in ("", "   "):
        verdict = judge(pages, "INA226AIDGSR", nothing)
        assert (verdict.status, verdict.reason) == (
            Status.CANDIDATE,
            Reason.MANUFACTURER_UNKNOWN,
        )
    assert datasheets.maker_names("") == ()


@pytest.mark.parametrize(
    "mpn, maker, text, dropped",
    [
        ("SS34-E3/57T", "Vishay", "SS32 thru SS36. SS34 Vishay General", "-E3/57T"),
        ("SM04B-SRSS-TB(LF)(SN)", "JST", "SM04B-SRSS-TB 4 circuits. JST", "(LF)(SN)"),
    ],
)
def test_a_makers_own_packaging_code_may_be_missing(
    mpn: str, maker: str, text: str, dropped: str
) -> None:
    """Verify a number its maker prints without its packaging code."""
    verdict = judge([text], mpn, maker)
    assert verdict.status is Status.VERIFIED
    assert verdict.suffix_dropped == dropped


def test_a_packaging_code_is_dropped_only_for_its_own_maker() -> None:
    """Keep Vishay's code from excusing another maker's number."""
    verdict = judge(["SS34 Schottky. Acme Parts"], "SS34-E3/57T", "Acme Parts")
    assert verdict.reason is Reason.PART_NUMBER_MISSING


def test_without_its_code_the_rest_of_the_number_must_be_whole() -> None:
    """Refuse SS3 for SS34-E3/57T: dropping the code allows no other slack."""
    verdict = judge(["SS3 series. Vishay"], "SS34-E3/57T", "Vishay")
    assert verdict.reason is Reason.PART_NUMBER_MISSING


# --- confirming a candidate -------------------------------------------------------

SERIES_PAGES = [
    "RC series thick film chip resistors. Yageo",
    "GLOBAL PART NUMBER\nRC XXXX X X X XX XXXX L\n(1) SIZE 0402/0603/0805",
    "Fig. 1 RC0603",
]
LOGO_PAGES = ["P-Channel MOSFET\nAO3401A 30V SOT-23", "Ordering: AO3401A"]


def refused(*args: object) -> str:
    """Return why check_confirmation refuses ``args``; fail if it accepts them."""
    with pytest.raises(ConfirmationRefused) as caught:
        datasheets.check_confirmation(*args)  # type: ignore[arg-type]
    return str(caught.value)


def test_a_series_datasheet_is_confirmed_by_a_quote_with_its_series_code() -> None:
    """Accept a quote, on its page, naming the series where the number is explained."""
    verdict = judge(SERIES_PAGES, "RC0603FR-0710KL", "Yageo", built=True)
    assert verdict.part_number_found == "rc0603"
    datasheets.check_confirmation(
        SERIES_PAGES, "RC0603FR-0710KL", "Yageo", verdict, 2, "RC XXXX X X X XX XXXX L"
    )


def test_a_quote_must_be_on_the_page_it_names_with_its_spacing() -> None:
    """Refuse a quote from another page, or one joining words the page keeps apart."""
    verdict = judge(SERIES_PAGES, "RC0603FR-0710KL", "Yageo", built=True)
    mpn = "RC0603FR-0710KL"
    assert "not on page 1" in refused(
        SERIES_PAGES, mpn, "Yageo", verdict, 1, "RC XXXX X X X XX XXXX L"
    )
    assert "not on page 2" in refused(
        SERIES_PAGES, mpn, "Yageo", verdict, 2, "RCXXXX X X X XX XXXX L"
    )
    assert "pages 1 to 3" in refused(SERIES_PAGES, mpn, "Yageo", verdict, 4, "RC")


def test_a_quote_line_break_may_be_a_space() -> None:
    """Accept a quote that has a space where the page has a line break."""
    verdict = judge(LOGO_PAGES, "AO3401A", "Alpha & Omega")
    assert verdict.reason is Reason.MANUFACTURER_MISSING
    datasheets.check_confirmation(
        LOGO_PAGES, "AO3401A", "Alpha & Omega", verdict, 1, "MOSFET AO3401A 30V"
    )


def test_a_series_quote_must_hold_the_series_code() -> None:
    """Refuse a quote from the right page that does not show the series code."""
    verdict = judge(SERIES_PAGES, "RC0603FR-0710KL", "Yageo", built=True)
    assert "series code 'RC'" in refused(
        SERIES_PAGES, "RC0603FR-0710KL", "Yageo", verdict, 2, "GLOBAL PART NUMBER"
    )


def test_a_maker_candidate_needs_the_whole_part_number_in_the_quote() -> None:
    """Refuse a quote without the part's whole number for an unnamed maker."""
    verdict = judge(LOGO_PAGES, "AO3401A", "Alpha & Omega")
    assert "whole part number AO3401A" in refused(
        LOGO_PAGES, "AO3401A", "Alpha & Omega", verdict, 1, "P-Channel MOSFET"
    )


@pytest.mark.parametrize(
    "pages, mpn, maker",
    [
        (["BSS138 N-Channel FET onsemi"], "BSS138LT1G", "onsemi"),
        (["", " "], "INA226AIDGSR", "Texas Instruments"),
    ],
)
def test_a_sibling_or_a_textless_pdf_cannot_be_confirmed(
    pages: list[str], mpn: str, maker: str
) -> None:
    """Refuse to confirm what only another datasheet can settle."""
    verdict = judge(pages, mpn, maker)
    assert "cannot be confirmed" in refused(pages, mpn, maker, verdict, 1, pages[0])


def test_a_short_series_code_is_no_evidence() -> None:
    """Refuse a series confirmation when too little of the number was found."""
    pages = ["RC series. Yageo"]
    verdict = judge(pages, "RC0603FR-0710KL", "Yageo", built=True)
    assert verdict.part_number_found == "rc"
    assert "too little" in refused(
        pages, "RC0603FR-0710KL", "Yageo", verdict, 1, "RC series"
    )


def test_a_verified_datasheet_needs_no_confirmation() -> None:
    """Say so rather than confirm a datasheet that is verified."""
    pages = ["INA226AIDGSR Texas Instruments"]
    verdict = judge(pages, "INA226AIDGSR", "Texas Instruments")
    assert "verified already" in refused(
        pages, "INA226AIDGSR", "Texas Instruments", verdict, 1, pages[0]
    )


def test_every_confirmable_reason_says_what_the_quote_must_hold() -> None:
    """Name what to quote for each reason a confirmation can settle."""
    assert set(datasheets.QUOTE_MUST_HOLD) == datasheets.CONFIRMABLE


@pytest.mark.parametrize(
    "mpn, series",
    [
        ("RC0603FR-0710KL", "rc"),
        ("GRM188R71C104KA01D", "grm"),
        ("0ZCJ0050FF2E", "0zcj"),
        ("X7R-100", "x7r1"),
    ],
)
def test_the_series_code_is_the_leading_letters(mpn: str, series: str) -> None:
    """Take the leading letters, or the first four characters when there are few."""
    assert datasheets.series_code(mpn) == series


def test_a_series_quote_that_only_repeats_the_number_is_refused() -> None:
    """Refuse a figure label that merely shows the series and size again."""
    verdict = judge(SERIES_PAGES, "RC0603FR-0710KL", "Yageo", built=True)
    assert "only repeats 'RC0603'" in refused(
        SERIES_PAGES, "RC0603FR-0710KL", "Yageo", verdict, 3, "Fig. 1 RC0603"
    )


def test_a_series_pattern_that_starts_with_letters_and_digits_is_accepted() -> None:
    """Accept a pattern such as ABM8 - XX.XXXMHZ, where the code holds a digit."""
    pages = [
        "ABM8 series crystal. Abracon",
        "PART IDENTIFICATION\nABM8 - XX.XXXMHZ - XX - X - T",
        "ABM8-16 frequencies",
    ]
    verdict = judge(pages, "ABM8-16.000MHZ-B2-T", "Abracon", built=True)
    assert verdict.part_number_found == "abm816"
    datasheets.check_confirmation(
        pages,
        "ABM8-16.000MHZ-B2-T",
        "Abracon",
        verdict,
        2,
        "ABM8 - XX.XXXMHZ - XX - X - T",
    )
