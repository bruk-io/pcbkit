"""Measure how often Mouser's datasheet link gets us the right datasheet.

For each part in parts.csv: ask Mouser (through pcbkit's client and cache, answers of
any age), take the datasheet link of the exact part, download it, and judge it by
rules a script can check:

- the download is a PDF (it starts with ``%PDF``), not a landing page or a block page;
- it is this part's datasheet, by ``pcbkit.datasheets.judge`` on its text
  (``pdftotext``): VERIFIED, or a CANDIDATE with the reason. Parts whose kind in
  parts.csv is in ``BUILT_KINDS`` have numbers built from a series code.

Mouser is asked in batches of ten part numbers (the most one request takes), at least
``MOUSER_GAP`` seconds apart, and the answers are cached, so 52 parts cost 6 requests
once and none on a second run. Downloads wait ``PAUSE`` seconds before each request,
since most links point at mouser.com too.

Each link is downloaded once, into pcbkit's own datasheet cache (as
``pcbkit datasheet find`` does). Results go to ``<out>/results.jsonl``; a table is
printed. (A first version also tried a browser's User-Agent when pcbkit's failed: on
2026-10-09 no link needed it.) Needs MOUSER_API_KEY for parts not yet in the cache.

    uv run python tools/datasheet_sources/measure.py <out-folder>
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pcbkit import cache, datasheet_find, datasheets, mouser, parts

HERE = Path(__file__).parent
PARTS = HERE / "parts.csv"
TIMEOUT = 40.0
PAUSE = 3.0
MOUSER_GAP = 3.0
BATCH = mouser.MAX_PART_NUMBERS
BUILT_KINDS = {"resistor", "capacitor", "hybrid capacitor", "crystal", "PTC fuse"}


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


def fetch(link: str) -> tuple[str, str]:
    """Return the hash of the PDF at ``link``, or "" and why none came.

    Goes through pcbkit's datasheet cache (``datasheet_find.fetch_pdf``), so a link
    is downloaded once; a download waits ``PAUSE`` seconds first.
    """
    known = cache.read(datasheet_find.links_folder(), link)
    if known is None or not datasheet_find.pdf_path(known.value["sha256"]).is_file():
        time.sleep(PAUSE)
    return datasheet_find.fetch_pdf(link)


def pdf_pages(pdf: Path) -> list[str]:
    """Return the text of each page of ``pdf``: pdftotext ends each with a form feed."""
    done = subprocess.run(
        ["pdftotext", "-q", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.split("\f")


def outcome_of(pdf: Path, row: dict[str, str]) -> dict[str, Any]:
    """Judge ``pdf`` for the part in ``row``; return the outcome and its evidence."""
    verdict = datasheets.judge(
        pdf_pages(pdf),
        row["mpn"],
        row["manufacturer"],
        built=row["kind"] in BUILT_KINDS,
    )
    return {
        "outcome": verdict.status.value
        + (f" {verdict.reason.value}" if verdict.reason else ""),
        "part_number_found": verdict.part_number_found,
        "part_number_page": verdict.part_number_page,
        "manufacturer_name": verdict.manufacturer_name,
    }


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
    digest, problem = fetch(link)
    if not digest:
        result.update(outcome="link is not a PDF", problem=problem)
        return result
    result["sha256"] = digest
    pdf = datasheet_find.pdf_path(digest)
    result.update(outcome_of(pdf, row))
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
    by_maker: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        by_maker[result["manufacturer"]][result["outcome"]] += 1
    print("\nNot verified, by manufacturer:")
    for maker, outcomes in sorted(by_maker.items()):
        bad = {k: v for k, v in outcomes.items() if k != "VERIFIED"}
        if bad:
            print(f"  {maker}: {dict(bad)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
