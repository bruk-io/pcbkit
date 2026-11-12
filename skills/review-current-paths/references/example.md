# Worked example: my-board

A generic two-layer board, made up to show the review. 60 x 40 mm. 24 V comes in on J1
at the left edge. U2, a 500 kHz buck with inductor L2, makes 5 V at up to 3 A. U4, a
motor driver on the right, draws up to 4 A peaks from 24 V through shunt R10, read by
amplifier U7. A thermistor on J6 feeds an ADC pin of U1, a radio module whose antenna
overhangs the top edge. Ground pours on both sides; stitching from `[stitch]`.

Intent from the user: U2 sits by J1 so the 24 V path is short; the thermistor input is
the victim that matters; motors may stall at 4 A for a second.

## Inputs to ask for first

- U2's datasheet layout example, and whether L2 is shielded.
- Where J1's ground pin is, relative to U4's ground and U7's ground.
- The motor stall current and how long it lasts (`review-derating` writes the budget).

## Paths

| Path (source - load) | Peak current or edge rate | Return route | Verdict |
|---|---|---|---|
| J1 - R10 - U4 - motor | 4 A, stall | U4 GND pads, bottom pour, under U1's ADC corner, J1 pin 2 | Problem |
| U2 hot loop: C20 - U2 - GND - C20 | 3 A switched, ns edges | C20 on top beside U2, via to the bottom pour, back up at U2 | Concern |
| U2 switch node and L2 | 24 V swing at 500 kHz | capacitive: no conducted return | Measured by a check once `COUPLING` lists it |
| R10 sense pair - U7 | tens of mV full scale | differential, top | Measured by a check once `SENSE_LOOPS` lists it |
| J6 thermistor - U1 ADC | slow, high impedance | bottom pour | Concern |

## Findings

1. **Location:** motor return, bottom pour, x 20 to 40, y 25 to 35, under U1's ADC
   corner. **Problem:** up to 4 A of motor return crosses under the ADC's ground on its
   way to J1. **Why it matters:** milliohms of shared pour carry amps, so the ADC
   ground shifts by millivolts with motor load and the reading moves with it.
   **Change:** move J1 to the right edge so the motor return never passes U1, or keep a
   track-free strip in the bottom pour along U4 to J1 (a keep-out in routing.py).
2. **Location:** U2, C20. **Problem:** the input capacitor's ground reaches U2 through a
   via pair, where the datasheet example keeps the loop on the top side. **Why it
   matters:** the via inductance in the hot loop rings at every edge. **Change:** rotate
   C20 in layout.py so its ground pad sits beside U2's ground pin on the top side.

## Written to specs.py

```python
COUPLING = {
    "classes": {"adc": (0.005, 10_000, 5e6)},  # 5 mV budget, thermistor divider
    "sensitive": {"adc": ["TEMP_SENSE"]},
    "aggressors": {
        "buck_sw": {"nets": ["SW"], "kind": "switch", "region": (8, 4, 22, 18)},
        "motor": {"nets": ["VMOT", "MOT_A", "MOT_B"], "kind": "current",
                  "region": (40, 5, 60, 35)},
    },
    "filters": {"TEMP_SENSE": {"t_edge": 1e-6, "pin_ref": "U1", "max_mm": 3.0}},
}
SENSE_LOOPS = [
    {"name": "motor shunt", "nets": ("SNS_P", "SNS_N"), "from_ref": "R10",
     "to_ref": "U7", "to_pads": ("3", "4"), "max_mm2": 10.0},
]
RESERVED_COPPER = [
    ("antenna_top", "top", 0, 0, 18, 6, []),
    ("antenna_bottom", "bottom", 0, 0, 18, 6, []),
]
COPPER_SEGMENTS = [
    {"name": "motor return", "net": "GND", "heating": False,
     "terminals": [("U4", "9", 1.0), ("J1", "2", -1.0)]},
]
```

Each number carries its source in the real file (the budget from the ADC's resolution,
the 3 mm from the filter's design); routing.py gains the antenna keep-out, and layout.py
gains `SHOTS` regions for U2 and the ADC corner.
