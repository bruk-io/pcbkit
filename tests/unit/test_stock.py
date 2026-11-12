"""Unit tests for pcbkit.stock and `pcbkit parts stock`.

Mouser is a fake (`mouser._transport` is replaced) and so is the clock's sleep; HOME
is the fake machine's, so the cache is a temporary folder. The recorded answer in
tests/fixtures/mouser (INA226, a public part) is used once as it came; the rest are
made-up answers of the same shape for generic public part numbers.
"""

from __future__ import annotations

import http.client
import json
import time
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
    status: int = 200

    def transport(
        self, method: str, url: str, body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        """Answer a search by part number, or fail as ``error`` says."""
        assert body is not None
        self.urls.append(url)
        numbers = json.loads(body)["SearchByPartRequest"]["mouserPartNumber"]
        self.asked.append(numbers.split("|"))
        if self.status != 200:
            return self.status, b"{}"
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
            "needed_total",
            "order_qty",
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
            "unit_price_at_order",
            "min",
            "mult",
            "suggested_replacement",
        ]
    resistor = rows(result)[RESISTOR]
    assert resistor["refs"] == ["R1", "R2"]
    assert resistor["mfr"] == "Yageo"
    assert resistor["qty_per_board"] == 2
    assert (resistor["needed"], resistor["needed_total"]) == (2, 2)
    assert resistor["order_qty"] == 2
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
        "unit_price_at_order": 0.1,
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
    assert mine["unit_price_at_order"] == 3.96


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
    assert mine["mouser"]["unit_price_at_order"] == 0.02


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
        ("$1e3", None),
        ("$1.50 per 100", None),
        ("-$1.00", None),
        ("$1.2.3", None),
        ("US$ 3.96", 3.96),
        ("3.96 USD", 3.96),
        ("$1,23", None),
        ("$,123", None),
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
    assert cap["mouser"]["unit_price_at_order"] is None
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


def status_of(part: dict[str, Any], needed: int = 1, maker: str = "") -> str:
    """Return the status a one-line BOM gets for ``part`` from a recorded answer."""
    line = BomLine(1, needed, ("C1",), maker, CAP, "100n", "", "C_0603", False)
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    entry = cache.Entry({"SearchResults": {"Parts": [part]}}, now)
    return stock.build_row(line, 1, {CAP: stock.Answer(entry)}, now)["status"]


@pytest.mark.parametrize(
    "lifecycle",
    [
        "Obsolete",
        "End of Life",
        "Not Recommended for New Designs",
        "Discontinued",
        "Inactive",
        "Not For New Designs",
        "Phase Out",
        "Last Time Buy",
        " obsolete ",
    ],
)
def test_lifecycle_texts_that_mean_not_active(lifecycle: str) -> None:
    assert status_of(mouser_part(CAP, LifecycleStatus=lifecycle)) == "not-active"


@pytest.mark.parametrize(
    "lifecycle", ["Nonstock", "Factory Special Order", "Limited Availability", "?"]
)
def test_a_lifecycle_text_nobody_listed_is_unreadable_and_quoted(
    lifecycle: str,
) -> None:
    """The review's H2 inputs: the deny-list let every one of these through as ok."""
    line = BomLine(1, 1, ("C1",), "", CAP, "100n", "", "C_0603", False)
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    part = mouser_part(CAP, LifecycleStatus=lifecycle)
    entry = cache.Entry({"SearchResults": {"Parts": [part]}}, now)
    row = stock.build_row(line, 1, {CAP: stock.Answer(entry)}, now)
    assert row["status"] == "unreadable"
    assert any(repr(lifecycle) in note or lifecycle in note for note in row["notes"])


@pytest.mark.parametrize(
    "lifecycle",
    [None, "", "  ", "New Product", "New at Mouser", "Active", "ACTIVE", "active "],
)
def test_lifecycle_texts_that_are_fine(lifecycle: Any) -> None:
    assert status_of(mouser_part(CAP, LifecycleStatus=lifecycle)) == "ok"


@pytest.mark.parametrize("lifecycle", [5, ["Active"], {"a": 1}, True])
def test_a_lifecycle_that_is_not_text_is_unreadable(lifecycle: Any) -> None:
    assert status_of(mouser_part(CAP, LifecycleStatus=lifecycle)) == "unreadable"


@pytest.mark.parametrize(
    ("flag", "expected"),
    [(True, "not-active"), (False, "ok"), (None, "ok"), ("yes", "unreadable")],
)
def test_is_discontinued_is_read_when_present(flag: Any, expected: str) -> None:
    assert status_of(mouser_part(CAP, IsDiscontinued=flag)) == expected


def test_a_discontinued_flag_and_an_active_text_is_not_active() -> None:
    part = mouser_part(CAP, LifecycleStatus="Active", IsDiscontinued=True)
    assert status_of(part) == "not-active"


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


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,234", 1234),
        ("12", 12),
        (" 5 ", 5),
        ("12,34", None),
        ("1,2,3", None),
        ("1234,567", None),
        (",123", None),
        ("", None),
        (True, None),
        (12.5, None),
        (7.0, 7),
    ],
)
def test_a_whole_number_has_commas_only_between_thousands(
    text: Any, value: int | None
) -> None:
    assert stock._whole(text) == value


def test_the_minimum_above_stock_makes_the_line_short(board: Path, shop: Shop) -> None:
    """The review's B3 input: Min 100, stock 50, 10 needed."""
    stocked(shop, "50")
    shop.parts[1].update(Min="100", Mult="1")
    mine = rows(run("--qty", "10"))[CAP]
    assert mine["status"] == "short"
    assert (mine["needed"], mine["needed_total"], mine["order_qty"]) == (10, 10, 100)
    assert mine["mouser"]["unit_price_at_order"] == 0.02  # the 100 break
    assert any("at least 100" in note for note in mine["notes"])


def test_the_multiple_above_stock_makes_the_line_short(board: Path, shop: Shop) -> None:
    """The review's B3 input: Mult 25, stock 20, 10 needed."""
    stocked(shop, "20")
    shop.parts[1].update(Min="1", Mult="25")
    mine = rows(run("--qty", "10"))[CAP]
    assert mine["status"] == "short"
    assert mine["order_qty"] == 25
    assert any("multiples of 25" in note for note in mine["notes"])


@pytest.mark.parametrize(
    ("minimum", "multiple", "needed", "order"),
    [
        ("1", "1", 7, 7),
        ("10", "1", 3, 10),
        ("10", "5", 3, 10),
        ("1", "5", 7, 10),
        ("12", "5", 3, 15),
        ("1", "5", 10, 10),
        ("1", "0001", 7, 7),
    ],
)
def test_the_order_is_the_need_raised_to_the_minimum_then_to_a_multiple(
    minimum: str, multiple: str, needed: int, order: int
) -> None:
    part = mouser_part(CAP, stock_count="1000", Min=minimum, Mult=multiple)
    line = BomLine(1, needed, ("C1",), "", CAP, "100n", "", "C_0603", False)
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    entry = cache.Entry({"SearchResults": {"Parts": [part]}}, now)
    row = stock.build_row(line, 1, {CAP: stock.Answer(entry)}, now)
    assert row["order_qty"] == order
    assert row["status"] == "ok"


@pytest.mark.parametrize("field", ["Min", "Mult"])
@pytest.mark.parametrize("bad", ["many", "0", "", None, "1.5", "-5", [1]])
def test_a_minimum_or_multiple_that_cannot_be_read_is_unreadable(
    field: str, bad: Any
) -> None:
    assert status_of(mouser_part(CAP, **{field: bad})) == "unreadable"


def test_the_same_part_on_two_lines_is_judged_on_the_total(
    board: Path, shop: Shop
) -> None:
    """The review's B2 input: two lines of one part number, 1 each, stock 1."""
    write_file(
        board / "design.py",
        PRELUDE + f'part("C1", "Device:C", "1u", "x:c0603", {{}}, mpn="{CAP}")\n'
        f'part("C2", "Device:C", "1u", "x:c0805", {{}}, mpn="{CAP}")\n',
    )
    shop.parts = [mouser_part(CAP, "Murata", "1")]
    result = run()
    found = json.loads(result.output)
    assert [row["status"] for row in found] == ["short", "short"]
    assert [row["needed"] for row in found] == [1, 1]
    assert [row["needed_total"] for row in found] == [2, 2]
    assert result.exit_code == 1


def test_the_total_over_lines_and_boards_picks_the_price_break(
    board: Path, shop: Shop
) -> None:
    write_file(
        board / "design.py",
        PRELUDE + f'part("C1", "Device:C", "1u", "x:c0603", {{}}, mpn="{CAP}")\n'
        f'part("C2", "Device:C", "1u", "x:c0805", {{}}, mpn="{CAP}")\n'
        f'part("C3", "Device:C", "1u", "x:c0402", {{}}, mpn="{CAP.lower()}")\n',
    )
    shop.parts = [mouser_part(CAP, "Murata", "1000")]
    found = json.loads(run("--qty", "4").output)
    assert [row["needed_total"] for row in found] == [12, 12, 12]
    assert {row["mouser"]["unit_price_at_order"] for row in found} == {0.05}


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


def seed_stale(
    shop: Shop, age: timedelta = timedelta(days=200), count: Any = "5000"
) -> None:
    """Cache a good answer (stock ``count``) fetched ``age`` ago."""
    stocked(shop, count)
    seed(
        everything_query(),
        {"SearchResults": {"Parts": shop.parts}},
        cache.utc_now() - age,
    )


def test_no_key_and_a_stale_cache_is_stale_and_exit_2_not_ok(
    board: Path, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's B1 input: no key, a 200-day-old answer with stock 5000."""
    seed_stale(shop)
    monkeypatch.delenv("MOUSER_API_KEY")
    result = run()
    assert result.exit_code == 2
    mine = rows(result)[CAP]
    assert mine["status"] == "stale"
    assert mine["mouser"]["stock"] == 5000
    assert mine["age_hours"] == pytest.approx(200 * 24, abs=0.1)
    assert any("MOUSER_API_KEY is not set" in note for note in mine["notes"])
    assert any("older than" in note for note in mine["notes"])
    assert {row["status"] for row in json.loads(result.output)} == {"stale"}


@pytest.mark.parametrize(
    "failure",
    [OSError("no route"), http.client.IncompleteRead(b"x"), 429, 503],
    ids=["down", "incomplete-read", "rate-limited", "server-error"],
)
def test_when_mouser_cannot_answer_the_stale_cache_is_stale_not_ok(
    board: Path, shop: Shop, failure: Any
) -> None:
    seed_stale(shop, timedelta(days=3))
    if isinstance(failure, int):
        shop.status = failure
    else:
        shop.error = failure
    result = run()
    assert result.exit_code == 2
    mine = rows(result)[CAP]
    assert mine["status"] == "stale"
    assert mine["age_hours"] == pytest.approx(72, abs=0.1)
    assert mine["mouser"]["stock"] == 5000
    assert "Mouser could not be asked" in mine["notes"][0]


def test_a_cache_dated_in_the_future_is_stale_not_age_zero(
    board: Path, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_stale(shop, timedelta(days=-5))
    monkeypatch.delenv("MOUSER_API_KEY")
    mine = rows(run())[CAP]
    assert mine["status"] == "stale"
    assert mine["age_hours"] < 0
    assert any("future" in note for note in mine["notes"])
    # and with Mouser reachable it is asked again, not trusted
    monkeypatch.setenv("MOUSER_API_KEY", KEY)
    stocked(shop, "7")
    result = run()
    assert len(shop.asked) == 1
    assert rows(result)[CAP]["mouser"]["stock"] == 7


def test_offline_judges_an_old_answer_normally_and_shows_its_age(
    board: Path, shop: Shop
) -> None:
    seed_stale(shop)
    result = run("--offline")
    assert result.exit_code == 0
    assert rows(result)[CAP]["status"] == "ok"
    assert rows(result)[CAP]["age_hours"] == pytest.approx(200 * 24, abs=0.1)


def test_the_fallback_note_says_what_happened_in_whole_sentences(
    board: Path, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_stale(shop)
    monkeypatch.delenv("MOUSER_API_KEY")
    notes = rows(run())[CAP]["notes"]
    assert notes[0].startswith("Mouser could not be asked: MOUSER_API_KEY is not set")
    assert ".)" not in notes[0] and "): this" not in notes[0]
    assert "This is the cached answer of any age." in notes[0]
    assert "Nothing is cached" not in notes[0]


def test_when_mouser_fails_and_nothing_is_cached_the_line_is_not_asked(
    board: Path, shop: Shop
) -> None:
    shop.error = OSError("no route")
    result = run()
    assert result.exit_code == 2
    found = json.loads(result.output)
    assert {row["status"] for row in found} == {"not-asked"}
    assert "Nothing is cached" in found[0]["notes"][0]
    assert "Mouser could not be asked" in found[0]["notes"][0]


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


# --- choosing the part -------------------------------------------------------------


def one_line_row(
    found: list[dict[str, Any]], mpn: str = CAP, maker: str = ""
) -> dict[str, Any]:
    """Return the row of a one-line BOM against ``found`` as Mouser's parts."""
    line = BomLine(1, 1, ("C1",), maker, mpn, "100n", "", "C_0603", False)
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    entry = cache.Entry({"SearchResults": {"Parts": found}}, now)
    return stock.build_row(line, 1, {mpn: stock.Answer(entry)}, now)


def test_a_short_maker_name_is_never_a_prefix_match() -> None:
    """The review's B4 input: line "TI", Mouser has Texas Instruments and Tiny Co."""
    found = [
        mouser_part(CAP, "Texas Instruments"),
        mouser_part(CAP, "Tiny Co", MouserPartNumber="999-X"),
    ]
    row = one_line_row(found, maker="TI")
    assert row["status"] == "ambiguous"
    assert row["mouser"] is None


@pytest.mark.parametrize(
    ("line_maker", "mouser_maker"),
    [
        ("Texas Instruments", "Texas Instruments Inc."),
        ("ON Semiconductor", "onsemi"),
        ("Murata", "murata"),
        ("Yageo", "YAGEO"),
        ("Diodes Inc", "Diodes Incorporated"),
        ("Murata", "Murata Electronics"),
    ],
)
def test_the_maker_is_matched_by_whole_name_or_known_alias(
    line_maker: str, mouser_maker: str
) -> None:
    other = mouser_part(CAP, "Tiny Co", MouserPartNumber="999-X")
    row = one_line_row([mouser_part(CAP, mouser_maker), other], maker=line_maker)
    assert row["status"] == "ok"
    assert row["mouser"]["manufacturer"] == mouser_maker


def test_a_maker_that_only_starts_the_same_in_characters_is_another_maker() -> None:
    found = [mouser_part(CAP, "Murataland")]
    assert one_line_row(found, maker="Murata")["status"] == "ambiguous"


def test_the_only_match_from_another_maker_is_ambiguous_naming_both() -> None:
    """The review's H1 input: line Murata, the only match is by Yageo."""
    row = one_line_row([mouser_part(CAP, "Yageo")], maker="Murata")
    assert row["status"] == "ambiguous"
    assert row["mouser"] is None
    assert any("Murata" in note and "Yageo" in note for note in row["notes"])


def test_a_line_with_no_maker_is_judged_on_the_number_alone() -> None:
    assert one_line_row([mouser_part(CAP, "Yageo")], maker="  ")["status"] == "ok"


def test_the_only_match_with_no_readable_maker_is_ambiguous_when_the_line_has_one() -> (
    None
):
    row = one_line_row([mouser_part(CAP, None)], maker="Murata")
    assert row["status"] == "ambiguous"
    assert any("Murata" in note for note in row["notes"])


def test_two_matches_of_the_same_maker_are_ambiguous_not_the_first() -> None:
    found = [
        mouser_part(CAP, "Murata", MouserPartNumber="81-A"),
        mouser_part(CAP, "Murata", MouserPartNumber="81-B"),
    ]
    row = one_line_row(found, maker="Murata")
    assert row["status"] == "ambiguous"
    assert "81-A" in row["notes"][0] and "81-B" in row["notes"][0]


def test_a_part_number_that_only_starts_the_same_is_not_a_match() -> None:
    row = one_line_row([mouser_part("LM358DR", "Texas Instruments")], mpn="LM358")
    assert row["status"] == "not-found"


def test_mouser_s_own_part_number_matches() -> None:
    found = [mouser_part("LM358DR", "Texas Instruments", MouserPartNumber="595-LM358")]
    row = one_line_row(found, mpn="595-LM358")
    assert row["status"] == "ok"
    assert row["mouser"]["part_number"] == "595-LM358"


def test_parts_with_no_part_number_make_the_answer_unreadable_not_not_found() -> None:
    row = one_line_row([{}])
    assert row["status"] == "unreadable"
    assert row["notes"]
    other = one_line_row([{"ManufacturerPartNumber": "OTHER"}, {"Manufacturer": "x"}])
    assert other["status"] == "unreadable"


def test_an_answer_with_a_part_that_is_not_an_object_is_unreadable() -> None:
    line = BomLine(1, 1, ("C1",), "", CAP, "100n", "", "C_0603", False)
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    entry = cache.Entry({"SearchResults": {"Parts": ["junk"]}}, now)
    row = stock.build_row(line, 1, {CAP: stock.Answer(entry)}, now)
    assert row["status"] == "unreadable"


@pytest.mark.parametrize("breaks", [None, "text", {"a": 1}, []])
def test_price_breaks_that_are_not_a_list_say_so(breaks: Any) -> None:
    row = one_line_row([mouser_part(CAP, PriceBreaks=breaks)])
    assert row["mouser"]["price_breaks"] == []
    assert row["mouser"]["unit_price_at_order"] is None
    assert any("PriceBreaks" in note for note in row["notes"])


def test_a_dropped_price_break_is_noted() -> None:
    row = one_line_row([mouser_part(CAP, PriceBreaks=[{"Quantity": "x"}, "junk"])])
    assert any("PriceBreaks" in note for note in row["notes"])


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


@pytest.mark.parametrize("hours", ["inf", "-inf", "nan", "1e12", "1e308", "NaN"])
def test_an_absurd_max_age_is_a_usage_error_not_a_traceback(
    board: Path, shop: Shop, hours: str
) -> None:
    result = run("--max-age", hours)
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert shop.asked == []


def test_a_large_but_sane_max_age_is_fine(board: Path, shop: Shop) -> None:
    stocked(shop)
    assert run("--max-age", "100000").exit_code == 0


def test_a_failed_cache_write_still_uses_the_fresh_answer_and_says_so(
    board: Path, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    stocked(shop)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError(f"disk full writing {KEY}")

    monkeypatch.setattr(cache, "write", refuse)
    result = run()
    assert result.exit_code == 0, result.output
    mine = rows(result)[CAP]
    assert mine["status"] == "ok"
    assert mine["age_hours"] == 0.0
    assert any("could not be cached" in note for note in mine["notes"])
    assert not any("could not be asked" in note for note in mine["notes"])
    assert KEY not in result.output


@pytest.mark.parametrize(
    "failure",
    [
        http.client.IncompleteRead(b"abc"),
        http.client.BadStatusLine("HTTP/9 " + KEY),
        http.client.RemoteDisconnected("closed"),
    ],
)
def test_an_http_client_error_is_could_not_be_asked_by_type_only(
    board: Path, shop: Shop, failure: Exception
) -> None:
    shop.error = failure
    result = run()
    assert result.exit_code == 2
    found = json.loads(result.output)
    assert {row["status"] for row in found} == {"not-asked"}
    note = found[0]["notes"][0]
    assert type(failure).__name__ in note
    assert KEY not in result.output
    assert "abc" not in result.output


def test_the_scrubber_hides_the_key_in_any_case_and_url_encoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOUSER_API_KEY", "Ab/Cd+12 34")
    text = "x Ab/Cd+12 34 | AB/CD+12 34 | Ab%2FCd%2B12%2034 | ab%2fcd%2b12%2034 | y"
    scrubbed = stock._scrub(text)
    assert scrubbed == "x *** | *** | *** | *** | y"
    assert stock._scrub("nothing here") == "nothing here"


def test_no_unit_test_can_really_sleep_in_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(seconds: float) -> None:
        raise AssertionError("a unit test really slept")

    monkeypatch.setattr(time, "sleep", refuse)
    stock._sleep(1000)


def test_an_empty_bom_is_an_empty_list_and_exit_0() -> None:
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    assert stock.build_rows([], 1, {}, now) == []
    assert stock.exit_code([]) == 0


def test_exit_code_ranks_not_asked_over_not_ok_over_ok() -> None:
    row = {"status": "ok"}
    assert stock.exit_code([row]) == 0
    assert stock.exit_code([row, {"status": "short"}]) == 1
    assert stock.exit_code([{"status": "short"}, {"status": "not-asked"}]) == 2
    assert stock.exit_code([{"status": "short"}, {"status": "stale"}]) == 2
    assert stock.exit_code([row, {"status": "stale"}]) == 2
    for status in ("not-active", "not-found", "ambiguous", "no-mpn", "unreadable"):
        assert stock.exit_code([row, {"status": status}]) == 1
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


# --- the second review (N1 to N8) ---------------------------------------------------


def _line(item: int, qty: int, mpn: str, mfr: str = "", ref: str = "U1") -> BomLine:
    return BomLine(item, qty, (ref,), mfr, mpn, "x", "", "SOIC", False)


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _answers(found: list[dict[str, Any]], *numbers: str) -> dict[str, Any]:
    entry = cache.Entry({"SearchResults": {"Parts": found}}, NOW)
    return {number: stock.Answer(entry) for number in numbers}


def test_one_part_bought_by_two_numbers_is_judged_on_both_through_the_cli(
    board: Path, shop: Shop
) -> None:
    """N1: U1 by the maker's number, U2 by Mouser's; one Mouser part, stock 1."""
    write_file(
        board / "design.py",
        PRELUDE
        + 'part("U1", "Amp:LM358", "LM358", "x:soic8", {}, mfr="Texas Instruments",'
        ' mpn="LM358DR")\n'
        'part("U2", "Amp:LM358", "LM358", "x:soic8", {}, mfr="Texas Instruments",'
        ' mpn="595-LM358DR")\n',
    )
    shop.parts = [
        mouser_part("LM358DR", "Texas Instruments", "1", MouserPartNumber="595-LM358DR")
    ]
    result = run()
    found = json.loads(result.output)
    assert [row["mpn"] for row in found] == ["LM358DR", "595-LM358DR"]
    assert [row["status"] for row in found] == ["short", "short"]
    assert [row["needed_total"] for row in found] == [2, 2]
    assert result.exit_code == 1


def test_one_part_under_two_texts_is_summed_without_a_mouser_number() -> None:
    """The same part with no MouserPartNumber is keyed on its number and maker."""
    found = [mouser_part("LM358DR", "Texas Instruments", "1", MouserPartNumber=None)]
    lines = [_line(1, 1, "LM358DR", "Texas Instruments"), _line(2, 1, "lm-358dr")]
    got = stock.build_rows(lines, 1, _answers(found, "LM358DR", "lm-358dr"), NOW)
    assert [row["needed_total"] for row in got] == [2, 2]
    assert [row["status"] for row in got] == ["short", "short"]


def test_the_same_number_by_two_makers_is_two_parts_not_one_total() -> None:
    found = [
        mouser_part(CAP, "Murata", "5", MouserPartNumber="81-A"),
        mouser_part(CAP, "Another Maker", "5", MouserPartNumber="999-B"),
    ]
    lines = [_line(1, 3, CAP, "Murata"), _line(2, 4, CAP, "Another Maker")]
    got = stock.build_rows(lines, 1, _answers(found, CAP), NOW)
    assert [row["needed_total"] for row in got] == [3, 4]
    assert [row["status"] for row in got] == ["ok", "ok"]


def test_no_mpn_lines_have_no_total() -> None:
    lines = [_line(1, 3, "", ref="R1"), _line(2, 4, "", ref="R2")]
    got = stock.build_rows(lines, 1, {}, NOW)
    assert [row["status"] for row in got] == ["no-mpn", "no-mpn"]
    assert [row["needed_total"] for row in got] == [None, None]
    assert [row["needed"] for row in got] == [3, 4]


def test_lines_that_matched_no_part_are_summed_by_their_number() -> None:
    lines = [_line(1, 3, "ZZZ-1"), _line(2, 4, "zzz1"), _line(3, 5, "OTHER-2")]
    answers = _answers([mouser_part("QQQ", "x")], "ZZZ-1", "zzz1", "OTHER-2")
    got = stock.build_rows(lines, 1, answers, NOW)
    assert [row["status"] for row in got] == ["not-found"] * 3
    assert [row["needed_total"] for row in got] == [7, 7, 5]
    not_asked = stock.build_rows(lines[:2], 1, {}, NOW)
    assert [row["status"] for row in not_asked] == ["not-asked"] * 2
    assert [row["needed_total"] for row in not_asked] == [7, 7]


@pytest.mark.parametrize(
    ("line_maker", "mouser_maker"),
    [
        ("Microchip", "Microchip Technology"),
        ("Espressif", "Espressif Systems"),
        ("Vishay", "Vishay Semiconductors"),
        ("Vishay", "Vishay / Dale"),
        ("Infineon", "Infineon Technologies"),
        ("Bosch", "Bosch Sensortec"),
        ("NXP", "NXP Semiconductors"),
        ("JST", "JST Commercial"),
        ("Sullins", "Sullins Connector Solutions"),
        ("Panasonic", "Panasonic Industry"),
        ("Monolithic Power Systems", "Monolithic Power Systems (MPS)"),
        ("Alpha & Omega", "Alpha & Omega Semiconductor"),
        ("Analog Devices", "Analog Devices / Maxim Integrated"),
        ("Microchip Technology", "Microchip"),
    ],
)
def test_a_maker_is_also_the_leading_whole_words_of_a_longer_name(
    line_maker: str, mouser_maker: str
) -> None:
    assert stock.same_maker(line_maker, mouser_maker)
    assert stock.same_maker(mouser_maker, line_maker)
    row = one_line_row([mouser_part(CAP, mouser_maker)], maker=line_maker)
    assert row["status"] == "ok"


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("TI", "Tiny Co"),
        ("Murata", "Murataland"),
        ("Vishay", "Vishayx Semiconductors"),
        ("Power Systems", "Monolithic Power Systems"),
        ("Alpha", "Omega Alpha"),
        ("", "Murata"),
    ],
)
def test_a_maker_is_never_a_character_prefix_or_a_trailing_part(
    one: str, other: str
) -> None:
    assert not stock.same_maker(one, other)
    assert not stock.same_maker(other, one)


def test_two_parts_that_both_pass_the_looser_maker_test_are_ambiguous() -> None:
    found = [
        mouser_part(CAP, "Vishay Semiconductors", MouserPartNumber="78-A"),
        mouser_part(CAP, "Vishay / Dale", MouserPartNumber="71-B"),
    ]
    row = one_line_row(found, maker="Vishay")
    assert row["status"] == "ambiguous"
    assert "78-A" in row["notes"][0] and "71-B" in row["notes"][0]


def test_a_stale_short_line_is_stale_and_exit_2_through_the_cli(
    board: Path, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N3: it would have been `short`, and exit 1, if it were fresh."""
    seed_stale(shop, count="0")
    monkeypatch.delenv("MOUSER_API_KEY")
    result = run()
    assert result.exit_code == 2
    assert {row["status"] for row in json.loads(result.output)} == {"stale"}
    assert any("judged short" in n for n in rows(result)[CAP]["notes"])


def test_a_stale_not_found_line_is_stale_and_exit_2() -> None:
    entry = cache.Entry(
        {"SearchResults": {"Parts": [mouser_part("QQQ", "x")]}},
        NOW - timedelta(days=9),
    )
    got = stock.build_rows(
        [_line(1, 1, "ZZZ-1")],
        1,
        {"ZZZ-1": stock.Answer(entry)},
        NOW,
        max_age=timedelta(hours=24),
    )
    assert got[0]["status"] == "stale"
    assert any("judged not-found" in note for note in got[0]["notes"])
    assert stock.exit_code(got) == 2


def test_status_precedence_is_pinned() -> None:
    """Not active, then unreadable, then short, then ok; and see the docs."""
    bad_mult = {"Mult": "x"}
    # not-active outranks unreadable
    assert status_of(mouser_part(CAP, LifecycleStatus="Obsolete", **bad_mult)) == (
        "not-active"
    )
    assert status_of(mouser_part(CAP, IsDiscontinued=True, Min="x")) == "not-active"
    # not-active outranks short
    assert status_of(mouser_part(CAP, "x", "0", LifecycleStatus="Obsolete")) == (
        "not-active"
    )
    # unreadable outranks short: stock 0 but the multiple cannot be read
    assert status_of(mouser_part(CAP, "x", "0", **bad_mult)) == "unreadable"
    assert status_of(mouser_part(CAP, "x", "0", LifecycleStatus="Weird")) == (
        "unreadable"
    )
    assert status_of(mouser_part(CAP, "x", "0")) == "short"
    assert status_of(mouser_part(CAP, "x", "5")) == "ok"
    # stale outranks every judged status, offline or not asked aside
    old = cache.Entry(
        {
            "SearchResults": {
                "Parts": [mouser_part(CAP, "x", LifecycleStatus="Obsolete")]
            }
        },
        NOW - timedelta(days=9),
    )
    line = _line(1, 1, CAP)
    stale = stock.build_row(
        line, 1, {CAP: stock.Answer(old)}, NOW, max_age=timedelta(hours=1)
    )
    assert stale["status"] == "stale"
    # no-mpn outranks everything: nothing is asked
    nothing = stock.build_row(_line(1, 1, ""), 1, {"": stock.Answer(old)}, NOW)
    assert nothing["status"] == "no-mpn"
