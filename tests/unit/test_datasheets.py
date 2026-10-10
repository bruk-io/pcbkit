"""Unit tests for pcbkit.datasheets.judge: is this datasheet the part's?

The page texts are short stand-ins written to show what the real PDFs showed in the
2026-10-09 measurement (plans/wp14-parts.md), one case per mistake found there.
"""

from __future__ import annotations

import pytest

from pcbkit import datasheets
from pcbkit.datasheets import Reason, Status, judge


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
    assert "confirm" in verdict.next_step


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
