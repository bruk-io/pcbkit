"""A small design that exercises each path of the schematic generator.

Two blocks; a connector with a pin left unconnected; a resistor, an LED and a resistor
whose symbol `extends` another; a part from the project's own library; power flags and
a mounting hole, whose pins are left alone when they have no net.
tests/unit/test_sch.py generates the schematic and compares it with expected.kicad_sch.
"""

from __future__ import annotations

from pcbkit.design import FP, LED, R, part

BLOCK_ORDER = ["Indicator", "Housekeeping"]
NOTES = ["Golden board for the schematic generator", "A second line of notes"]
BLOCK_WIDTHS = {"Indicator": 90, "Housekeeping": 60}
BLOCK_TITLES = {"Housekeeping": "Flags and holes"}
COMPANY = "Example Co"
COMMENT = "Fixture for the schematic generator's golden test"

B = "Indicator"
part(
    "J1",
    "Connector_Generic:Conn_01x02",
    "Supply",
    "Lib:Header",
    {"1": "+3V3", "2": None},
    "Acme",
    "HDR-2",
    "Supply header, pin 2 left open",
    B,
)
R("R1", "330", "+3V3", "LED_A", B)
part("R2", "Device:R_Variant", "1k", FP["R0603"], {"1": "LED_A", "2": "GND"}, block=B)
LED("D1", "Green", "LED_A", "GND", B, "GRN-0603", "Acme")
part(
    "D2",
    "Device:LED",
    "Red",
    FP["LED0603"],
    {"1": "GND", "2": "LED_B"},
    "Acme",
    "RED-0603",
    "Not fitted",
    B,
    dnp=True,
)
part("TP1", "proj:Probe", "Probe", "Lib:Pad", {"1": "LED_B"}, block=B)

B = "Housekeeping"
part("#FLG01", "power:PWR_FLAG", "PWR_FLAG", "", {"1": "GND"}, block=B, bom=False)
part("#FLG02", "power:PWR_FLAG", "PWR_FLAG", "", {}, block=B, bom=False)
part(
    "H1",
    "Mechanical:MountingHole",
    "M3",
    "MountingHole:MountingHole_3.2mm_M3",
    {},
    block=B,
    bom=False,
)
