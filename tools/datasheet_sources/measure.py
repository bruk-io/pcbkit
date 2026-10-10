"""Measure how often Mouser's datasheet link gets us the right datasheet.

For each part in parts.csv: ask Mouser (through pcbkit's client and cache, answers of
any age), take the datasheet link of the exact part, download it, and judge it by
rules a script can check:

- the download is a PDF (it starts with ``%PDF``), not a landing page or a block page;
- it is this part's datasheet: the family name from parts.csv appears in the text of
  its first three pages (``pdftotext``), ignoring case, spaces and hyphens.

Mouser is asked in batches of ten part numbers (the most one request takes), at least
``MOUSER_GAP`` seconds apart, and the answers are cached, so 52 parts cost 6 requests
once and none on a second run. Downloads wait ``PAUSE`` seconds before each request,
since most links point at mouser.com too.

Each download is tried first as pcbkit would send it (its own User-Agent), and once
more with a browser's when that fails, so the results show whether a CDN blocks
scripts. Results go to ``<out>/results.jsonl`` and the PDFs to ``<out>/pdfs/``; a table
is printed. Needs MOUSER_API_KEY for parts not yet in the cache.

    uv run python tools/datasheet_sources/measure.py <out-folder>
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pcbkit import cache, mouser, parts

HERE = Path(__file__).parent
PARTS = HERE / "parts.csv"
OWN_AGENT = "pcbkit-datasheet/1"
BROWSER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
TIMEOUT = 40.0
PAUSE = 3.0
MOUSER_GAP = 3.0
BATCH = mouser.MAX_PART_NUMBERS


def squash(text: str) -> str:
    """Return ``text`` in lower case with everything but letters and digits removed."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def mouser_parts(mpns: list[str]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Return Mouser's parts for each of ``mpns``, and how many requests went out.

    Ask in batches of ten, exact matches, from the cache when it has the batch.
    """
    found: dict[str, list[dict[str, Any]]] = {mpn: [] for mpn in mpns}
    sent = 0
    for start in range(0, len(mpns), BATCH):
        batch = mpns[start : start + BATCH]
        query = mouser.part_number(batch, exact=True)
        cached = cache.read(parts.mouser_folder(), query.cache_key)
        if cached is None and sent:
            time.sleep(MOUSER_GAP)
        entry = parts.ask_mouser(query, max_age=None)
        sent += cached is None
        for part in (entry.value.get("SearchResults") or {}).get("Parts") or []:
            for mpn in batch:
                if squash(part.get("ManufacturerPartNumber", "")) == squash(mpn):
                    found[mpn].append(part)
    return found, sent


def download(url: str, agent: str) -> dict[str, Any]:
    """Fetch ``url`` with User-Agent ``agent``, following redirects; say what came."""
    request = Request(url, headers={"User-Agent": agent, "Accept": "*/*"})
    try:
        with urlopen(request, timeout=TIMEOUT) as answer:
            data = answer.read()
            return {
                "status": answer.status,
                "final_host": urlsplit(answer.geturl()).netloc,
                "content_type": answer.headers.get("Content-Type", ""),
                "bytes": len(data),
                "data": data,
            }
    except HTTPError as err:
        return {"status": err.code, "error": f"HTTP {err.code}", "data": b""}
    except (URLError, OSError) as err:
        reason = getattr(err, "reason", None) or err
        return {"status": None, "error": f"{type(reason).__name__}", "data": b""}


def first_pages_text(pdf: Path) -> str:
    """Return the text of the first three pages of ``pdf``, or "" if none comes."""
    done = subprocess.run(
        ["pdftotext", "-l", "3", "-q", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout


def judge(
    row: dict[str, str], matches: list[dict[str, Any]], out: Path
) -> dict[str, Any]:
    """Return the measurement for one part of parts.csv, given Mouser's matches."""
    links = [part["DataSheetUrl"] for part in matches if part.get("DataSheetUrl")]
    link = links[0] if links else None
    result: dict[str, Any] = dict(row)
    result.update(
        mouser_matches=len(matches),
        mouser_manufacturer=matches[0].get("Manufacturer") if matches else None,
        link=link,
    )
    if not matches:
        result["outcome"] = "not at Mouser"
        return result
    if not link:
        result["outcome"] = "no link"
        return result
    result["link_host"] = urlsplit(link).netloc
    for agent_name, agent in (("own", OWN_AGENT), ("browser", BROWSER_AGENT)):
        time.sleep(PAUSE)
        got = download(link, agent)
        data = got.pop("data")
        result[f"download_{agent_name}"] = got
        if data.startswith(b"%PDF"):
            result["agent_needed"] = agent_name
            break
    else:
        result["outcome"] = "link is not a PDF"
        return result
    digest = hashlib.sha256(data).hexdigest()
    pdf = out / "pdfs" / f"{digest}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(data)
    result["sha256"] = digest
    text = first_pages_text(pdf)
    result["text_chars"] = len(text)
    if not text.strip():
        result["outcome"] = "PDF with no text"
    elif squash(row["family"]) in squash(text):
        result["outcome"] = "right datasheet"
    else:
        result["outcome"] = "PDF, family not found"
    return result


def main(out: Path) -> None:
    """Measure every part in parts.csv; write results and print a summary."""
    out.mkdir(parents=True, exist_ok=True)
    with PARTS.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if "--mouser-only" in sys.argv[2:]:
        rows = rows[:BATCH]
    found, sent = mouser_parts([row["mpn"] for row in rows])
    print(f"Mouser requests sent: {sent} (the rest from the cache)\n")
    if "--mouser-only" in sys.argv[2:]:
        for row in rows:
            matches = found[row["mpn"]]
            link = next(
                (p["DataSheetUrl"] for p in matches if p.get("DataSheetUrl")), ""
            )
            print(f"{len(matches)} match(es)  {row['mpn']:28} {link}")
        return
    results = []
    for row in rows:
        result = judge(row, found[row["mpn"]], out)
        results.append(result)
        print(f"{result['outcome']:24} {row['mpn']}", flush=True)
    with (out / "results.jsonl").open("w") as handle:
        for result in results:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
    print("\nOutcomes:")
    for outcome, count in Counter(r["outcome"] for r in results).most_common():
        print(f"  {count:3}  {outcome}")
    print("\nAgent needed for the PDFs that came:")
    for agent, count in Counter(
        r.get("agent_needed") for r in results if r.get("agent_needed")
    ).items():
        print(f"  {count:3}  {agent}")
    by_maker: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        by_maker[result["manufacturer"]][result["outcome"]] += 1
    print("\nNot right, by manufacturer:")
    for maker, outcomes in sorted(by_maker.items()):
        bad = {k: v for k, v in outcomes.items() if k != "right datasheet"}
        if bad:
            print(f"  {maker}: {dict(bad)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
