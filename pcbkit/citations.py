"""Check that a value read from a datasheet is really there: quote, page and numbers.

A value extracted from a datasheet (by a model or a person) is trusted only with its
evidence: a short verbatim quote and the page it is on. Everything here checks that
evidence against the PDF's own text, with no model and no judgement:

- the page is one of the PDF's pages, counted from 1 in the file's own order;
- the quote is at least ``MIN_QUOTE_CHARS`` long once normalised, so that "6 V" cannot
  match by accident;
- the quote is on that page (``quote_on_page``), and neither end of it cuts a number
  of the page: "1.2" is not on a page that says "1.25", "0.3" not on one that says
  "-0.3", "70" not on one that says "70h" or "0x70";
- every number in the value is a number of the page inside the quote, whole and of the
  same kind, unless the value is marked as worked out from the quote (an 8-bit address
  made 7-bit, a unit conversion), which a person then checks;
- the PDF is the one the values were read from: its SHA-256 is the one recorded
  (``check_pin``), so a new revision of the datasheet invalidates them.

It proves the text exists, not that it is the right row, part variant or test
condition: that is what the extraction eval measures (plans/wp14-parts.md).

Normalisation, the same for the quote and the page:

1. Look-alike characters are folded (``_FOLD``): every dash and minus sign becomes
   "-", curly quotes and primes straight ones, the micro sign mu, the ohm sign omega;
   soft hyphens and zero-width characters go. Superscript digits and signs become "^"
   and the plain character ("10⁶" is "10^6", not "106", which NFKC alone would make,
   and "mm²" is "mm^2"). Then Unicode NFKC (ligatures such as "fi", full-width forms).
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

Tokens. One tokeniser (``_tokens``) reads the folded text, with its spacing, for the
page and for the value alike. A run of letters, digits, a point or comma between two
of them, an exponent's sign and a "^" with its sign is one word; a word with a digit
in it is one token of one kind, never split:

- decimal: "19", "1.25", ".25", with a unit's letters after it outside the token
  ("19V" is 19, "4.7k" is 4.7);
- exponent: "1e6", "2.5E-3", compared whole with the E's case folded;
- power: "10^6" (from "10⁶" too) and a bare "^2" (from "mm²");
- hexadecimal: "0x1F" and "1Fh", one form, "0x1f", apart from decimals;
- grouped: digits with commas, "5,5" or "1,000": a span on the page, so a quote cannot
  stop at its first "5", but never equal to anything, so a value written with a comma
  is refused;
- code: anything else with a digit, such as "4k7", "3V3", "2N7002", "VIN1": compared
  whole, so "4k7" needs "4k7" and "2N7002" backs no "2".

The sign rule, decided once in the tokeniser: a "-" joined to the front of a number is
a sign when white space, the start of the text or one of ``([{=:;,/<>~`` comes before
it ("-40", "VIN -0.3"); a range dash when a number ends right before it ("2.7-3.6",
"0-40", "2.7V-3.6V"); and otherwise, after a letter or a code ("VDD-0.3", "pin-5"),
it cannot be told, so the number is kept with its dash and backs no value at all. A
"+" is never part of a number. Every "-" that is not a sign is a dash token.

The span rule: the quote is looked for in the normalised page, and an occurrence is
refused if either end falls strictly inside a page token, if it starts on a dash
token (that would turn a range dash into a sign), or if an end is next to a space the
normalisation kept, which is always between two digits: "1 000" may be one number
with a thin-space separator or two table cells, so a quote takes both or neither.
An occurrence with clean ends is enough, even if another occurrence is cut. The
numbers that back the value are the page's tokens inside that occurrence, read with
the page's spacing, never the quote's own: a quote typed "5, 5" on a page that says
"5,5" has no 5 in it.

Blind spots, which this module does not prove:

- Units and SI prefixes are not checked: "1.25 mV" passes against a quote that says
  "1.25 V". Only the numbers are compared.
- Numbers are compared as written, not as values. A thousands separator ("1,000"
  for "1000"), a decimal comma ("-0,3"), a trailing zero ("19.0" for "19") and a
  "±" form ("-1% to +1%" for "±1%") are refused, not normalised: the value must be
  written as the quote writes it, or marked derived. Neither "+" nor "±" is part of a
  number: "+19" backs "19", and so does "±19".
- A digit run followed by "h" is hexadecimal, so hours read as hex: "12" is refused
  against "Retention 12h" (and "24h" against "24 h"). A right value is refused, never a
  wrong one passed: write it as the page does or mark it derived.
- A number after a letter or a code and a dash ("VDD-0.3", "pin-5") backs nothing,
  and a range written with units and spaced dashes reads as separate numbers.
- A quote that stops between two numbers with only space between them (table cells,
  "-0.3 6 V") is refused, right or not: quote the whole row.
- A value made of numbers that are all in the quote, but in the wrong roles (the
  typical as the maximum, a number from another row of the quote), passes.
- Figures and scans (no text: the value is "read from figure N" for a person to
  check instead), a quote across a page break, and a number whose digits the PDF
  splits across table cells (rejected, which shows).

Everything here is pure: it takes page texts and bytes, never a file. pcbkit reads a
PDF's pages with pdftotext (``pcbkit.datasheet_find.page_texts``), and the extraction
must be given the same text the checks read.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Sequence
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
        "Ω": "Ω",  # ohm sign to Greek omega
        "­": None,  # soft hyphen
        "​": None,  # zero-width space
        "‌": None,
        "‍": None,
        "⁠": None,  # word joiner
        "﻿": None,  # byte order mark
    }
)
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_SUPERSCRIPT_RUN = re.compile("[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_LETTER_HYPHEN = re.compile(r"(?<=[^\W\d_])-(?:[ \t]*\r?\n\s*)?(?=[^\W\d_])")
_SPACE = re.compile(r"\s+")

# A word: letters and digits, a point or comma between two of them, a point before a
# digit, an exponent's sign, and "^" with its sign (at the start, or after a digit).
_WORD = re.compile(
    r"(?:\^[-+]?(?=\d)|\.(?=\d))?"
    r"(?:\w|(?<=\w)[.,](?=\w)|(?<=\d[eE])[-+](?=\d)|(?<=\d)\^[-+]?(?=\d))+"
)
_HEX = re.compile(r"0[xX]([0-9A-Fa-f]+)|([0-9][0-9A-Fa-f]*)[hH]")
_UNIT = r"([^\W\d_]*)"
_SIGN_AFTER = frozenset("([{=:;,/<>~")


class _Kind(Enum):
    """The kinds of token; a number is equal only to one of its own kind."""

    DECIMAL = "decimal"
    EXPONENT = "exponent"
    POWER = "power"
    HEX = "hex"
    GROUPED = "grouped"
    CODE = "code"
    DASH = "dash"


# Tried in order on a word with no hexadecimal reading; group 1 is the number, group 2
# a unit's letters after it, which are not part of the token.
_NUMBERS = (
    (_Kind.EXPONENT, re.compile(r"(\d+(?:\.\d+)?[eE][-+]?\d+)" + _UNIT)),
    (_Kind.POWER, re.compile(r"((?:\d+(?:\.\d+)?)?\^[-+]?\d+)" + _UNIT)),
    (_Kind.GROUPED, re.compile(r"(\d+(?:,\d+)+(?:\.\d+)?)" + _UNIT)),
    (_Kind.DECIMAL, re.compile(r"(\d+(?:\.\d+)?|\.\d+)" + _UNIT)),
)


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


@dataclass(frozen=True)
class _Token:
    """One indivisible token of folded text: its kind, its span and what it equals.

    ``key`` is what two tokens are compared by; None means it equals nothing (a
    grouped number, a number whose dash cannot be read, a dash).
    """

    kind: _Kind
    text: str
    start: int
    end: int
    key: tuple[_Kind, str] | None


@dataclass(frozen=True)
class _Page:
    """A page's normalised text, where a quote may not end, and its numbers."""

    text: str
    inside: frozenset[int]
    dashes: frozenset[int]
    numbers: tuple[tuple[int, int, _Token], ...]


def _fold(text: str) -> str:
    """Return ``text`` with look-alike characters folded and NFKC applied.

    A run of superscripts becomes "^" and the plain characters ("10⁻⁶" is "10^-6")
    before NFKC, which would otherwise write them as digits of the number before.

    Spacing and case are kept.
    """
    text = _SUPERSCRIPT_RUN.sub(
        lambda match: "^" + match[0].translate(_SUPERSCRIPTS), text
    )
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


def _sub_mapped(
    pattern: re.Pattern[str],
    replace: Callable[[re.Match[str]], str],
    text: str,
    origin: list[int],
) -> tuple[str, list[int]]:
    """Substitute like ``pattern.sub``, carrying each character's origin with it.

    ``origin[i]`` is where character ``i`` of ``text`` came from; a replacement's
    characters come from the start of what they replace.
    """
    pieces: list[str] = []
    where: list[int] = []
    last = 0
    for match in pattern.finditer(text):
        pieces.append(text[last : match.start()])
        where.extend(origin[last : match.start()])
        new = replace(match)
        pieces.append(new)
        where.extend([origin[match.start()]] * len(new))
        last = match.end()
    pieces.append(text[last:])
    where.extend(origin[last:])
    return "".join(pieces), where


def _normalised(folded: str) -> tuple[str, list[int]]:
    """Return folded text normalised, and where in it each character came from."""
    text, origin = _sub_mapped(
        _LETTER_HYPHEN, lambda match: "", folded, list(range(len(folded)))
    )
    return _sub_mapped(_SPACE, _join, text, origin)


def _normalise(text: str) -> str:
    """Return the form of ``text`` that quotes and pages are compared in."""
    return _normalised(_fold(text))[0]


def _read_word(word: str) -> tuple[_Kind, str, str]:
    """Return a word's kind, its number (the token's text) and the comparison form.

    The number is the whole word for hex and codes, and the word without the unit's
    letters after it otherwise.
    """
    hexadecimal = _HEX.fullmatch(word)
    if hexadecimal:
        digits = hexadecimal[1] or hexadecimal[2]
        return _Kind.HEX, word, f"0x{int(digits, 16):x}"
    for kind, pattern in _NUMBERS:
        match = pattern.fullmatch(word)
        if match:
            number = match[1]
            same = number.lower() if kind is _Kind.EXPONENT else number
            return kind, number, same
    return _Kind.CODE, word, word


def _sign(text: str, start: int, previous: _Token | None) -> str:
    """Say what a "-" right before a number at ``start`` is: sign, range or unknown.

    ``previous`` is the token before it, if that token ends at the dash.
    """
    before = text[start - 2] if start >= 2 else ""
    if not before or before.isspace() or before in _SIGN_AFTER:
        return "sign"
    if previous is not None and previous.kind not in (_Kind.CODE, _Kind.DASH):
        return "range"
    return "unknown"


def _tokens(folded: str) -> list[_Token]:
    """Return the tokens of folded text in order: numbers, codes and dashes.

    Spans are indices into ``folded``. A "-" that is not a number's sign is a dash.
    """
    found: list[_Token] = []
    words: list[tuple[int, int]] = []  # every word, with or without a digit
    previous_word_end = -1
    previous: _Token | None = None
    for match in _WORD.finditer(folded):
        start, end, word = match.start(), match.end(), match[0]
        after_word = previous_word_end == start - 1
        previous_word_end = end
        words.append((start, end))
        if not any(character.isdigit() for character in word):
            previous = None
            continue
        kind, number, same = _read_word(word)
        token_end = start + len(number)
        key: tuple[_Kind, str] | None = None if kind is _Kind.GROUPED else (kind, same)
        if (
            kind not in (_Kind.CODE, _Kind.HEX)
            and start > 0
            and folded[start - 1] == "-"
            and word[0] != "^"
        ):
            reading = _sign(folded, start, previous if after_word else None)
            if reading == "sign":
                start, number = start - 1, "-" + number
                key = None if key is None else (kind, "-" + same)
            elif reading == "unknown":
                start, number, key = start - 1, "-" + number, None
        previous = _Token(kind, number, start, token_end, key)
        found.append(previous)
    taken = {i for token in found for i in range(token.start, token.end)}
    for word_start, word_end in words:
        taken.update(range(word_start, word_end))
    for at, character in enumerate(folded):
        if character == "-" and at not in taken:
            found.append(_Token(_Kind.DASH, "-", at, at + 1, None))
    return sorted(found, key=lambda token: token.start)


def _numbers(tokens: Sequence[_Token]) -> list[_Token]:
    """Return the tokens that are numbers or codes, leaving out dashes."""
    return [token for token in tokens if token.kind is not _Kind.DASH]


def _read_page(page_text: str) -> _Page:
    """Return a page's normalised text with its token spans in that text."""
    folded = _fold(page_text)
    text, origin = _normalised(folded)
    position = {source: at for at, source in enumerate(origin)}
    inside: set[int] = set()
    dashes: set[int] = set()
    numbers: list[tuple[int, int, _Token]] = []
    for token in _tokens(folded):
        if token.start not in position:
            continue  # a dash between two letters, which normalisation drops
        start = position[token.start]
        end = position[token.end - 1] + 1
        if token.kind is _Kind.DASH:
            dashes.add(start)
            continue
        inside.update(range(start + 1, end))
        numbers.append((start, end, token))
    for at, character in enumerate(text):
        if character == " ":  # only ever between two digits
            inside.update((at, at + 1))
    return _Page(text, frozenset(inside), frozenset(dashes), tuple(numbers))


def _occurrences(quote: str, page: _Page) -> list[tuple[int, int]]:
    """Return where ``quote`` is in ``page`` with neither end cutting a page token."""
    wanted = _normalise(quote)
    found: list[tuple[int, int]] = []
    if not wanted:
        return found
    at = page.text.find(wanted)
    while at != -1:
        end = at + len(wanted)
        if at not in page.inside and end not in page.inside and at not in page.dashes:
            found.append((at, end))
        at = page.text.find(wanted, at + 1)
    return found


def quote_on_page(quote: str, page_text: str) -> bool:
    """Say whether ``quote`` is in ``page_text``, both normalised.

    A quote whose start or end falls inside a token of the page ("1.2" for "1.25",
    "0.3" for "-0.3", "70" for "70h"), or that starts on a range dash, is not there
    at that place; it counts if it is somewhere else with clean ends. An empty quote
    is on no page.
    """
    return bool(_occurrences(quote, _read_page(page_text)))


def _pages_with(quote: str, pages: Sequence[str]) -> list[int]:
    """Return the pages, counted from 1, that ``quote`` is on."""
    return [
        number for number, text in enumerate(pages, 1) if quote_on_page(quote, text)
    ]


def _missing(value: str, quote: str, page_text: str) -> list[str] | None:
    """Return the numbers of ``value`` that the quote on ``page_text`` lacks.

    The quote's numbers are the page's tokens inside an occurrence of the quote; if
    one occurrence has them all, nothing is missing. None if the quote is not on the
    page.
    """
    page = _read_page(page_text)
    occurrences = _occurrences(quote, page)
    if not occurrences:
        return None
    wanted = _numbers(_tokens(_fold(value)))
    best: list[str] | None = None
    for start, end in occurrences:
        keys = {
            token.key
            for token_start, token_end, token in page.numbers
            if start <= token_start and token_end <= end and token.key is not None
        }
        missing = [
            token.text for token in wanted if token.key is None or token.key not in keys
        ]
        if not missing:
            return []
        if best is None:
            best = missing
    return best


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
    if not pages:
        return Problem(
            Rule.PAGE,
            f"page {page} is not a page of the PDF: no page text was given for it, so "
            "the PDF has no pages to cite",
        )
    if not 1 <= page <= len(pages):
        return Problem(
            Rule.PAGE,
            f"page {page} is not a page of the PDF: it has pages 1 to {len(pages)}, "
            "counted in the file's own order, not the numbers printed on them",
        )
    if len(_normalise(quote)) < MIN_QUOTE_CHARS:
        return Problem(
            Rule.SHORT,
            f"the quote {quote!r} is under {MIN_QUOTE_CHARS} characters without its "
            "spaces, short enough to match by accident: quote the whole table row or "
            "sentence",
        )
    missing = _missing(value, quote, pages[page - 1])
    if missing is None:
        elsewhere = _pages_with(quote, pages)
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
    if derived or not missing:
        return None
    return Problem(
        Rule.NUMBER,
        f"the value {value!r} has {', '.join(missing)}, which the quote does not: "
        "a right quote with a wrong number. If the value is worked out from the "
        "quote, mark it derived and say how",
    )


def _sha256_of(data: bytes) -> str:
    """Return the SHA-256 of ``data`` in lower-case hex, as pcbkit records it."""
    return hashlib.sha256(data).hexdigest()


def check_pin(recorded: str, data: bytes) -> Problem | None:
    """Check that ``data`` is the PDF whose SHA-256 was ``recorded``; else a problem."""
    actual = _sha256_of(data)
    if recorded == actual:
        return None
    return Problem(
        Rule.PIN,
        f"the values were read from the PDF with SHA-256 {recorded or '(none)'}, and "
        f"this PDF's is {actual}: a different file, perhaps a new revision of the "
        "datasheet. Read the values again from this one",
    )
