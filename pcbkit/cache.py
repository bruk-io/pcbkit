"""pcbkit's cache: answers fetched from the network, kept on disk with their date.

Everything here can be fetched again, so the folder is safe to delete. It is
``$XDG_CACHE_HOME/pcbkit``, or ``~/.cache/pcbkit`` when that is not set. A cache is a
folder of entries, one JSON file each, named by a hash of the entry's key: a key can be
any text (a part number with a slash, two keys that differ only in case on a Mac's
disk), and the key is kept inside the file and checked on reading.

An entry holds a JSON value and the time it was fetched. How old an entry may be is
the reader's choice, not the writer's: the same answer from a distributor serves a
stock question for a day and a datasheet-link question for good. ``fetch`` returns a
fresh enough entry or fetches a new one; a failed fetch leaves the old entry in place
and raises, and ``read`` still returns the old entry for a caller that would rather
show stale data with its date than nothing.

A file that cannot be read or does not hold an entry for its key is a miss, not an
error: the next fetch writes over it. Writes go to a temporary file first and are
moved into place, so a reader never sees half an entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FORMAT = 1


@dataclass(frozen=True)
class Entry:
    """A cached value and the time, in UTC, it was fetched."""

    value: Any
    fetched: datetime

    def age(self, now: datetime) -> timedelta:
        """Return how long before ``now`` this entry was fetched."""
        return now - self.fetched


def home() -> Path:
    """Return pcbkit's cache folder: $XDG_CACHE_HOME/pcbkit, else ~/.cache/pcbkit."""
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".cache") / "pcbkit"


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


def _path(folder: Path, key: str) -> Path:
    """Return the file that holds ``key``'s entry in ``folder``."""
    return folder / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}.json"


def read(folder: Path, key: str) -> Entry | None:
    """Return the entry for ``key`` in ``folder``, whatever its age, or None."""
    try:
        stored = json.loads(_path(folder, key).read_text(encoding="utf-8"))
        if stored["format"] != FORMAT or stored["key"] != key:
            return None
        fetched = datetime.fromisoformat(stored["fetched"])
        if fetched.tzinfo is None:
            return None
        return Entry(stored["value"], fetched)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write(folder: Path, key: str, value: Any, fetched: datetime) -> Entry:
    """Store ``value`` as the entry for ``key`` in ``folder``; return the entry.

    Raise TypeError or ValueError if ``value`` is not JSON, and OSError if the folder
    cannot be written.
    """
    if fetched.tzinfo is None:
        raise ValueError("fetched must be a time with a time zone")
    text = json.dumps(
        {
            "format": FORMAT,
            "key": key,
            "fetched": fetched.astimezone(timezone.utc).isoformat(),
            "value": value,
        },
        sort_keys=True,
    )
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temporary, _path(folder, key))
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Entry(json.loads(text)["value"], fetched)


def fetch(
    folder: Path,
    key: str,
    get: Callable[[], Any],
    *,
    max_age: timedelta | None,
    now: datetime | None = None,
) -> Entry:
    """Return ``key``'s entry if it is at most ``max_age`` old, else store ``get()``.

    ``max_age`` None takes an entry of any age. ``now`` defaults to the current time.
    Whatever ``get`` raises is raised, and the old entry, if any, stays.
    """
    moment = now or utc_now()
    found = read(folder, key)
    if found is not None and (max_age is None or found.age(moment) <= max_age):
        return found
    return write(folder, key, get(), moment)
