"""Unit tests for pcbkit.stock and `pcbkit parts stock`.

Mouser is a fake (`mouser._transport` is replaced) and so is the clock's sleep; HOME
is the fake machine's, so the cache is a temporary folder. The recorded answer in
tests/fixtures/mouser (INA226, a public part) is used once as it came; the rest are
made-up answers of the same shape for generic public part numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from pcbkit import cache, mouser, parts, stock
from pcbkit.cli import cli
from pcbkit.fab.bom import BomLine
from tests.board_files import write_file, write_project

KEY = "0000-test-key-1234"
FIXTURES = Path(__file__).parent.parent / "fixtures" / "mouser"
INA226 = json.loads((FIXTURES / "partnumber_ina226.json").read_text("utf-8"))
RESISTOR = "RC0603FR-0710KL"  # Yageo; pcbkit's default for a 10k 0603 resistor
CAP = "GRM188R71C104KA01D"  # Murata

# design.py needs a block on every part; this `part` supplies one.
PRELUDE = """
from pcbkit.design import R
from pcbkit.design import part as _part


def part(*args, **kwargs):
    kwargs.setdefault("block", "x")
    _part(*args, **kwargs)

"""
DESIGN = (
    PRELUDE
    + f"""

part("C1", "Device:C", "100n", "Capacitor_SMD:C_0603_1608Metric",
     {{"1": "A", "2": "B"}}, mfr="Murata", mpn="{CAP}")
R("R1", "10k", "A", "B", "x")
R("R2", "10k", "A", "B", "x")
part("U1", "Sensor:INA226", "INA226", "Package_SO:VSSOP-10_3x3mm_P0.5mm", {{"1": "A"}},
     mfr="Texas Instruments", mpn="INA226AIDGSR")
"""
)


def mouser_part(
    mpn: str,
    maker: str = "Yageo",
    stock_count: Any = "100",
    **fields: Any,
) -> dict[str, Any]:
    """Return a part as Mouser's search answers it (the fields stock reads)."""
    found = {
        "ManufacturerPartNumber": mpn,
        "MouserPartNumber": f"603-{mpn}",
        "Manufacturer": maker,
        "AvailabilityInStock": stock_count,
        "LifecycleStatus": None,
        "LeadTime": "42 Days",
        "Min": "1",
        "Mult": "1",
        "SuggestedReplacement": "",
        "PriceBreaks": [
            {"Quantity": 1, "Price": "$0.10", "Currency": "USD"},
            {"Quantity": 10, "Price": "$0.05", "Currency": "USD"},
            {"Quantity": 100, "Price": "$0.02", "Currency": "USD"},
        ],
    }
    found.update(fields)
    return found


@dataclass
class Shop:
    """Mouser, as far as these tests need it: parts to find, and what was asked."""

    parts: list[dict[str, Any]] = field(default_factory=list)
    asked: list[list[str]] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    error: Exception | dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    def transport(
        self, method: str, url: str, body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        """Answer a search by part number, or fail as ``error`` says."""
        assert body is not None
        self.urls.append(url)
        numbers = json.loads(body)["SearchByPartRequest"]["mouserPartNumber"]
        self.asked.append(numbers.split("|"))
        if isinstance(self.error, Exception):
            raise self.error
        if isinstance(self.error, dict):
            return 200, json.dumps(self.error).encode()
        if self.raw is not None:
            return 200, json.dumps(self.raw).encode()
        found = [
            item
            for item in self.parts
            if item["ManufacturerPartNumber"] in numbers.split("|")
        ]
        answer = {"Errors": [], "SearchResults": {"NumberOfResult": len(found)}}
        answer["SearchResults"]["Parts"] = found
        return 200, json.dumps(answer).encode()


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch) -> Shop:
    """Install a fake Mouser, a recording sleep and an API key."""
    fake = Shop()
    monkeypatch.setattr(mouser, "_transport", fake.transport)
    monkeypatch.setattr(stock, "_sleep", fake.waits.append)
    monkeypatch.setenv("MOUSER_API_KEY", KEY)
    return fake


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make a board project of four parts (three lines) and work in it."""
    root = tmp_path / "my-board"
    write_project(root, DESIGN)
    monkeypatch.chdir(root)
    return root


def run(*args: str) -> Result:
    """Run `pcbkit parts stock ...`."""
    return CliRunner().invoke(cli, ["parts", "stock", *args])


def rows(result: Result) -> dict[str, dict[str, Any]]:
    """Return the JSON rows of a run, by part number."""
    return {row["mpn"]: row for row in json.loads(result.output)}


def stocked(shop: Shop, count: Any = "100") -> None:
    """Make Mouser carry the three parts of DESIGN."""
    shop.parts = [
        mouser_part(RESISTOR, "Yageo", count),
        mouser_part(CAP, "Murata", count),
        dict(INA226["SearchResults"]["Parts"][0], AvailabilityInStock=count),
    ]


def seed(query: mouser.Query, value: Any, fetched: datetime) -> None:
    """Put an answer in the cache as it was fetched at ``fetched``."""
    cache.write(parts.mouser_folder(), query.cache_key, value, fetched)


def everything_query() -> mouser.Query:
    """Return the request the board's three part numbers make, in BOM order."""
    return mouser.part_number([CAP, RESISTOR, "INA226AIDGSR"], exact=True)


# --- the row ----------------------------------------------------------------------


def test_every_field_of_the_plan_is_in_each_row(board: Path, shop: Shop) -> None:
    stocked(shop)
    result = run()
    assert result.exit_code == 0, result.output
    found = json.loads(result.output)
    assert [row["item"] for row in found] == [1, 2, 3]
    for row in found:
        assert list(row) == [
            "item",
            "refs",
            "mpn",
            "mfr",
            "qty_per_board",
            "needed",
            "status",
            "fetched",
            "age_hours",
            "mouser",
            "notes",
        ]
        assert list(row["mouser"]) == [
            "part_number",
            "manufacturer",
            "stock",
            "availability",
            "lifecycle",
            "lead_time",
            "price_breaks",
            "unit_price_at_needed",
            "min",
            "mult",
            "suggested_replacement",
        ]
    resistor = rows(result)[RESISTOR]
    assert resistor["refs"] == ["R1", "R2"]
    assert resistor["mfr"] == "Yageo"
    assert resistor["qty_per_board"] == 2
    assert resistor["status"] == "ok"
    assert resistor["age_hours"] == 0.0
    assert datetime.fromisoformat(resistor["fetched"]).tzinfo is not None
    assert resistor["mouser"] == {
        "part_number": f"603-{RESISTOR}",
        "manufacturer": "Yageo",
        "stock": 100,
        "availability": None,
        "lifecycle": None,
        "lead_time": "42 Days",
        "price_breaks": [
            {"qty": 1, "price": "$0.10", "price_value": 0.1, "currency": "USD"},
            {"qty": 10, "price": "$0.05", "price_value": 0.05, "currency": "USD"},
            {"qty": 100, "price": "$0.02", "price_value": 0.02, "currency": "USD"},
        ],
        "unit_price_at_needed": 0.1,
        "min": 1,
        "mult": 1,
        "suggested_replacement": None,
    }


def test_a_recorded_answer_is_read_as_it_came(board: Path, shop: Shop) -> None:
    """The INA226 answer Mouser really gave: stock "0", no Availability, CAD prices."""
    shop.raw = INA226
    result = run()
    ina = rows(result)["INA226AIDGSR"]
    assert ina["status"] == "short"
    mine = ina["mouser"]
    assert (mine["stock"], mine["availability"], mine["lifecycle"]) == (0, None, None)
    assert mine["lead_time"] == "140 Days"
    assert mine["suggested_replacement"] == "595-INA226AIDGST"
    assert mine["price_breaks"][0] == {
        "qty": 1,
        "price": "$3.96",
        "price_value": 3.96,
        "currency": "CAD",
    }
    assert mine["unit_price_at_needed"] == 3.96


def test_needed_is_the_quantity_per_board_times_qty(board: Path, shop: Shop) -> None:
    stocked(shop)
    found = rows(run("--qty", "7"))
    assert found[RESISTOR]["needed"] == 14
    assert found[CAP]["needed"] == 7
    assert found[CAP]["qty_per_board"] == 1


def test_short_is_set_when_stock_is_below_needed_and_not_when_equal(
    board: Path, shop: Shop
) -> None:
    stocked(shop, "14")
    assert rows(run("--qty", "7"))[RESISTOR]["status"] == "ok"
    result = run("--qty", "8")
    assert rows(result)[RESISTOR]["status"] == "short"
    assert rows(result)[CAP]["status"] == "ok"
    assert result.exit_code == 1


def test_the_price_is_read_at_the_total_needed_not_the_count_per_board(
    board: Path, shop: Shop
) -> None:
    """Two resistors per board, 50 boards: 100 parts, the 100 break, not the 1 break."""
    stocked(shop, "100000")
    mine = rows(run("--qty", "50"))[RESISTOR]
    assert mine["needed"] == 100
    assert mine["mouser"]["unit_price_at_needed"] == 0.02


@pytest.mark.parametrize(
    ("needed", "expected"),
    [(1, 0.1), (9, 0.1), (10, 0.05), (99, 0.05), (100, 0.02), (5000, 0.02)],
)
def test_unit_price_is_the_highest_break_at_or_below_needed(
    needed: int, expected: float
) -> None:
    breaks = stock.price_breaks(mouser_part("X"))
    assert stock.unit_price_at(breaks, needed) == expected


def test_a_need_below_every_break_takes_the_lowest_and_no_breaks_is_null() -> None:
    breaks = [{"qty": 10, "price": "$1.00", "price_value": 1.0, "currency": None}]
    assert stock.unit_price_at(breaks, 3) == 1.0
    assert stock.unit_price_at([], 3) is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("$3.96", 3.96),
        ("CA$0.10", 0.1),
        ("$1,234.50", 1234.5),
        ("$0.00", 0.0),
        ("0,123 EUR", None),
        ("1.234,56 EUR", None),
        ("call us", None),
        ("", None),
        (None, None),
        (3.96, None),
    ],
)
def test_a_price_is_parsed_or_null_never_zero(text: Any, value: float | None) -> None:
    assert stock.parse_price(text) == value


def test_an_unreadable_price_keeps_its_text_and_says_so(
    board: Path, shop: Shop
) -> None:
    stocked(shop)
    shop.parts[1]["PriceBreaks"] = [
        {"Quantity": 1, "Price": "0,12 EUR", "Currency": "EUR"},
        {"Quantity": "x", "Price": "$1.00"},
        "junk",
    ]
    cap = rows(run())[CAP]
    assert cap["mouser"]["price_breaks"] == [
        {"qty": 1, "price": "0,12 EUR", "price_value": None, "currency": "EUR"}
    ]
    assert cap["mouser"]["unit_price_at_needed"] is None
    assert any("price break" in note for note in cap["notes"])


# --- statuses ---------------------------------------------------------------------


def test_each_status_a_line_can_get(board: Path, shop: Shop) -> None:
    shop.parts = [
        mouser_part(RESISTOR, "Yageo", "100", LifecycleStatus="Obsolete"),
        mouser_part(CAP, "Murata", "100"),
        mouser_part(CAP, "Another Maker", "5"),
    ]
    found = rows(run())
    assert found[RESISTOR]["status"] == "not-active"
    assert found["INA226AIDGSR"]["status"] == "not-found"
    # two parts with the same number: the maker on the line narrows it to one
    assert found[CAP]["status"] == "ok"
    assert found[CAP]["mouser"]["manufacturer"] == "Murata"


def test_two_matches_that_the_maker_cannot_tell_apart_are_ambiguous(
    board: Path, shop: Shop
) -> None:
    shop.parts = [
        mouser_part(CAP, "Another Maker", "5"),
        mouser_part(CAP, "A Third Maker", "5", MouserPartNumber="999-X"),
    ]
    mine = rows(run())[CAP]
    assert mine["status"] == "ambiguous"
    assert mine["mouser"] is None
    assert "2 Mouser parts match" in mine["notes"][0]


@pytest.mark.parametrize(
    "lifecycle",
    ["Obsolete", "End of Life", "Not Recommended for New Designs", "Discontinued"],
)
def test_lifecycle_texts_that_mean_not_active(lifecycle: str) -> None:
    assert stock.NOT_ACTIVE.search(lifecycle)


@pytest.mark.parametrize("lifecycle", ["New Product", "Active", ""])
def test_lifecycle_texts_that_do_not(lifecycle: str) -> None:
    assert not stock.NOT_ACTIVE.search(lifecycle)


def test_a_part_with_no_orderable_number_is_no_mpn_and_never_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shop: Shop
) -> None:
    root = tmp_path / "my-board"
    write_project(
        root,
        PRELUDE
        + f"""
R("R1", "7.77k", "A", "B", "x")
part("J1", "Connector:Conn", "hdr", "x:y", {{"1": "A"}}, mpn="pin header 1x3")
part("J2", "Connector:Conn", "hdr", "x:y2", {{"1": "A"}}, mpn="AB")
part("J3", "Connector:Conn", "hdr", "x:y3", {{"1": "A"}}, mpn="")
part("J4", "Connector:Conn", "hdr", "x:y4", {{"1": "A"}}, mpn="ABC|DEF")
part("J5", "Connector:Conn", "hdr", "x:y5", {{"1": "A"}}, mpn="---")
part("C1", "Device:C", "100n", "x:c", {{"1": "A"}}, mpn="{CAP}")
""",
    )
    monkeypatch.chdir(root)
    shop.parts = [mouser_part(CAP, "Murata")]
    result = run()
    statuses = [row["status"] for row in json.loads(result.output)]
    assert sorted(statuses) == ["no-mpn"] * 6 + ["ok"]
    assert shop.asked == [[CAP]]
    assert result.exit_code == 1


def test_an_answer_with_no_part_list_is_unreadable_not_not_found(
    board: Path, shop: Shop
) -> None:
    shop.raw = {"Errors": [], "Results": {"Items": []}}
    result = run()
    assert {row["status"] for row in json.loads(result.output)} == {"unreadable"}
    assert result.exit_code == 1


def test_a_part_with_no_stock_field_is_unreadable_with_null_stock(
    board: Path, shop: Shop
) -> None:
    stocked(shop)
    del shop.parts[1]["AvailabilityInStock"]
    mine = rows(run())[CAP]
    assert mine["status"] == "unreadable"
    assert mine["mouser"]["stock"] is None
    assert any("stock is unknown" in note for note in mine["notes"])


def test_stock_falls_back_to_the_availability_text_and_says_so(
    board: Path, shop: Shop
) -> None:
    stocked(shop)
    shop.parts[1]["AvailabilityInStock"] = ""
    shop.parts[1]["Availability"] = "1,234 In Stock"
    mine = rows(run())[CAP]
    assert (mine["status"], mine["mouser"]["stock"]) == ("ok", 1234)
    assert mine["mouser"]["availability"] == "1,234 In Stock"
    assert any("Availability text" in note for note in mine["notes"])


def test_min_and_mult_that_the_need_does_not_meet_are_noted(
    board: Path, shop: Shop
) -> None:
    stocked(shop)
    shop.parts[1].update(Min="10", Mult="5")
    mine = rows(run("--qty", "3"))[CAP]
    assert mine["mouser"]["min"] == 10
    assert mine["mouser"]["mult"] == 5
    assert any("at least 10" in note for note in mine["notes"])
    assert any("multiples of 5" in note for note in mine["notes"])


def test_exit_0_when_every_line_is_ok(board: Path, shop: Shop) -> None:
    stocked(shop)
    assert run().exit_code == 0


# --- asking Mouser and the cache --------------------------------------------------


def test_offline_with_an_empty_cache_is_not_asked_and_exit_2(
    board: Path, shop: Shop
) -> None:
    result = run("--offline")
    assert result.exit_code == 2
    found = json.loads(result.output)
    assert {row["status"] for row in found} == {"not-asked"}
    assert all(row["fetched"] is None and row["mouser"] is None for row in found)
    assert shop.asked == []


def test_offline_uses_the_cache_at_any_age_and_asks_nothing(
    board: Path, shop: Shop
) -> None:
    stocked(shop)
    old = cache.utc_now() - timedelta(days=90)
    answer = {"SearchResults": {"Parts": shop.parts}}
    seed(everything_query(), answer, old)
    result = run("--offline")
    assert result.exit_code == 0
    assert rows(result)[CAP]["age_hours"] == pytest.approx(90 * 24, abs=0.1)
    assert shop.asked == []


def test_no_key_and_nothing_cached_is_not_asked_and_exit_2(
    board: Path, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MOUSER_API_KEY")
    result = run()
    assert result.exit_code == 2
    found = json.loads(result.output)
    assert {row["status"] for row in found} == {"not-asked"}
    assert "MOUSER_API_KEY is not set" in found[0]["notes"][0]
    assert shop.asked == []


def test_a_fresh_cached_answer_is_used_without_asking(board: Path, shop: Shop) -> None:
    stocked(shop)
    seed(
        everything_query(),
        {"SearchResults": {"Parts": shop.parts}},
        cache.utc_now() - timedelta(hours=2),
    )
    result = run()
    assert result.exit_code == 0
    assert shop.asked == []
    assert rows(result)[CAP]["age_hours"] == pytest.approx(2, abs=0.1)


def test_a_cached_answer_older_than_max_age_is_asked_again_and_dated_now(
    board: Path, shop: Shop
) -> None:
    stocked(shop, "100")
    then = cache.utc_now() - timedelta(hours=30)
    old = [mouser_part(CAP, "Murata", "0")]
    seed(everything_query(), {"SearchResults": {"Parts": old}}, then)
    before = cache.utc_now()
    result = run()
    assert len(shop.asked) == 1
    mine = rows(result)[CAP]
    assert mine["mouser"]["stock"] == 100
    fetched = datetime.fromisoformat(mine["fetched"])
    assert before <= fetched <= cache.utc_now()
    assert fetched > then
    assert mine["age_hours"] < 0.1


def test_max_age_is_in_hours(board: Path, shop: Shop) -> None:
    stocked(shop)
    seed(
        everything_query(),
        {"SearchResults": {"Parts": shop.parts}},
        cache.utc_now() - timedelta(hours=2),
    )
    run("--max-age", "3")
    assert shop.asked == []
    run("--max-age", "1")
    assert len(shop.asked) == 1


def test_when_mouser_cannot_answer_the_stale_cache_is_shown_with_its_date(
    board: Path, shop: Shop
) -> None:
    stocked(shop)
    seed(
        everything_query(),
        {"SearchResults": {"Parts": shop.parts}},
        cache.utc_now() - timedelta(days=3),
    )
    shop.error = OSError("no route")
    result = run()
    assert result.exit_code == 0
    mine = rows(result)[CAP]
    assert mine["age_hours"] == pytest.approx(72, abs=0.1)
    assert "could not be asked" in mine["notes"][0]


def test_when_mouser_fails_and_nothing_is_cached_the_line_is_not_asked(
    board: Path, shop: Shop
) -> None:
    shop.error = OSError("no route")
    result = run()
    assert result.exit_code == 2
    assert {row["status"] for row in json.loads(result.output)} == {"not-asked"}


def test_the_api_key_and_the_request_never_reach_the_output(
    board: Path, shop: Shop
) -> None:
    def leaks(text: str) -> list[str]:
        banned = [KEY, "apiKey", "api.mouser.com", "https://", "User-Agent"]
        return [word for word in banned if word in text]

    stocked(shop)
    assert run().exit_code == 0
    assert any(KEY in url for url in shop.urls)  # the key was really in the request
    assert leaks(run("--offline").output) == []
    first = run("--max-age", "0")
    assert leaks(first.output) == []
    # a refusal that echoes the key back, and a dropped connection
    for failure in (
        {"Errors": [{"Code": "Invalid", "Message": f"key {KEY} was refused"}]},
        OSError(f"connection to https://api.mouser.com/api?apiKey={KEY} dropped"),
    ):
        shop.error = failure
        result = run("--max-age", "0")
        assert result.exit_code in (0, 2)
        assert leaks(result.output) == [], result.output


def test_a_failed_batch_is_not_retried_for_the_rest(
    board: Path, shop: Shop, tmp_path: Path
) -> None:
    shop.error = OSError("no route")
    write_file(
        board / "design.py",
        PRELUDE
        + "".join(
            f'part("U{n}", "x:y", "v", "x:y", {{}}, mpn="PART{n:03d}")\n'
            for n in range(25)
        ),
    )
    run()
    # mouser.send tries one request three times; the other two batches are not asked
    assert {tuple(batch) for batch in shop.asked} == {tuple(shop.asked[0])}
    assert len(shop.asked[0]) == 10


# --- batching ---------------------------------------------------------------------


def test_part_numbers_go_ten_to_a_request_spaced_apart(board: Path, shop: Shop) -> None:
    write_file(
        board / "design.py",
        PRELUDE
        + "".join(
            f'part("U{n}", "x:y", "v", "x:y", {{}}, mpn="PART{n:03d}")\n'
            for n in range(25)
        ),
    )
    shop.parts = [mouser_part(f"PART{n:03d}") for n in range(25)]
    result = run()
    assert [len(batch) for batch in shop.asked] == [10, 10, 5]
    assert shop.waits == [stock.GAP_SECONDS, stock.GAP_SECONDS]
    assert result.exit_code == 0
    again = run()
    assert len(shop.asked) == 3  # every batch cached: no request, no wait
    assert len(shop.waits) == 2
    assert again.exit_code == 0


def test_batches_drop_repeats_and_keep_order() -> None:
    assert stock.batches(["B", "A", "B", "C"], 2) == [["B", "A"], ["C"]]
    assert stock.batches([]) == []


def test_a_line_per_footprint_asks_one_part_number_once(
    board: Path, shop: Shop
) -> None:
    write_file(
        board / "design.py",
        PRELUDE
        + 'part("C1", "Device:C", "1u", "x:c0603", {}, mpn="GRM188R71C104KA01D")\n'
        'part("C2", "Device:C", "1u", "x:c0805", {}, mpn="GRM188R71C104KA01D")\n',
    )
    shop.parts = [mouser_part(CAP, "Murata")]
    result = run()
    assert shop.asked == [[CAP]]
    assert [row["status"] for row in json.loads(result.output)] == ["ok", "ok"]


def test_the_projects_bom_py_decides_what_is_asked(board: Path, shop: Shop) -> None:
    write_file(
        board / "bom.py",
        "MPN_OVERRIDE = {\n"
        f"    '{CAP}': {{'mpn': 'GRM155R71C104KA88D', 'mfr': 'Murata'}}\n"
        "}\n",
    )
    shop.parts = [mouser_part("GRM155R71C104KA88D", "Murata")]
    result = run()
    assert "GRM155R71C104KA88D" in shop.asked[0]
    assert rows(result)["GRM155R71C104KA88D"]["status"] == "ok"


# --- the command ------------------------------------------------------------------


def test_offline_with_max_age_is_a_usage_error(board: Path, shop: Shop) -> None:
    result = run("--offline", "--max-age", "5")
    assert result.exit_code == 2
    assert "--offline" in result.output
    assert shop.asked == []


@pytest.mark.parametrize("args", [["--qty", "0"], ["--max-age", "-1"], ["--qty", "x"]])
def test_bad_options_are_usage_errors(board: Path, args: list[str]) -> None:
    assert run(*args).exit_code == 2


def test_outside_a_project_it_says_so_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shop: Shop
) -> None:
    monkeypatch.chdir(tmp_path)
    result = run()
    assert result.exit_code == 1
    assert "pcbkit.toml" in result.output


def test_exit_code_ranks_not_asked_over_not_ok_over_ok() -> None:
    row = {"status": "ok"}
    assert stock.exit_code([row]) == 0
    assert stock.exit_code([row, {"status": "short"}]) == 1
    assert stock.exit_code([{"status": "short"}, {"status": "not-asked"}]) == 2
    assert stock.exit_code([]) == 0


def test_every_status_is_one_the_docs_name() -> None:
    docs = (Path(__file__).parent.parent.parent / "docs" / "ordering.md").read_text()
    for status in stock.STATUSES:
        assert f"`{status}`" in docs, status


def test_build_row_needs_no_network() -> None:
    """The row is a pure function of the line, the answer and the time."""
    line = BomLine(1, 2, ("C1", "C2"), "Murata", CAP, "100n", "", "C_0603", False)
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    answer = {"SearchResults": {"Parts": [mouser_part(CAP, "Murata", "3")]}}
    entry = cache.Entry(answer, now - timedelta(hours=6))
    row = stock.build_row(line, 2, {CAP: stock.Answer(entry)}, now)
    assert (row["status"], row["needed"], row["age_hours"]) == ("short", 4, 6.0)
    assert row["fetched"] == "2026-11-11T18:00:00+00:00"
