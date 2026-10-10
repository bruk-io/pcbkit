"""Unit tests for pcbkit.cache, the on-disk store of fetched answers.

The folder is a temporary one and the clock is passed in, so nothing here depends on
when or where the tests run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from pcbkit import cache
from tests.fake_machine import FakeMachine

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


class Source:
    """A value to fetch, counting how often it is fetched."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        return self.value


def test_home_is_xdg_cache_home_or_dot_cache(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Use $XDG_CACHE_HOME/pcbkit when it is set, else ~/.cache/pcbkit."""
    assert cache.home() == machine.home / ".cache" / "pcbkit"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache.home() == tmp_path / "xdg" / "pcbkit"


def test_fetch_gets_on_a_miss_and_keeps_the_answer(tmp_path: Path) -> None:
    """Fetch and store on a miss; the next fetch within max_age reads the store."""
    source = Source({"parts": [1, 2]})
    first = cache.fetch(tmp_path, "k", source, max_age=DAY, now=T0)
    again = cache.fetch(tmp_path, "k", source, max_age=DAY, now=T0 + DAY)
    assert first == again == cache.Entry({"parts": [1, 2]}, T0)
    assert source.calls == 1


def test_fetch_gets_again_once_the_entry_is_too_old(tmp_path: Path) -> None:
    """Fetch again past max_age, and keep the new answer with the new time."""
    cache.fetch(tmp_path, "k", Source("old"), max_age=DAY, now=T0)
    later = T0 + DAY + timedelta(seconds=1)
    fresh = cache.fetch(tmp_path, "k", Source("new"), max_age=DAY, now=later)
    assert fresh == cache.Entry("new", later)
    assert cache.read(tmp_path, "k") == fresh


def test_a_max_age_of_none_takes_an_entry_of_any_age(tmp_path: Path) -> None:
    """Never fetch again when the reader does not mind the age."""
    cache.fetch(tmp_path, "k", Source("kept"), max_age=None, now=T0)
    source = Source("unused")
    entry = cache.fetch(tmp_path, "k", source, max_age=None, now=T0 + 1000 * DAY)
    assert entry.value == "kept" and source.calls == 0


def test_the_reader_decides_the_age_not_the_writer(tmp_path: Path) -> None:
    """Serve one entry to a lenient reader and fetch again for a strict one."""
    cache.fetch(tmp_path, "k", Source("v1"), max_age=None, now=T0)
    lenient = cache.fetch(tmp_path, "k", Source("v2"), max_age=7 * DAY, now=T0 + DAY)
    strict = cache.fetch(
        tmp_path, "k", Source("v2"), max_age=timedelta(hours=1), now=T0 + DAY
    )
    assert (lenient.value, strict.value) == ("v1", "v2")


def test_a_failed_fetch_raises_and_keeps_the_old_entry(tmp_path: Path) -> None:
    """Raise what the fetch raised, and leave the stale entry for read() to give."""
    cache.fetch(tmp_path, "k", Source("stale"), max_age=DAY, now=T0)

    def broken() -> Any:
        raise ConnectionError("offline")

    with pytest.raises(ConnectionError):
        cache.fetch(tmp_path, "k", broken, max_age=DAY, now=T0 + 2 * DAY)
    assert cache.read(tmp_path, "k") == cache.Entry("stale", T0)


def test_keys_that_differ_only_in_case_or_by_a_slash_stay_apart(tmp_path: Path) -> None:
    """Keep keys apart even on a disk that ignores case, and allow any character."""
    keys = ["ina226aidgsr", "INA226AIDGSR", "A/B C|D", "A_B C|D"]
    for key in keys:
        cache.write(tmp_path, key, key, T0)
    assert [cache.read(tmp_path, key).value for key in keys] == keys  # type: ignore[union-attr]
    assert len(list(tmp_path.glob("*.json"))) == len(keys)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        '{"format": 1, "key": "k"}',
        '{"format": 99, "key": "k", "fetched": "2026-10-09T12:00:00Z", "value": 1}',
        '{"format": 1, "key": "j", "fetched": "2026-10-09T12:00:00+00:00", "value": 1}',
        '{"format": 1, "key": "k", "fetched": "2026-10-09T12:00:00", "value": 1}',
        '{"format": 1, "key": "k", "fetched": "yesterday", "value": 1}',
    ],
)
def test_a_damaged_or_foreign_file_is_a_miss(tmp_path: Path, content: str) -> None:
    """Read a file that is not this key's entry as nothing, and fetch over it."""
    cache.write(tmp_path, "k", "good", T0)
    (path,) = tmp_path.glob("*.json")
    path.write_text(content)
    assert cache.read(tmp_path, "k") is None
    assert cache.fetch(tmp_path, "k", Source("again"), max_age=DAY, now=T0).value == (
        "again"
    )


def test_an_entry_is_plain_json_with_its_key_and_utc_time(tmp_path: Path) -> None:
    """Store the format, the key, the UTC time with its offset, and the value."""
    eastern = timezone(timedelta(hours=-4))
    cache.write(tmp_path, "k", {"a": 1}, T0.astimezone(eastern))
    (path,) = tmp_path.glob("*.json")
    assert json.loads(path.read_text()) == {
        "format": 1,
        "key": "k",
        "fetched": "2026-10-09T12:00:00+00:00",
        "value": {"a": 1},
    }
    assert cache.read(tmp_path, "k") == cache.Entry({"a": 1}, T0)


def test_write_refuses_a_time_with_no_zone_and_a_value_that_is_not_json(
    tmp_path: Path,
) -> None:
    """Refuse a naive time, and a value JSON cannot hold, leaving no file behind."""
    folder = tmp_path / "cache"
    with pytest.raises(ValueError, match="time zone"):
        cache.write(folder, "k", 1, datetime(2026, 10, 9))
    with pytest.raises(TypeError):
        cache.write(folder, "k", {1, 2}, T0)
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_failed_write_leaves_neither_a_temporary_file_nor_a_half_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove the temporary file when the move into place fails."""
    folder = tmp_path / "cache"
    cache.write(folder, "k", "first", T0)

    def broken_replace(source: Any, target: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        cache.write(folder, "k", "second", T0 + DAY)
    assert [path.suffix for path in folder.iterdir()] == [".json"]
    assert cache.read(folder, "k") == cache.Entry("first", T0)


def test_fetch_makes_the_folder(tmp_path: Path) -> None:
    """Create the cache folder, and any above it, on the first write."""
    folder = tmp_path / "a" / "b"
    cache.fetch(folder, "k", Source(1), max_age=DAY, now=T0)
    assert folder.is_dir()


def test_age_is_measured_from_the_fetch() -> None:
    """Give the time since the entry was fetched."""
    assert cache.Entry(None, T0).age(T0 + DAY) == DAY
