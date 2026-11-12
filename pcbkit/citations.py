"""Check that a value read from a datasheet is really there: quote, page and numbers.

A value extracted from a datasheet (by a model or a person) is trusted only with its
evidence: a short verbatim quote and the page it is on. Everything here checks that
evidence against the PDF's own text, with no model and no judgement:

- the page is one of the PDF's pages, counted from 1 in the file's own order;
- the quote is at least ``MIN_QUOTE_CHARS`` long, counting each run of white space
  between words as one character (W3), so that "6 V" cannot match by accident. The
  spaces counted are the page's, at the shortest place the quote is on the cited
  page, never the quote's own ("a b c d e f 5" against a page "abcdef 5" is 8). Why
  counting spaces is safe: the minimum plays no part in checking the value, only in
  how likely a quote is to be found by chance, and a space between two words is as
  much a part of a phrase as a letter. It also stops the rule pushing an extractor
  into quotes that span two table rows, which is where values of the wrong row leak;
- the quote is on that page (``quote_on_page``), and neither end of it cuts a number
  of the page;
- every number of the value is a *known* number of the page inside the quote, of the
  same kind and sign, and of the same unit and prefix where both state one, at every
  place the quote occurs on the page, unless the value is marked as worked out from
  the quote (``derived``), which a person then checks;
- the PDF is the one the values were read from (``check_pin``).

It proves the text exists, not that it is the right row, part variant or test
condition: that is what the extraction eval measures (plans/wp14-parts.md).

Refuse by default
-----------------

Refusing a right value costs a retry; accepting a wrong one puts a wrong number on a
circuit board. So the reader below has a small grammar, and a number backs a value only
when the number and everything touching it are inside that grammar. Anything the
grammar has no rule for is *unknown*, and an unknown number backs nothing. The value is
read with the same grammar and must be read completely: a value with any part the
grammar cannot read is refused, never compared in part.

Text. The page and the value are read after three steps:

1. Look-alikes are folded, one character for one (``_FOLD``): the minus sign, the
   hyphen, non-breaking hyphen, figure, en and em dashes and the small and full-width
   hyphen-minus to "-" (no other dash: the rest are unknown, the horizontal bar
   included), curly quotes and primes to straight ones, the micro sign to mu, the ohm
   sign to omega, the ordinal and ring to the degree sign. Full-width forms and the
   Latin ligatures (U+FF01-FF5E, U+FB00-FB06) are
   replaced by their NFKC form. Nothing else is: there is no general NFKC, so
   superscripts, subscripts, fractions and digits of other scripts stay what they are.
2. Between two letters, a hyphen (with the line break after it), a soft hyphen or a
   zero-width character goes: pdftotext joins "pull-" + "up" into "pullup" and keeps
   it as it is with ``-layout``, so all forms become one.
3. Nothing else changes before reading. White space keeps its kind: any white-space
   character but space, tab, line break, carriage return, form feed and vertical tab
   (a no-break, thin, narrow, figure or ideographic space, ...) is a *grouping space*.

Words. A word is a run of ASCII digits, letters (any script) and "_", with a point or
comma between two of those, a point before a digit at its start, an exponent's sign
after "<mantissa>e", and "^" with its sign after a digit. A word with an ASCII digit in
it is one token of exactly one kind, never split:

- decimal: ``\\d+(\\.\\d+)?`` or ``\\.\\d+``, optionally followed in the same word by a
  unit from the closed list ``_UNITS`` with an optional SI prefix (``_PREFIXES``), or
  by a prefix alone ("19V", "10MHz", "4.7k", "100n"). Compared by the digits as
  written: "5" is not "5.0" and "0.5" is not ".5".
- hex: ``0[xX][0-9A-Fa-f]+``, or digits and hex letters with at least one of A-F and a
  final "h" ("1Fh"). Compared by value, so "0x1F" is "1Fh".
- exponent: ``<mantissa>[eE][-+]?\\d+``, compared exactly as written.
- power: ``<mantissa>^[-+]?\\d+``, written with a caret, compared exactly as written.
- code: any other word with a digit: "2N7002", "4k7", "3V3", "VIN1", "1F", "12h",
  "1010b", "1G", "2Ah". A code backs only the identical code. An integer followed by
  only the letters A-F (and an optional h), such as "1F", "2A", "10dB" or "70h", is a
  code and not a number with a unit, because it is also bare hex, a pin name of a
  74-series part ("1A", "2B") or hours. So is an integer followed by one upper-case G,
  S, K, M, H or W alone: those letters name pins as often as units ("1G" output
  enable, "2S", "1K" clear), so "1G" is not 1 giga and "10K" is not 10 kilo; a value
  must write "1M" or "10K" as the page does, and "10K" does not back "10 kΩ". "V" and
  the lower-case prefixes ("1k", "100n") stay a number with a unit. Digits and "Ah"
  ("2Ah") are a code: hex 0x2A or 2 ampere-hours, and the text cannot say which.
  Hex that starts with a letter ("FFh", "FEh") has no digit-led word at all: it is no
  token, so "0xFF" is refused against it and it can be cited only as ``text_only``.
- grouped: digits with a comma ("1,000", "5,5"): unknown.

Signs. A "-" glued to the front of a number is read only where its role is certain:

- a sign, when it starts the text, or follows (glued, or after white space) one of
  ``_OPENERS`` ("(", "=", ":", "<", ...) or a bullet "•" that starts its line (only
  spaces or tabs before it on the line), or follows a word of ``_FUNCTION_WORDS``
  ("to", "from", "and", ...) after white space: none of those can be a range's first
  end or the left side of a subtraction;
- a sign, when it follows a cell gap (two or more plain spaces or a tab on one line,
  or a blank line) after a word with no digit that is not a unit or prefix, an
  exponent letter or "x" (W1): "VIN    –0.3", "Tstg    –65". Why it is safe: a
  subtraction such as "VIN − 0.3" is set inside one table cell with at most one
  space around its minus, and pdftotext's ``-layout`` puts two or more spaces only
  between separate text blocks, so the dash cannot join the word before it. A number
  before the gap is still excluded ("2.7    –3.6" may be a range whose space before
  the dash was widened), and so is a unit ("2.7 V    –3.6 V") in any spelling the
  closed lists cover (``_UNITS``; ``_SPELT_UNITS``: volt, millivolt, amp, ampere,
  milliamp, milliampere, watt, milliwatt, ohm, kilohm, megohm, farad, henry, hertz,
  second, millisecond, microsecond, nanosecond, degree, kelvin and celsius, lower case
  or capitalised, singular or plural; ``_SUFFIXED_UNITS``: Vdc, Vac, Vpk, Vp, Vpp,
  Vrms, Adc, Aac, Apk, Arms, as written or in capitals, with or without a prefix),
  an exponent letter ("1.5 E    -3") and "x". A unit word outside those lists is
  read as a label. What it can still get wrong: a cell holding an offset from a rail
  ("VCC    –0.4" meaning VCC minus 0.4) backs -0.4, which is the "offset from a rail"
  blind spot below; and a dash that starts a list item after a label and a gap
  ("Notes    –5 V tolerant inputs") is read as the sign of -5;
- "+/-", glued: plus-minus;
- a range dash, when it is glued between two decimals (a unit, "%" or "°C" may be
  glued to the first), the first is not 10, and the first is less than the second:
  "2.7-3.6", "-40°C-85°C", "5%-10%";
- anything else is unknown, and the number after it is unknown: after a letter word
  or a code, glued ("VDD-0.3") or not ("VIN -0.3", which may be "VIN - 0.3" or a range
  from a symbol), after a number and white space ("2.7 -3.6"), a dash standing alone
  ("3.0 - 5.5"), a descending or 10-based range ("10-6").

A "+" glued to a number, and "±", leave the number positive in every reading, so the
number backs its magnitude: "+19" and "±19" back "19" (but "1+2", a sign glued after a
digit, makes both unknown). In the value a sign is compared, never dropped: a value's
"-" needs a page "-"; its "±" (or "+/-") needs a page "±" (or "+/-"), so "±5 V" is
refused against "5 V max"; its "+" needs a page "+" or no sign, both of which are
positive, and not "±". A sign of any kind before a hex
number or a code is unknown, in the page and in the value. A "-" or "+" after the
number 10 makes both numbers unknown: it may be a power of ten flattened onto the line
("10-6" for 10⁻⁶).

Context. A number is unknown when any of these touch it:

- glued before it: anything but white space, a sign as above, a line-start bullet,
  or ``_SAFE_BEFORE``. A line-start "•" opens a list item: nothing before it on the
  line can be a range's first end or be subtracted from, and it is no footnote or
  power mark on the number, so it is known. A "•" inside a line, and every other
  bullet character ("∙", "◦", "‣", ...), has no rule and is unknown;
- glued after it: anything but white space, "-" (a range or a hyphen, judged from the
  other side), or ``_SAFE_AFTER``; a code or hex number glued to a dash and a digit is
  unknown too ("2N7002-7");
- any character outside the known ones (``_known_char``), glued or across white space:
  superscripts, subscripts, fractions, other scripts' digits, "*", "^" outside a power,
  footnote daggers, soft hyphens, zero-width characters and the dash look-alikes that
  are not folded;
- any one character between two digits, other than a decimal point or a range dash:
  "1/2", "12:30", "5×6";
- a grouping space between it and another number: "1 000" with a thin space is two
  unknown halves;
- plain white space between it and a following integer of exactly three digits, when
  it is an integer, or a decimal with exactly three digits after the point (then the
  next integer may have one to three): "100 000" may be 100000, so both halves are
  unknown. A table row "1.5 250" is not refused: no grouped number has a group after a
  fraction shorter than three digits. Nor is a cell gap (W2: "–65    150",
  "0.594    0.6"): a thousands separator is at most one character wide, a thin,
  no-break or plain space, and pdftotext never makes it two spaces, a tab or a blank
  line. A single line break, indented or not, may still split a group, so it is no
  gap and the rule fires across it;
- a dash standing alone before it, across white space ("VEE- 12V", "3.0 - 5.5");
- a times-ten form: "x", "X" or "×" before the number 10 makes the number before it,
  the 10 and the next number after the 10 (across white space and one sign) unknown
  ("1 x 10-6"), and so does a code ending "x10" ("1x10 -6"); "5 x 6 mm" is not
  refused, because a power of ten always has 10 after the sign;
- an exponent letter on its own after it: "1.5 E-3" and "1.5 E3";
- a footnote form: a bare integer in parentheses or brackets, "VDD(2)", "(1)", "[3]".

A range whose one end is unknown makes the other unknown too.

Units. A number's stated unit is the unit glued to it ("19V", "4.7k", "85%",
"25°C"); the unit joined to it by a hyphen, as TI writes component values ("6.8-pF",
"10-kΩ", "60-V", "5-%": a hyphen and then a unit, never a number, so the range
"2.7-3.6 V" states nothing for 2.7); or the word right after it across white
space ("19 V", "45  µA", and a unit column that ``-layout`` put on the next line).
The unit is a word of ``_UNITS``, a prefix, or a prefix and a unit, or is "%" or "°"
with "C", "F" or "K". Reading a unit across a line break only refuses more: measured
on three datasheets, every unit found that way was the row's own. Where the value's
number and the page's number both state a unit, the two must be the same text, after
the folding above (micro sign and mu, ohm sign and omega) and with a "u" prefix read
as micro: "10 Ω" is refused against "10 kΩ", "3.3 V" against "3.3 A", "100 nF"
against "100 µF" or "100-µF". Nothing else is equated: "K" is not "k" (it may be
kelvin) and "ohm" is not "Ω", so those are refused. Where either side states none
("1.25" against "1.25 V", or a page whose unit is before the number, as in "kHz
500", or only in a column header) the units are not compared.

Row units. In a table row most numbers state no unit: only the last before the UNIT
column does ("VIN    4.5    28    V"). A number with no unit of its own takes its
row's unit column: a unit standing apart, after white space, from the last number of
a run of numbers on the same line, with only spaces, tabs and numbers stating no unit
between ("= 100 mV 5.8 6.6 7.7 V" gives 5.8 and 6.6 the "V", and 100 keeps its
"mV"; a second unit column starts a new run: "456 684 kHz 1.75 2.2 MHz"). A unit
glued or joined by a hyphen to its own cell ("22uF", "6.8-pF", "93%") is not a column
and is not passed on. This only refuses more. Its cost: a row of cells in different
units with a spaced unit after the last ("500  5  73.2  10  3.3  22 µF") gives every
cell that unit, so "73.2 kΩ" is refused there; on three datasheets and TLV62569 (179
facts) it refused no right value. Before it, a unit swapped on an accepted value
("4.5 A" for "4.5 V" in "VIN 4.5 28 V") was accepted 96 times in 225; with it, 27
times, every one a number with no unit column it can reach: the unit is before it
("kHz 500"), only in a header ("±2000" under "V(ESD)"), past a mark ("–0.5
0.3×VVS V") or glued to another cell ("73.2 10 3.3 22uF").

Values. The value is read like a page, with the same rules, and every number in it must
be known. A value with an unknown character anywhere, or with no number at all, is
refused (``Rule.VALUE``), unless it is marked ``text_only``: then its words, joined as
on the page, must occur as a run of whole words of the page inside every occurrence of
the quote ("active low" is refused against "active high"; "low" is refused inside
"allow"). A ``derived`` value is not read at all: a person checks it.

Quote. The quote is looked for in the page's normalised text: the read text with
soft hyphens and zero-width characters removed, and white space removed except between
two digits, where any run of it is one space ("-0.3 6 V" is never "-0.36 V"). Case is
kept: "mA" and "MA" are nine orders of magnitude apart. An occurrence is refused if
either end falls inside a number of the page (a number's span takes its sign, its
glued unit or prefix and any unknown character glued to it, so "R1 is 10" does not
cut "10k") or if it starts on a dash outside a number. The quote's
numbers are the page's own numbers inside the occurrence, with the page's spacing and
context, never the quote's. Where the quote occurs more than once, every occurrence
must back the value.

What is checked, refused and blind
----------------------------------

Checked: the page, the quote's length and presence, the quote's ends, each number of
the value against the known numbers inside every occurrence (kind, digits, sign, and
unit where both state one), the words of a ``text_only`` value, and the PDF's SHA-256.

Refused as unknown (right values among them; write the value as the page writes it,
or mark it derived): everything listed under Context and Signs; superscript powers
("10⁶"); numbers glued to a unit outside ``_UNITS``; "0x1F" against a bare "1F",
"70h" or "FFh"; "2 Ah" against "2Ah"; "10 kΩ" against "10K" and "52.3 kΩ" against
"52.3K"; "1000" against "1 000" or "1,000"; a decimal comma; "5" against "5.0";
"3 A" against "3A" (a code); a range written with a spaced dash ("3.0 - 5.5") or
with units and spaces ("2.7 V-3.6 V"); hex ranges; a pin number after a pin name
that ends in a dash ("IN– 9" reads like "VEE- 12V").

The commonest real cost, measured on three public datasheets' ``-layout`` text after
W1-W3: a negative number whose dash has no certain role. That is a negative after a
symbol with a single space ("VIN -0.3 19 V", where W1 needs a cell gap), after a
footnote mark or other ")" ("range(2)    –40 150 °C"), after a unit
("> 0 V    –0.3 65 V"), on the line after a wrapped line ("the range\\n    –0.3 V"),
and the second and later numbers of a row of negatives ("V(AK REV)  –17  –11  –2").
Write such a value derived, for a person to check.

Blind, not detectable from the text at all:

- A unit or prefix that only one side states is not compared: "1.25 mV" passes
  against a "1.25" whose "V" is before it ("kHz 500"), in a column header, or past a
  word or mark on its line; "10" passes against "10k". Measured: of 225 unit swaps
  on accepted values from four datasheets, 27 pass, every one of this kind. The
  value's unit must be inside the value ("6.8 pF", not "6.8" with the unit kept
  elsewhere), or it is never compared.
- A footnote digit glued to a number ("3.61" for 3.6 and footnote 1), and a power of
  ten that pdftotext flattened with no mark left ("106" for 10⁶, or "10 6" and
  "10\\n6" with plain white space): the text is a plain number.
- Roles: a value made of numbers that are all in the quote, but in the wrong roles
  (the typical as the maximum, a number of another row of the quote, an offset from a
  rail read as an absolute: "-0.3" from "VSS = -0.3", or "VCC    –0.4" read as a sign
  by W1), passes; so does a magnitude from "±" or "+". A cross-reference reads as a
  range: "see Figure 8-10" backs "8" and "10".
- A text value's meaning: "active low" passes against "not active low".
- Figures and scans (no text: the value is "read from figure N" for a person to
  check), a quote across a page break, and a number whose digits the PDF splits across
  table cells (refused, which shows).

Text mode
---------

The text is pdftotext's ``-layout`` mode, for the extractor and this gate alike: the
default (raw) mode scrambles table cells, putting a row's cells on separate lines or
out of order, so a quote of a row cannot be copied from it. This module is pure and
does not know the mode, but the cell-gap rules (W1, W2) assume ``-layout``: there two
or more spaces or a tab separate text blocks. In raw text only a blank line counts as
a gap. The caller (``pcbkit.datasheet_find.page_texts``, which runs pdftotext without
``-layout`` today) changes when the extractor is built, so that the extraction and
the checks read the same text.

Everything here is pure: it takes page texts and bytes, never a file.
"""

from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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
        "\u2126": "\u03a9",  # ohm sign to Greek omega (escaped: they look alike)
    }
)
_COMPATIBLE = re.compile("[ﬀ-ﬆ！-～]")  # ligatures, full-width forms
_INVISIBLE = "­​‌‍⁠﻿"  # soft hyphen, zero widths, BOM
_INVISIBLE_RUN = re.compile(f"[{_INVISIBLE}]+")
_JOIN = re.compile(
    rf"(?<=[^\W\d_])(?:[{_INVISIBLE}]+|-)(?:[ \t]*\r?\n\s*)?(?=[^\W\d_])"
)
_SPACE = re.compile(r"\s+")
_PLAIN_SPACE = frozenset(" \t\n\r\f\v")

# A dash after one of these is a sign: none can end a range or be subtracted from.
_OPENERS = frozenset("([{<>=~≈≤≥:;,@")
_FUNCTION_WORDS = frozenset(
    {"to", "from", "and", "or", "between", "than", "of", "at", "is", "are", "be"}
    | {"by", "with", "for", "as"}
)
_SAFE_BEFORE = _OPENERS | frozenset("/#$'\"|×")
_SAFE_AFTER = frozenset(")]}<>=~≈≤≥,;:.!?/%°'\"|×")
_SIGNS = frozenset("-+±")
_KNOWN_CHARS = _SAFE_BEFORE | _SAFE_AFTER | _SIGNS | frozenset("&\\")

_PREFIXES = frozenset({"p", "n", "u", "μ", "m", "k", "K", "M", "G"})
_UNITS = frozenset(
    {"V", "A", "W", "Hz", "Ω", "ohm", "ohms", "Ohm", "Ohms", "F", "H", "S", "s"}
    | {"C", "K", "m", "mil", "mils", "dB", "dBm", "dBc", "dBV", "dBFS", "ppm"}
    | {"LSB", "LSBs", "bit", "bits", "bps", "SPS", "sps", "Vpp", "Vrms", "VDC"}
    | {"VAC", "Ah"}
)
# Unit words W1 also excludes before a cell gap, beyond ``_UNITS``: unit names spelt
# out, in lower case or capitalised, singular or plural, and voltages and currents
# with a suffix ("Vdc", "Vpk"), with or without an SI prefix ("mVpk").
_PLURALS = {"henry": "henries", "hertz": "hertz", "celsius": "celsius"}
_SPELT_UNITS = frozenset(
    name.capitalize() if upper else name
    for base in (
        "volt",
        "millivolt",
        "amp",
        "ampere",
        "milliamp",
        "milliampere",
        "watt",
        "milliwatt",
        "ohm",
        "kilohm",
        "megohm",
        "farad",
        "henry",
        "hertz",
        "second",
        "millisecond",
        "microsecond",
        "nanosecond",
        "degree",
        "kelvin",
        "celsius",
    )
    for name in (base, _PLURALS.get(base, base + "s"))
    for upper in (False, True)
)
_SUFFIXED_UNITS = frozenset(
    {"Vdc", "VDC", "Vac", "VAC", "Vpk", "VPK", "Vp", "Vpp", "VPP", "Vrms", "VRMS"}
    | {"Adc", "ADC", "Aac", "AAC", "Apk", "APK", "Arms", "ARMS"}
)

_MANTISSA = r"(?:\d+(?:\.\d+)?|\.\d+)"
_MANTISSA_E = re.compile(_MANTISSA + "[eE]")
_HEX = re.compile(r"0[xX]([0-9A-Fa-f]+)|([0-9][0-9A-Fa-f]*[A-Fa-f][0-9A-Fa-f]*)[hH]")
# Digits and "Ah": hex ("2Ah" is 0x2A) or ampere-hours ("2 Ah"). Neither: a code.
_AMPERE_HOURS = re.compile(r"\d+Ah")
# One upper-case letter after an integer is a pin name as often as a unit or prefix:
# "1G" (output enable), "2S", "1K", "3M", "4H", "1W". Such a word is a code. "H" is
# also a code by the hex and hours rule (``_HEX_LETTERS`` takes a lone "h" or "H"),
# so it is listed for the reader: removing it alone changes nothing.
_PIN_LETTERS = frozenset("GSKMHW")
_EXPONENT = re.compile(_MANTISSA + r"[eE][-+]?\d+")
_POWER = re.compile(r"\d+(?:\.\d+)?\^[-+]?\d+")
_DECIMAL = re.compile(r"(\d+(?:\.\d+)?|\.\d+)(.*)")
_HEX_LETTERS = re.compile(r"[A-Fa-f]*[hH]?")
_EXPONENT_WORD = re.compile(r"[eE](?:[-+]?\d+)?")
_TIMES_CODE = re.compile(r"(?:\d+(?:\.\d+)?)?[xX]10")
_THREE = re.compile(r"\d{3}(?!\d)")
_LEADING_DIGITS = re.compile(r"\d+")


class _Lex(Enum):
    """What a lexeme is: a word, white space, a line-start bullet or a character."""

    WORD = "word"
    SPACE = "space"
    BULLET = "bullet"
    CHAR = "char"


@dataclass(frozen=True)
class _Lexeme:
    """One lexeme of read text and its span."""

    lex: _Lex
    text: str
    start: int
    end: int


class _Kind(Enum):
    """The kinds of token; a number is equal only to one of its own kind."""

    DECIMAL = "decimal"
    EXPONENT = "exponent"
    POWER = "power"
    HEX = "hex"
    CODE = "code"
    GROUPED = "grouped"


_SIGNABLE = frozenset({_Kind.DECIMAL, _Kind.EXPONENT, _Kind.POWER})


class _Role(Enum):
    """What a "-" right before a number is."""

    SIGN = "sign"
    PLUS_MINUS = "plus-minus"
    RANGE = "range"
    UNKNOWN = "unknown"


class Rule(Enum):
    """Which check a citation failed."""

    PAGE = "page"
    SHORT = "quote-short"
    QUOTE = "quote"
    VALUE = "value"
    NUMBER = "number"
    PIN = "pin"


@dataclass(frozen=True)
class Problem:
    """A failed check and what it means, for a person or a model to act on."""

    rule: Rule
    message: str


@dataclass(frozen=True)
class _Token:
    """One word with a digit: its kind, sign, spans and whether it is known.

    ``start`` takes the sign; ``end`` ends the number (before a glued unit).
    ``lump_start`` and ``lump_end`` widen the span over the glued unit and any unknown
    characters glued to it, none of which a quote may cut off. ``stated`` is the unit
    the text states for the number, glued or after white space ("" for none), with a
    "u" prefix read as micro.
    """

    kind: _Kind
    number: str
    unit: str
    sign: str
    start: int
    end: int
    lump_start: int
    lump_end: int
    word: int
    known: bool
    text: str
    stated: str = ""

    @property
    def key(self) -> tuple[_Kind, str]:
        """Return what two known tokens are compared by, apart from sign and unit."""
        if self.kind is _Kind.HEX:
            match = _HEX.fullmatch(self.number)
            assert match is not None
            return self.kind, f"0x{int(match[1] or match[2], 16):x}"
        return self.kind, self.number


# The page signs that back each value sign: a "-" only a "-"; a "±" only a "±"; a "+"
# a "+" or no sign (both positive); no sign any but "-" (the magnitude of a "±").
_SIGN_BACKED_BY = {
    "-": frozenset({"-"}),
    "±": frozenset({"±"}),
    "+": frozenset({"+", ""}),
    "": frozenset({"", "+", "±"}),
}


def _backs(page: _Token, value: _Token) -> bool:
    """Say whether a known page token backs a known value token.

    The number (``key``), the sign (``_SIGN_BACKED_BY``) and, where both state one,
    the unit must agree. This is the only place signs and units are compared.
    """
    return (
        page.key == value.key
        and page.sign in _SIGN_BACKED_BY[value.sign if value.kind in _SIGNABLE else ""]
        and (not page.stated or not value.stated or page.stated == value.stated)
    )


@dataclass(frozen=True)
class _Reading:
    """Text as read: the read text, its lexemes and its tokens."""

    text: str
    lexemes: tuple[_Lexeme, ...]
    tokens: tuple[_Token, ...]


@dataclass(frozen=True)
class _Page:
    """A page's reading, its normalised text and where a quote may not end."""

    reading: _Reading
    text: str
    origin: tuple[int, ...]
    inside: frozenset[int]
    dashes: frozenset[int]


def _sub_mapped(
    pattern: re.Pattern[str],
    replace_with: Callable[[re.Match[str]], str],
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
        new = replace_with(match)
        pieces.append(new)
        where.extend([origin[match.start()]] * len(new))
        last = match.end()
    pieces.append(text[last:])
    where.extend(origin[last:])
    return "".join(pieces), where


def _join_letters(match: re.Match[str]) -> str:
    """Return what a hyphen or invisible run becomes: nothing between two letters."""
    text = match.string
    start, end = match.start(), match.end()
    if text[start - 1].isalpha() and text[end].isalpha():
        return ""
    return match[0]


def _read_text(text: str) -> str:
    """Return ``text`` folded and with words broken between letters joined."""
    folded = _COMPATIBLE.sub(
        lambda match: unicodedata.normalize("NFKC", match[0]).translate(_FOLD),
        text.translate(_FOLD),
    )
    return _JOIN.sub(_join_letters, folded)


def _join_spaces(match: re.Match[str]) -> str:
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


def _normalised(read: str) -> tuple[str, list[int]]:
    """Return read text normalised for quotes, and where each character came from."""
    text, origin = _sub_mapped(
        _INVISIBLE_RUN, lambda match: "", read, list(range(len(read)))
    )
    return _sub_mapped(_SPACE, _join_spaces, text, origin)


def _normalise(text: str) -> str:
    """Return the form of ``text`` that quotes and pages are compared in."""
    return _normalised(_read_text(text))[0]


def _is_digit(character: str) -> bool:
    """Say whether ``character`` is an ASCII digit, the only digits read."""
    return "0" <= character <= "9"


def _is_word_char(character: str) -> bool:
    """Say whether ``character`` is part of a word: ASCII digit, letter or "_"."""
    return _is_digit(character) or character.isalpha() or character == "_"


def _known_char(character: str) -> bool:
    """Say whether a character outside words and white space has a rule here."""
    return character in _KNOWN_CHARS


def _word_end(text: str, start: int) -> int:
    """Return where the word starting at ``start`` ends."""
    end = start + 1
    while end < len(text):
        character = text[end]
        after = text[end + 1] if end + 1 < len(text) else ""
        if _is_word_char(character):
            end += 1
        elif (
            character in ".," and _is_word_char(text[end - 1]) and _is_word_char(after)
        ):
            end += 1
        elif (
            character in "-+"
            and _MANTISSA_E.fullmatch(text[start:end])
            and _is_digit(after)
        ):
            end += 1
        elif character == "^" and _is_digit(text[end - 1]):
            power = end + 1
            if power < len(text) and text[power] in "-+":
                power += 1
            if power < len(text) and _is_digit(text[power]):
                end = power
            else:
                break
        else:
            break
    return end


def _starts_line(text: str, at: int) -> bool:
    """Say whether only spaces and tabs stand between ``at`` and the line's start."""
    while at > 0 and text[at - 1] in " \t":
        at -= 1
    return at == 0 or text[at - 1] in "\n\r\f\v"


def _lex(text: str) -> list[_Lexeme]:
    """Return the lexemes of read text: words, white-space runs and characters."""
    found: list[_Lexeme] = []
    at = 0
    while at < len(text):
        character = text[at]
        after = text[at + 1] if at + 1 < len(text) else ""
        if character.isspace():
            end = at + 1
            while end < len(text) and text[end].isspace():
                end += 1
            found.append(_Lexeme(_Lex.SPACE, text[at:end], at, end))
        elif _is_word_char(character) or (character == "." and _is_digit(after)):
            end = _word_end(text, at)
            found.append(_Lexeme(_Lex.WORD, text[at:end], at, end))
        elif character == "•" and _starts_line(text, at):
            end = at + 1
            found.append(_Lexeme(_Lex.BULLET, character, at, end))
        else:
            end = at + 1
            found.append(_Lexeme(_Lex.CHAR, character, at, end))
        at = end
    return found


def _is_unit(suffix: str) -> bool:
    """Say whether letters glued after a number are a unit of the closed list."""
    return (
        suffix in _UNITS
        or suffix in _PREFIXES
        or (suffix[:1] in _PREFIXES and suffix[1:] in _UNITS)
    )


def _read_word(word: str) -> tuple[_Kind, str, str]:
    """Return a word's kind, its number (without a glued unit) and that unit."""
    if _AMPERE_HOURS.fullmatch(word):
        return _Kind.CODE, word, ""
    if _HEX.fullmatch(word):
        return _Kind.HEX, word, ""
    if "," in word:
        return _Kind.GROUPED, word, ""
    if _EXPONENT.fullmatch(word):
        return _Kind.EXPONENT, word, ""
    if _POWER.fullmatch(word):
        return _Kind.POWER, word, ""
    decimal = _DECIMAL.fullmatch(word)
    if decimal:
        number, suffix = decimal[1], decimal[2]
        if not suffix:
            return _Kind.DECIMAL, number, ""
        integer = "." not in number
        hex_like = integer and _HEX_LETTERS.fullmatch(suffix)
        pin_like = integer and suffix in _PIN_LETTERS
        if _is_unit(suffix) and not hex_like and not pin_like:
            return _Kind.DECIMAL, number, suffix
    return _Kind.CODE, word, ""


def _stated_unit(lexemes: Sequence[_Lexeme], token: _Token) -> str:
    """Return the unit the text states for a number: glued, or after plain spaces.

    A unit is a word of the closed list (``_is_unit``), "%", or "°" and "C", "F" or
    "K". It may be glued ("19V", "85%", "25°C"), joined by a hyphen ("6.8-pF") or
    follow after white space of any kind ("19 V", "45  µA", "1.22\\n   V"). A "u"
    prefix is micro.
    """
    if token.kind not in _SIGNABLE:
        return ""
    unit = token.unit
    if not unit:
        at = token.word + 1
        between = _get(lexemes, at)
        if _is_char(between, "-"):
            at += 1  # "6.8-pF", "60-V", "5-%": joined by a hyphen, if a unit below
        elif between is not None and between.lex is _Lex.SPACE:
            at += 1
        following = _get(lexemes, at)
        if following is None:
            return ""
        if following.lex is _Lex.WORD and _is_unit(following.text):
            unit = following.text
        elif _is_char(following, "%"):
            unit = "%"
        elif _is_char(following, "°"):
            scale = _get(lexemes, at + 1)
            if scale is not None and scale.text in ("C", "F", "K"):
                unit = "°" + scale.text
    if unit[:1] == "u":
        unit = "μ" + unit[1:]
    return unit


def _get(lexemes: Sequence[_Lexeme], index: int) -> _Lexeme | None:
    """Return the lexeme at ``index``, or None outside the text."""
    return lexemes[index] if 0 <= index < len(lexemes) else None


def _is_char(lexeme: _Lexeme | None, characters: frozenset[str] | str) -> bool:
    """Say whether ``lexeme`` is one character from ``characters``."""
    return lexeme is not None and lexeme.lex is _Lex.CHAR and lexeme.text in characters


def _is_opener(lexeme: _Lexeme | None) -> bool:
    """Say whether a dash after ``lexeme`` is a sign: an opener or a bullet."""
    return _is_char(lexeme, _OPENERS) or (
        lexeme is not None and lexeme.lex is _Lex.BULLET
    )


def _is_foreign(lexeme: _Lexeme | None) -> bool:
    """Say whether ``lexeme`` is a character with no rule here."""
    return (
        lexeme is not None and lexeme.lex is _Lex.CHAR and not _known_char(lexeme.text)
    )


def _is_gap(space: str) -> bool:
    """Say whether a white-space run is a table-cell gap of ``pdftotext -layout`` text.

    A gap is two or more spaces or a tab on one line, or a blank line. Only plain
    spaces count: a run with a no-break, thin or other grouping space is no gap. A
    single line break is no gap, with or without indentation after it.
    """
    if any(c not in _PLAIN_SPACE for c in space):
        return False
    lines = space.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) == 1:
        return "\t" in space or "  " in space
    return len(lines) > 2


def _is_unit_word(word: str) -> bool:
    """Say whether a word is a unit: of ``_UNITS``, spelt out, or with a suffix."""
    return (
        _is_unit(word)
        or word in _SPELT_UNITS
        or word in _SUFFIXED_UNITS
        or (word[:1] in _PREFIXES and word[1:] in _SUFFIXED_UNITS)
    )


def _signs_after_gap(word: _Lexeme | None) -> bool:
    """Say whether a dash after ``word`` and a cell gap is a sign (W1).

    The word must be a symbol or label: no digit (a number may be a range's first
    end), not a unit or prefix of any spelling (``_is_unit_word``: "2.7 V  -3.6 V"
    and "2.7 Volts  -3.6 Volts" may be ranges), not an exponent letter ("1.5 E  -3")
    and not a times sign ("x").
    """
    return (
        word is not None
        and word.lex is _Lex.WORD
        and not any(_is_digit(c) for c in word.text)
        and not _is_unit_word(word.text)
        and not _EXPONENT_WORD.fullmatch(word.text)
        and word.text not in ("x", "X")
    )


def _past_space(lexemes: Sequence[_Lexeme], index: int, step: int) -> int:
    """Return the first index from ``index`` on, going by ``step``, not white space."""
    while 0 <= index < len(lexemes) and lexemes[index].lex is _Lex.SPACE:
        index += step
    return index


def _is_ten(token: _Token | None) -> bool:
    """Say whether ``token`` is the bare number 10, a base of a power of ten."""
    return (
        token is not None
        and token.kind is _Kind.DECIMAL
        and token.number == "10"
        and not token.unit
    )


def _range_left(lexemes: Sequence[_Lexeme], dash: int, at: dict[int, int]) -> int:
    """Return the lexeme of the number a glued dash may end a range from, or -1.

    The number is glued to the dash, with nothing or "%", "°" or "°C"/"°F"/"°K"
    between them.
    """
    before = _get(lexemes, dash - 1)
    if before is None:
        return -1
    if dash - 1 in at:
        return dash - 1
    if _is_char(before, "%°"):
        return dash - 2 if dash - 2 in at else -1
    degree = _get(lexemes, dash - 2)
    if before.text in ("C", "F", "K") and _is_char(degree, "°"):
        return dash - 3 if dash - 3 in at else -1
    return -1


def _is_range(left: _Token, right: _Token, left_sign: str) -> bool:
    """Say whether ``left``-``right``, glued by a dash, is certainly a range."""
    if left.kind is not _Kind.DECIMAL or right.kind is not _Kind.DECIMAL:
        return False
    if _is_ten(left) or left.number == "10":
        return False
    low = float(("-" if left_sign == "-" else "") + left.number)
    return low < float(right.number)


def _dash_role(
    lexemes: Sequence[_Lexeme], dash: int, at: dict[int, int]
) -> tuple[_Role, int]:
    """Return the role of the dash at ``dash`` before a number, and a lexeme index.

    The index is the number the dash may join (for a range or a 10 before it), or -1.
    """
    before = _get(lexemes, dash - 1)
    if before is None:
        return _Role.SIGN, -1
    if before.lex is _Lex.SPACE:
        previous_index = _past_space(lexemes, dash - 1, -1)
        previous = _get(lexemes, previous_index)
        if previous is None or _is_opener(previous):
            return _Role.SIGN, -1
        if previous.lex is _Lex.WORD and previous.text in _FUNCTION_WORDS:
            return _Role.SIGN, -1
        if _is_gap(before.text) and _signs_after_gap(previous):
            return _Role.SIGN, -1
        return _Role.UNKNOWN, previous_index if previous_index in at else -1
    if _is_char(before, "/") and _is_char(_get(lexemes, dash - 2), "+"):
        return _Role.PLUS_MINUS, -1
    if _is_opener(before):
        return _Role.SIGN, -1
    left = _range_left(lexemes, dash, at)
    if left == -1:
        return _Role.UNKNOWN, -1
    return _Role.RANGE, left


def _read(text: str) -> _Reading:
    """Return the reading of read text: lexemes and tokens with what is known."""
    lexemes = _lex(text)
    drafts: list[_Token] = []
    at: dict[int, int] = {}  # lexeme index -> token index
    for index, lexeme in enumerate(lexemes):
        if lexeme.lex is _Lex.WORD and any(_is_digit(c) for c in lexeme.text):
            kind, number, unit = _read_word(lexeme.text)
            end = lexeme.start + len(number)
            at[index] = len(drafts)
            drafts.append(
                _Token(
                    kind,
                    number,
                    unit,
                    "",
                    lexeme.start,
                    end,
                    lexeme.start,
                    lexeme.end,
                    index,
                    kind is not _Kind.GROUPED,
                    "",
                )
            )
    unknown: set[int] = {i for i, token in enumerate(drafts) if not token.known}
    sign: dict[int, str] = {}
    start: dict[int, int] = {}
    ranges: list[tuple[int, int]] = []

    def forget(*lexeme_indices: int) -> None:
        """Mark the tokens at these lexeme indices unknown."""
        for lexeme_index in lexeme_indices:
            if lexeme_index in at:
                unknown.add(at[lexeme_index])

    def token_at(lexeme_index: int) -> _Token | None:
        """Return the token at a lexeme index, if there is one."""
        return drafts[at[lexeme_index]] if lexeme_index in at else None

    def ends_in_digit(lexeme_index: int) -> bool:
        """Say whether the lexeme at the index is a word ending in an ASCII digit."""
        lexeme = _get(lexemes, lexeme_index)
        return (
            lexeme is not None
            and lexeme.lex is _Lex.WORD
            and _is_digit(lexeme.text[-1])
        )

    # Signs and what is glued before each number.
    for t, token in enumerate(drafts):
        w = token.word
        before = _get(lexemes, w - 1)
        if _is_char(before, "-"):
            role, other = _dash_role(lexemes, w - 1, at)
            if role is _Role.RANGE:
                left = drafts[at[other]]
                if _is_range(left, token, sign.get(at[other], "")):
                    ranges.append((at[other], t))
                else:
                    forget(other, w)
                    start[t] = w - 1
            elif role is _Role.UNKNOWN:
                start[t] = w - 1
                forget(w)
                if _is_ten(token_at(other)):
                    forget(other)
            else:
                start[t] = w - 1 if role is _Role.SIGN else w - 3
                sign[t] = "-" if role is _Role.SIGN else "±"
        elif before is not None and _is_char(before, "+±"):
            start[t] = w - 1
            sign[t] = before.text
            glued = _get(lexemes, w - 2)
            if ends_in_digit(w - 2) or _is_char(glued, "."):
                forget(w - 2, w)
            elif _is_foreign(glued):
                forget(w)
            if before.text == "+":
                previous = _past_space(lexemes, w - 2, -1)
                if _is_ten(token_at(previous)):
                    forget(previous, w)
        elif before is not None and before.lex is _Lex.CHAR:
            if ends_in_digit(w - 2):
                forget(w - 2, w)  # one character between two digits
            elif before.text not in _SAFE_BEFORE:
                forget(w)
        if t in sign and token.kind not in _SIGNABLE:
            forget(w)

    # What is glued after each number.
    for token in drafts:
        w = token.word
        after = _get(lexemes, w + 1)
        if after is None or after.lex is _Lex.SPACE or _is_char(after, _SAFE_AFTER):
            continue
        if _is_char(after, "-"):
            following = _get(lexemes, w + 2)
            continues = following is not None and (
                following.lex is _Lex.WORD and _is_digit(following.text[0])
            )
            if token.kind in (_Kind.HEX, _Kind.CODE) and continues:
                forget(w, w + 2)
            continue
        forget(w)

    # Across white space.
    for index, lexeme in enumerate(lexemes):
        if lexeme.lex is not _Lex.SPACE:
            continue
        left, right = _get(lexemes, index - 1), _get(lexemes, index + 1)
        if left is None or right is None:
            continue
        if _is_foreign(left):
            forget(index + 1)
            if _is_char(right, _SIGNS):
                forget(index + 2)
        if _is_foreign(right):
            forget(index - 1)
        left_token, right_token = token_at(index - 1), token_at(index + 1)
        if left_token is not None and right_token is not None:
            if ends_in_digit(index - 1) and any(
                c not in _PLAIN_SPACE for c in lexeme.text
            ):
                forget(index - 1, index + 1)  # a grouping space between digits
            elif (
                not _is_gap(lexeme.text)
                and left_token.kind is _Kind.DECIMAL
                and not left_token.unit
                and _is_digit(right.text[0])
            ):
                whole, _, fraction = left_token.number.partition(".")
                lead = _LEADING_DIGITS.match(right.text)
                digits = len(lead[0]) if lead else 0
                if (whole and not fraction and _THREE.match(right.text)) or (
                    len(fraction) == 3 and 1 <= digits <= 3
                ):
                    forget(index - 1, index + 1)  # may be one grouped number
        if left_token is not None and right.lex is _Lex.WORD:
            if _EXPONENT_WORD.fullmatch(right.text):
                forget(index - 1, index + 1)
        if _is_char(left, _SIGNS) and right_token is not None:
            if left.text == "-":
                forget(index + 1)  # a dash standing apart from the number
            dash_before = _past_space(lexemes, index - 2, -1)
            if left.text in "-+" and _is_ten(token_at(dash_before)):
                forget(dash_before, index + 1)

    # Times ten: "1 x 10-6", "1 × 10 -6", "1x10 -6".
    for index, lexeme in enumerate(lexemes):
        is_times = _is_char(lexeme, "×") or (
            lexeme.lex is _Lex.WORD and lexeme.text in ("x", "X")
        )
        times_code = lexeme.lex is _Lex.WORD and _TIMES_CODE.fullmatch(lexeme.text)
        if not is_times and not times_code:
            continue
        previous = _past_space(lexemes, index - 1, -1)
        ten = index
        if is_times:
            ten = _past_space(lexemes, index + 1, 1)
            if not _is_ten(token_at(ten)):
                continue  # "5 x 6 mm": no power of ten without a 10 after the sign
            forget(previous, ten)
        else:
            forget(index)
            if lexeme.text[0] in "xX":
                forget(previous)
        following = _past_space(lexemes, ten + 1, 1)
        if _is_char(_get(lexemes, following), "-+"):
            following = _past_space(lexemes, following + 1, 1)
        forget(following)

    # Footnote forms: "(2)", "[1]".
    for token in drafts:
        w = token.word
        bare = token.kind is _Kind.DECIMAL and "." not in token.number
        if (
            bare
            and not token.unit
            and _is_char(_get(lexemes, w - 1), "([")
            and _is_char(_get(lexemes, w + 1), ")]")
        ):
            forget(w)

    # A range is as known as its less known end.
    changed = True
    while changed:
        changed = False
        for left_index, right_index in ranges:
            if (left_index in unknown) != (right_index in unknown):
                unknown.update((left_index, right_index))
                changed = True

    tokens = []
    for t, token in enumerate(drafts):
        token_start = lexemes[start[t]].start if t in start else token.start
        lump_start, lump_end = token_start, lexemes[token.word].end
        first = start.get(t, token.word)
        while _is_foreign(_get(lexemes, first - 1)):
            first -= 1
            lump_start = lexemes[first].start
        last = token.word
        while _is_foreign(_get(lexemes, last + 1)):
            last += 1
            lump_end = lexemes[last].end
        tokens.append(
            replace(
                token,
                sign=sign.get(t, ""),
                start=token_start,
                lump_start=lump_start,
                lump_end=lump_end,
                known=t not in unknown,
                text=text[token_start : token.end],
                stated=_stated_unit(lexemes, token),
            )
        )
    return _Reading(text, tuple(lexemes), tuple(_row_units(text, tokens)))


def _row_units(text: str, tokens: list[_Token]) -> list[_Token]:
    """Give each number with no unit the unit column of its row.

    A row's unit column is a unit standing apart, after white space, from the last
    number of a run of numbers on one line: "VIN 4.5 28 V" gives 4.5 the "V" of 28,
    and "= 100 mV 5.8 6.6 7.7 V" gives 5.8 and 6.6 the "V". A number with no unit
    takes it when only spaces or tabs, and numbers that state no unit, stand between
    them. A unit glued or joined by a hyphen to its number ("22uF", "6.8-pF", "93%")
    is that cell's own and is not passed on. Only the signable kinds chain.
    """
    result = list(tokens)
    column = ""
    for i in range(len(result) - 1, -1, -1):
        here = result[i]
        if i + 1 < len(result):
            after = result[i + 1]
            between = text[here.lump_end : after.start]
            joined = (
                here.kind in _SIGNABLE
                and after.kind in _SIGNABLE
                and bool(between)
                and all(c in " \t" for c in between)
            )
            if not joined:
                column = ""
        if here.stated:
            apart = here.kind in _SIGNABLE and text[here.end : here.end + 1].isspace()
            column = here.stated if apart else ""
        elif column:
            result[i] = replace(here, stated=column)
    return result


def _read_page(page_text: str) -> _Page:
    """Return a page's reading with its normalised text and the quote boundaries."""
    reading = _read(_read_text(page_text))
    text, origin = _normalised(reading.text)

    def mapped(start: int, end: int) -> tuple[int, int]:
        """Return the normalised span of a span of read text."""
        return bisect.bisect_left(origin, start), bisect.bisect_left(origin, end)

    inside: set[int] = set()
    covered: set[int] = set()
    for token in reading.tokens:
        first, last = mapped(token.lump_start, token.lump_end)
        inside.update(range(first + 1, last))
        number_first, number_last = mapped(token.start, token.end)
        inside.update(range(number_first + 1, number_last))
        covered.update(range(token.start, token.end))
    dashes: set[int] = set()
    for lexeme in reading.lexemes:
        if _is_char(lexeme, "-") and lexeme.start not in covered:
            at = bisect.bisect_left(origin, lexeme.start)
            if at < len(origin) and origin[at] == lexeme.start:
                dashes.add(at)
    return _Page(reading, text, tuple(origin), frozenset(inside), frozenset(dashes))


def _occurrences(quote: str, page: _Page) -> list[tuple[int, int]]:
    """Return where ``quote`` is in ``page``, as read-text spans, with clean ends."""
    wanted = _normalise(quote)
    found: list[tuple[int, int]] = []
    if not wanted:
        return found
    at = page.text.find(wanted)
    while at != -1:
        end = at + len(wanted)
        if at not in page.inside and end not in page.inside and at not in page.dashes:
            found.append((page.origin[at], page.origin[end - 1] + 1))
        at = page.text.find(wanted, at + 1)
    return found


def quote_on_page(quote: str, page_text: str) -> bool:
    """Say whether ``quote`` is in ``page_text``, both normalised.

    A quote whose start or end falls inside a number of the page ("1.2" for "1.25",
    "0.3" for "-0.3", "70" for "70h"), or that starts on a dash outside a number, is
    not there at that place; it counts if it is somewhere else with clean ends. An
    empty quote is on no page.
    """
    return bool(_occurrences(quote, _read_page(page_text)))


def _span_length(read: str) -> int:
    """Return the length of read text with each white-space run counted as one.

    Invisible characters count nothing, and white space at the ends nothing (W3).
    """
    return len(_SPACE.sub(" ", _INVISIBLE_RUN.sub("", read)).strip())


def _quote_length(quote: str, page_text: str) -> int:
    """Return a quote's length for ``MIN_QUOTE_CHARS``, counted on the page (W3).

    Each place the quote is on the page is counted with the page's own spacing, and
    the shortest counts: a quote may not lengthen itself with spaces the page does
    not have. A quote that is not on the page is counted with its own spacing.
    """
    if len(_normalise(quote)) >= MIN_QUOTE_CHARS:
        return len(_normalise(quote))  # a lower bound: spaces can only add to it
    page = _read_page(page_text)
    places = _occurrences(quote, page)
    if not places:
        return _span_length(_read_text(quote))
    return min(_span_length(page.reading.text[start:end]) for start, end in places)


def _pages_with(quote: str, pages: Sequence[str]) -> list[int]:
    """Return the pages, counted from 1, that ``quote`` is on."""
    return [
        number for number, text in enumerate(pages, 1) if quote_on_page(quote, text)
    ]


def _unreadable(value: str) -> list[str]:
    """Return the parts of ``value`` outside the grammar: unknown numbers and marks."""
    reading = _read(_read_text(value))
    parts = [token.text for token in reading.tokens if not token.known]
    parts += [lexeme.text for lexeme in reading.lexemes if _is_foreign(lexeme)]
    return parts


def _missing(value: str, quote: str, page_text: str) -> list[str] | None:
    """Return the numbers of ``value`` that some occurrence of the quote lacks.

    An unknown number of the value is always missing. None if the quote is not on the
    page.
    """
    page = _read_page(page_text)
    occurrences = _occurrences(quote, page)
    if not occurrences:
        return None
    wanted = _read(_read_text(value)).tokens
    missing: list[str] = []
    for token in wanted:
        backed = token.known and all(
            any(
                mine.known and _backs(mine, token)
                for mine in page.reading.tokens
                if start <= mine.start and mine.end <= end
            )
            for start, end in occurrences
        )
        if not backed:
            missing.append(token.text)
    return missing


def _words(reading: _Reading, start: int, end: int) -> list[str]:
    """Return the whole words of a reading inside a span of it."""
    return [
        lexeme.text
        for lexeme in reading.lexemes
        if lexeme.lex is _Lex.WORD and start <= lexeme.start and lexeme.end <= end
    ]


def _phrase_missing(value: str, quote: str, page_text: str) -> bool:
    """Say whether some occurrence of the quote lacks the value's words, in order."""
    page = _read_page(page_text)
    reading = _read(_read_text(value))
    wanted = _words(reading, 0, len(reading.text))
    if not wanted:
        return True
    for start, end in _occurrences(quote, page):
        words = _words(page.reading, start, end)
        if not any(
            words[at : at + len(wanted)] == wanted
            for at in range(len(words) - len(wanted) + 1)
        ):
            return True
    return False


def check_citation(
    value: str,
    quote: str,
    page: int,
    pages: Sequence[str],
    *,
    derived: bool = False,
    text_only: bool = False,
) -> Problem | None:
    """Check one value against its quote and page; return the first problem, or None.

    ``pages`` are the PDF's page texts, page 1 first. ``derived`` says the value is
    worked out from the quote rather than copied: only the quote is checked. A value
    with no number is refused unless ``text_only`` says it is words, which must then
    be whole words of the quote, in order, wherever the quote occurs.
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
    if _quote_length(quote, pages[page - 1]) < MIN_QUOTE_CHARS:
        return Problem(
            Rule.SHORT,
            f"the quote {quote!r} is under {MIN_QUOTE_CHARS} characters, counting "
            "each run of white space between words as one, short enough to match by "
            "accident: quote the whole table row or sentence",
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
    if derived:
        return None
    return _check_value(value, quote, pages[page - 1], missing, text_only=text_only)


def _check_value(
    value: str, quote: str, page_text: str, missing: list[str], *, text_only: bool
) -> Problem | None:
    """Check a copied value against a quote that is on the page."""
    unreadable = _unreadable(value)
    if unreadable:
        return Problem(
            Rule.VALUE,
            f"the value {value!r} has {', '.join(unreadable)}, which this check "
            "cannot read for certain: write the value with plain numbers and units as "
            "the page writes them, or mark it derived and say how",
        )
    has_number = bool(_read(_read_text(value)).tokens)
    if not has_number and not text_only:
        return Problem(
            Rule.VALUE,
            f"the value {value!r} has no number to check: if it is words, mark it "
            "text only, and they must be words of the quote; else mark it derived",
        )
    if text_only and _phrase_missing(value, quote, page_text):
        return Problem(
            Rule.VALUE,
            f"the words of the value {value!r} are not whole words of the quote, in "
            "that order, at every place the quote is on the page",
        )
    if not missing:
        return None
    occurrences = len(_occurrences(quote, _read_page(page_text)))
    where = (
        f" at every one of the {occurrences} places it is on the page"
        if occurrences > 1
        else ""
    )
    return Problem(
        Rule.NUMBER,
        f"the value {value!r} has {', '.join(missing)}, which the quote does not "
        f"have{where} as a number this check can read: a right quote with a wrong "
        "number, a unit or prefix other than the page's (write it as the page does), "
        "or a number whose sign or grouping is not certain. If the value is worked "
        "out from the quote, mark it derived and say how",
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
