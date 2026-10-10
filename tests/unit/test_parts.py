"""Unit tests for pcbkit.parts: Mouser's answers through pcbkit's cache.

Mouser is a fake (`mouser._transport` is replaced), and HOME is the fake machine's, so
the cache is a temporary folder.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pcbkit import mouser, parts
from pcbkit.mouser import MouserError
from tests.fake_machine import FakeMachine

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mouser"
INA226 = (FIXTURES / "partnumber_ina226.json").read_bytes()
KEY = "0000-test-key-1234"
T0 = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer every request to Mouser with the INA226 search; list the URLs asked."""
    urls: list[str] = []

    def transport(
        method: str, url: str, body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        urls.append(url)
        return 200, INA226

    monkeypatch.setattr(mouser, "_transport", transport)
    return urls


def test_the_first_lookup_asks_mouser_and_the_next_reads_the_cache(
    asked: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ask Mouser once; the same query within the day comes from the cache."""
    monkeypatch.setenv("MOUSER_API_KEY", KEY)
    query = mouser.part_number("INA226AIDGSR", exact=True)
    first = parts.ask_mouser(query, now=T0)
    again = parts.ask_mouser(query, now=T0 + timedelta(hours=23))
    assert first == again
    assert first.value == json.loads(INA226)
    assert len(asked) == 1


def test_stock_answers_are_asked_again_after_a_day(
    asked: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ask Mouser again once the cached answer is older than STOCK_MAX_AGE."""
    monkeypatch.setenv("MOUSER_API_KEY", KEY)
    query = mouser.part_number("INA226AIDGSR", exact=True)
    parts.ask_mouser(query, now=T0)
    later = parts.ask_mouser(query, now=T0 + parts.STOCK_MAX_AGE + timedelta(minutes=1))
    assert len(asked) == 2
    assert later.fetched == T0 + parts.STOCK_MAX_AGE + timedelta(minutes=1)


def test_a_cached_answer_needs_no_key(
    asked: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answer from the cache on a machine with no key, such as CI."""
    query = mouser.part_number("INA226AIDGSR", exact=True)
    parts.ask_mouser(query, key=KEY, now=T0)
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    entry = parts.ask_mouser(query, max_age=None, now=T0 + timedelta(days=400))
    assert entry.fetched == T0 and len(asked) == 1


def test_a_miss_with_no_key_says_how_to_set_one(asked: list[str]) -> None:
    """Say MOUSER_API_KEY is not set when Mouser must be asked and there is no key."""
    with pytest.raises(MouserError, match="MOUSER_API_KEY is not set"):
        parts.ask_mouser(mouser.part_number("INA226AIDGSR"), now=T0)
    assert asked == []


def test_answers_live_in_the_mouser_cache_folder_without_the_key(
    asked: list[str], machine: FakeMachine
) -> None:
    """Keep answers under ~/.cache/pcbkit/mouser, and never write the key there."""
    parts.ask_mouser(mouser.part_number("INA226AIDGSR"), key=KEY, now=T0)
    folder = machine.home / ".cache" / "pcbkit" / "mouser"
    assert parts.mouser_folder() == folder
    (stored,) = folder.glob("*.json")
    assert KEY not in stored.read_text()


def test_different_queries_are_cached_apart(
    asked: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep an exact and a loose search for the same number apart."""
    monkeypatch.setenv("MOUSER_API_KEY", KEY)
    parts.ask_mouser(mouser.part_number("INA226AIDGSR", exact=True), now=T0)
    parts.ask_mouser(mouser.part_number("INA226AIDGSR"), now=T0)
    assert len(asked) == 2
