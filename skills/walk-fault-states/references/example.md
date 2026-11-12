# Worked example: my-board

A generic heater controller, made up to show the walk. A 24 V input on J1 feeds a
heater on J2 through Q1, a low-side N-channel FET. Q1's gate is driven by U3, a gate
driver powered from `+12V`, which U4, a linear regulator, makes from the input. U3 has
an input `HEAT_PWM` from U1, the controller, and an enable pin `DRV_EN`. U2 makes `+3V3`
for U1; USB on J3 also feeds `+3V3` through D1. A thermistor on J4 is read by U1 and by
U5, a comparator that pulls `DRV_EN` low above a set temperature. A debug probe with its
own supply plugs into J5.

Intent, as the user gave it: the heater is off whenever U1 is not actively in control,
and the over-temperature trip works with no firmware at all.

## Facts gathered (parts-researcher, cited in the real review)

- U3: `DRV_EN` has an internal pull-up, so the driver is enabled when it floats; its
  output follows `HEAT_PWM`; below its UVLO on `+12V` the output is held low.
- U1: every GPIO is an input with no pull from reset until firmware sets it.
- U5 runs from `+3V3` in the first draft.

## Rows written, then checked

```python
# Proposed tables: checks/test_fault_states.py and checks/test_power_paths.py
# (from add-check) read them. U3 pad 3 is EN (net DRV_EN), pad 2 is IN (net
# HEAT_PWM): below 0.8 V each reads low (U3 datasheet, input thresholds).
FAULT_STATES = {
    "controller_in_reset": {
        "scenario": {"vin": 24.0, "u1_pins_floating": True},
        "pins": [("U3", "3", (0.0, 0.8)), ("U3", "2", (0.0, 0.8))],
    },
    "logic_absent": {
        "scenario": {"vin": 24.0, "logic": False},
        "pins": [("U3", "3", (0.0, 0.8)), ("U3", "2", (0.0, 0.8))],
    },
}
POWER_PATHS = {
    "nets": ["VIN", "+12V", "+3V3", "VBUS"],
    "paths": [
        ("VIN", "+12V", ("U4",)),
        ("VIN", "+3V3", ("U2",)),
        ("VBUS", "+3V3", ("D1",)),
    ],
    # in pad to out pad for the regulators (datasheets); the rest pass no power
    "parts": {"U4": [("1", "3")], "U2": [("2", "5")], "U1": [], "U3": [], "U5": []},
}
```

`scenario` in circuits.py gained the `u1_pins_floating` and `logic` keywords: the first
leaves U1's pins unfixed with no pull, the second also fixes `+3V3` at 0 V and adds U3's
internal pull-up on `DRV_EN` to `+12V`. On the first draft both conditions fail on both
pads. `DRV_EN` sits at the pull-up, outside its window. `HEAT_PWM` is floating: the
solve alone reads it at 0.0 V, inside the window, and the check fails it as floating
whatever the window, as the table's rule says. `POWER_PATHS` passes: nothing joins
`VBUS` to `VIN`.

## State table, first draft

| Condition | U3 output (heater) | U5 trip | +3V3 |
|---|---|---|---|
| 1. Main supply, idle | undefined [a] | works | on |
| 2. Logic rail absent | undefined [a] | dead [b] | off |
| 3. Reset or bootloader | undefined [a] | works | on |
| 4. Firmware hung | depends [c] | works | on |
| 5. USB only | off [d] | works | on |
| 8. Probe powered, board not | off | dead | undefined [e] |

- [a] `test_fault_states`: `DRV_EN` at the pull-up and `HEAT_PWM` floating (failed as
  floating); the heater follows whatever the gate driver's input floats to.
- [b] U5 runs from the rail whose loss it should cover: a judgement, not a solve.
- [c] A hung CPU leaves the PWM peripheral running at its last duty; only U5 limits it.
- [d] No 24 V, so no heater supply; `POWER_PATHS` shows USB reaches `+3V3` only.
- [e] The probe drives U1's pins through their ESD diodes into `+3V3`: no check models
  it, so the cell comes from U1's injection-current limit.

## Findings

1. Rows 1 to 3, U3: the heater can turn on before U1 boots and with U1 dead, and a
   heater left on is a fire. Fix: a pull-down on `DRV_EN` stronger than the internal
   pull-up, and one on `HEAT_PWM`, so U1 must drive both to heat. Hardware holds it.
2. Row 2, U5: power the comparator from `+12V`, so the trip outlives the logic rail.
3. Row 4: the trip is the only limit on a hang; add a watchdog that U1 must service to
   keep `DRV_EN` high.
4. Row 8: a series resistor on each probe line keeps the injected current under U1's
   limit (`parts-researcher` for the figure).

After the fixes `test_fault_states` passes both conditions, and `mutants.py` gained
"DRV_EN pull-down missing", which the check catches.
