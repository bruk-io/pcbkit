"""Judge whether a datasheet is the one for a given part.

Measured on 2026-10-09 (plans/wp14-parts.md): checking only that a part's family name
is in a PDF passes wrong datasheets, such as a sibling part's (BSS138 for BSS138LT1G)
or another maker's (Diodes Inc.'s BAT54S for onsemi's). So the judgement here looks
for the whole orderable part number and the manufacturer's name in the PDF's own
text, and never at where the link came from.

A datasheet is VERIFIED for a part when its text has:

- the part number, within one word, letters and digits only, ignoring case, missing
  at most ``TRAILING_SLACK`` characters at the end (reel and packaging codes, which
  datasheets often leave off: ``DS3231SN`` for ``DS3231SN#T&R``), and only once at
  least ``MIN_KEPT`` characters match, so ``AO3401`` never stands in for ``AO3401A``;
- one of the manufacturer's names (``MAKERS``), as whole words: "diodes" alone is in
  every diode datasheet, so Diodes Incorporated is known only by its full name.

Otherwise it is a CANDIDATE, with one reason from ``Reason``. Each reason has a next
step (``NEXT``) that the command line prints, so that a person or a model working the
tool follows the tool rather than remembering these rules. ``judge`` is pure: it takes
page texts, never a file or a URL.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

TRAILING_SLACK = 2
MIN_KEPT = 8


class Status(Enum):
    """What a datasheet is to a part."""

    VERIFIED = "VERIFIED"
    CANDIDATE = "CANDIDATE"


class Reason(Enum):
    """Why a datasheet is only a candidate."""

    NO_TEXT = "no-text"
    PART_NUMBER_MISSING = "part-number-missing"
    PART_NUMBER_BUILT = "part-number-built"
    MANUFACTURER_MISSING = "manufacturer-missing"


NEXT = {
    Reason.NO_TEXT: (
        "the PDF has no text to check (a scan?): find another source, or give one "
        "with --url"
    ),
    Reason.PART_NUMBER_MISSING: (
        "this may be a sibling part's datasheet: find another source, or give one "
        "with --url"
    ),
    Reason.PART_NUMBER_BUILT: (
        "a series datasheet that builds part numbers: confirm with the page and a "
        "quote that shows how this number is built"
    ),
    Reason.MANUFACTURER_MISSING: (
        "the manufacturer is not named: check the PDF is theirs, then confirm with "
        "the page and a quote"
    ),
}

# Every name a manufacturer's datasheets go by. The first is the name pcbkit uses.
# Names are matched as whole words, ignoring case and spacing; leave out any name that
# is also an ordinary word in a datasheet.
MAKERS: tuple[tuple[str, ...], ...] = (
    ("Texas Instruments",),
    ("onsemi", "ON Semiconductor", "Semiconductor Components Industries", "Fairchild"),
    ("Diodes Incorporated", "Diodes Inc"),
    ("Analog Devices", "Maxim Integrated", "Linear Technology"),
    ("Microchip", "Atmel"),
    ("NXP",),
    ("STMicroelectronics",),
    ("Infineon", "International Rectifier"),
    ("Vishay",),
    ("Espressif",),
    ("Bosch",),
    ("TDK InvenSense", "InvenSense"),
    ("Silicon Labs", "Silicon Laboratories"),
    ("Monolithic Power Systems",),
    ("Alpha & Omega", "Alpha and Omega"),
    ("JST", "J.S.T."),
    ("Wurth Elektronik", "Würth Elektronik"),
    ("Sullins",),
    ("Yageo",),
    ("Murata",),
    ("Samsung Electro-Mechanics",),
    ("Panasonic",),
    ("Coilcraft",),
    ("Bourns",),
    ("Kingbright",),
    ("Abracon",),
    ("Bel Fuse",),
)


@dataclass(frozen=True)
class Verdict:
    """A judgement and its evidence: pages are numbered from 1, None when not found.

    ``part_number_found`` is the longest leading part of the part number that is in
    the text (letters and digits only), and ``part_number_page`` the first page with
    it; ``manufacturer_name`` is the name found and ``manufacturer_page`` its page.
    """

    status: Status
    reason: Reason | None
    part_number_found: str
    part_number_page: int | None
    manufacturer_name: str | None
    manufacturer_page: int | None

    @property
    def next_step(self) -> str:
        """Return what to do next, or "" for a verified datasheet."""
        return NEXT[self.reason] if self.reason else ""


def squash(text: str) -> str:
    """Return ``text`` in lower case with only its letters and digits."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def maker_names(manufacturer: str) -> tuple[str, ...]:
    """Return every name ``manufacturer`` goes by, or just its own if it is unknown."""
    wanted = squash(manufacturer)
    for names in MAKERS:
        if any(squash(name) and wanted.startswith(squash(name)) for name in names):
            return names
    return (manufacturer.strip(),)


def _name_pattern(name: str) -> re.Pattern[str]:
    """Return a pattern for ``name`` as whole words, with any spacing between them."""
    words = [re.escape(word) for word in name.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def _find_maker(
    pages: Sequence[str], manufacturer: str
) -> tuple[str | None, int | None]:
    """Return the first of the manufacturer's names in ``pages``, and its page."""
    patterns = [(name, _name_pattern(name)) for name in maker_names(manufacturer)]
    for number, text in enumerate(pages, start=1):
        for name, pattern in patterns:
            if pattern.search(text):
                return name, number
    return None, None


def _words(text: str) -> list[str]:
    """Return the words of ``text``, each squashed (see ``squash``).

    Punctuation inside a word goes (``PCA9685PW,118``, ``DS3231SN#``) but white space
    still separates words: squashing a whole page would join ``AO3401 Alpha`` into
    something that contains ``AO3401A``.
    """
    return [word for word in (squash(word) for word in text.split()) if word]


def _find_part_number(pages: Sequence[str], mpn: str) -> tuple[str, int | None]:
    """Return the longest start of ``mpn`` within a word of ``pages``, and its page."""
    wanted = squash(mpn)
    paged = [_words(text) for text in pages]
    for length in range(len(wanted), 0, -1):
        piece = wanted[:length]
        for number, words in enumerate(paged, start=1):
            if any(piece in word for word in words):
                return piece, number
    return "", None


def judge(
    pages: Sequence[str], mpn: str, manufacturer: str, *, built: bool = False
) -> Verdict:
    """Judge whether the datasheet with text ``pages`` is the one for ``mpn``.

    ``built`` says the part number is built from a series code (resistors,
    capacitors, crystals) rather than listed, so its absence is not a sign of a
    sibling part.
    """
    if not any(text.strip() for text in pages):
        return Verdict(Status.CANDIDATE, Reason.NO_TEXT, "", None, None, None)
    found, found_page = _find_part_number(pages, mpn)
    maker, maker_page = _find_maker(pages, manufacturer)
    missing = len(squash(mpn)) - len(found)
    whole = missing == 0 or (missing <= TRAILING_SLACK and len(found) >= MIN_KEPT)
    reason: Reason | None = None
    if not whole:
        reason = Reason.PART_NUMBER_BUILT if built else Reason.PART_NUMBER_MISSING
    elif maker is None:
        reason = Reason.MANUFACTURER_MISSING
    return Verdict(
        Status.CANDIDATE if reason else Status.VERIFIED,
        reason,
        found,
        found_page,
        maker,
        maker_page,
    )
