from __future__ import annotations

from pcbkit.design import FP, LED, R, part

BLOCK_ORDER = ["Supply", "Indicator"]
NOTES = [
    "Blinky: a 3 V to 5.5 V supply lights one green LED.",
    "R1 sets the LED current: about 8 mA from 5 V.",
]

B = "Supply"
part(
    "J1",
    "Connector_Generic:Conn_01x02",
    "VIN / GND",
    FP["XH2"],
    {"1": "VIN", "2": "GND"},
    "JST",
    "B2B-XH-A(LF)(SN)",
    "Power connector: VIN on pin 1, GND on pin 2",
    B,
)

B = "Indicator"
R("R1", "330", "VIN", "LED_A", B, "RC0603FR-07330RL", "Yageo")
LED("D1", "Green", "LED_A", "GND", B, "APT1608SGC", "Kingbright")
