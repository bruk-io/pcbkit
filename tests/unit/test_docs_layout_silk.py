"""Unit tests that keep the layout.py and silk.py docs true.

They cover the part of docs/project-interface.md of that name.

The names in its tables are the ones the code reads, its examples run, and the `api`
listing matches the signatures. (The rest of the document is checked by test_docs.py.)
"""

from __future__ import annotations

import inspect
import re
import types
from typing import Any

import pytest

from pcbkit import place, silk
from tests.unit.test_docs import code_blocks, section_text, table_rows

LAYOUT = "layout.py"
SILK = "silk.py"
API = "The `api` argument of `extra`"


def documented_names(heading: str) -> set[str]:
    """Return every name in the first column of the tables under ``heading``."""
    names: set[str] = set()
    for row in table_rows(heading):
        names |= set(re.findall(r"`(\w+)`", row[0]))
    return names


def run(source: str, name: str) -> types.ModuleType:
    """Execute a doc example as a module called ``name`` and return it."""
    module = types.ModuleType(name)
    exec(source, module.__dict__)
    return module


# --- the names -----------------------------------------------------------------------

LAYOUT_SAMPLES: dict[str, Any] = {
    "W": 40.0,
    "H": 24.0,
    "P": {"R1": (1.0, 2.0, 90.0)},
    "CORNER_R": 3.0,
    "HOLES": [(4.0, 5.0)],
    "outline": lambda board, api: None,
}

SILK_SAMPLES: dict[str, Any] = {
    "LABELS": [("A", 1.0, 2.0, 1.0, 90.0)],
    "CONN_LABELS": {"J1": "TEXT"},
    "HIDE_REF": {"H1"},
    "KEEP_REF": {"A1"},
    "extra": lambda board, api: None,
    "COMPANY": "Acme",
    "COMMENTS": ["one"],
    "DATE": "2026-01-02",
}


def test_the_layout_table_lists_exactly_the_names_the_code_reads() -> None:
    assert documented_names(LAYOUT) == set(LAYOUT_SAMPLES)


def test_each_documented_layout_name_reaches_the_placement() -> None:
    module = types.ModuleType("layout")
    for name, value in LAYOUT_SAMPLES.items():
        setattr(module, name, value)
    layout = place.read_layout(module)
    assert (layout.width, layout.height, layout.corner) == (40.0, 24.0, 3.0)
    assert layout.parts == {"R1": (1.0, 2.0, 90.0)}
    assert layout.holes == [(4.0, 5.0)]
    assert layout.outline is LAYOUT_SAMPLES["outline"]


def test_the_silk_table_lists_exactly_the_names_the_code_reads() -> None:
    assert documented_names(SILK) == set(SILK_SAMPLES)


def test_each_documented_silk_name_reaches_the_pass() -> None:
    module = types.ModuleType("silk")
    for name, value in SILK_SAMPLES.items():
        setattr(module, name, value)
    data = silk.read_silk(module)
    assert data.labels == [("A", 1.0, 2.0, 1.0, 90.0)]
    assert data.conn_labels == {"J1": "TEXT"}
    assert (data.hide_ref, data.keep_ref) == (frozenset({"H1"}), frozenset({"A1"}))
    assert data.extra is SILK_SAMPLES["extra"]
    assert (data.company, data.comments, data.date) == ("Acme", ["one"], "2026-01-02")


# --- the examples ----------------------------------------------------------------


def test_the_layout_example_is_a_valid_layout() -> None:
    layout = place.read_layout(run(code_blocks("python", LAYOUT)[0], "layout"))
    assert (layout.width, layout.height, layout.corner) == (40.0, 24.0, 2.0)
    assert layout.holes == [(4.0, 4.0), (36.0, 20.0)]
    assert layout.parts["R1"] == (26.0, 12.0, 90.0)


def test_the_outline_example_draws_a_closed_shape_with_the_documented_helper() -> None:
    drawn: list[tuple[Any, ...]] = []

    class Api:
        @staticmethod
        def add_line(board: Any, x0: float, y0: float, x1: float, y1: float) -> None:
            drawn.append((x0, y0, x1, y1))

    module = run(code_blocks("python", LAYOUT)[1], "layout")
    module.outline("the board", Api)
    assert len(drawn) == 5
    for here, there in zip(drawn, drawn[1:] + drawn[:1]):
        assert here[2:] == there[:2], "each side starts where the last one ended"


def test_the_silk_example_is_a_valid_silk_py_and_its_hook_runs() -> None:
    module = run(code_blocks("python", SILK)[0], "silk")
    data = silk.read_silk(module)
    assert data.conn_labels == {"J1": "3V3 / GND"}
    assert data.hide_ref == frozenset({"H1", "H2"})
    assert data.comments == ["2 layer, 1.6 mm FR4"]
    assert data.extra is not None


def test_the_silk_example_hook_draws_one_mark_per_pad_through_the_api(
    fake_pcbnew: types.ModuleType,
) -> None:
    class Pad:
        def __init__(self, number: str, x: float, y: float) -> None:
            self.number, self.x, self.y = number, x, y

        def GetNumber(self) -> str:
            return self.number

        def GetX(self) -> int:
            return int((50 + self.x) * 1_000_000)

        def GetY(self) -> int:
            return int((50 + self.y) * 1_000_000)

    placed: list[tuple[str, Any, float]] = []
    warned: list[str] = []

    class Api:
        footprints = {
            "J1": types.SimpleNamespace(
                Pads=lambda: [Pad("1", 10, 5), Pad("2", 10, 7.5)]
            )
        }

        @staticmethod
        def place(text: str, spots: Any, size: float) -> bool:
            placed.append((text, spots, size))
            return text == "1"

        @staticmethod
        def warn(message: str) -> None:
            warned.append(message)

    module = run(code_blocks("python", SILK)[0], "silk")
    module.extra(object(), Api)
    assert placed == [
        ("1", [(12.0, 5.0), (8.0, 5.0)], 0.8),
        ("2", [(12.0, 7.5), (8.0, 7.5)], 0.8),
    ]
    assert warned == ["no room for the mark of J1 pin 2"]


# --- the api listing ---------------------------------------------------------------


class NoBoard:
    """A board with no parts: enough to make a Silk to look at."""

    @staticmethod
    def GetFootprints() -> list[Any]:
        return []


def listing() -> str:
    """Return the python block that lists the api."""
    return code_blocks("python", API)[0]


def test_the_api_listing_names_only_what_the_pass_hands_over(
    fake_pcbnew: types.ModuleType,
) -> None:
    handed_over = silk.Silk(NoBoard(), 40.0, 24.0)
    names = set(re.findall(r"^api\.(\w+)", listing(), flags=re.MULTILINE))
    assert names == {
        "board",
        "footprints",
        "obstacles",
        "width",
        "text",
        "free",
        "place",
        "warn",
    }
    for name in names:
        assert hasattr(handed_over, name), name
    assert hasattr(handed_over, "height")  # listed beside width


@pytest.mark.parametrize("method", ["text", "free", "place"])
def test_each_documented_method_has_the_documented_parameters(method: str) -> None:
    """Match the names and the defaults in the listing to the real signature."""
    line = re.search(rf"^api\.{method}\((.*?)\)", listing(), flags=re.MULTILINE)
    assert line is not None
    documented = [part.strip() for part in line.group(1).split(",")]
    parameters = list(
        inspect.signature(getattr(silk.Silk, method)).parameters.values()
    )[1:]
    assert [d.split("=")[0] for d in documented] == [p.name for p in parameters]
    for text, parameter in zip(documented, parameters):
        if "=" in text:
            assert text.split("=")[1] == repr(parameter.default), (method, text)
        else:
            assert parameter.default is inspect.Parameter.empty, (method, text)


def test_the_module_functions_the_prose_names_exist() -> None:
    prose = section_text(SILK)
    for name in ("add_text", "text_box", "bbox_of", "iloc", "Boxes"):
        assert f"`{name}" in prose, name
        assert hasattr(silk, name), name
