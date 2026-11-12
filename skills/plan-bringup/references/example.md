# Worked example: my-board

A generic board, made up to show how a plan runs. Test point names (`TP_*`) are what the
plan asks for; on a real board use the names on the board and list every missing one in
Part 1. Expected currents are estimates from typical datasheet figures.

## Power tree and enable chain

```
J1 24 V -> F1 -> Q1 (reverse-polarity FET) -> VIN
  |- U2 buck -> VMOT 12 V -> U4 motor driver
  |     EN: held low by a hold-down from VIN, released by U1 MOT_EN through the stop loop (J4)
  |- U3 regulator -> +3V3 -> U1 controller, U6 temperature sensor (I2C), U7 current amplifier
USB J2 VBUS -> D2 -+-> +3V3 input (D3 from U3's side)
```

The `i2c` record in `out/checks/results.json` lists U6 at 0x48 and nothing else on the
bus: that is the expected scan.

## How the order falls out

- `+3V3` runs from USB with no 24 V and no motor rail: lowest energy, so U1 and the bus
  come first.
- The input stage (F1, Q1) can be live with the hold-down keeping U2 off: second, and
  the place to prove the hold-down and reverse polarity.
- `VMOT` depends on U1, the stop loop and VIN: third, with no motor.
- One motor last.

## Part 1. Design findings

| # | Kind | Where | Why the plan needs it | Suggested change |
|---|---|---|---|---|
| 1 | missing test point | VIN after Q1, Q1 gate | Step 2.2 tells "FET on" from "body diode only" by the VIN drop and the gate voltage | `TP_VIN`, `TP_GATE` |
| 2 | missing test point | `VMOT`, `+3V3` | every rail step probes its rail | a labelled test point per rail, each with a ground pad within 10 mm |
| 3 | missing test point | `MOT_EN` before and after the stop loop | steps 1.6 and 3.1 probe the enable chain at each end | `TP_EN_MCU`, `TP_EN_LOOP` |
| 4 | unreachable probe point | anything between J10 and J13 | probing with the sensor plugs mated | move to the board edge, clear of the plugs' bodies |
| 5 | cannot power alone | U3 output to `+3V3` | if stage 0 reads `+3V3` low, nothing separates U3 from its loads | a 0 ohm link between U3's output and the `+3V3` net |

Hand-offs: `walk-fault-states`: on stop release, should `VMOT` return while `MOT_EN` is
still high, or wait for firmware? `review-derating`: the input capacitors' rating
against 28 V with hot-plug ringing.

## Part 2. Bring-up plan (stages 0 to 2 shown)

Initial state: J1 and J2 unplugged, stop switch fitted and released, no motor, the 0 ohm
link of finding 5 fitted. Bench supply off, 0 V.

| Step | Action | Probe point | Expected | Limit | Stop if |
|---|---|---|---|---|---|
| 0.1 | Inspect orientation of D2, D3, Q1, U1-U7 and J1's polarity mark against the assembly drawing; loupe on U2's pins | - | matches the drawing | no power | any part reversed or missing, any bridge |
| 0.2 | Ohms VIN to GND, both polarities, let settle | `TP_VIN` | climbs past 10 kohm | no power | under 1 kohm |
| 0.3 | Ohms `+3V3` to GND | `TP_3V3` | hundreds of ohm to tens of kohm; compare boards | no power | under 50 ohm, or ten times below a sibling board |
| 1.1 | Bench supply into USB VBUS through a breakout, ramp 0 to 5.0 V | supply readout | 20-50 mA settled | 5.0 V, 120 mA = ~2.5 x 50 mA | constant current over 1 s; over 80 mA settled |
| 1.2 | Probe `+3V3` | `TP_3V3` | 3.30 V +/-3 % | 5.0 V, 120 mA | over 3.45 V: power off now |
| 1.3 | Probe VIN | `TP_VIN` | under 0.3 V | 5.0 V, 120 mA | over 0.5 V: USB is back-feeding the input side |
| 1.4 | Flash heartbeat firmware, radios off | serial console | steady for 5 min, power-on reset reason | USB port | any brownout reset |
| 1.5 | I2C scan | serial console | exactly 0x48 | USB port | missing or extra address |
| 1.6 | Drive `MOT_EN` high, stop released, then pressed | `TP_EN_MCU`, `TP_EN_LOOP` | loop side high when released, low when pressed | USB port | loop side high with the stop pressed |
| 2.1 | USB off. Bench supply into J1, ramp 0 to 24.0 V | supply readout | under 5 mA settled (regulators held off) | 24.0 V, 50 mA | constant current over 1 s |
| 2.2 | Probe VIN and the gate | `TP_VIN`, `TP_GATE` | VIN within 0.1 V of the input, gate above VIN | 24.0 V, 50 mA | VIN 0.5-0.7 V low: only Q1's body diode conducts |
| 2.3 | Probe `VMOT` with U1 unpowered | `TP_VMOT` | under 0.3 V | 24.0 V, 50 mA | over 0.5 V: the hold-down is not holding; do not go on |
| 2.4 | Reverse polarity: supply off, pigtail reversed, 12.0 V | supply readout | under 1 mA | 12.0 V reversed, 50 mA | the supply hits its limit |

Stage 3 brings up `VMOT` with no motor (step by step through the stop loop, then a reset
with the scope on `MOT_EN`), and stage 4 adds one motor, then the over-current alert.

## Written to specs.py

`TEST_POINTS = [("VIN", 10.0), ("VMOT", 10.0), ("+3V3", 10.0), ("MOT_EN", 10.0)]` and
`I2C_EXPECTED = {"main": [0x48]}`, both proposed tables handed to `add-check`.
