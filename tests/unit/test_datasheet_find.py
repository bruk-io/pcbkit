"""Unit tests for pcbkit.datasheet_find and the `pcbkit datasheet` commands.

The world is fake: `Web` stands in for the internet (each link answers with a PDF, a
web page or an error) and for pdftotext (each PDF's pages are set by the test),
Mouser answers from a list of parts, and KiCad's symbols are a small library written
into the fake machine. The cache is the fake machine's ~/.cache/pcbkit.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from pcbkit import cache, datasheet_find, mouser
from pcbkit.cli import cli
from pcbkit.datasheet_find import DatasheetError
from pcbkit.kicad import env
from pcbkit.kicad.env import Tool
from tests.fake_machine import FakeMachine

KEY = "0000-test-key-1234"

INA226_PAGES = [
    "INA226 Current and Power Monitor\nTexas Instruments",
    "Package option addendum\nINA226AIDGSR ACTIVE VSSOP",
]
SIBLING_PAGES = ["1N5817 thru 1N5819 Schottky barrier rectifiers. Vishay"]
SERIES_PAGES = [
    "RC series thick film chip resistors. Yageo",
    "GLOBAL PART NUMBER\nRC XXXX X X X XX XXXX L\n(1) SIZE 0402/0603",
    "Fig. 1 RC0603",
]
LOGO_PAGES = ["30V P-Channel MOSFET\nAO3401A General Description"]

SYMBOLS = """(kicad_symbol_lib
  (symbol "INA226"
    (property "Reference" "U")
    (property "Datasheet" "www.ti.com/lit/ds/symlink/ina226.pdf"))
  (symbol "1N5819"
    (property "Datasheet" "http://diodes.test/1n5817.pdf"))
  (symbol "AO3401A"
    (property "Datasheet" "http://aos.test/AO3401A.pdf"))
  (symbol "MCP1700x-330xxTT"
    (extends "MCP1700x-300xxTT"))
  (symbol "MCP1700x-300xxTT"
    (property "Datasheet" "http://microchip.test/mcp1700.pdf"))
  (symbol "R"
    (property "Datasheet" "~"))
)
"""


@dataclass
class Web:
    """The internet and pdftotext, as far as these tests need them."""

    pages: dict[str, Any] = field(default_factory=dict)
    texts: dict[bytes, list[str]] = field(default_factory=dict)
    fetched: list[str] = field(default_factory=list)
    mouser_parts: list[dict[str, Any]] = field(default_factory=list)
    mouser_requests: list[bytes] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)

    def pdf(self, url: str, pages: Sequence[str]) -> None:
        """Serve a PDF with ``pages`` at ``url``."""
        data = ("%PDF-1.7 " + url + " " + "|".join(pages)).encode()
        self.pages[url] = data
        self.texts[data] = list(pages)

    def download(self, url: str) -> bytes:
        """Answer a download, or raise what the link raises."""
        self.fetched.append(url)
        answer = self.pages.get(url, OSError("no route to host"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def pdftotext(self, tool: str, pdf: Path) -> str:
        """Return the pages of a stored PDF, each ended by a form feed."""
        return "".join(page + "\f" for page in self.texts[pdf.read_bytes()])

    def transport(
        self, method: str, url: str, body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        """Answer a Mouser search with the parts whose number was asked for."""
        assert body is not None
        self.mouser_requests.append(body)
        asked = json.loads(body)["SearchByPartRequest"]["mouserPartNumber"].split("|")
        parts = [p for p in self.mouser_parts if p["ManufacturerPartNumber"] in asked]
        answer = {"Errors": [], "SearchResults": {"NumberOfResult": len(parts)}}
        answer["SearchResults"]["Parts"] = parts
        return 200, json.dumps(answer).encode()


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch, machine: FakeMachine) -> Web:
    """Install a fake web, pdftotext, Mouser and KiCad symbol library."""
    fake = Web()
    monkeypatch.setattr(datasheet_find, "_download", fake.download)
    monkeypatch.setattr(datasheet_find, "_pdftotext", fake.pdftotext)
    monkeypatch.setattr(datasheet_find, "_sleep", fake.waits.append)
    monkeypatch.setattr(mouser, "_transport", fake.transport)
    monkeypatch.setattr(env, "find_pdftotext", lambda: Tool("/bin/pdftotext", "26"))
    monkeypatch.setenv("MOUSER_API_KEY", KEY)
    (machine.linux_share / "footprints").mkdir(parents=True)
    (machine.linux_share / "symbols").mkdir()
    (machine.linux_share / "symbols" / "Test.kicad_sym").write_text(SYMBOLS)
    return fake


def mouser_part(mpn: str, maker: str, link: str = "", category: str = "") -> dict:
    """Return a part as Mouser's search answers it."""
    return {
        "ManufacturerPartNumber": mpn,
        "Manufacturer": maker,
        "DataSheetUrl": link,
        "Category": category,
    }


def run(*args: str, into: Path) -> Result:
    """Run `pcbkit datasheet ...` with records in ``into``."""
    return CliRunner().invoke(cli, ["datasheet", *args, "--into", str(into)])


def record(into: Path, mpn: str) -> dict[str, Any]:
    """Return the record written for ``mpn``."""
    found = datasheet_find.read_record(into, mpn)
    assert found is not None
    return found


# --- finding -------------------------------------------------------------------------


def test_find_verifies_a_kicad_link_when_mouser_has_none(
    web: Web, tmp_path: Path
) -> None:
    """Use the symbol's link (given without a scheme), judge it and record VERIFIED."""
    web.mouser_parts = [mouser_part("INA226AIDGSR", "Texas Instruments")]
    web.pdf("https://www.ti.com/lit/ds/symlink/ina226.pdf", INA226_PAGES)
    result = run("find", "INA226AIDGSR", into=tmp_path)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Indexing KiCad's 1 symbol libraries")
    assert "VERIFIED   INA226AIDGSR" in result.output
    assert "(from KiCad Test:INA226)" in result.output
    assert "'INA226AIDGSR' on page 2; 'Texas Instruments' on page 1" in result.output
    saved = record(tmp_path, "INA226AIDGSR")
    assert saved["status"] == "VERIFIED" and saved["manufacturer_from"] == "Mouser"
    chosen = saved["candidates"][saved["chosen"]]
    assert datasheet_find.pdf_path(chosen["sha256"]).is_file()


def test_mousers_verified_link_ends_the_search(web: Web, tmp_path: Path) -> None:
    """Stop at the first verified datasheet, without downloading KiCad's link."""
    web.mouser_parts = [
        mouser_part("INA226AIDGSR", "Texas Instruments", "https://m.test/ina.pdf")
    ]
    web.pdf("https://m.test/ina.pdf", INA226_PAGES)
    assert run("find", "INA226AIDGSR", into=tmp_path).exit_code == 0
    assert web.fetched == ["https://m.test/ina.pdf"]


def test_a_sibling_parts_datasheet_is_a_candidate_with_the_next_step(
    web: Web, tmp_path: Path
) -> None:
    """Record a sibling's datasheet as a candidate, say why and how to go on; exit 3."""
    web.mouser_parts = [mouser_part("1N5819HW-7-F", "Diodes Incorporated")]
    web.pdf("http://diodes.test/1n5817.pdf", SIBLING_PAGES)
    result = run("find", "1N5819HW-7-F", into=tmp_path)
    assert result.exit_code == datasheet_find.NOT_VERIFIED == 3
    assert "CANDIDATE  1N5819HW-7-F: part-number-missing" in result.output
    assert "pcbkit datasheet find 1N5819HW-7-F --url <url>" in result.output
    assert "'Diodes Incorporated' not named in the text" in result.output


def test_mousers_category_marks_a_built_number(web: Web, tmp_path: Path) -> None:
    """Treat a resistor's number as built from a series code, and ask to confirm."""
    web.mouser_parts = [
        mouser_part(
            "RC0603FR-0710KL", "Yageo", "https://m.test/rc.pdf", "Thick Film Resistors"
        )
    ]
    web.pdf("https://m.test/rc.pdf", SERIES_PAGES)
    result = run("find", "RC0603FR-0710KL", into=tmp_path)
    assert result.exit_code == 3
    assert "part-number-built" in result.output
    assert "--quote '<text holding the series code where" in result.output
    assert "pages count from 1 in the PDF's own order" in result.output
    assert record(tmp_path, "RC0603FR-0710KL")["built"] is True


def test_without_a_key_mouser_is_skipped_and_the_maker_is_unknown(
    web: Web, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Go on with KiCad alone, say Mouser was skipped, and ask for the maker."""
    monkeypatch.delenv("MOUSER_API_KEY")
    web.pdf("http://aos.test/AO3401A.pdf", LOGO_PAGES)
    result = run("find", "AO3401A", into=tmp_path)
    assert result.exit_code == 3
    assert "Mouser skipped: MOUSER_API_KEY is not set" in result.output
    assert "Mouser: no match" not in result.output
    assert "manufacturer-unknown" in result.output
    assert "pcbkit datasheet find AO3401A --maker '<manufacturer>'" in result.output


def test_a_part_mouser_does_not_carry_says_so(web: Web, tmp_path: Path) -> None:
    """Note that Mouser had no match when it was asked."""
    web.pdf("http://aos.test/AO3401A.pdf", LOGO_PAGES)
    result = run("find", "AO3401A", "--maker", "Alpha & Omega", into=tmp_path)
    assert "note       Mouser: no match" in result.output
    assert "manufacturer-missing" in result.output


def test_url_judges_only_that_link(web: Web, tmp_path: Path) -> None:
    """Judge the given link and nothing else; with --maker, ask no one."""
    web.pdf("https://given.test/ina.pdf", INA226_PAGES)
    result = run(
        "find",
        "INA226AIDGSR",
        "--url",
        "https://given.test/ina.pdf",
        "--maker",
        "Texas Instruments",
        into=tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "(from --url)" in result.output
    assert web.fetched == ["https://given.test/ina.pdf"]
    assert web.mouser_requests == []


@pytest.mark.parametrize("option", ["--url", "--maker"])
def test_url_and_maker_take_one_part_number(
    web: Web, tmp_path: Path, option: str
) -> None:
    """Refuse --url or --maker with two part numbers."""
    result = run("find", "INA226AIDGSR", "AO3401A", option, "x", into=tmp_path)
    assert result.exit_code == 2
    assert "give one number" in result.output


def test_a_link_that_gives_no_pdf_is_not_found(web: Web, tmp_path: Path) -> None:
    """Record why each link failed and say how to give one."""
    web.mouser_parts = [
        mouser_part("INA226AIDGSR", "Texas Instruments", "https://m.test/page")
    ]
    web.pages["https://m.test/page"] = b"<html>product page</html>"
    result = run("find", "INA226AIDGSR", into=tmp_path)
    assert result.exit_code == 3
    assert result.output.count("NOT FOUND") == 1
    saved = record(tmp_path, "INA226AIDGSR")
    problems = [c["problem"] for c in saved["candidates"]]
    assert problems == [
        "not a PDF (a web page?)",
        "no answer (OSError: no route to host)",
    ]
    assert "pcbkit datasheet find INA226AIDGSR --url <url>" in result.output


def test_mouser_is_asked_ten_at_a_time_spaced_and_cached(
    web: Web, tmp_path: Path
) -> None:
    """Ask twice for twelve parts, wait between the requests, and not at all again."""
    numbers = [f"PART{n:04}" for n in range(12)]
    first = run("find", *numbers, into=tmp_path)
    assert first.exit_code == 3
    assert len(web.mouser_requests) == 2
    assert web.waits == [datasheet_find.MOUSER_GAP_SECONDS]
    run("find", *numbers, into=tmp_path)
    assert len(web.mouser_requests) == 2


def test_pdfs_are_kept_by_hash_and_not_fetched_again(web: Web, tmp_path: Path) -> None:
    """Download a link once; the cache answers the next find."""
    web.mouser_parts = [
        mouser_part("INA226AIDGSR", "Texas Instruments", "https://m.test/ina.pdf")
    ]
    web.pdf("https://m.test/ina.pdf", INA226_PAGES)
    run("find", "INA226AIDGSR", into=tmp_path)
    run("find", "INA226AIDGSR", into=tmp_path)
    assert web.fetched == ["https://m.test/ina.pdf"]


def test_without_kicad_only_mouser_is_tried(
    web: Web, tmp_path: Path, machine: FakeMachine
) -> None:
    """Say KiCad's symbols were not searched when there is no KiCad."""
    (machine.linux_share / "footprints").rmdir()
    result = run("find", "INA226AIDGSR", into=tmp_path)
    assert "KiCad: not installed" in result.output


def test_without_pdftotext_find_says_how_to_install_it(
    web: Web, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail with the install command when pdftotext is missing."""
    monkeypatch.setattr(env, "find_pdftotext", lambda: None)
    web.pdf("https://www.ti.com/lit/ds/symlink/ina226.pdf", INA226_PAGES)
    result = run("find", "INA226AIDGSR", into=tmp_path)
    assert result.exit_code == 1
    assert "brew install poppler" in result.output


# --- KiCad's symbols -----------------------------------------------------------------


def test_the_symbol_index_is_built_once_per_library_state(web: Web) -> None:
    """Build the index once, then read it from the cache until a library changes."""
    said: list[str] = []
    first = datasheet_find.symbol_index(said.append)
    assert datasheet_find.symbol_index(said.append) == first
    assert len(said) == 1
    assert ["MCP1700x-330xxTT", "Test", "http://microchip.test/mcp1700.pdf"] in first


@pytest.mark.parametrize(
    "mpn, symbol",
    [
        ("INA226AIDGSR", "INA226"),
        ("MCP1700T-3302E/TT", "MCP1700x-330xxTT"),
        ("MCP1700T-1202E/TT", None),
        ("RC0603FR-0710KL", None),
        ("1N5819HW-7-F", "1N5819"),
    ],
)
def test_the_longest_symbol_name_that_starts_the_number_matches(
    mpn: str, symbol: str | None
) -> None:
    """Match the longest name, x for any character; never R or C alone."""
    index = datasheet_find.symbols_from([("Test", SYMBOLS)])
    found = datasheet_find.best_symbol(mpn, index)
    assert (found[0] if found else None) == symbol


@pytest.mark.parametrize(
    "link, normal",
    [
        ("http://a.test/x.pdf", "http://a.test/x.pdf"),
        (
            "www.ti.com/lit/ds/symlink/txs0108e.pdf",
            "https://www.ti.com/lit/ds/symlink/txs0108e.pdf",
        ),
        ("~", ""),
        ("", ""),
        ("see the manual", ""),
        ("ftp://a.test/x.pdf", ""),
    ],
)
def test_links_are_made_web_addresses_or_dropped(link: str, normal: str) -> None:
    """Add https to a bare host, and drop what is not a web address."""
    assert datasheet_find.normal_link(link) == normal


# --- records -------------------------------------------------------------------------


def test_records_are_named_safely_and_hold_their_part_number(tmp_path: Path) -> None:
    """Make a file name from any part number, and refuse another part's record."""
    assert datasheet_find.record_path(tmp_path, "DS3231SN#T&R").name == (
        "DS3231SN_T_R.datasheet.json"
    )
    assert datasheet_find.record_path(tmp_path, "SS34-E3/57T").name == (
        "SS34-E3_57T.datasheet.json"
    )
    datasheet_find.write_record(
        tmp_path, {"format": 1, "mpn": "DS3231SN#T&R", "status": "x"}
    )
    with pytest.raises(DatasheetError, match="is the record of 'DS3231SN#T&R'"):
        datasheet_find.read_record(tmp_path, "DS3231SN/T&R")


def test_a_file_that_is_not_a_record_is_refused(tmp_path: Path) -> None:
    """Refuse a damaged record rather than overwrite or trust it."""
    datasheet_find.record_path(tmp_path, "ABC123").write_text("{not json")
    with pytest.raises(DatasheetError, match="is not a datasheet record"):
        datasheet_find.read_record(tmp_path, "ABC123")


# --- confirming ----------------------------------------------------------------------


@pytest.fixture
def series_found(web: Web, tmp_path: Path) -> Path:
    """Find the resistor, leaving a part-number-built candidate; return its folder."""
    web.mouser_parts = [
        mouser_part("RC0603FR-0710KL", "Yageo", "https://m.test/rc.pdf", "Resistors")
    ]
    web.pdf("https://m.test/rc.pdf", SERIES_PAGES)
    assert run("find", "RC0603FR-0710KL", into=tmp_path).exit_code == 3
    return tmp_path


def test_confirm_records_the_page_and_quote(series_found: Path) -> None:
    """Turn the candidate into CONFIRMED with the evidence kept in the record."""
    result = run(
        "confirm",
        "RC0603FR-0710KL",
        "--page",
        "2",
        "--quote",
        "RC XXXX X X X XX XXXX L",
        into=series_found,
    )
    assert result.exit_code == 0, result.output
    assert "CONFIRMED  RC0603FR-0710KL" in result.output
    saved = record(series_found, "RC0603FR-0710KL")
    assert saved["status"] == "CONFIRMED"
    assert saved["confirmation"]["page"] == 2
    assert saved["confirmation"]["reason"] == "part-number-built"


def test_confirm_refuses_a_quote_not_on_the_page(series_found: Path) -> None:
    """Refuse, saying how pages are counted, and leave the record a candidate."""
    result = run(
        "confirm",
        "RC0603FR-0710KL",
        "--page",
        "3",
        "--quote",
        "RC XXXX X X X XX XXXX L",
        into=series_found,
    )
    assert result.exit_code == 1
    assert "not on page 3" in result.output and "file's own order" in result.output
    assert record(series_found, "RC0603FR-0710KL")["status"] == "CANDIDATE"


def test_confirm_judges_the_pdf_again_not_the_records_word(
    series_found: Path,
) -> None:
    """Ignore a status edited into the record: the PDF decides what can be confirmed."""
    path = datasheet_find.record_path(series_found, "RC0603FR-0710KL")
    edited = json.loads(path.read_text())
    edited["candidates"][edited["chosen"]]["verdict"]["reason"] = "manufacturer-missing"
    edited["built"] = False
    path.write_text(json.dumps(edited))
    result = run(
        "confirm",
        "RC0603FR-0710KL",
        "--page",
        "3",
        "--quote",
        "RC0603",
        into=series_found,
    )
    assert result.exit_code == 1
    assert "part-number-missing cannot be confirmed" in result.output


def test_confirm_needs_a_record_and_a_candidate(web: Web, tmp_path: Path) -> None:
    """Say to run find first, or to give a link when nothing was found."""
    first = run("confirm", "ABC123", "--page", "1", "--quote", "x", into=tmp_path)
    assert "run pcbkit datasheet find ABC123 first" in first.output
    run("find", "ABC123", into=tmp_path)
    second = run("confirm", "ABC123", "--page", "1", "--quote", "x", into=tmp_path)
    assert "nothing to confirm" in second.output
    assert second.exit_code == 1


def test_confirm_fetches_a_lost_pdf_again_and_refuses_a_changed_one(
    series_found: Path, web: Web
) -> None:
    """Fetch the PDF again when the cache lost it, and refuse it if it changed."""
    saved = record(series_found, "RC0603FR-0710KL")
    digest = saved["candidates"][saved["chosen"]]["sha256"]
    datasheet_find.pdf_path(digest).unlink()
    quote = ("--page", "2", "--quote", "RC XXXX X X X XX XXXX L")
    assert run("confirm", "RC0603FR-0710KL", *quote, into=series_found).exit_code == 0
    datasheet_find.pdf_path(digest).unlink()
    web.pdf("https://m.test/rc.pdf", ["A new revision", *SERIES_PAGES])
    result = run("confirm", "RC0603FR-0710KL", *quote, into=series_found)
    assert result.exit_code == 1
    assert "has changed since it was found" in result.output


def test_finding_again_keeps_a_confirmation_that_still_holds(
    series_found: Path, web: Web
) -> None:
    """Keep CONFIRMED through a new find of the same PDF."""
    quote = ("--page", "2", "--quote", "RC XXXX X X X XX XXXX L")
    run("confirm", "RC0603FR-0710KL", *quote, into=series_found)
    result = run("find", "RC0603FR-0710KL", into=series_found)
    assert result.exit_code == 0, result.output
    assert record(series_found, "RC0603FR-0710KL")["status"] == "CONFIRMED"


def test_finding_another_datasheet_drops_the_confirmation(
    series_found: Path, web: Web
) -> None:
    """Drop a confirmation, and say so, when find chooses a different PDF."""
    quote = ("--page", "2", "--quote", "RC XXXX X X X XX XXXX L")
    run("confirm", "RC0603FR-0710KL", *quote, into=series_found)
    web.pdf("https://other.test/rc.pdf", SERIES_PAGES[1:])
    result = run(
        "find",
        "RC0603FR-0710KL",
        "--url",
        "https://other.test/rc.pdf",
        "--maker",
        "Yageo",
        "--built",
        into=series_found,
    )
    assert "earlier confirmation was dropped" in result.output
    assert record(series_found, "RC0603FR-0710KL")["status"] == "CANDIDATE"


def test_cached_answers_live_under_the_cache_home(
    web: Web, machine: FakeMachine
) -> None:
    """Keep PDFs and the link map in ~/.cache/pcbkit."""
    assert datasheet_find.pdf_folder() == cache.home() / "datasheets"
    assert cache.home() == machine.home / ".cache" / "pcbkit"
