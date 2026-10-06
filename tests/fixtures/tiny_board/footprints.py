"""The tiny board's own symbol and footprint, written as S-expression nodes."""

from __future__ import annotations

from pcbkit.kicad.sexp import q

LIB = "tiny"

FONT = ["font", ["size", 1.27, 1.27]]
SMALL = ["effects", ["font", ["size", 0.8, 0.8], ["thickness", 0.12]]]


def _property(name: str, value: str, at: list, hide: bool = False) -> list:
    effects = ["effects", FONT] + ([["hide", "yes"]] if hide else [])
    return ["property", q(name), q(value), ["at"] + at, effects]


def _pin(number: str, y: float) -> list:
    return [
        "pin",
        "passive",
        "line",
        ["at", -5.08, y, 0],
        ["length", 3.81],
        ["name", q(f"Pin_{number}"), ["effects", FONT]],
        ["number", q(number), ["effects", FONT]],
    ]


def symbols() -> list:
    """Return the board's one symbol: a two-pin header."""
    return [
        [
            "symbol",
            q("Header_1x02"),
            ["pin_names", ["offset", 1.016], ["hide", "yes"]],
            ["exclude_from_sim", "no"],
            ["in_bom", "yes"],
            ["on_board", "yes"],
            _property("Reference", "J", [0, 2.54, 0]),
            _property("Value", "Header_1x02", [0, -5.08, 0]),
            _property("Footprint", "", [0, 0, 0], True),
            _property("Datasheet", "~", [0, 0, 0], True),
            _property("Description", "Two-pin header", [0, 0, 0], True),
            [
                "symbol",
                q("Header_1x02_1_1"),
                [
                    "rectangle",
                    ["start", -1.27, 1.27],
                    ["end", 1.27, -3.81],
                    ["stroke", ["width", 0.254], ["type", "default"]],
                    ["fill", ["type", "background"]],
                ],
                _pin("1", 0),
                _pin("2", -2.54),
            ],
            ["embedded_fonts", "no"],
        ]
    ]


def _uuid(n: int) -> list:
    return ["uuid", q(f"00000000-0000-4000-8000-{n:012d}")]


def footprints() -> dict:
    """Return the board's one generated footprint: a 1x2 through-hole header."""
    pad = ["layers", q("*.Cu"), q("*.Mask")]
    node = [
        "footprint",
        q("Header_1x02_P2.54mm"),
        ["version", 20241229],
        ["generator", q("pcbkit")],
        ["generator_version", q("9.0")],
        ["layer", q("F.Cu")],
        ["descr", q("1x02 2.54 mm pin header")],
        ["tags", q("header 1x02")],
        [
            "property",
            q("Reference"),
            q("REF**"),
            ["at", 0, -2.2, 90],
            ["layer", q("F.Fab")],
            _uuid(1),
            SMALL,
        ],
        [
            "property",
            q("Value"),
            q("Header_1x02"),
            ["at", 0, 3.5, 90],
            ["layer", q("F.Fab")],
            _uuid(2),
            SMALL,
        ],
        [
            "property",
            q("Footprint"),
            q(""),
            ["at", 0, 0, 0],
            ["layer", q("F.Fab")],
            ["hide", "yes"],
            _uuid(3),
            SMALL,
        ],
        [
            "property",
            q("Datasheet"),
            q(""),
            ["at", 0, 0, 0],
            ["layer", q("F.Fab")],
            ["hide", "yes"],
            _uuid(4),
            SMALL,
        ],
        [
            "property",
            q("Description"),
            q(""),
            ["at", 0, 0, 0],
            ["layer", q("F.Fab")],
            ["hide", "yes"],
            _uuid(5),
            SMALL,
        ],
        ["attr", "through_hole"],
        [
            "fp_rect",
            ["start", -1.25, -1.27],
            ["end", 1.25, 3.81],
            ["stroke", ["width", 0.05], ["type", "solid"]],
            ["fill", "no"],
            ["layer", q("F.CrtYd")],
            _uuid(6),
        ],
        [
            "pad",
            q("1"),
            "thru_hole",
            "rect",
            ["at", 0, 0],
            ["size", 1.7, 1.7],
            ["drill", 1.0],
            pad,
            ["remove_unused_layers", "no"],
            _uuid(7),
        ],
        [
            "pad",
            q("2"),
            "thru_hole",
            "circle",
            ["at", 0, 2.54],
            ["size", 1.7, 1.7],
            ["drill", 1.0],
            pad,
            ["remove_unused_layers", "no"],
            _uuid(8),
        ],
        ["embedded_fonts", "no"],
    ]
    return {"Header_1x02_P2.54mm": node}
