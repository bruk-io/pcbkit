"""A client for Mouser's Search API, and nothing more.

It knows Mouser and nothing of pcbkit (a unit test holds it to that): it builds the
requests the API documents, sends them, and returns the decoded answer as Mouser wrote
it. What to keep, cache or show is the caller's business.

The endpoints, from Mouser's Swagger specs (``https://api.mouser.com/api/docs/v1`` and
``.../v2``, read 2026-10-09):

- v1 ``search/partnumber`` and ``search/keyword``;
- v2 ``search/partnumberandmanufacturer``, ``search/keywordandmanufacturer`` and
  ``search/manufacturerlist`` (their v1 forms are deprecated).

A request is built in two steps. A builder (``part_number``, ``keyword``, ...) checks
its arguments and returns a ``Query``, plain data that names the request and can serve
as a cache key; ``send`` sends it with an API key and returns the answer. Answers are
dicts shaped like the spec's ``SearchResponseRoot``: ``Errors`` and ``SearchResults``.

Measured on 2026-10-09: Mouser's CDN answered urllib's default client with a redirect,
and a client with no User-Agent with an HTML error page. So ``send`` names itself and
asks for JSON, refuses redirects (the API never needs one, and urllib would turn the
POST into a GET), and tries again when the answer is not JSON. A response header
echoes the API key, and the request URL holds it: neither ever goes into an error.

``send`` raises ``MouserError``. The machine is reached through ``_transport`` and
``_sleep`` only, so unit tests replace those two.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Union
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://api.mouser.com/api"
KEY_VARIABLE = "MOUSER_API_KEY"
USER_AGENT = "pcbkit-mouser/1"
TIMEOUT_SECONDS = 30.0
# How many times to try again after a failure that may pass (a dropped connection, a
# server error, a CDN error page), and how long to wait before each try.
RETRIES = 2
BACKOFF_SECONDS = (2.0, 5.0)

# The spec's limits on the part number field.
MAX_PART_NUMBERS = 10
PART_NUMBER_LENGTH = (3, 40)
MAX_RECORDS = 50
KEYWORD_OPTIONS = ("None", "Rohs", "InStock", "RohsAndInStock")

PartNumbers = Union[str, Sequence[str]]


class MouserError(Exception):
    """Say why a request to Mouser failed or was refused before it was sent."""


@dataclass(frozen=True)
class Query:
    """One request to the Search API: method, path below ``API``, and JSON body.

    The body is the JSON text with sorted keys, so two equal queries are equal, and
    ``cache_key`` names the request without the API key.
    """

    method: str
    path: str
    body: str | None = None

    @property
    def cache_key(self) -> str:
        """Return a text that names this request, and is the same for equal ones."""
        return f"{self.method} {self.path} {self.body or ''}"


def key_from_env() -> str:
    """Return the API key from MOUSER_API_KEY; raise MouserError if it is not set."""
    key = os.environ.get(KEY_VARIABLE, "").strip()
    if not key:
        raise MouserError(
            f"{KEY_VARIABLE} is not set: get a Search API key from Mouser's API hub "
            "and export it in your shell profile."
        )
    return key


# --- the queries ---------------------------------------------------------------------


def _body(root: str, fields: dict[str, Any]) -> str:
    """Return the JSON body ``{root: fields}`` with sorted keys."""
    return json.dumps({root: fields}, sort_keys=True)


def _part_numbers(numbers: PartNumbers) -> str:
    """Return ``numbers`` joined by "|"; raise MouserError if the spec forbids them."""
    items = numbers.split("|") if isinstance(numbers, str) else list(numbers)
    items = [item.strip() for item in items]
    if not items or not any(items):
        raise MouserError("no part number given")
    if len(items) > MAX_PART_NUMBERS:
        raise MouserError(
            f"{len(items)} part numbers given; Mouser takes at most {MAX_PART_NUMBERS}"
        )
    low, high = PART_NUMBER_LENGTH
    for item in items:
        if "|" in item or not low <= len(item) <= high:
            raise MouserError(
                f"part number {item!r}: Mouser takes {low} to {high} characters "
                "with no '|'"
            )
    return "|".join(items)


def _records(records: int) -> int:
    """Return ``records``; raise MouserError unless it is 1 to 50."""
    if not 1 <= records <= MAX_RECORDS:
        raise MouserError(f"records must be 1 to {MAX_RECORDS}, not {records}")
    return records


def _keyword_option(option: str) -> str:
    """Return ``option``; raise MouserError unless the keyword search knows it."""
    if option not in KEYWORD_OPTIONS:
        raise MouserError(
            f"search option {option!r}: use one of {', '.join(KEYWORD_OPTIONS)}"
        )
    return option


def part_number(
    numbers: PartNumbers, *, exact: bool = False, customs_paid: bool = False
) -> Query:
    """Build a search by part number: Mouser's or the manufacturer's, up to 10."""
    return Query(
        "POST",
        "v1/search/partnumber",
        _body(
            "SearchByPartRequest",
            {
                "mouserPartNumber": _part_numbers(numbers),
                "partSearchOptions": "Exact" if exact else "None",
                "mouserPaysCustomsAndDuties": customs_paid,
            },
        ),
    )


def part_number_and_manufacturer(
    numbers: PartNumbers,
    manufacturer: str,
    *,
    exact: bool = False,
    customs_paid: bool = False,
) -> Query:
    """Build a search by part number, kept to one manufacturer (by name)."""
    return Query(
        "POST",
        "v2/search/partnumberandmanufacturer",
        _body(
            "SearchByPartMfrNameRequest",
            {
                "manufacturerName": manufacturer,
                "mouserPartNumber": _part_numbers(numbers),
                "partSearchOptions": "Exact" if exact else "None",
                "mouserPaysCustomsAndDuties": customs_paid,
            },
        ),
    )


def keyword(
    words: str,
    *,
    records: int = MAX_RECORDS,
    start: int = 0,
    option: str = "None",
    customs_paid: bool = False,
) -> Query:
    """Build a keyword search: ``records`` results from result number ``start``."""
    if not words.strip():
        raise MouserError("no keyword given")
    if start < 0:
        raise MouserError(f"start must be 0 or more, not {start}")
    return Query(
        "POST",
        "v1/search/keyword",
        _body(
            "SearchByKeywordRequest",
            {
                "keyword": words.strip(),
                "records": _records(records),
                "startingRecord": start,
                "searchOptions": _keyword_option(option),
                "mouserPaysCustomsAndDuties": customs_paid,
            },
        ),
    )


def keyword_and_manufacturer(
    words: str,
    manufacturer: str,
    *,
    records: int = MAX_RECORDS,
    page: int = 1,
    option: str = "None",
    customs_paid: bool = False,
) -> Query:
    """Build a keyword search kept to one manufacturer: page ``page`` of ``records``."""
    if not words.strip():
        raise MouserError("no keyword given")
    if page < 1:
        raise MouserError(f"page must be 1 or more, not {page}")
    return Query(
        "POST",
        "v2/search/keywordandmanufacturer",
        _body(
            "SearchByKeywordMfrNameRequest",
            {
                "manufacturerName": manufacturer,
                "keyword": words.strip(),
                "records": _records(records),
                "pageNumber": page,
                "searchOptions": _keyword_option(option),
                "mouserPaysCustomsAndDuties": customs_paid,
            },
        ),
    )


def manufacturer_list() -> Query:
    """Build the request for the names of every manufacturer Mouser carries."""
    return Query("GET", "v2/search/manufacturerlist")


# --- the machine ---------------------------------------------------------------------


class _NoRedirect(HTTPRedirectHandler):
    """Refuse every redirect: the API never needs one."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Return None, so urllib raises the redirect as an HTTPError."""
        return None


def _transport(
    method: str, url: str, body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    """Send one request; return its status and body. Raise OSError if none came."""
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except HTTPError as err:
        with err:
            return err.code, err.read()


def _sleep(seconds: float) -> None:
    """Wait ``seconds``."""
    time.sleep(seconds)


# --- sending -------------------------------------------------------------------------


def _errors(answer: dict[str, Any]) -> str:
    """Return Mouser's errors in ``answer`` as one line, or "" if there are none."""
    found = answer.get("Errors") or []
    return "; ".join(
        f"{item.get('Code') or 'error'}: {item.get('Message') or ''}".strip()
        if isinstance(item, dict)
        else str(item)
        for item in found
    )


def _decode(data: bytes) -> dict[str, Any] | None:
    """Return the JSON object in ``data``, or None if it is not one."""
    try:
        answer = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return answer if isinstance(answer, dict) else None


def send(
    key: str,
    query: Query,
    *,
    timeout: float = TIMEOUT_SECONDS,
    retries: int = RETRIES,
) -> dict[str, Any]:
    """Send ``query`` with API key ``key``; return Mouser's answer.

    Raise MouserError when Mouser reports an error, when the answer is not JSON after
    ``retries`` more tries, or when no answer comes. Only failures that may pass are
    tried again: no answer, a server error (5xx or 429), or a body that is not JSON.
    """
    if not key:
        raise MouserError("no API key given")
    url = f"{API}/{query.path}?apiKey={quote(key, safe='')}"
    body = query.body.encode("utf-8") if query.body is not None else None
    problem = ""
    for attempt in range(retries + 1):
        if attempt:
            _sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1])
        try:
            status, data = _transport(query.method, url, body, timeout)
        except (URLError, OSError) as err:
            reason = getattr(err, "reason", None) or err
            problem = f"no answer from Mouser ({type(reason).__name__})"
            continue
        answer = _decode(data)
        if answer is not None and _errors(answer):
            raise MouserError(f"Mouser refused {query.path}: {_errors(answer)}")
        if 300 <= status < 400:
            raise MouserError(f"Mouser answered {query.path} with a {status} redirect")
        if status == 429 or status >= 500 or answer is None:
            problem = f"Mouser answered {query.path} with {status} and " + (
                "no JSON" if answer is None else "no error message"
            )
            continue
        if status != 200:
            raise MouserError(f"Mouser answered {query.path} with {status}")
        return answer
    raise MouserError(f"{problem}, after {retries + 1} tries")
