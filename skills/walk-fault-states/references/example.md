# Worked example: my-board

A generic board, made up to show the walk. A 24 V input on J1 feeds U2, a buck
regulator making the 12 V motor rail `VMOT`, and U3, a regulator making `+3V3` for the
controller U1. USB on J2 also feeds `+3V3`. U4 is a two-channel motor driver on `VMOT`
with an active-high enable, `DRV_EN`. A normally-closed stop switch plugs into J3. A
host computer talks to U1 over a UART on J5 and has its own supply.

Assumed at the top of the review, as a real one would say it: intent is that the motors
are off whenever U1 is not actively in control. U2's datasheet says its EN pin has an
internal pull-up and the regulator runs when EN floats (many bucks do: check yours).

## First pass: the schematic as drawn

U2 EN is driven straight from a U1 GPIO, `DRV_EN` is tied to `+3V3` through 10 kohm,
and USB VBUS is joined to the 24 V input side through a diode.

| Condition | U2 EN (VMOT) | U4 outputs | +3V3 |
|---|---|---|---|
| 1. Main supply, idle | on [a] | undefined [b] | on |
| 2. Logic rail absent | on [a] | off [c] | off |
| 3. Reset or bootloader | on [a] | undefined [b] | on |
| 5. USB only | depends [d] | undefined [b] | on |

- [a] EN floats while the GPIO is unpowered, in reset or not yet set, and the internal
  pull-up turns U2 on: `VMOT` comes up the instant J1 is connected.
- [b] `DRV_EN` is held on, so the outputs follow whatever U4's inputs float to until U1
  drives them.
- [c] With `+3V3` at 0 V the 10 kohm pull-up pulls down: off, by luck rather than design.
- [d] VBUS reaches U2's input through the diode; whether `VMOT` rises depends on U2's
  UVLO at 5 V.

Findings: (1) rows 1 to 3, U2: the motor rail is up before U1 boots and stays up with
the logic dead; a pull-down to `+3V3` does not fix row 2. Fix: a hold-down powered from
the 24 V side that U1 releases. (2) rows 1 and 3, U4: pull `DRV_EN` down, so firmware
enables U4 only after setting its inputs. (3) row 5: USB must not reach the 24 V side;
feed `+3V3` from USB and from U3 through an OR-ing diode pair instead.

## Second pass: after the fixes

| Condition | U2 EN (VMOT) | U4 outputs | +3V3 |
|---|---|---|---|
| 1. Main supply, idle | off | off | on |
| 2. Logic rail absent | off | off | off |
| 3. Reset or bootloader | off | off | on |
| 4. Firmware hung | depends [e] | depends [e] | on |
| 5. USB only | off [f] | off | on |
| 7. Stop open | off | depends [g] | on |
| 8. Host powered, board not | off | off | undefined [h] |

- [e] A crash that resets U1 drops the release and the hold-down wins: off with no
  firmware involved. A hang without a reset leaves the release on. It depends on
  whether firmware configures the watchdog.
- [f] No 24 V means no input to U2; EN does not matter.
- [g] The stop loop sits in the release path, so `VMOT` drops in hardware; U4's inputs
  may still be driven, into a dead rail.
- [h] The host's UART TX drives U1's RX pin through its ESD diode into `+3V3`.

Findings on the second pass: (1) row 4: safety on a hang rests on a firmware setting;
make the release need a toggling signal. (2) row 8: a 1 kohm series resistor on J5's TX
line keeps the injected current under U1's limit (`parts-researcher` for the figure).

## Written to specs.py

```python
# Proposed table: checks/test_fault_states.py (from add-check) reads it.
# U2 EN: below 0.4 V is off (U2 datasheet, EN threshold table).
FAULT_STATES = {
    "logic_absent": {
        "scenario": {"vin": 24.0, "logic": False},
        "pins": [("U2", "EN", (0.0, 0.4)), ("U4", "EN", (0.0, 0.8))],
    },
    "controller_in_reset": {
        "scenario": {"vin": 24.0, "u1_pins_floating": True},
        "pins": [("U2", "EN", (0.0, 0.4)), ("U4", "EN", (0.0, 0.8))],
    },
}
```

`scenario` in circuits.py gains the `logic` and `u1_pins_floating` keywords, and
mutants.py gains "hold-down pull-down missing", which the new check must catch.
