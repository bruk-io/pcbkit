"""Measure how often KiCad's stock symbols lead to the right datasheet.

For each part in parts.csv, find the stock symbol whose name matches the start of the
part number (KiCad writes a lowercase ``x`` for "any character", as in
``MCP1700x-330xxTT``; the longest match wins, and names under four characters or with
no digit are left out so ``R`` and ``C`` do not match everything). Take its
``Datasheet`` property, or its parent's for a derived symbol, then download and judge
it as measure.py does (``pcbkit.datasheets.judge``). No request goes to a
distributor: the symbols are on disk, and the links point at manufacturers.

The symbol's name is also compared with the family in parts.csv, to see whether
matching a symbol is a way to find a part's family.

    uv run python tools/datasheet_sources/kicad_symbols.py <out-folder>
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from measure import PARTS, fetch, outcome_of, squash

from pcbkit import datasheet_find
from pcbkit.kicad import env
from pcbkit.kicad.sexp import parse


@dataclass(frozen=True)
class Symbol:
    """A stock symbol's name, library and the properties that matter here."""

    library: str
    name: str
    datasheet: str
    parent: str


def _text(value: Any) -> str:
    """Return a parsed atom as plain text."""
    return str(value).strip('"')


def load_symbols(folder: Path) -> dict[str, Symbol]:
    """Return every top-level symbol in the ``.kicad_sym`` files of ``folder``."""
    found: dict[str, Symbol] = {}
    for path in sorted(folder.glob("*.kicad_sym")):
        tree = parse(path.read_text(encoding="utf-8"))
        for node in tree[1:]:
            if not isinstance(node, list) or not node or node[0] != "symbol":
                continue
            name = _text(node[1])
            datasheet, parent = "", ""
            for item in node[2:]:
                if not isinstance(item, list) or not item:
                    continue
                if item[0] == "extends":
                    parent = _text(item[1])
                if item[0] == "property" and _text(item[1]) == "Datasheet":
                    datasheet = _text(item[2])
            found[name] = Symbol(path.stem, name, datasheet, parent)
    return found


def datasheet_of(symbol: Symbol, symbols: dict[str, Symbol]) -> str:
    """Return the symbol's datasheet link, or its parent's if it has none."""
    link = symbol.datasheet
    seen = {symbol.name}
    while (not link or link == "~") and symbol.parent and symbol.parent not in seen:
        seen.add(symbol.parent)
        symbol = symbols.get(symbol.parent, symbol)
        link = symbol.datasheet
    return "" if link == "~" else link


def _pattern(name: str) -> re.Pattern[str]:
    """Return a pattern matching a part number that starts with symbol ``name``."""
    parts = ["." if char == "x" else re.escape(char) for char in name]
    return re.compile("".join(parts), re.IGNORECASE)


def best_symbol(mpn: str, symbols: dict[str, Symbol]) -> Symbol | None:
    """Return the longest stock symbol name that matches the start of ``mpn``."""
    best: Symbol | None = None
    for name, symbol in symbols.items():
        if len(name) < 4 or not re.search(r"\d", name):
            continue
        for candidate in (mpn, squash(mpn)):
            target = name if candidate == mpn else squash(name)
            if target and _pattern(target).match(candidate):
                if best is None or len(name) > len(best.name):
                    best = symbol
                break
    return best


def main(out: Path, match_only: bool) -> None:
    """Match every part to a stock symbol, judge its datasheet link, print a summary.

    With ``match_only``, stop at the match and download nothing.
    """
    out.mkdir(parents=True, exist_ok=True)
    symbols = load_symbols(env.symbols_dir())
    print(f"{len(symbols)} stock symbols loaded from {env.symbols_dir()}\n")
    with PARTS.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    mouser_links = {}
    mouser_results = out / "results.jsonl"
    if mouser_results.exists():
        for line in mouser_results.read_text().splitlines():
            record = json.loads(line)
            mouser_links[record["mpn"]] = record["outcome"] == "VERIFIED"
    results: list[dict[str, Any]] = []
    fetched: dict[str, dict[str, Any]] = {}
    for row in rows:
        result: dict[str, Any] = dict(row)
        symbol = best_symbol(row["mpn"], symbols)
        result["mouser_right"] = mouser_links.get(row["mpn"])
        if symbol is None:
            result["outcome"] = "no symbol"
        else:
            link = datasheet_of(symbol, symbols)
            result.update(
                symbol=f"{symbol.library}:{symbol.name}",
                family_matches=squash(row["family"]) in squash(symbol.name)
                or squash(symbol.name) in squash(row["family"]),
                link=link,
            )
            if not link.startswith("http"):
                result["outcome"] = "symbol, no link"
            elif match_only:
                result["outcome"] = "symbol with link"
            else:
                if link not in fetched:
                    fetched[link] = judge_link(link, out)
                judged = dict(fetched[link])
                result.update(judged)
                if judged.get("sha256"):
                    pdf = datasheet_find.pdf_path(judged["sha256"])
                    result.update(outcome_of(pdf, row))
        results.append(result)
        print(
            f"{result['outcome']:24} {row['mpn']:26} {result.get('symbol', '')}"
            + (f"  {result.get('link', '')}" if match_only else ""),
            flush=True,
        )
    if match_only:
        return
    with (out / "kicad_results.jsonl").open("w") as handle:
        for result in results:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
    print("\nOutcomes (all parts):")
    for outcome, count in Counter(r["outcome"] for r in results).most_common():
        print(f"  {count:3}  {outcome}")
    missed = [r for r in results if r["mouser_right"] is False]
    print(f"\nOn the {len(missed)} parts Mouser did not cover:")
    for outcome, count in Counter(r["outcome"] for r in missed).most_common():
        print(f"  {count:3}  {outcome}")
    named = [r for r in results if "symbol" in r]
    print(
        f"\nSymbol name agrees with the family: "
        f"{sum(r['family_matches'] for r in named)} of {len(named)} matched parts"
    )
    for r in named:
        if not r["family_matches"]:
            print(f"  {r['mpn']:26} family {r['family']:20} symbol {r['symbol']}")


def judge_link(link: str, out: Path) -> dict[str, Any]:
    """Download ``link`` once; return the PDF's hash, or the outcome when none came."""
    digest, problem = fetch(link)
    if digest:
        return {"sha256": digest}
    return {"outcome": "link is not a PDF", "problem": problem}


if __name__ == "__main__":
    main(Path(sys.argv[1]), "--match-only" in sys.argv[2:])
