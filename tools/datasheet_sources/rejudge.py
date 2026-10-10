"""Judge again, offline, the datasheets that measure.py and kicad_symbols.py downloaded.

Reads ``<out>/results.jsonl`` (Mouser) and ``<out>/kicad_results.jsonl`` (KiCad's
symbols), judges each downloaded PDF with ``pcbkit.datasheets.judge`` as it stands
now, and prints the verdicts per source and for the two together. Nothing is fetched,
so a change to the rules can be measured in seconds.

    uv run python tools/datasheet_sources/rejudge.py <out-folder>
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from measure import PARTS, outcome_of

SOURCES = (("Mouser", "results.jsonl"), ("KiCad", "kicad_results.jsonl"))


def main(out: Path) -> None:
    """Judge every downloaded PDF again and print the verdicts."""
    with PARTS.open(newline="") as handle:
        rows = {row["mpn"]: row for row in csv.DictReader(handle)}
    verdicts: dict[str, dict[str, dict[str, Any]]] = {}
    for source, name in SOURCES:
        verdicts[source] = {}
        for line in (out / name).read_text().splitlines():
            record = json.loads(line)
            mpn = record["mpn"]
            if record.get("sha256"):
                pdf = out / "pdfs" / f"{record['sha256']}.pdf"
                verdicts[source][mpn] = outcome_of(pdf, rows[mpn])
            else:
                verdicts[source][mpn] = {"outcome": f"no PDF ({record['outcome']})"}
    for source, _ in SOURCES:
        print(f"{source}:")
        counts = Counter(v["outcome"] for v in verdicts[source].values())
        for outcome, count in counts.most_common():
            print(f"  {count:3}  {outcome}")
    print("\nPer part (Mouser | KiCad):")
    either = 0
    for mpn in rows:
        found = [verdicts[source][mpn]["outcome"] for source, _ in SOURCES]
        either += "VERIFIED" in found
        if any(not outcome.startswith("no PDF") for outcome in found):
            print(f"  {mpn:26} {found[0]:34} | {found[1]}")
    print(f"\nVerified by either source: {either} of {len(rows)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
