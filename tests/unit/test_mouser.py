"""Unit tests for pcbkit.mouser, the Search API client.

Mouser is never reached: `mouser._transport` (one HTTP request) and `mouser._sleep` are
replaced, and tests/unit/conftest.py fails any test that reaches for the real one. The
answers are Mouser's own, from tests/fixtures/mouser/ (a real search, 2026-10-09), or
made to the shape the Swagger spec gives for errors.
"""

from __future__ import annotations

import ast
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Union
from urllib.parse import parse_qs, urlsplit

import pytest

from pcbkit import mouser
from pcbkit.mouser import MouserError, Query

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mouser"
INA226 = (FIXTURES / "partnumber_ina226.json").read_bytes()
KEY = "0000-test-key-1234"
REFUSED = json.dumps(
    {
        "Errors": [
            {"Id": 0, "Code": "Invalid", "Message": "Invalid unique identifier."}
        ],
        "SearchResults": None,
    }
).encode()
TRANSPORT = mouser._transport
CDN_PAGE = b"<HTML><HEAD><TITLE>Error</TITLE></HEAD><BODY>An error</BODY></HTML>"

Reply = Union[tuple[int, bytes], Exception]


@dataclass
class Server:
    """A stand-in for Mouser: answers each request with the next reply in line."""

    replies: list[Reply]
    requests: list[tuple[str, str, bytes | None, float]] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)

    def transport(
        self, method: str, url: str, body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        """Record the request and give the next reply, or raise it."""
        self.requests.append((method, url, body, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Server]:
    """Return a maker of fake Mouser servers, each installed as it is made."""

    def make(*replies: Reply) -> Server:
        fake = Server(list(replies))
        monkeypatch.setattr(mouser, "_transport", fake.transport)
        monkeypatch.setattr(mouser, "_sleep", fake.waits.append)
        return fake

    return make


def body_of(query: Query) -> dict[str, Any]:
    """Return a query's JSON body as a dict."""
    assert query.body is not None
    return json.loads(query.body)


# --- the queries ---------------------------------------------------------------------


def test_part_number_builds_the_v1_request_the_spec_documents() -> None:
    """Put the part numbers, the search option and the customs flag in the body."""
    query = mouser.part_number("INA226AIDGSR", exact=True)
    assert (query.method, query.path) == ("POST", "v1/search/partnumber")
    assert body_of(query) == {
        "SearchByPartRequest": {
            "mouserPartNumber": "INA226AIDGSR",
            "partSearchOptions": "Exact",
            "mouserPaysCustomsAndDuties": False,
        }
    }


def test_part_number_joins_a_list_with_pipes_and_takes_a_joined_string() -> None:
    """Give the same query for a list and for the same numbers joined by '|'."""
    listed = mouser.part_number(["ABC123", " XYZ789 "])
    joined = mouser.part_number("ABC123|XYZ789")
    assert listed == joined
    assert body_of(listed)["SearchByPartRequest"]["mouserPartNumber"] == "ABC123|XYZ789"


@pytest.mark.parametrize(
    "numbers, says",
    [
        ("", "no part number"),
        ([], "no part number"),
        (["AB"], "3 to 40 characters"),
        (["A" * 41], "3 to 40 characters"),
        (["ABC|DEF"], "no '|'"),
        ([f"PART{n:02}" for n in range(11)], "at most 10"),
    ],
)
def test_part_number_refuses_what_the_spec_does_not_allow(
    numbers: Any, says: str
) -> None:
    """Refuse an empty list, too many numbers, and a number of the wrong length."""
    with pytest.raises(MouserError, match=says):
        mouser.part_number(numbers)


def test_part_number_takes_exactly_ten_and_forty_characters() -> None:
    """Accept the spec's limits themselves."""
    mouser.part_number([f"PART{n:02}" for n in range(10)])
    mouser.part_number(["A" * 40, "ABC"])


def test_the_manufacturer_searches_use_v2_and_the_name() -> None:
    """Send the manufacturer by name to the v2 endpoints, as the spec now asks."""
    by_part = mouser.part_number_and_manufacturer("INA226AIDGSR", "Texas Instruments")
    assert by_part.path == "v2/search/partnumberandmanufacturer"
    assert body_of(by_part)["SearchByPartMfrNameRequest"]["manufacturerName"] == (
        "Texas Instruments"
    )
    by_word = mouser.keyword_and_manufacturer("current monitor", "Texas Instruments")
    assert by_word.path == "v2/search/keywordandmanufacturer"
    assert body_of(by_word)["SearchByKeywordMfrNameRequest"]["pageNumber"] == 1
    assert mouser.manufacturer_list() == Query("GET", "v2/search/manufacturerlist")


def test_keyword_builds_the_v1_request_with_paging() -> None:
    """Put the words, the page of results and the option in the body."""
    query = mouser.keyword(" current monitor ", records=20, start=40, option="InStock")
    assert query.path == "v1/search/keyword"
    assert body_of(query)["SearchByKeywordRequest"] == {
        "keyword": "current monitor",
        "records": 20,
        "startingRecord": 40,
        "searchOptions": "InStock",
        "mouserPaysCustomsAndDuties": False,
    }


@pytest.mark.parametrize(
    "call, says",
    [
        (lambda: mouser.keyword("  "), "no keyword"),
        (lambda: mouser.keyword("led", records=0), "records must be 1 to 50"),
        (lambda: mouser.keyword("led", records=51), "records must be 1 to 50"),
        (lambda: mouser.keyword("led", start=-1), "start must be 0 or more"),
        (lambda: mouser.keyword("led", option="Cheap"), "search option 'Cheap'"),
        (
            lambda: mouser.keyword_and_manufacturer("led", "Acme", page=0),
            "page must be 1 or more",
        ),
    ],
)
def test_keyword_searches_refuse_what_the_spec_does_not_allow(
    call: Callable[[], Query], says: str
) -> None:
    """Refuse empty words, records out of 1 to 50, a bad start, page or option."""
    with pytest.raises(MouserError, match=says):
        call()


def test_equal_queries_share_a_cache_key_and_different_ones_do_not() -> None:
    """Name a request by its method, path and body, so equal requests match."""
    assert (
        mouser.part_number("INA226AIDGSR").cache_key
        == mouser.part_number(["INA226AIDGSR"]).cache_key
    )
    assert (
        mouser.part_number("INA226AIDGSR").cache_key
        != mouser.part_number("INA226AIDGSR", exact=True).cache_key
    )


# --- sending -------------------------------------------------------------------------


def test_send_posts_the_body_with_the_key_in_the_query_and_returns_the_answer(
    server: Callable[..., Server],
) -> None:
    """Send to the documented URL and return Mouser's answer exactly as it came."""
    fake = server((200, INA226))
    query = mouser.part_number("INA226AIDGSR", exact=True)
    answer = mouser.send(KEY, query)
    assert answer == json.loads(INA226)
    assert answer["SearchResults"]["Parts"][0]["ManufacturerPartNumber"] == (
        "INA226AIDGSR"
    )
    (method, url, body, timeout) = fake.requests[0]
    parts = urlsplit(url)
    assert (method, parts.netloc, parts.path) == (
        "POST",
        "api.mouser.com",
        "/api/v1/search/partnumber",
    )
    assert parse_qs(parts.query) == {"apiKey": [KEY]}
    assert body == json.dumps(body_of(query), sort_keys=True).encode()
    assert timeout == mouser.TIMEOUT_SECONDS


def test_send_sends_a_get_with_no_body(server: Callable[..., Server]) -> None:
    """Send the manufacturer list as a GET with no body."""
    fake = server((200, b'{"Errors": [], "MouserManufacturerList": {}}'))
    mouser.send(KEY, mouser.manufacturer_list())
    assert fake.requests[0][0] == "GET" and fake.requests[0][2] is None


def test_send_raises_mousers_own_error_and_does_not_try_again(
    server: Callable[..., Server],
) -> None:
    """Give Mouser's error code and message, once: a refused key will not improve."""
    fake = server((200, REFUSED))
    with pytest.raises(MouserError, match="Invalid: Invalid unique identifier"):
        mouser.send(KEY, mouser.part_number("INA226AIDGSR"))
    assert len(fake.requests) == 1


def test_send_reads_mousers_error_from_a_failed_status_too(
    server: Callable[..., Server],
) -> None:
    """Prefer Mouser's message over the bare status when a 4xx carries one."""
    server((400, REFUSED))
    with pytest.raises(MouserError, match="Invalid unique identifier"):
        mouser.send(KEY, mouser.part_number("INA226AIDGSR"))


def test_send_tries_again_after_a_cdn_page_and_then_succeeds(
    server: Callable[..., Server],
) -> None:
    """Take an HTML error page (seen from Mouser's CDN) as a failure that may pass."""
    fake = server((200, CDN_PAGE), (503, b""), (200, INA226))
    assert mouser.send(KEY, mouser.part_number("INA226AIDGSR"))["Errors"] == []
    assert len(fake.requests) == 3
    assert fake.waits == list(mouser.BACKOFF_SECONDS)


def test_send_gives_up_after_the_retries_and_says_what_came_back(
    server: Callable[..., Server],
) -> None:
    """Stop after one try and two retries, naming the last failure."""
    fake = server((200, CDN_PAGE), (200, CDN_PAGE), (429, b"{}"))
    with pytest.raises(MouserError, match="429 and no error message, after 3 tries"):
        mouser.send(KEY, mouser.part_number("INA226AIDGSR"))
    assert len(fake.requests) == 3


def test_send_tries_again_when_no_answer_comes(server: Callable[..., Server]) -> None:
    """Take a dropped connection or a timeout as a failure that may pass."""
    fake = server(
        urllib.error.URLError(ConnectionResetError()), TimeoutError(), (200, INA226)
    )
    mouser.send(KEY, mouser.part_number("INA226AIDGSR"))
    assert len(fake.requests) == 3


def test_send_does_not_try_again_after_a_redirect_or_a_plain_4xx(
    server: Callable[..., Server],
) -> None:
    """Fail at once on a redirect or a client error with no message from Mouser."""
    server((302, b""))
    with pytest.raises(MouserError, match="302 redirect"):
        mouser.send(KEY, mouser.part_number("INA226AIDGSR"))
    fake = server((404, b"{}"))
    with pytest.raises(MouserError, match="with 404"):
        mouser.send(KEY, mouser.part_number("INA226AIDGSR"))
    assert len(fake.requests) == 1


def test_send_never_puts_the_key_in_an_error(server: Callable[..., Server]) -> None:
    """Keep the key, which rides in the URL, out of every message."""
    replies: list[Reply] = [
        (200, REFUSED),
        (302, b""),
        (404, b"{}"),
        urllib.error.URLError(f"cannot reach https://api.mouser.com/?apiKey={KEY}"),
    ]
    for reply in replies:
        server(reply, reply, reply)
        with pytest.raises(MouserError) as caught:
            mouser.send(KEY, mouser.part_number("INA226AIDGSR"))
        assert KEY not in str(caught.value)


def test_send_refuses_an_empty_key(server: Callable[..., Server]) -> None:
    """Say there is no key rather than ask Mouser without one."""
    fake = server()
    with pytest.raises(MouserError, match="no API key"):
        mouser.send("", mouser.part_number("INA226AIDGSR"))
    assert fake.requests == []


def test_key_from_env_reads_the_variable_and_says_how_to_set_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return MOUSER_API_KEY, and name it in the error when it is not set."""
    with pytest.raises(MouserError, match="MOUSER_API_KEY is not set"):
        mouser.key_from_env()
    monkeypatch.setenv("MOUSER_API_KEY", f" {KEY}\n")
    assert mouser.key_from_env() == KEY


# --- the transport -------------------------------------------------------------------


def test_the_transport_names_itself_asks_for_json_and_refuses_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send a User-Agent and Accept header, and never follow a redirect.

    Measured: Mouser's CDN answered urllib's default User-Agent with a redirect, and a
    client with none with an HTML page; and a followed redirect turns a POST into a GET.
    """
    seen: dict[str, Any] = {}

    class Answer(BytesIO):
        status = 200

    class Opener:
        def open(self, request: urllib.request.Request, timeout: float) -> Answer:
            seen.update(request=request, timeout=timeout)
            return Answer(b"{}")

    def build_opener(*handlers: Any) -> Opener:
        seen["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(mouser, "build_opener", build_opener)
    monkeypatch.setattr(mouser, "_transport", TRANSPORT)
    assert mouser._transport("POST", "https://api.test/x", b"{}", 7.0) == (200, b"{}")
    request = seen["request"]
    assert request.get_method() == "POST" and request.data == b"{}"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("User-agent") == mouser.USER_AGENT
    assert seen["timeout"] == 7.0
    assert seen["handlers"] == (mouser._NoRedirect,)
    assert (
        mouser._NoRedirect().redirect_request(
            request, BytesIO(), 302, "Found", {}, "https://elsewhere.test"
        )
        is None
    )


# --- the boundary --------------------------------------------------------------------


def test_the_client_knows_nothing_of_pcbkit() -> None:
    """Import nothing from pcbkit: the client stays a plain Mouser client."""
    tree = ast.parse(Path(mouser.__file__).read_text())
    imported = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not [name for name in imported if name.split(".")[0] == "pcbkit"]
