"""``pcbkit datasheet find`` and ``confirm``: a part's datasheet, judged and recorded.

``find`` gathers candidate links for a part number, in this order, and stops at the
first that ``pcbkit.datasheets.judge`` verifies:

1. ``--url``, when given: then nothing else is tried;
2. Mouser's ``DataSheetUrl`` (a search by part number, cached for ``LINK_MAX_AGE``);
3. the ``Datasheet`` link of the KiCad stock symbol whose name starts the part number.

Measured on 52 parts (plans/wp14-parts.md): Mouser's links, where it has one, were all
right; KiCad's symbols added links for 15 more, but some were a sibling part's, which
is why every link is judged by the PDF's own text and never trusted for its source.

The manufacturer comes from ``--maker`` or Mouser; whether the number is built from a
series code (resistors, capacitors, crystals) from ``--built`` or Mouser's category.

Each PDF is kept in the cache by its SHA-256, with a map from link to hash. The answer
for each part goes to ``<into>/<part>.datasheet.json``: every candidate with its
verdict, the one chosen, and its status (VERIFIED, CANDIDATE, NOT FOUND, CONFIRMED).
The status there is a summary for people: ``confirm``, and any later stage, judges the
PDF named by the hash again rather than believing it.

``confirm`` turns a CANDIDATE into CONFIRMED when a page and a quote pass
``datasheets.check_confirmation``. The machine is reached through ``_download``,
``_pdftotext`` and ``_sleep`` only, so unit tests replace those (and Mouser's).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import click

from pcbkit import cache, datasheets, mouser, parts
from pcbkit.datasheets import Reason, Status, Verdict
from pcbkit.kicad import env
from pcbkit.kicad.sexp import parse

LINK_MAX_AGE = timedelta(days=30)
# Mouser takes ten part numbers a request; uncached requests are spaced this far apart.
MOUSER_GAP_SECONDS = 3.0
DOWNLOAD_SECONDS = 60
MAX_PDF_BYTES = 64 * 1024 * 1024
USER_AGENT = "pcbkit-datasheet/1"
# Mouser categories whose part numbers are built from a series code, not listed.
BUILT_CATEGORY = re.compile(r"resistor|capacitor|crystal|oscillator", re.IGNORECASE)
RECORD_FORMAT = 1
RECORD_SUFFIX = ".datasheet.json"
NOT_VERIFIED = 3
NOT_FOUND = "NOT FOUND"
CONFIRMED = "CONFIRMED"

Say = Callable[[str], None]


class DatasheetError(click.ClickException):
    """Say why a datasheet could not be found, read or confirmed, and what to do."""


# --- the machine ---------------------------------------------------------------------


def _download(url: str) -> bytes:
    """Fetch ``url``, following redirects; raise OSError if nothing usable came."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(request, timeout=DOWNLOAD_SECONDS) as answer:
        data = answer.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise OSError(f"larger than {MAX_PDF_BYTES // (1024 * 1024)} MB")
    return data


def _pdftotext(tool: str, pdf: Path) -> str:
    """Return the text of ``pdf``, each page ended by a form feed."""
    done = subprocess.run(
        [tool, "-q", str(pdf), "-"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    return done.stdout


def _sleep(seconds: float) -> None:
    """Wait ``seconds``."""
    time.sleep(seconds)


# --- PDFs in the cache ---------------------------------------------------------------


def pdf_folder() -> Path:
    """Return the cache folder of downloaded datasheets, named by their SHA-256."""
    return cache.home() / "datasheets"


def links_folder() -> Path:
    """Return the cache folder that maps each datasheet link to its PDF's hash."""
    return cache.home() / "datasheet-links"


def pdf_path(sha256: str) -> Path:
    """Return where the PDF with hash ``sha256`` is kept."""
    return pdf_folder() / f"{sha256}.pdf"


def _store_pdf(data: bytes) -> str:
    """Keep ``data`` in the cache under its hash; return the hash."""
    digest = hashlib.sha256(data).hexdigest()
    target = pdf_path(digest)
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return digest


def fetch_pdf(url: str, *, refresh: bool = False) -> tuple[str, str]:
    """Return the hash of the PDF at ``url``, from the cache unless ``refresh``.

    Return ("", problem) when no PDF came: the problem says why. A failure is not
    cached, so the next ``find`` tries again.
    """
    if not refresh:
        known = cache.read(links_folder(), url)
        if known is not None and pdf_path(known.value["sha256"]).is_file():
            return known.value["sha256"], ""
    try:
        data = _download(url)
    except HTTPError as err:
        return "", f"HTTP {err.code}"
    except (URLError, OSError, ValueError) as err:
        reason = getattr(err, "reason", None) or err
        return "", f"no answer ({type(reason).__name__}: {reason})"
    if not data.startswith(b"%PDF"):
        return "", "not a PDF (a web page?)"
    digest = _store_pdf(data)
    cache.write(links_folder(), url, {"sha256": digest}, cache.utc_now())
    return digest, ""


def page_texts(sha256: str) -> list[str]:
    """Return the text of each page of a cached PDF, page 1 first.

    Raise DatasheetError if pdftotext is not installed.
    """
    tool = env.find_pdftotext()
    if tool is None:
        raise DatasheetError(
            "pdftotext is not installed, and pcbkit datasheet reads PDFs with it: "
            "brew install poppler (macOS) or sudo apt install poppler-utils (Linux)"
        )
    pages = _pdftotext(tool.path, pdf_path(sha256)).split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


# --- KiCad's stock symbols -----------------------------------------------------------


def _atom(value: Any) -> str:
    """Return a parsed S-expression atom as plain text."""
    return str(value).strip('"')


def symbols_from(libraries: Sequence[tuple[str, str]]) -> list[list[str]]:
    """Return [name, library, datasheet link] for every symbol in ``libraries``.

    ``libraries`` are (library name, .kicad_sym text). A derived symbol without a link
    takes its parent's.
    """
    found: dict[str, tuple[str, str, str]] = {}
    for library, text in libraries:
        tree = parse(text)
        for node in tree[1:]:
            if not isinstance(node, list) or not node or node[0] != "symbol":
                continue
            link, parent = "", ""
            for item in node[2:]:
                if isinstance(item, list) and item and item[0] == "extends":
                    parent = _atom(item[1])
                if (
                    isinstance(item, list)
                    and len(item) > 2
                    and item[0] == "property"
                    and _atom(item[1]) == "Datasheet"
                ):
                    link = _atom(item[2])
            found[_atom(node[1])] = (library, link, parent)
    rows = []
    for name, (library, link, parent) in found.items():
        seen = {name}
        while (not link or link == "~") and parent and parent not in seen:
            seen.add(parent)
            _, link, parent = found.get(parent, ("", "", ""))
        rows.append([name, library, "" if link == "~" else link])
    return rows


def symbol_index(say: Say) -> list[list[str]] | None:
    """Return the stock symbols as [name, library, link], or None without KiCad.

    The index is cached, keyed by the library files' names, sizes and times, so a new
    KiCad builds a new one.
    """
    share = env.find_share()
    folder = share / "symbols" if share else None
    if folder is None or not folder.is_dir():
        return None
    files = sorted(folder.glob("*.kicad_sym"))
    stamp = hashlib.sha256(
        "\n".join(
            f"{path.name} {path.stat().st_size} {path.stat().st_mtime_ns}"
            for path in files
        ).encode()
    ).hexdigest()
    key = f"{folder} {stamp}"
    found = cache.read(cache.home() / "kicad-symbols", key)
    if found is not None:
        return list(found.value)
    say(f"Indexing KiCad's {len(files)} symbol libraries, once (about 30 s)...")
    rows = symbols_from(
        [(path.stem, path.read_text(encoding="utf-8")) for path in files]
    )
    cache.write(cache.home() / "kicad-symbols", key, rows, cache.utc_now())
    return rows


def _symbol_pattern(name: str) -> re.Pattern[str]:
    """Return a pattern for a part number starting with symbol ``name``.

    KiCad writes a lowercase x for "any character", as in ``MCP1700x-330xxTT``.
    """
    pieces = ["." if char == "x" else re.escape(char) for char in name]
    return re.compile("".join(pieces), re.IGNORECASE)


def best_symbol(mpn: str, index: Sequence[Sequence[str]]) -> Sequence[str] | None:
    """Return the symbol with the longest name that starts ``mpn``, or None.

    Names under four characters or with no digit are left out, so R and C do not
    match every part. Punctuation is ignored on both sides when the plain name does
    not match.
    """
    best: Sequence[str] | None = None
    squashed = datasheets.squash(mpn)
    for row in index:
        name = row[0]
        if len(name) < 4 or not re.search(r"\d", name):
            continue
        if best is not None and len(name) <= len(best[0]):
            continue
        plain = re.sub(r"[^A-Za-z0-9]", "", name)
        if _symbol_pattern(name).match(mpn) or (
            plain and _symbol_pattern(plain).match(squashed)
        ):
            best = row
    return best


def normal_link(link: str) -> str:
    """Return ``link`` as a web address, or "" if it is none.

    KiCad leaves some links without a scheme (``www.ti.com/lit/...``).
    """
    link = link.strip()
    if not link or link == "~" or " " in link:
        return ""
    if urlsplit(link).scheme in ("http", "https"):
        return link
    if re.match(r"^[\w-]+(\.[\w-]+)+/", link):
        return f"https://{link}"
    return ""


# --- Mouser --------------------------------------------------------------------------


def mouser_parts(
    mpns: Sequence[str], say: Say
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Return Mouser's parts matching each of ``mpns``, and the numbers it was asked.

    Ten part numbers a request. Answers come from the cache when younger than
    LINK_MAX_AGE; uncached requests are spaced MOUSER_GAP_SECONDS apart. Without a
    key, or if Mouser fails, say so and go on without it.
    """
    found: dict[str, list[dict[str, Any]]] = {mpn: [] for mpn in mpns}
    asked: set[str] = set()
    sent = 0
    for start in range(0, len(mpns), mouser.MAX_PART_NUMBERS):
        batch = list(mpns[start : start + mouser.MAX_PART_NUMBERS])
        query = mouser.part_number(batch, exact=True)
        known = cache.read(parts.mouser_folder(), query.cache_key)
        fresh = known is not None and known.age(cache.utc_now()) <= LINK_MAX_AGE
        if not fresh and sent:
            _sleep(MOUSER_GAP_SECONDS)
        try:
            entry = parts.ask_mouser(query, max_age=LINK_MAX_AGE)
        except mouser.MouserError as err:
            say(f"Mouser skipped: {err}")
            return found, asked
        sent += not fresh
        asked.update(batch)
        results = entry.value.get("SearchResults") or {}
        for part in results.get("Parts") or []:
            number = datasheets.squash(part.get("ManufacturerPartNumber") or "")
            for mpn in batch:
                if number == datasheets.squash(mpn):
                    found[mpn].append(part)
    return found, asked


# --- finding -------------------------------------------------------------------------


def verdict_dict(verdict: Verdict) -> dict[str, Any]:
    """Return a verdict as plain data for the record."""
    return {
        "status": verdict.status.value,
        "reason": verdict.reason.value if verdict.reason else None,
        "part_number_found": verdict.part_number_found,
        "part_number_page": verdict.part_number_page,
        "suffix_dropped": verdict.suffix_dropped,
        "manufacturer_name": verdict.manufacturer_name,
        "manufacturer_page": verdict.manufacturer_page,
    }


def _links(
    mpn: str,
    url: str | None,
    matches: Sequence[dict[str, Any]],
    index: Sequence[Sequence[str]] | None,
) -> list[tuple[str, str]]:
    """Return (source, link) for each candidate link, once each, in order."""
    if url:
        return [("--url", url)]
    found: list[tuple[str, str]] = []
    for part in matches:
        link = normal_link(part.get("DataSheetUrl") or "")
        if link:
            found.append(("Mouser", link))
    symbol = best_symbol(mpn, index) if index else None
    if symbol is not None and normal_link(symbol[2]):
        found.append((f"KiCad {symbol[1]}:{symbol[0]}", normal_link(symbol[2])))
    seen: set[str] = set()
    return [(s, link) for s, link in found if not (link in seen or seen.add(link))]


def _choose(candidates: Sequence[dict[str, Any]]) -> int | None:
    """Return the index of the candidate to keep: verified, confirmable, any judged."""

    def first(test: Callable[[dict[str, Any]], bool]) -> int | None:
        return next((i for i, c in enumerate(candidates) if test(c)), None)

    confirmable = {reason.value for reason in datasheets.CONFIRMABLE}
    for test in (
        lambda c: bool(c["verdict"]) and c["verdict"]["status"] == "VERIFIED",
        lambda c: bool(c["verdict"]) and c["verdict"]["reason"] in confirmable,
        lambda c: bool(c["verdict"]),
    ):
        chosen = first(test)
        if chosen is not None:
            return chosen
    return None


def find(
    mpn: str,
    *,
    url: str | None,
    maker: str | None,
    built: bool,
    matches: Sequence[dict[str, Any]],
    index: Sequence[Sequence[str]] | None,
    notes: Sequence[str] = (),
    mouser_asked: bool = True,
) -> dict[str, Any]:
    """Find and judge ``mpn``'s datasheet; return its record (not yet written).

    ``matches`` are Mouser's parts for it (``mouser_asked`` False when Mouser was not
    asked) and ``index`` KiCad's symbols (None without KiCad). Stops at the first
    verified datasheet.
    """
    manufacturer = (maker or "").strip()
    source = "--maker" if manufacturer else ""
    if not manufacturer and matches:
        manufacturer = (matches[0].get("Manufacturer") or "").strip()
        source = "Mouser" if manufacturer else ""
    is_built = built or any(
        BUILT_CATEGORY.search(part.get("Category") or "") for part in matches
    )
    candidates: list[dict[str, Any]] = []
    for origin, link in _links(mpn, url, matches, index):
        digest, problem = fetch_pdf(link)
        verdict = None
        if digest:
            verdict = datasheets.judge(
                page_texts(digest), mpn, manufacturer, built=is_built
            )
        candidates.append(
            {
                "source": origin,
                "url": link,
                "sha256": digest,
                "problem": problem,
                "verdict": verdict_dict(verdict) if verdict else None,
            }
        )
        if verdict is not None and verdict.status is Status.VERIFIED:
            break
    chosen = _choose(candidates)
    status = NOT_FOUND
    if chosen is not None:
        status = candidates[chosen]["verdict"]["status"]
    tried = list(notes)
    if not url and mouser_asked and not matches:
        tried.append("Mouser: no match")
    if not url and index is None:
        tried.append("KiCad: not installed, its symbols were not searched")
    return {
        "format": RECORD_FORMAT,
        "mpn": mpn,
        "manufacturer": manufacturer,
        "manufacturer_from": source,
        "built": is_built,
        "status": status,
        "chosen": chosen,
        "candidates": candidates,
        "notes": tried,
        "found": cache.utc_now().isoformat(),
        "confirmation": None,
    }


# --- records -------------------------------------------------------------------------


def record_path(folder: Path, mpn: str) -> Path:
    """Return the record file for ``mpn`` in ``folder``."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", mpn.strip()).strip("_.") or "part"
    return folder / f"{slug}{RECORD_SUFFIX}"


def read_record(folder: Path, mpn: str) -> dict[str, Any] | None:
    """Return the record for ``mpn`` in ``folder``, or None if there is none.

    Raise DatasheetError if the file is not a record, or is another part's.
    """
    path = record_path(folder, mpn)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise DatasheetError(f"{path} is not a datasheet record ({err})") from None
    if not isinstance(record, dict) or record.get("format") != RECORD_FORMAT:
        raise DatasheetError(f"{path} is not a datasheet record pcbkit can read")
    if record.get("mpn") != mpn:
        raise DatasheetError(
            f"{path} is the record of {record.get('mpn')!r}, not {mpn!r}: two part "
            "numbers share a file name; rename one of the files"
        )
    return record


def write_record(folder: Path, record: dict[str, Any]) -> Path:
    """Write ``record`` into ``folder``; return its path."""
    path = record_path(folder, record["mpn"])
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def _verdict_now(record: dict[str, Any], candidate: dict[str, Any]) -> Verdict:
    """Judge the record's chosen PDF again, as it is now, not as the record says.

    Fetch it again if the cache lost it; raise DatasheetError if it then differs.
    """
    digest = candidate["sha256"]
    if not pdf_path(digest).is_file():
        again, problem = fetch_pdf(candidate["url"], refresh=True)
        if again != digest:
            raise DatasheetError(
                f"the datasheet at {candidate['url']} "
                + (f"cannot be fetched ({problem})" if problem else "has changed")
                + f" since it was found: run pcbkit datasheet find {record['mpn']}"
            )
    return datasheets.judge(
        page_texts(digest),
        record["mpn"],
        record["manufacturer"],
        built=record["built"],
    )


def keep_confirmation(old: dict[str, Any] | None, new: dict[str, Any]) -> str:
    """Carry an earlier confirmation into ``new`` if it still holds; say what happened.

    It holds when the same PDF was chosen and its page and quote still pass.
    """
    if not old or old.get("status") != CONFIRMED or new["chosen"] is None:
        return ""
    then = old["candidates"][old["chosen"]]
    now = new["candidates"][new["chosen"]]
    if then["sha256"] != now["sha256"] or new["status"] != Status.CANDIDATE.value:
        return "The earlier confirmation was dropped: another datasheet was chosen."
    confirmation = old["confirmation"]
    try:
        datasheets.check_confirmation(
            page_texts(now["sha256"]),
            new["mpn"],
            new["manufacturer"],
            _verdict_now(new, now),
            confirmation["page"],
            confirmation["quote"],
        )
    except datasheets.ConfirmationRefused as err:
        return f"The earlier confirmation was dropped: {err}."
    new["status"] = CONFIRMED
    new["confirmation"] = confirmation
    return ""


def confirm(folder: Path, mpn: str, page: int, quote: str) -> dict[str, Any]:
    """Confirm the chosen candidate of ``mpn``'s record with a page and a quote.

    Judge the PDF again and check the quote against it; write and return the record.
    Raise DatasheetError when there is no record or no candidate, or the quote does
    not confirm it.
    """
    record = read_record(folder, mpn)
    if record is None:
        raise DatasheetError(
            f"no datasheet record for {mpn} in {folder}: run pcbkit datasheet find "
            f"{mpn} first"
        )
    if record["chosen"] is None:
        raise DatasheetError(
            f"no datasheet was found for {mpn}, so there is nothing to confirm: give "
            f"one with pcbkit datasheet find {mpn} --url <url>"
        )
    candidate = record["candidates"][record["chosen"]]
    verdict = _verdict_now(record, candidate)
    try:
        datasheets.check_confirmation(
            page_texts(candidate["sha256"]),
            mpn,
            record["manufacturer"],
            verdict,
            page,
            quote,
        )
    except datasheets.ConfirmationRefused as err:
        raise DatasheetError(f"{mpn} not confirmed: {err}") from None
    record["status"] = CONFIRMED
    record["confirmation"] = {
        "page": page,
        "quote": quote,
        "reason": verdict.reason.value if verdict.reason else None,
        "at": cache.utc_now().isoformat(),
    }
    candidate["verdict"] = verdict_dict(verdict)
    write_record(folder, record)
    return record


# --- what to print -------------------------------------------------------------------


def _quoted(mpn: str) -> str:
    """Return ``mpn`` quoted for a shell when it needs it."""
    return mpn if re.fullmatch(r"[A-Za-z0-9._/+-]+", mpn) else f"'{mpn}'"


def next_steps(record: dict[str, Any]) -> list[str]:
    """Return the next step for a record that is neither verified nor confirmed."""
    mpn = _quoted(record["mpn"])
    if record["status"] in (Status.VERIFIED.value, CONFIRMED):
        return []
    if record["chosen"] is None:
        return [
            "No datasheet found. Find the part's own datasheet and give it:",
            f"  pcbkit datasheet find {mpn} --url <url>",
        ]
    verdict = record["candidates"][record["chosen"]]["verdict"]
    reason = Reason(verdict["reason"])
    steps = [datasheets.NEXT[reason]]
    if reason in datasheets.CONFIRMABLE:
        steps.append(
            f"  pcbkit datasheet confirm {mpn} --page <n> --quote '<text holding "
            f"{datasheets.QUOTE_MUST_HOLD[reason]}>'"
        )
        steps.append(
            "  (pages count from 1 in the PDF's own order, not the numbers printed "
            "on them)"
        )
    if reason is Reason.MANUFACTURER_UNKNOWN:
        steps.append(f"  or: pcbkit datasheet find {mpn} --maker '<manufacturer>'")
    if reason in (Reason.PART_NUMBER_MISSING, Reason.NO_TEXT):
        steps.append(f"  pcbkit datasheet find {mpn} --url <url>")
    return steps


def describe(record: dict[str, Any], path: Path) -> str:
    """Return what to print for a record written to ``path``."""
    status = record["status"]
    lines = []
    chosen = record["chosen"]
    candidate = record["candidates"][chosen] if chosen is not None else None
    verdict = candidate["verdict"] if candidate else None
    head = f"{status:<10} {record['mpn']}"
    if status == Status.CANDIDATE.value and verdict:
        head += f": {verdict['reason']}"
    lines.append(head)
    if candidate and verdict:
        lines.append(f"  datasheet  {candidate['url']} (from {candidate['source']})")
        found = verdict["part_number_found"].upper()
        where = verdict["part_number_page"]
        evidence = f"part number {found!r} on page {where}" if where else "no number"
        if verdict["suffix_dropped"]:
            evidence += f" (without {verdict['suffix_dropped']})"
        if verdict["manufacturer_name"]:
            evidence += (
                f"; {verdict['manufacturer_name']!r} on page "
                f"{verdict['manufacturer_page']}"
            )
        elif record["manufacturer"]:
            evidence += f"; {record['manufacturer']!r} not named in the text"
        lines.append(f"  evidence   {evidence}")
        lines.append(f"  pdf        {pdf_path(candidate['sha256'])}")
    if record["confirmation"] and status == CONFIRMED:
        confirmation = record["confirmation"]
        lines.append(
            f"  confirmed  page {confirmation['page']}: {confirmation['quote']!r}"
        )
    for other in record["candidates"]:
        if other is candidate:
            continue
        why = other["problem"] or (other["verdict"] or {}).get("reason") or "verified"
        lines.append(f"  also tried {other['url']} (from {other['source']}): {why}")
    for note in record["notes"]:
        lines.append(f"  note       {note}")
    for number, step in enumerate(next_steps(record)):
        lines.append(f"  {'next' if number == 0 else '':<10} {step}".rstrip())
    lines.append(f"  record     {path}")
    return "\n".join(lines)
