# Worked example: my-board

A generic two-layer pump controller, made up to show the review. 50 x 35 mm. 12 V
comes in on J1 at the left edge. Q1, a low-side FET, switches a diaphragm pump on J2 at
the right edge with 20 kHz PWM, through shunt R10, read by amplifier U7; D3 clamps the
pump's flyback at J2. U2, a 500 kHz buck with inductor L2, makes 3.3 V at up to 0.5 A.
A pressure-sensor bridge on J6 feeds U8, an instrumentation amplifier, whose output
`PRESS_SENSE` goes to an ADC pin of U1; U1's clock is crystal Y1. Ground pours on both
sides; stitching from `[stitch]`.

Intent from the user: the pressure reading is the victim that matters (a few mV of
error is a visible fault); the pump draws 3 A running and 6 A for 200 ms at start.

## Inputs to ask for first

- U2's datasheet layout example, and whether L2 is shielded.
- Where J1's ground pin is, relative to R10's ground end and U1's analogue ground.
- The pump's start current and duration (`review-derating` writes the budget).

## Paths

| Path (source - load) | Peak current or edge rate | Return route | Verdict |
|---|---|---|---|
| J1 - J2 pump - Q1 - R10 | 6 A at start, 20 kHz edges | R10 ground pad, bottom pour, under U8 and U1's analogue corner, J1 pin 2 | Problem |
| Pump flyback: J2 - D3 - J2 | 3 A recirculating at each PWM off | D3 beside J2, top | OK |
| U2 hot loop: C20 - U2 - GND - C20 | 0.5 A switched, ns edges | C20 on top beside U2, via to the bottom pour, back up at U2 | Concern |
| Q1 drain and U2 switch node | 12 V swings | capacitive: no conducted return | Measured by a check once `COUPLING` lists them |
| R10 sense pair - U7 | tens of mV full scale | differential, top | Measured by a check once `SENSE_LOOPS` lists it |
| J6 bridge - U8 - U1 ADC | mV signal, slow | bottom pour | Concern |

## Findings

1. **Location:** pump return, bottom pour, x 15 to 40, y 20 to 30, under U8 and U1's
   analogue ground. **Problem:** up to 6 A of pump return crosses under the analogue
   grounds on its way to J1. **Why it matters:** milliohms of shared pour carry amps,
   so U1's analogue ground lifts by millivolts with pump load and the pressure reading
   moves with the pump. **Change:** move J1 to the right edge beside J2 so the return
   never passes the analogue corner, or move U8 and U1's analogue corner to the left of
   J1's ground pin.
2. **Location:** U2, C20. **Problem:** the input capacitor's ground reaches U2 through a
   via pair, where the datasheet example keeps the loop on the top side. **Why it
   matters:** via inductance in the hot loop rings at every edge. **Change:** rotate
   C20 in layout.py so its ground pad sits beside U2's ground pin on the top side.

## Written to specs.py

```python
COUPLING = {
    "classes": {"bridge": (0.002, 1_000, 5e6)},  # 2 mV budget: the ADC's resolution
    "sensitive": {"bridge": ["PRESS_SENSE"]},
    "aggressors": {
        "buck_sw": {"nets": ["SW"], "kind": "switch", "region": (5, 4, 18, 16)},
        "pump": {
            "nets": ["+12V", "PUMP_DRAIN"],
            "kind": "current",
            "region": (35, 5, 50, 30),
        },
    },
    "filters": {"PRESS_SENSE": {"t_edge": 1e-6, "pin_ref": "U1", "max_mm": 3.0}},
}
SENSE_LOOPS = [
    {
        "name": "pump shunt",
        "nets": ("SNS_P", "SNS_N"),
        "from_ref": "R10",
        "to_ref": "U7",
        "to_pads": ("3", "4"),
        "max_mm2": 10.0,
    },
]
RESERVED_COPPER = [
    ("under_crystal", "bottom", 22, 4, 28, 9, ["/GND"]),
]
COPPER_SEGMENTS = [
    {
        "name": "pump return",
        "net": "GND",
        "heating": False,
        # U1 pad 12 is its analogue ground: share 0, so the solve reports its voltage
        "terminals": [("R10", "2", 1.0), ("U1", "12", 0.0), ("J1", "2", -1.0)],
    },
]
```

`test_copper_segment` records the segment's largest drop, R10 to J1, not the lift at
U1's pad on its own. The solve has it (`pcbkit.check.copper.NetCopper` returns a
voltage per terminal, `v_terminals`), so `add-check` writes a small project check that
records U1's terminal and holds it under the 2 mV budget. Each number carries its source
in the real file; layout.py gains `SHOTS` regions for U2 and the analogue corner, and
routing.py the keep-out under Y1.
