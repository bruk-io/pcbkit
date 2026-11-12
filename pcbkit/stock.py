"""`pcbkit parts stock`: is each BOM line in stock, and at what price.

The functions from a Mouser answer to a row are pure: they take the answer as it came
(a decoded dict, whatever its age) and the time, and touch no network and no disk.
``fetch_answers`` is the one function that asks, through ``pcbkit.parts``, ten part
numbers to a request and spaced apart, like ``pcbkit datasheet find``. The rows and the
exit code are what ``pcbkit.cli`` prints.

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

import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pcbkit import cache, mouser, parts
from pcbkit.datasheets import maker_names, squash
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

# A lifecycle text that means: do not design this part in, or it cannot be had.
NOT_ACTIVE = re.compile(
    r"obsolete|end[ -]of[ -]life|\beol\b|not recommended|\bnrnd\b|discontinued"
    r"|last time buy",
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
# Not in the plan's list: Mouser answered, but not in a shape that can be judged.
UNREADABLE = "unreadable"
STATUSES = (
    OK,
    SHORT,
    NOT_ACTIVE_STATUS,
    NOT_FOUND,
    AMBIGUOUS,
    NO_MPN,
    NOT_ASKED,
    UNREADABLE,
)

EXIT_NOT_OK = 1
EXIT_NOT_ASKED = 2
DEFAULT_MAX_AGE_HOURS = parts.STOCK_MAX_AGE.total_seconds() / 3600
GAP_SECONDS = 3.0  # between two requests that are really sent, as datasheet find does

_PRICE = re.compile(r"^\d+(\.\d+)?$|^\d{1,3}(,\d{3})+(\.\d+)?$")


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
    """Return a whole number from a number or from text like "1,234", else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == int(value) else None
    if isinstance(value, str) and re.fullmatch(r"\s*\d[\d,]*\s*", value):
        return int(value.replace(",", ""))
    return None


def parse_price(text: Any) -> float | None:
    """Return the number in a price text such as "$3.96", or None if unsure.

    Mouser's price is text with a currency sign and a dot decimal. A text with a comma
    as its decimal mark ("0,123 EUR") or any other shape is None, never a guess, and
    never 0.
    """
    if not isinstance(text, str):
        return None
    digits = re.sub(r"[^0-9.,]", "", text)
    return float(digits.replace(",", "")) if _PRICE.match(digits) else None


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


def unit_price_at(breaks: Sequence[Mapping[str, Any]], needed: int) -> float | None:
    """Return the unit price for buying ``needed`` parts in all, or None.

    That is the price of the highest break whose quantity is at most ``needed``, and
    of the lowest break when ``needed`` is below every one (Mouser sells no fewer).
    ``needed`` is the total over every board, not the count on one board.
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


# --- choosing the part ---------------------------------------------------------------


def answer_parts(answer: Any) -> list[Mapping[str, Any]] | None:
    """Return the parts in Mouser's ``answer``, or None if it is not shaped like one.

    A search that found nothing has an empty list; an answer with no list at all is
    something else (a renamed field, a changed API) and is None.
    """
    found = answer
    for name in ANSWER_PATH:
        found = found.get(name) if isinstance(found, Mapping) else None
    if not isinstance(found, list):
        return None
    return [item for item in found if isinstance(item, Mapping)]


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


def choose(
    found: Sequence[Mapping[str, Any]], mpn: str, maker: str
) -> list[Mapping[str, Any]]:
    """Return the parts whose number is ``mpn``, letters and digits only.

    Mouser's own number counts too. If that leaves more than one and ``maker`` is
    known, the parts of that maker alone, when there are any.
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
        names = [squash(name) for name in maker_names(maker)] or [squash(maker)]
        narrowed = [
            item
            for item in same
            if any(
                name and squash(_text(item.get(F_MAKER)) or "").startswith(name)
                for name in names
            )
        ]
        if narrowed:
            return narrowed
    return same


def mouser_block(
    part: Mapping[str, Any], needed: int
) -> tuple[dict[str, Any], list[str]]:
    """Return the ``mouser`` object of a row for ``part``, and the notes about it."""
    notes = []
    stock, stock_note = stock_of(part)
    if stock_note:
        notes.append(stock_note)
    if stock is None:
        notes.append(f"Mouser's answer has no usable {F_STOCK}: stock is unknown")
    breaks = price_breaks(part)
    if any(
        item["price"] is not None and item["price_value"] is None for item in breaks
    ):
        notes.append("a price break could not be read as a number: it is null")
    minimum, multiple = _whole(part.get(F_MIN)), _whole(part.get(F_MULT))
    if minimum is not None and needed < minimum:
        notes.append(f"Mouser sells at least {minimum}, and {needed} are needed")
    if multiple is not None and multiple > 1 and needed % multiple:
        notes.append(f"Mouser sells in multiples of {multiple}; {needed} is not one")
    block = {
        "part_number": _text(part.get(F_MOUSER_PN)),
        "manufacturer": _text(part.get(F_MAKER)),
        "stock": stock,
        "availability": _text(part.get(F_AVAILABILITY)),
        "lifecycle": _text(part.get(F_LIFECYCLE)),
        "lead_time": _text(part.get(F_LEAD_TIME)),
        "price_breaks": breaks,
        "unit_price_at_needed": unit_price_at(breaks, needed),
        "min": minimum,
        "mult": multiple,
        "suggested_replacement": _text(part.get(F_REPLACEMENT)),
    }
    return block, notes


# --- rows ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """What was learned about one part number: Mouser's answer, and what went wrong."""

    entry: cache.Entry | None
    notes: tuple[str, ...] = ()


def _row(
    line: BomLine,
    boards: int,
    status: str,
    entry: cache.Entry | None,
    now: datetime,
    block: dict[str, Any] | None,
    notes: Sequence[str],
) -> dict[str, Any]:
    """Return one JSON row, in the order the plan lists its fields."""
    return {
        "item": line.item,
        "refs": list(line.refs),
        "mpn": line.mpn,
        "mfr": line.mfr,
        "qty_per_board": line.qty,
        "needed": line.qty * boards,
        "status": status,
        "fetched": entry.fetched.isoformat() if entry else None,
        "age_hours": (
            round(max(0.0, entry.age(now).total_seconds()) / 3600, 2) if entry else None
        ),
        "mouser": block,
        "notes": list(notes),
    }


def build_row(
    line: BomLine, boards: int, answers: Mapping[str, Answer], now: datetime
) -> dict[str, Any]:
    """Return the row for ``line``: its status and what Mouser said.

    ``answers`` maps a part number, as written in the line, to what was learned about
    it; a line whose number cannot be asked about has none.
    """
    needed = line.qty * boards
    problem = asked_number_problem(line)
    if problem is not None:
        shown = f"{line.mpn!r} " if line.mpn else ""
        return _row(line, boards, NO_MPN, None, now, None, [f"{shown}{problem}"])
    answer = answers.get(line.mpn)
    if answer is None or answer.entry is None:
        notes = list(answer.notes) if answer else []
        notes = notes or ["Mouser was not asked and nothing is cached for this part"]
        return _row(line, boards, NOT_ASKED, None, now, None, notes)
    entry, notes = answer.entry, list(answer.notes)
    found = answer_parts(entry.value)
    if found is None:
        notes.append(
            "Mouser's answer is not shaped like a part search "
            f"({'.'.join(ANSWER_PATH)} is missing)"
        )
        return _row(line, boards, UNREADABLE, entry, now, None, notes)
    matches = choose(found, line.mpn, line.mfr)
    if not matches:
        return _row(line, boards, NOT_FOUND, entry, now, None, notes)
    if len(matches) > 1:
        notes.append(
            f"{len(matches)} Mouser parts match: "
            + ", ".join(_text(item.get(F_MOUSER_PN)) or "?" for item in matches)
        )
        return _row(line, boards, AMBIGUOUS, entry, now, None, notes)
    block, more = mouser_block(matches[0], needed)
    notes += more
    lifecycle = block["lifecycle"]
    if lifecycle is not None and NOT_ACTIVE.search(lifecycle):
        status = NOT_ACTIVE_STATUS
    elif block["stock"] is None:
        status = UNREADABLE
    elif block["stock"] < needed:
        status = SHORT
    else:
        status = OK
    return _row(line, boards, status, entry, now, block, notes)


def build_rows(
    lines: Sequence[BomLine], boards: int, answers: Mapping[str, Answer], now: datetime
) -> list[dict[str, Any]]:
    """Return one row for each BOM line, in the BOM's order."""
    return [build_row(line, boards, answers, now) for line in lines]


def exit_code(rows: Sequence[Mapping[str, Any]]) -> int:
    """Return 2 if any line was not asked, else 1 if any is not ok, else 0."""
    statuses = {row["status"] for row in rows}
    if NOT_ASKED in statuses:
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
    """Return ``text`` with the API key, if one is set, hidden."""
    key = os.environ.get(mouser.KEY_VARIABLE, "").strip()
    return text.replace(key, "***") if key else text


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
    note, and a number the cache has nothing for gets an answer with no entry.
    """
    answers: dict[str, Answer] = {}
    online = not offline
    sent = 0
    reason = ""
    for batch in batches(numbers):
        query = mouser.part_number(batch, exact=True)
        known = cache.read(parts.mouser_folder(), query.cache_key)
        entry, notes = known, ()
        if online:
            fresh = known is not None and (max_age is None or known.age(now) <= max_age)
            if not fresh and sent:
                _sleep(GAP_SECONDS)
            try:
                entry = parts.ask_mouser(query, max_age=max_age, now=now)
                sent += not fresh
            except (mouser.MouserError, OSError) as err:
                online = False
                reason = _scrub(str(err))
        if not online and not offline and entry is not None and entry is known:
            notes = (
                f"Mouser could not be asked ({reason}): this is the cached answer "
                "of any age",
            )
        elif not online and not offline:
            notes = (f"Mouser could not be asked ({reason}) and nothing is cached",)
        for number in batch:
            answers[number] = Answer(entry, notes)
    return answers
