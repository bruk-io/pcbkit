"""Part lookups for pcbkit: Mouser's answers, through pcbkit's cache.

``pcbkit.mouser`` is a plain client and ``pcbkit.cache`` a plain store; this is where
pcbkit decides how they meet. Answers are kept under ``<cache>/mouser``, keyed by the
request (never the API key), with Mouser's answer exactly as it came.

The API key is read only when the cache has nothing fresh enough, so a lookup that was
made before works offline and on a machine with no key, such as CI.

How fresh is fresh enough is the caller's choice: stock, prices and lead times go stale
in a day (``STOCK_MAX_AGE``), while a manufacturer or a datasheet link hardly changes
(``max_age=None`` takes an answer of any age).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from pcbkit import cache, mouser

STOCK_MAX_AGE = timedelta(hours=24)


def mouser_folder() -> Path:
    """Return the cache folder for Mouser's answers."""
    return cache.home() / "mouser"


def ask_mouser(
    query: mouser.Query,
    *,
    max_age: timedelta | None = STOCK_MAX_AGE,
    key: str | None = None,
    folder: Path | None = None,
    now: datetime | None = None,
) -> cache.Entry:
    """Return Mouser's answer to ``query``, from the cache if it is fresh enough.

    ``key`` defaults to MOUSER_API_KEY, read only if Mouser must be asked. Raise
    MouserError if Mouser must be asked and cannot answer, or there is no key.
    """
    return cache.fetch(
        folder or mouser_folder(),
        query.cache_key,
        lambda: mouser.send(key or mouser.key_from_env(), query),
        max_age=max_age,
        now=now,
    )
