"""Check that a value read from a datasheet is really there: quote, page and numbers.

A value extracted from a datasheet (by a model or a person) is trusted only with its
evidence: a short verbatim quote and the page it is on. Everything here checks that
evidence against the PDF's own text, with no model and no judgement:

- the page is one of the PDF's pages, counted from 1 in the file's own order;
- the quote is at least ``MIN_QUOTE_CHARS`` long once normalised, so that "6 V" cannot
  match by accident;
- the quote is on that page (``quote_on_page``), and does not start or end in the
  middle of a number there ("1.2" is not on a page that says "1.25", "0.3" not on
  one that says "-0.3");
- every number in the value is a number in the quote (``missing_numbers``), unless the
  value is marked as worked out from the quote (an 8-bit address made 7-bit, a unit
  conversion), which a person then checks;
- the PDF is the one the values were read from: its SHA-256 is the one recorded
  (``check_pin``), so a new revision of the datasheet invalidates them.

It proves the text exists, not that it is the right row, part variant or test
condition: that is what the extraction eval measures (plans/wp14-parts.md).

Normalisation (``normalise``), the same for the quote and the page:

1. Look-alike characters are folded (``_FOLD``): every dash and minus sign becomes
   "-", curly quotes and primes straight ones, the micro sign mu, the ohm sign omega;
   soft hyphens and zero-width characters go. Then Unicode NFKC (ligatures such as
   "fi", superscripts, full-width forms).
2. A hyphen between two letters goes, with the line break after it if there is one.
   pdftotext joins a word broken at a line end and drops its hyphen ("regu-" + "lator"
   becomes "regulator", but "pull-" + "up" becomes "pullup" too), and with ``-layout``
   it keeps both lines as they are; a model may copy any of these. Dropping every
   letter-hyphen-letter on both sides makes all of them one form.
3. White space goes, except between two digits, where any run of it becomes one
   space. A PDF's text puts spaces in and leaves them out at random ("6V", "6 V",
   table cells on their own lines), so spacing is no evidence; but a quote must never
   join two numbers of the page ("-0.3 6 V" is not "-0.36 V").

Case is kept: "mA" and "MA" are nine orders of magnitude apart.

Numbers (``numbers``) are read from folded text with its spacing, as whole numbers
with their decimals: "9" is not a number of "19 V". A minus sign belongs to a number
unless a digit or a point comes before it ("3.0-5.5" is a range), so "-40" in the
datasheet and "40" in the value differ. A hexadecimal ``0x`` is dropped on both sides.

Blind spots: figures and scans (no text: the value is "read from figure N" for a person
to check instead), a quote across a page break, and a number whose digits the PDF
splits across table cells (rejected, which shows).

Everything here is pure: it takes page texts and bytes, never a file. pcbkit reads a
PDF's pages with pdftotext (``pcbkit.datasheet_find.page_texts``), and the extraction
must be given the same text the checks read.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

MIN_QUOTE_CHARS = 12

_FOLD = str.maketrans(
    {
        "−": "-",  # minus sign
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "-",  # figure dash
        "–": "-",  # en dash
        "—": "-",  # em dash
        "―": "-",  # horizontal bar
        "﹣": "-",  # small hyphen-minus
        "－": "-",  # full-width hyphen-minus
        "‘": "'",
        "’": "'",
        "‚": "'",
        "′": "'",  # prime
        "“": '"',
        "”": '"',
        "„": '"',
        "″": '"',  # double prime
        "º": "°",  # masculine ordinal, used as a degree sign
        "˚": "°",  # ring above, likewise
        "µ": "μ",  # micro sign to Greek mu
        "Ω": "Ω",  # ohm sign to Greek omega
        "­": None,  # soft hyphen
        "​": None,  # zero-width space
        "‌": None,
        "‍": None,
        "⁠": None,  # word joiner
        "﻿": None,  # byte order mark
    }
)
_LETTER_HYPHEN = re.compile(r"(?<=[^\W\d_])-(?:[ \t]*\r?\n\s*)?(?=[^\W\d_])")
_SPACE = re.compile(r"\s+")
_HEX_PREFIX = re.compile(r"(?<![\w.])0[xX](?=[0-9A-Fa-f])")
_NUMBER = re.compile(r"(?:(?<![\d.])-)?(?<![\w.])\d+(?:\.\d+)?")


class Rule(Enum):
    """Which check a citation failed."""

    PAGE = "page"
    SHORT = "quote-short"
    QUOTE = "quote"
    NUMBER = "number"
    PIN = "sha256"


@dataclass(frozen=True)
class Problem:
    """A failed check and what it means, for a person or a model to act on."""

    rule: Rule
    message: str


def fold(text: str) -> str:
    """Return ``text`` with look-alike characters folded and NFKC applied.

    Spacing and case are kept.
    """
    return unicodedata.normalize("NFKC", text.translate(_FOLD)).translate(_FOLD)


def _join(match: re.Match[str]) -> str:
    """Return what a run of white space becomes: one space between digits, else none."""
    text = match.string
    start, end = match.start(), match.end()
    between_digits = (
        start > 0
        and end < len(text)
        and text[start - 1].isdigit()
        and text[end].isdigit()
    )
    return " " if between_digits else ""


def normalise(text: str) -> str:
    """Return the form of ``text`` that quotes and pages are compared in."""
    return _SPACE.sub(_join, _LETTER_HYPHEN.sub("", fold(text)))


def _starts_inside_number(page: str, start: int) -> bool:
    """Say whether a match at ``start`` begins in the middle of a number of ``page``."""
    if start == 0:
        return False
    before = page[start - 1]
    if before.isdigit() or before == ".":
        return True
    # A "-" before the match is a sign unless a digit or point precedes it (a range).
    return before == "-" and not (start >= 2 and (page[start - 2] in "0123456789."))


def _ends_inside_number(page: str, end: int) -> bool:
    """Say whether a match ending at ``end`` stops in the middle of a number."""
    after = page[end : end + 2]
    return after[:1].isdigit() or (after[:1] == "." and after[1:].isdigit())


def quote_on_page(quote: str, page_text: str) -> bool:
    """Say whether ``quote`` is in ``page_text``, both normalised.

    A quote that starts or ends in the middle of a number of the page ("1.2" for
    "1.25", "0.3" for "-0.3") is not there at that place; it counts if it is
    somewhere else with whole numbers at both ends. An empty quote is on no page.
    """
    wanted = normalise(quote)
    if not wanted:
        return False
    page = normalise(page_text)
    check_start = wanted[0].isdigit() or wanted[0] == "."
    check_end = wanted[-1].isdigit()
    at = page.find(wanted)
    while at != -1:
        end = at + len(wanted)
        if not (check_start and _starts_inside_number(page, at)) and not (
            check_end and _ends_inside_number(page, end)
        ):
            return True
        at = page.find(wanted, at + 1)
    return False


def pages_with(quote: str, pages: Sequence[str]) -> list[int]:
    """Return the pages, counted from 1, that ``quote`` is on."""
    return [
        number for number, text in enumerate(pages, 1) if quote_on_page(quote, text)
    ]


def numbers(text: str) -> list[str]:
    """Return the numbers in ``text``, signed where a minus sign belongs to them."""
    return _NUMBER.findall(_HEX_PREFIX.sub("", fold(text)))


def missing_numbers(value: str, quote: str) -> list[str]:
    """Return the numbers of ``value`` that are not numbers of ``quote``, in order."""
    found = set(numbers(quote))
    return [number for number in numbers(value) if number not in found]


def check_citation(
    value: str,
    quote: str,
    page: int,
    pages: Sequence[str],
    *,
    derived: bool = False,
) -> Problem | None:
    """Check one value against its quote and page; return the first problem, or None.

    ``pages`` are the PDF's page texts, page 1 first. ``derived`` says the value is
    worked out from the quote rather than copied, so its numbers need not be in it.
    """
    if not 1 <= page <= len(pages):
        return Problem(
            Rule.PAGE,
            f"page {page} is not a page of the PDF: it has pages 1 to {len(pages)}, "
            "counted in the file's own order, not the numbers printed on them",
        )
    if len(normalise(quote)) < MIN_QUOTE_CHARS:
        return Problem(
            Rule.SHORT,
            f"the quote {quote!r} is under {MIN_QUOTE_CHARS} characters without its "
            "spaces, short enough to match by accident: quote the whole table row or "
            "sentence",
        )
    if not quote_on_page(quote, pages[page - 1]):
        elsewhere = pages_with(quote, pages)
        if elsewhere:
            where = f"it is on page {', '.join(str(n) for n in elsewhere)}"
        elif not pages[page - 1].strip():
            where = (
                f"page {page} has no text (a figure or a scan): a value read off a "
                "figure has no quote and is marked for a person to check"
            )
        else:
            where = (
                "it is on no page: quote the text exactly, line breaks may be spaces"
            )
        return Problem(
            Rule.QUOTE, f"the quote {quote!r} is not on page {page}; {where}"
        )
    if derived:
        return None
    missing = missing_numbers(value, quote)
    if missing:
        return Problem(
            Rule.NUMBER,
            f"the value {value!r} has {', '.join(missing)}, which the quote does not: "
            "a right quote with a wrong number. If the value is worked out from the "
            "quote, mark it derived and say how",
        )
    return None


def sha256_of(data: bytes) -> str:
    """Return the SHA-256 of ``data`` in lower-case hex, as pcbkit records it."""
    return hashlib.sha256(data).hexdigest()


def check_pin(recorded: str, data: bytes) -> Problem | None:
    """Check that ``data`` is the PDF whose SHA-256 was ``recorded``; else a problem."""
    actual = sha256_of(data)
    if recorded == actual:
        return None
    return Problem(
        Rule.PIN,
        f"the values were read from the PDF with SHA-256 {recorded or '(none)'}, and "
        f"this PDF's is {actual}: a different file, perhaps a new revision of the "
        "datasheet. Read the values again from this one",
    )
