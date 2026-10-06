"""A board of three parts: a header, a resistor and an LED.

The header and the resistor use parts of the board's own: a symbol and a footprint
made by footprints.py, and a footprint vendored in footprints/. The LED is stock.
tests/integration/test_sch_real_kicad.py runs `pcbkit sch` on it with real KiCad.
"""

from __future__ import annotations

from pcbkit.design import LED, R, part

BLOCK_ORDER = ["Indicator"]
NOTES = ["Tiny board: 3V3 in, one resistor, one LED."]

B = "Indicator"
part(
    "J1",
    "tiny:Header_1x02",
    "Supply",
    "tiny:Header_1x02_P2.54mm",
    {"1": "+3V3", "2": "GND"},
    "Acme",
    "HDR-1X02",
    "Supply header: 3V3 / GND",
    B,
)
R("R1", "330", "+3V3", "LED_A", B, fp="tiny:R_Vendored")
LED("D1", "Green", "LED_A", "GND", B, "GRN-0603", "Acme")
