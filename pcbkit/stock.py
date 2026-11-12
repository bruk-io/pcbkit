"""`pcbkit parts stock`: is each BOM line in stock, and at what price.

The functions from a Mouser answer to a row are pure: they take the answer as it came
(a decoded dict, whatever its age) and the time, and touch no network and no disk.
``fetch_answers`` is the one function that asks, ten part numbers to a request and
spaced apart, like ``pcbkit datasheet find``. The rows and the exit code are what
``pcbkit.cli`` prints.

It asks Mouser directly (``mouser.send`` and ``cache.write``), not through
``parts.ask_mouser``. That is a work-around for this slice's file limits, not a design
choice. The follow-up: give ``cache.fetch`` and ``parts.ask_mouser`` the future-date
rule (a future-dated entry is not fresh; today ``ask_mouser`` treats it as fresh, a
latent bug for ``datasheet find`` too) and a keep-on-write-failure path, have
``mouser.send`` wrap ``http.client.HTTPException`` as ``MouserError`` with a retry,
then point ``stock`` back at ``parts.ask_mouser``.

Mouser's field names are the one thing here that is partly assumed. They are all in the
block below, and nowhere else in pcbkit's code reads them for stock. The first
recorded answer in ``tests/fixtures/mouser`` (INA226, 2026-10-09) confirmed
``AvailabilityInStock``, ``LifecycleStatus``, ``LeadTime``, ``Min``, ``Mult``,
``SuggestedReplacement`` and ``PriceBreaks`` (with ``Quantity``, ``Price`` and
``Currency``), and showed that ``Availability`` is absent from it. Experiment M3 of
plans/wp17-gate-checks.md measures the rest on a real machine. A field that is missing,
or of a type nobody expected, gives null in the row or the status ``unreadable``, never
a number made up from nothing.
"""

from __future__ import annotations

import http.client
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, quote_plus

from pcbkit import cache, mouser, parts
from pcbkit.datasheets import MAKERS, squash
from pcbkit.fab.bom import BomLine, mpn_problem

# --- Mouser's field names (assumed until M3 measures them) ----------------------------

ANSWER_PATH = ("SearchResults", "Parts")
F_MPN = "ManufacturerPartNumber"
F_MOUSER_PN = "MouserPartNumber"
F_MAKER = "Manufacturer"
F_STOCK = "AvailabilityInStock"  # a whole number as text: "0", "1234"
F_AVAILABILITY = "Availability"  # raw text such as "1,234 In Stock"; may be absent
F_LIFECYCLE = "LifecycleStatus"  # null for an ordinary active part
F_LEAD_TIME = "LeadTime"
F_MIN = "Min"
F_MULT = "Mult"
F_REPLACEMENT = "SuggestedReplacement"
F_BREAKS = "PriceBreaks"
F_BREAK_QTY = "Quantity"
F_BREAK_PRICE = "Price"  # text with a currency sign and a dot decimal: "$3.96"
F_BREAK_CURRENCY = "Currency"

F_DISCONTINUED = "IsDiscontinued"  # a boolean, when Mouser sends it

# Lifecycle texts, compared in lower case. Only the allow-list makes a line fit to be
# `ok`: a text on neither list is `unreadable`, never taken as fine.
LIFECYCLE_ACTIVE = frozenset({"", "new product", "new at mouser", "active"})
NOT_ACTIVE = re.compile(
    r"obsolete|end[ -]of[ -]life|\beol\b|not recommended|\bnrnd\b|discontinued"
    r"|last time buy|not for new design|\bnfnd\b|inactive|phase[ds]?[ -]?out",
    re.IGNORECASE,
)
# The stock figure in an Availability text, used only when F_STOCK is missing.
IN_STOCK_TEXT = re.compile(r"^\s*(\d[\d,]*)\s+in stock\b", re.IGNORECASE)

# --- the rest ------------------------------------------------------------------------

OK = "ok"
SHORT = "short"
NOT_ACTIVE_STATUS = "not-active"
NOT_FOUND = "not-found"
AMBIGUOUS = "ambiguous"
NO_MPN = "no-mpn"
NOT_ASKED = "not-asked"
# Mouser answered, but not in a shape that can be judged (added by the slice 5 review).
UNREADABLE = "unreadable"
# The answer used is older than --max-age (or dated in the future) and Mouser was not
# asked afresh: what it says may no longer be true.
STALE = "stale"
STATUSES = (
    OK,
    SHORT,
    NOT_ACTIVE_STATUS,
    NOT_FOUND,
    AMBIGUOUS,
    NO_MPN,
    NOT_ASKED,
    UNREADABLE,
    STALE,
)

EXIT_NOT_OK = 1
EXIT_NOT_ASKED = 2
DEFAULT_MAX_AGE_HOURS = parts.STOCK_MAX_AGE.total_seconds() / 3600
GAP_SECONDS = 3.0  # between two requests that are really sent, as datasheet find does

_NUMBER = r"(\d+(?:\.\d+)?|[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?)"
# The whole text: optional currency letters and sign, the number, a currency code.
_PRICE = re.compile(
    rf"^\s*(?:[A-Z]{{1,3}})?\s*[$€£¥]?\s*{_NUMBER}\s*(?:[A-Z]{{3}})?\s*$"
)
_WHOLE = re.compile(r"^\d+$|^\d{1,3}(,\d{3})+$")
_COMPANY_WORDS = frozenset(
    "inc incorporated ltd limited co corp corporation company gmbh ag llc plc "
    "electronics electronic".split()
)


def _sleep(seconds: float) -> None:
    """Wait ``seconds``."""
    time.sleep(seconds)


# --- reading one part ----------------------------------------------------------------


def _text(value: Any) -> str | None:
    """Return ``value`` as text if it is a non-empty string or a number, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _whole(value: Any) -> int | None:
    """Return a whole number from a number or from text like "1,234", else None.

    Commas are accepted only as thousands separators ("1,234", not "12,34").
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value == int(value) else None
    if isinstance(value, str) and _WHOLE.match(value.strip()):
        return int(value.strip().replace(",", ""))
    return None


def parse_price(text: Any) -> float | None:
    """Return the number in a price text such as "$3.96", or None if unsure.

    The whole text must be an optional currency sign or letters, the number, and an
    optional currency code, with a dot decimal. A text with a comma as its decimal mark
    ("0,123 EUR"), an exponent, a sign, a unit ("$1.50 per 100") or any other shape is
    None, never a guess, and never 0.
    """
    if not isinstance(text, str):
        return None
    found = _PRICE.match(text)
    return float(found.group(1).replace(",", "")) if found else None


def price_breaks(part: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the part's price breaks, lowest quantity first.

    Each is ``{"qty", "price", "price_value", "currency"}``: ``price`` is Mouser's
    text as it came, ``price_value`` the number in it or None. An entry with no whole
    quantity is left out.
    """
    found = part.get(F_BREAKS)
    breaks = []
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, Mapping):
            continue
        qty = _whole(item.get(F_BREAK_QTY))
        if qty is None:
            continue
        raw = item.get(F_BREAK_PRICE)
        breaks.append(
            {
                "qty": qty,
                "price": raw if isinstance(raw, str) else None,
                "price_value": parse_price(raw),
                "currency": _text(item.get(F_BREAK_CURRENCY)),
            }
        )
    return sorted(breaks, key=lambda item: item["qty"])


def break_notes(part: Mapping[str, Any]) -> list[str]:
    """Return what is wrong with the part's price breaks, if anything."""
    found = part.get(F_BREAKS)
    if not isinstance(found, list):
        return [f"Mouser's answer has no list in {F_BREAKS}: the price is unknown"]
    breaks = price_breaks(part)
    notes = []
    if len(breaks) < len(found):
        notes.append(f"a {F_BREAKS} entry could not be read and was left out")
    elif not breaks:
        notes.append(f"{F_BREAKS} is empty: the price is unknown")
    if any(
        item["price"] is not None and item["price_value"] is None for item in breaks
    ):
        notes.append("a price break could not be read as a number: it is null")
    return notes


def unit_price_at(breaks: Sequence[Mapping[str, Any]], needed: int) -> float | None:
    """Return the unit price for buying ``needed`` parts in all, or None.

    That is the price of the highest break whose quantity is at most ``needed``, and
    of the lowest break when ``needed`` is below every one (Mouser sells no fewer).
    ``needed`` is the quantity ordered over every board and line, not the count on
    one board.
    """
    if not breaks:
        return None
    chosen = breaks[0]
    for item in breaks:
        if item["qty"] <= needed:
            chosen = item
    return chosen["price_value"]  # type: ignore[no-any-return]


def stock_of(part: Mapping[str, Any]) -> tuple[int | None, str | None]:
    """Return the part's stock, and a note when it was not read from its own field."""
    stock = _whole(part.get(F_STOCK))
    if stock is not None:
        return stock, None
    found = IN_STOCK_TEXT.match(_text(part.get(F_AVAILABILITY)) or "")
    if found:
        return int(found.group(1).replace(",", "")), (
            f"stock read from the {F_AVAILABILITY} text: no usable {F_STOCK}"
        )
    return None, None


def order_quantity(needed: int, minimum: int, multiple: int) -> int:
    """Return the order: ``needed`` raised to ``minimum``, then to a multiple."""
    wanted = max(needed, minimum)
    return -(-wanted // multiple) * multiple


def lifecycle_of(part: Mapping[str, Any]) -> tuple[str | None, str | None, bool]:
    """Return the lifecycle text, a problem reading it, and whether it is not active.

    The text is judged by the allow-list ``LIFECYCLE_ACTIVE``: a text on the
    ``NOT_ACTIVE`` pattern is not active, any other is a problem (unreadable), never
    taken as fine. ``IsDiscontinued`` true is not active.
    """
    raw = part.get(F_LIFECYCLE)
    text: str | None = None
    problem = None
    inactive = False
    if isinstance(raw, str):
        text = raw.strip() or None
        if text and text.lower() not in LIFECYCLE_ACTIVE:
            if NOT_ACTIVE.search(text):
                inactive = True
            else:
                problem = f"{F_LIFECYCLE} {text!r} is not a text pcbkit knows"
    elif raw is not None:
        problem = f"{F_LIFECYCLE} is not text ({type(raw).__name__})"
    flag = part.get(F_DISCONTINUED)
    if flag is True or (isinstance(flag, str) and flag.strip().lower() == "true"):
        inactive = True
    elif flag is not None and flag is not False:
        if not (isinstance(flag, str) and flag.strip().lower() == "false"):
            problem = problem or f"{F_DISCONTINUED} {flag!r} is not true or false"
    return text, problem, inactive


# --- choosing the part ---------------------------------------------------------------


def answer_parts(answer: Any) -> list[Mapping[str, Any]] | None:
    """Return the parts in Mouser's ``answer``, or None if it is not shaped like one.

    A search that found nothing has an empty list; an answer with no list at all, or
    with an item in it that is not an object, is something else (a renamed field, a
    changed API) and is None.
    """
    found = answer
    for name in ANSWER_PATH:
        found = found.get(name) if isinstance(found, Mapping) else None
    if not isinstance(found, list) or not all(isinstance(i, Mapping) for i in found):
        return None
    return list(found)


def asked_number_problem(line: BomLine) -> str | None:
    """Return why Mouser cannot be asked about the line's part number, or None."""
    reason = mpn_problem(line.mpn, line.value)
    if reason is not None:
        return reason
    low, high = mouser.PART_NUMBER_LENGTH
    if not low <= len(line.mpn.strip()) <= high or "|" in line.mpn:
        return (
            f"is not {low} to {high} characters with no '|', so Mouser cannot take it"
        )
    if not squash(line.mpn):
        return "has no letters or digits"
    return None


def _maker_words(name: str) -> list[str]:
    """Return ``name`` as lower-case words, without trailing company words."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    while len(words) > 1 and words[-1] in _COMPANY_WORDS:
        words.pop()
    return words


def _maker_key(name: str) -> str:
    """Return ``name`` as lower-case words without a trailing company word, joined."""
    return "".join(_maker_words(name))


# The longer Mouser-side names in the tests of this rule ("Microchip Technology",
# "Vishay / Dale", "Analog Devices / Maxim Integrated", ...) are written from memory,
# not measured: experiment M3 of plans/wp17-gate-checks.md is to record Mouser's real
# maker strings.
#
# Names that differ only by a dropped company word compare equal ("Samsung" and
# "Samsung Electronics"), and so do the leading words of a longer name ("Samsung" and
# "Samsung Semiconductor"). That only matters when the part number matches exactly, and
# M3's recorded maker strings will settle whether the rule needs to be tighter.
def same_maker(one: str | None, other: str | None) -> bool:
    """Say whether two maker names are the same maker.

    They are when, after dropping trailing company words ("Inc.", "Electronics") and
    punctuation, the words are the same; when one name's whole words are the leading
    whole words of the other ("Microchip" and "Microchip Technology", "Vishay" and
    "Vishay / Dale"); or when both are known aliases. Never a character prefix: "TI" is
    not "Tiny Co", and "Murata" is not "Murataland". When either name is empty, the
    answer is no.
    """
    left, right = _maker_words(one or ""), _maker_words(other or "")
    if not left or not right:
        return False
    short, long = sorted((left, right), key=len)
    if long[: len(short)] == short or "".join(left) == "".join(right):
        return True
    keys_left, keys_right = "".join(left), "".join(right)
    for names in MAKERS:
        keys = {_maker_key(name) for name in names}
        if keys_left in keys and keys_right in keys:
            return True
    return False


def choose(
    found: Sequence[Mapping[str, Any]], mpn: str, maker: str
) -> list[Mapping[str, Any]]:
    """Return the parts whose number is ``mpn``, letters and digits only.

    Mouser's own number counts too. If that leaves more than one and ``maker`` is
    known, the parts of that maker alone, when there are any. The caller judges what is
    left: more than one is ambiguous, and so is one of another maker than ``maker``.
    """
    wanted = squash(mpn)
    same = [
        item
        for item in found
        if wanted
        in (
            squash(_text(item.get(F_MPN)) or ""),
            squash(_text(item.get(F_MOUSER_PN)) or ""),
        )
    ]
    if len(same) > 1 and maker.strip():
        narrowed = [
            item for item in same if same_maker(maker, _text(item.get(F_MAKER)))
        ]
        if narrowed:
            return narrowed
    return same


@dataclass(frozen=True)
class Judged:
    """What a Mouser part says: status, ``mouser`` block, order quantity and notes."""

    status: str
    block: dict[str, Any]
    order_qty: int | None
    notes: list[str]


def judge(part: Mapping[str, Any], needed: int) -> Judged:
    """Judge one Mouser ``part`` for ``needed`` of it in all, over every line using it.

    Not ``ok`` unless everything that decides it was read: the lifecycle, the stock, and
    the minimum and multiple Mouser sells in. The order is ``needed`` raised to the
    minimum and then to a multiple, and the stock and the price are judged at it.
    """
    notes = []
    problems = []
    stock, stock_note = stock_of(part)
    if stock_note:
        notes.append(stock_note)
    if stock is None:
        notes.append(f"Mouser's answer has no usable {F_STOCK}: stock is unknown")
    lifecycle, lifecycle_problem, inactive = lifecycle_of(part)
    if lifecycle_problem:
        notes.append(lifecycle_problem)
        problems.append(lifecycle_problem)
    minimum, multiple = _whole(part.get(F_MIN)), _whole(part.get(F_MULT))
    for name, found in ((F_MIN, minimum), (F_MULT, multiple)):
        if found is None or found < 1:
            notes.append(f"Mouser's answer has no usable {name}: the order is unknown")
            problems.append(name)
    order = None
    if minimum and multiple and minimum >= 1 and multiple >= 1:
        order = order_quantity(needed, minimum, multiple)
        if order != needed:
            notes.append(
                f"order {order}, not {needed}: Mouser sells at least {minimum}, "
                f"in multiples of {multiple}"
            )
    breaks = price_breaks(part)
    notes += break_notes(part)
    block = {
        "part_number": _text(part.get(F_MOUSER_PN)),
        "manufacturer": _text(part.get(F_MAKER)),
        "stock": stock,
        "availability": _text(part.get(F_AVAILABILITY)),
        "lifecycle": lifecycle,
        "lead_time": _text(part.get(F_LEAD_TIME)),
        "price_breaks": breaks,
        "unit_price_at_order": unit_price_at(breaks, order or needed),
        "min": minimum,
        "mult": multiple,
        "suggested_replacement": _text(part.get(F_REPLACEMENT)),
    }
    if inactive:
        status = NOT_ACTIVE_STATUS
    elif problems or stock is None:
        status = UNREADABLE
    elif order is not None and stock < order:
        status = SHORT
    else:
        status = OK
    return Judged(status, block, order, notes)


# --- rows ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """What was learned about one part number: Mouser's answer, and what went wrong."""

    entry: cache.Entry | None
    notes: tuple[str, ...] = ()


def is_stale(
    entry: cache.Entry, now: datetime, max_age: timedelta | None, offline: bool
) -> bool:
    """Say whether ``entry`` is older than ``max_age`` or dated after ``now``.

    With ``offline`` the user asked for the cache at any age, so only a date in the
    future (which is no age at all) makes it stale.
    """
    age = entry.age(now)
    if age < timedelta(0):
        return True
    return not offline and max_age is not None and age > max_age


def _row(
    line: BomLine,
    boards: int,
    needed_total: int | None,
    status: str,
    entry: cache.Entry | None,
    now: datetime,
    judged: Judged | None,
    notes: Sequence[str],
) -> dict[str, Any]:
    """Return one JSON row, in the order the docs list its fields."""
    return {
        "item": line.item,
        "refs": list(line.refs),
        "mpn": line.mpn,
        "mfr": line.mfr,
        "qty_per_board": line.qty,
        "needed": line.qty * boards,
        "needed_total": needed_total,
        "order_qty": judged.order_qty if judged else None,
        "status": status,
        "fetched": entry.fetched.isoformat() if entry else None,
        "age_hours": (
            round(entry.age(now).total_seconds() / 3600, 2) if entry else None
        ),
        "mouser": judged.block if judged else None,
        "notes": list(notes),
    }


@dataclass(frozen=True)
class _Resolved:
    """What a line came to before it is judged: a status, or the one part it matched."""

    line: BomLine
    answer: Answer | None
    status: str | None  # None when ``part`` is the one match, still to be judged
    part: Mapping[str, Any] | None
    notes: tuple[str, ...]


def _match_line(
    line: BomLine, entry: cache.Entry
) -> tuple[str | None, Mapping[str, Any] | None, list[str]]:
    """Return a line's status or its one matched part against an entry, and notes."""
    found = answer_parts(entry.value)
    if found is None:
        return (
            UNREADABLE,
            None,
            [
                "Mouser's answer is not shaped like a part search "
                f"({'.'.join(ANSWER_PATH)} is missing, or holds something else)"
            ],
        )
    matches = choose(found, line.mpn, line.mfr)
    if not matches:
        if any(_text(item.get(F_MPN)) is None for item in found):
            return (
                UNREADABLE,
                None,
                [f"a part in Mouser's answer has no {F_MPN}: it cannot be matched"],
            )
        return NOT_FOUND, None, []
    if len(matches) > 1:
        names = ", ".join(_text(item.get(F_MOUSER_PN)) or "?" for item in matches)
        return AMBIGUOUS, None, [f"{len(matches)} Mouser parts match: {names}"]
    maker = _text(matches[0].get(F_MAKER))
    if line.mfr.strip() and not same_maker(line.mfr, maker):
        return (
            AMBIGUOUS,
            None,
            [
                f"the only Mouser part is by {maker or 'an unknown maker'}, "
                f"not {line.mfr.strip()}: it may be another part"
            ],
        )
    return None, matches[0], []


def _resolve(line: BomLine, answers: Mapping[str, Answer]) -> _Resolved:
    """Match ``line`` to a Mouser part, or to the status that says why not."""
    problem = asked_number_problem(line)
    if problem is not None:
        shown = f"{line.mpn!r} " if line.mpn else ""
        return _Resolved(line, None, NO_MPN, None, (f"{shown}{problem}",))
    answer = answers.get(line.mpn)
    if answer is None or answer.entry is None:
        notes = list(answer.notes) if answer else []
        notes = notes or ["Mouser was not asked and nothing is cached for this part"]
        return _Resolved(line, answer, NOT_ASKED, None, tuple(notes))
    status, part, more = _match_line(line, answer.entry)
    return _Resolved(line, answer, status, part, tuple(answer.notes) + tuple(more))


def part_key(part: Mapping[str, Any], line: BomLine) -> tuple[str, ...]:
    """Return what makes ``part`` one Mouser part: its number, else number and maker."""
    mouser_number = squash(_text(part.get(F_MOUSER_PN)) or "")
    if mouser_number:
        return ("mouser", mouser_number)
    return (
        "mpn",
        squash(_text(part.get(F_MPN)) or line.mpn),
        _maker_key(_text(part.get(F_MAKER)) or ""),
    )


def _finish(
    found: _Resolved,
    boards: int,
    needed_total: int | None,
    now: datetime,
    max_age: timedelta | None,
    offline: bool,
) -> dict[str, Any]:
    """Return the row of a resolved line: judged, and stale if its answer is too old."""
    line, answer = found.line, found.answer
    if answer is None or answer.entry is None:
        status = found.status or NOT_ASKED
        return _row(line, boards, needed_total, status, None, now, None, found.notes)
    entry = answer.entry
    status, judged, notes = found.status, None, list(found.notes)
    if found.part is not None:
        judged = judge(found.part, needed_total or 0)
        status = judged.status
        notes += judged.notes
    status = status or UNREADABLE
    if is_stale(entry, now, max_age, offline):
        hours = entry.age(now).total_seconds() / 3600
        if hours < 0:
            reason = f"the answer is dated {-hours:.1f} hours in the future"
        else:
            reason = (
                f"the answer is {hours:.1f} hours old, older than "
                f"{(max_age or timedelta(0)).total_seconds() / 3600:g} hours"
            )
        notes.append(f"{reason}: it was judged {status} but may no longer be true")
        status = STALE
    return _row(line, boards, needed_total, status, entry, now, judged, notes)


def build_row(
    line: BomLine,
    boards: int,
    answers: Mapping[str, Answer],
    now: datetime,
    *,
    needed_total: int | None = None,
    max_age: timedelta | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Return the row for ``line``: its status and what Mouser said.

    ``answers`` maps a part number, as written in the line, to what was learned about
    it; a line whose number cannot be asked about has none. ``needed_total`` is the
    count of the part over every BOM line that uses it (by default this line's own; a
    ``no-mpn`` line has none). An answer that is not ``offline`` and older than
    ``max_age`` makes the status ``stale``, whatever it said, with what it said in the
    notes.
    """
    found = _resolve(line, answers)
    if found.status == NO_MPN:
        needed_total = None
    elif needed_total is None:
        needed_total = line.qty * boards
    return _finish(found, boards, needed_total, now, max_age, offline)


def build_rows(
    lines: Sequence[BomLine],
    boards: int,
    answers: Mapping[str, Answer],
    now: datetime,
    *,
    max_age: timedelta | None = None,
    offline: bool = False,
) -> list[dict[str, Any]]:
    """Return one row for each BOM line, in the BOM's order.

    Every line is matched to its Mouser part first; the count is then summed over the
    lines that matched the same part (by its Mouser number, or its number and maker),
    however each wrote the number, and the part is judged on that sum. A line that
    matched no part gets the sum over the lines with the same squashed number, and a
    ``no-mpn`` line has no total (null).
    """
    resolved = [_resolve(line, answers) for line in lines]
    by_part: dict[tuple[str, ...], int] = {}
    by_number: dict[str, int] = {}
    for found in resolved:
        need = found.line.qty * boards
        if found.part is not None:
            key = part_key(found.part, found.line)
            by_part[key] = by_part.get(key, 0) + need
        elif found.status != NO_MPN:
            number = squash(found.line.mpn)
            by_number[number] = by_number.get(number, 0) + need
    rows = []
    for found in resolved:
        total: int | None
        if found.part is not None:
            total = by_part[part_key(found.part, found.line)]
        elif found.status == NO_MPN:
            total = None
        else:
            total = by_number[squash(found.line.mpn)]
        rows.append(_finish(found, boards, total, now, max_age, offline))
    return rows


def exit_code(rows: Sequence[Mapping[str, Any]]) -> int:
    """Return 2 if any line is not-asked or stale, else 1 if any is not ok, else 0."""
    statuses = {row["status"] for row in rows}
    if statuses & {NOT_ASKED, STALE}:
        return EXIT_NOT_ASKED
    return EXIT_NOT_OK if statuses - {OK} else 0


# --- asking --------------------------------------------------------------------------


def batches(
    numbers: Sequence[str], size: int = mouser.MAX_PART_NUMBERS
) -> list[list[str]]:
    """Return ``numbers`` without repeats, in order, in lists of at most ``size``."""
    unique = list(dict.fromkeys(numbers))
    return [unique[start : start + size] for start in range(0, len(unique), size)]


def _scrub(text: str) -> str:
    """Return ``text`` with the API key, if set, hidden in any case or encoding."""
    key = os.environ.get(mouser.KEY_VARIABLE, "").strip()
    if not key:
        return text
    for spelling in {key, quote(key, safe=""), quote(key), quote_plus(key)}:
        text = re.sub(re.escape(spelling), "***", text, flags=re.IGNORECASE)
    return text


def _reason(err: Exception) -> str:
    """Return why a request failed, without the key; only the type for a stray error."""
    if isinstance(err, (mouser.MouserError, OSError)):
        return _scrub(str(err)).strip().rstrip(".") or type(err).__name__
    return type(err).__name__


def _usable(
    known: cache.Entry | None, max_age: timedelta | None, now: datetime
) -> bool:
    """Say whether the cached ``known`` answer needs no new request.

    It is at most ``max_age`` old and not dated in the future (that is no age at all).
    """
    if known is None:
        return False
    age = known.age(now)
    return timedelta(0) <= age and (max_age is None or age <= max_age)


def _ask(query: mouser.Query, now: datetime) -> tuple[cache.Entry, tuple[str, ...]]:
    """Ask Mouser ``query``, cache the answer, and return it with its notes.

    If the answer cannot be cached it is still used, with a note. Raise what
    ``mouser.send`` raises when Mouser cannot be asked.
    """
    answer = mouser.send(mouser.key_from_env(), query)
    try:
        return cache.write(parts.mouser_folder(), query.cache_key, answer, now), ()
    except OSError as err:
        note = f"the fresh answer could not be cached ({type(err).__name__})"
        return cache.Entry(answer, now), (note,)


def fetch_answers(
    numbers: Sequence[str],
    *,
    max_age: timedelta | None,
    offline: bool,
    now: datetime,
) -> dict[str, Answer]:
    """Return Mouser's answer for each of ``numbers``, ten to a request.

    An answer younger than ``max_age`` comes from the cache; an older one is asked for
    again, and requests that are really sent are ``GAP_SECONDS`` apart. ``offline``
    asks nothing and takes what the cache holds at any age. When Mouser cannot be
    asked (no key, no answer), the rest are served from the cache at any age, with a
    note, and a number the cache has nothing for gets an answer with no entry. Whether
    such an answer is too old is for ``build_row`` to say (status ``stale``).
    """
    answers: dict[str, Answer] = {}
    online = not offline
    sent = False
    reason = ""
    for batch in batches(numbers):
        query = mouser.part_number(batch, exact=True)
        known = cache.read(parts.mouser_folder(), query.cache_key)
        entry, notes = known, ()
        if online and not _usable(known, max_age, now):
            if sent:
                _sleep(GAP_SECONDS)
            try:
                entry, notes = _ask(query, now)
                sent = True
            except (mouser.MouserError, OSError, http.client.HTTPException) as err:
                online = False
                reason = _reason(err)
        if not online and not offline:
            if entry is not None and entry is known:
                notes = (
                    f"Mouser could not be asked: {reason}. "
                    "This is the cached answer of any age.",
                )
            else:
                notes = (f"Mouser could not be asked: {reason}. Nothing is cached.",)
        for number in batch:
            answers[number] = Answer(entry, notes)
    return answers
