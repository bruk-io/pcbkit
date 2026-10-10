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

A maker's own packaging codes (``SUFFIXES``: Vishay's ``-E3/57T``, JST's ``(LF)(SN)``)
are often left out of its datasheets; when the number is only found without one, the
rest of it must then be there whole.

Otherwise it is a CANDIDATE, with one reason from ``Reason``. Each reason has a next
step (``NEXT``) that the command line prints, so that a person or a model working the
tool follows the tool rather than remembering these rules.

A candidate can be confirmed with a page and a quote from it (``check_confirmation``),
but only for the reasons where a quote can show what the text check could not: a
number built from a series code, or a maker the text does not name. A datasheet that
lacks the part's own number, or has no text, cannot be confirmed: find another.

Everything here is pure: it takes page texts, never a file or a URL.
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
    MANUFACTURER_UNKNOWN = "manufacturer-unknown"


CONFIRMABLE = frozenset(
    {Reason.PART_NUMBER_BUILT, Reason.MANUFACTURER_MISSING, Reason.MANUFACTURER_UNKNOWN}
)


NEXT = {
    Reason.NO_TEXT: (
        "the PDF has no text to check (a scan?). Find another datasheet and give it "
        "with --url."
    ),
    Reason.PART_NUMBER_MISSING: (
        "this may be a sibling part's datasheet: it never shows this part's whole "
        "number. Find the part's own datasheet and give it with --url."
    ),
    Reason.PART_NUMBER_BUILT: (
        "a series datasheet that builds part numbers. Confirm it with the page and "
        "a quote holding the series code where the datasheet explains it."
    ),
    Reason.MANUFACTURER_MISSING: (
        "the maker is not named in the text (perhaps only in a logo). Check the PDF "
        "is theirs, then confirm it with a page and a quote holding the part number."
    ),
    Reason.MANUFACTURER_UNKNOWN: (
        "no manufacturer is known for this part. Give it with --maker, or confirm "
        "with a page and a quote holding the part number."
    ),
}

# What to quote when confirming, by reason. A built number is explained by a pattern
# such as "RC XXXX X X X XX XXXX L", so its quote must name the series code; whether
# each segment decodes is the reader's judgement, and the quote is kept for review.
QUOTE_MUST_HOLD = {
    Reason.PART_NUMBER_BUILT: "the series code where the number is explained",
    Reason.MANUFACTURER_MISSING: "the whole part number",
    Reason.MANUFACTURER_UNKNOWN: "the whole part number",
}

# A series code short enough to turn up in another series is no evidence.
MIN_SERIES = 4

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

# Packaging codes a maker leaves out of its datasheets, as patterns on the end of the
# orderable number. Measured: Vishay's SS34 datasheet lists SS34, not SS34-E3/57T, and
# JST's SH datasheet SM04B-SRSS-TB, not SM04B-SRSS-TB(LF)(SN). Add a maker's codes
# here, with the datasheet that shows them, rather than loosening the slack.
SUFFIXES: dict[str, tuple[str, ...]] = {
    "Vishay": (r"-[EM]3/\w+$",),
    "JST": (r"\(LF\)\(SN\)$", r"\(LF\)$"),
}


class ConfirmationRefused(ValueError):
    """Say why a quote does not confirm a datasheet."""


@dataclass(frozen=True)
class Verdict:
    """A judgement and its evidence: pages are numbered from 1, None when not found.

    ``part_number_found`` is the longest leading part of the part number that is in
    the text (letters and digits only), and ``part_number_page`` the first page with
    it; ``suffix_dropped`` is the maker's packaging code it was found without, if any;
    ``manufacturer_name`` is the name found and ``manufacturer_page`` its page.
    """

    status: Status
    reason: Reason | None
    part_number_found: str
    part_number_page: int | None
    manufacturer_name: str | None
    manufacturer_page: int | None
    suffix_dropped: str = ""

    @property
    def next_step(self) -> str:
        """Return what to do next, or "" for a verified datasheet."""
        return NEXT[self.reason] if self.reason else ""


def squash(text: str) -> str:
    """Return ``text`` in lower case with only its letters and digits."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def maker_names(manufacturer: str) -> tuple[str, ...]:
    """Return every name ``manufacturer`` goes by, or just its own if it is unknown.

    Return no names at all for an empty one: an empty name would match anywhere.
    """
    wanted = squash(manufacturer)
    if not wanted:
        return ()
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


def _whole(mpn: str, found: str) -> bool:
    """Say whether ``found`` is all of ``mpn``, allowing a short reel code."""
    missing = len(squash(mpn)) - len(found)
    return missing == 0 or (missing <= TRAILING_SLACK and len(found) >= MIN_KEPT)


def _without_suffix(mpn: str, manufacturer: str) -> tuple[str, str] | None:
    """Return ``mpn`` without its maker's packaging code, and the code; else None."""
    names = maker_names(manufacturer)
    for pattern in SUFFIXES.get(names[0], ()) if names else ():
        match = re.search(pattern, mpn, re.IGNORECASE)
        if match and match.start() > 0:
            return mpn[: match.start()], match.group(0)
    return None


def judge(
    pages: Sequence[str], mpn: str, manufacturer: str, *, built: bool = False
) -> Verdict:
    """Judge whether the datasheet with text ``pages`` is the one for ``mpn``.

    ``manufacturer`` may be "" when it is not known: the datasheet is then at best a
    candidate. ``built`` says the part number is built from a series code (resistors,
    capacitors, crystals) rather than listed, so its absence is not a sign of a
    sibling part.
    """
    if not any(text.strip() for text in pages):
        return Verdict(Status.CANDIDATE, Reason.NO_TEXT, "", None, None, None)
    found, found_page = _find_part_number(pages, mpn)
    whole = _whole(mpn, found)
    dropped = ""
    if not whole:
        shorter = _without_suffix(mpn, manufacturer)
        if shorter is not None:
            base, code = shorter
            base_found, base_page = _find_part_number(pages, base)
            if base_found == squash(base):
                found, found_page, whole, dropped = base_found, base_page, True, code
    maker, maker_page = _find_maker(pages, manufacturer)
    reason: Reason | None = None
    if not whole:
        reason = Reason.PART_NUMBER_BUILT if built else Reason.PART_NUMBER_MISSING
    elif not maker_names(manufacturer):
        reason = Reason.MANUFACTURER_UNKNOWN
    elif maker is None:
        reason = Reason.MANUFACTURER_MISSING
    return Verdict(
        Status.CANDIDATE if reason else Status.VERIFIED,
        reason,
        found,
        found_page,
        maker,
        maker_page,
        dropped,
    )


def series_code(mpn: str) -> str:
    """Return the series code a built number starts with: its leading letters.

    A number that starts with fewer than two letters gives its first ``MIN_SERIES``
    letters and digits instead.
    """
    letters = re.match(r"[a-z]*", squash(mpn)).group(0)  # type: ignore[union-attr]
    return letters if len(letters) >= 2 else squash(mpn)[:MIN_SERIES]


def _plain(text: str) -> str:
    """Return ``text`` in lower case with each run of white space made one space.

    White space is kept, never removed: a quote must not join two words of the page.
    """
    return " ".join(text.lower().split())


def check_confirmation(
    pages: Sequence[str],
    mpn: str,
    manufacturer: str,
    verdict: Verdict,
    page: int,
    quote: str,
) -> None:
    """Check that ``quote``, on page ``page`` of ``pages``, confirms a candidate.

    ``verdict`` is the candidate's judgement. Pages count from 1, the PDF's own order,
    not the numbers printed on them. Raise ConfirmationRefused, saying why, when the
    reason cannot be confirmed, the page does not exist, the quote is not on it, or
    the quote does not hold what the reason needs (``QUOTE_MUST_HOLD``).
    """
    if verdict.reason is None:
        raise ConfirmationRefused("the datasheet is verified already")
    if verdict.reason not in CONFIRMABLE:
        raise ConfirmationRefused(
            f"a datasheet that is a candidate for {verdict.reason.value} cannot be "
            f"confirmed: {NEXT[verdict.reason]}"
        )
    if not 1 <= page <= len(pages):
        raise ConfirmationRefused(
            f"the PDF has pages 1 to {len(pages)}, counted in the file's own order"
        )
    if not _plain(quote) or _plain(quote) not in _plain(pages[page - 1]):
        raise ConfirmationRefused(
            f"the quote is not on page {page} (pages are counted in the file's own "
            "order from 1; quote the text exactly, line breaks may be spaces)"
        )
    if verdict.reason is Reason.PART_NUMBER_BUILT:
        found = verdict.part_number_found
        if len(found) < MIN_SERIES:
            raise ConfirmationRefused(
                f"too little of {mpn} is in the datasheet ({found!r}) to show it is "
                "this series: find the series datasheet and give it with --url"
            )
        series = series_code(mpn)
        naming = [word for word in _words(quote) if word.startswith(series)]
        if not naming:
            raise ConfirmationRefused(
                f"the quote must hold the series code {series.upper()!r}, where the "
                "datasheet explains how the number is built"
            )
        if all(len(word) >= len(found) for word in naming):
            raise ConfirmationRefused(
                f"the quote only repeats {found.upper()!r}, which the datasheet was "
                "already seen to hold: quote where it explains how the number is "
                f"built, the series code {series.upper()!r} followed by its fields"
            )
        return
    found, _ = _find_part_number([quote], mpn)
    if not _whole(mpn, found):
        raise ConfirmationRefused(f"the quote must hold the whole part number {mpn}")
