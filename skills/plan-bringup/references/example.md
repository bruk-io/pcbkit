# Worked example: my-board

A generic battery-powered data logger, made up to show how a plan runs. Test point
names (`TP_*`) are what the plan asks for; on a real board use the names on the board
and list every missing one in Part 1. Expected readings are the ones the project check
recorded, rounded; the numbers here are illustrative.

## Power tree and enable chain

```
J1 USB-C VBUS -> U2 Li-ion charger -> VBAT <- J2 cell connector (2-pin)
VBAT -> U3 buck-boost -> +3V3 -> U1 controller, U5 real-time clock (I2C)
+3V3 -> U4 load switch (EN: PWR_EN from U1) -> SD_3V3 -> J3 SD card
+3V3 -> U6 load switch (EN: PWR_EN from U1) -> SNS_3V3 -> U7, U8 sensors (I2C)
```

The `i2c` record in `out/checks/results.json` lists U5 at 0x68, U7 at 0x44 and U8 at
0x5C on one bus. With `SNS_3V3` off, only 0x68 can answer.

## How the order falls out

- `+3V3` from a bench supply on the `VBAT` pads, cell connector empty, USB unplugged:
  the controller and the clock with no charger and no loads.
- The charger from USB with no cell, then with the bench supply standing in for a cell.
- `SD_3V3` and `SNS_3V3` one at a time, which the draft cannot do: both switches hang
  on one `PWR_EN`.
- A real cell last.

## Rows written, then checked

```python
# Proposed tables: project checks from add-check read them.
TEST_POINTS = [("VBAT", 8.0), ("+3V3", 8.0), ("SD_3V3", 8.0), ("SNS_3V3", 8.0)]
PROBE_KEEPAWAY = [("cell holder", 30, 2, 48, 20)]
ENABLE_LINKS = {"PWR_EN": ["U4", "U6"]}
I2C_EXPECTED = {"main": [0x44, 0x5C, 0x68]}
```

The first full run failed `TEST_POINTS` for `SD_3V3` and `SNS_3V3` (no test point),
`PROBE_KEEPAWAY` for `TP_VBAT` (under the cell holder), and `ENABLE_LINKS` for
`PWR_EN` (one net, no link per switch). The readings check recorded `+3V3` at about
47 kohm to ground and stage 1 at about 0.4 mA.

## Part 1. Design findings

| # | Kind | Where | Why the plan needs it | Suggested change |
|---|---|---|---|---|
| 1 | missing test point | `SD_3V3`, `SNS_3V3` | steps 3.2 and 4.2 probe each switched rail | a labelled test point on each, a ground pad within 8 mm |
| 2 | unreachable probe point | `TP_VBAT` | the cell holder covers it once a cell is fitted, and stage 5 probes it | move it beside J2, outside the holder |
| 3 | missing isolation | U4, U6 on `PWR_EN` | stages 3 and 4 bring the SD card and the sensors up apart | a 0 ohm link from `PWR_EN` to each switch's enable |
| 4 | cannot power alone | U3 output to `+3V3` | if stage 0 reads `+3V3` low, nothing separates U3 from its loads | a 0 ohm link between U3's output and the `+3V3` net |

Hand-offs: `walk-fault-states`: with U1 in reset, does `PWR_EN` float, and do the loads
come up? `review-misplug`: J2 takes third-party cell leads, which arrive reversed.

## Part 2. Bring-up plan (stages 0 to 2 shown)

Initial state: J1 and J2 empty, no SD card, both `PWR_EN` links open, the link of
finding 4 fitted. Bench supply off, 0 V.

| Step | Action | Probe point | Expected | Limit | Stop if |
|---|---|---|---|---|---|
| 0.1 | Inspect orientation of U2-U8 and J2's polarity mark against the assembly drawing; loupe on U3's pins | - | matches the drawing | no power | any part reversed or missing, any bridge |
| 0.2 | Ohms `+3V3` to GND, both polarities, let settle | `TP_3V3` | about 47 kohm (recorded), lower through IC inputs; compare boards | no power | under 50 ohm, or ten times below a sibling board |
| 1.1 | Bench supply on the `VBAT` pads, ramp 0 to 3.7 V | supply readout | about 0.4 mA settled (recorded) | 3.7 V, 20 mA | constant current over 1 s; over 5 mA settled |
| 1.2 | Probe `+3V3` | `TP_3V3` | 3.30 V, within the `SUPPLY_PINS` window | 3.7 V, 20 mA | over 3.45 V: power off now |
| 1.3 | Flash heartbeat firmware | serial console | steady for 5 min, reset reason | 3.7 V, 100 mA | any brownout reset |
| 1.4 | I2C scan, `SNS_3V3` off | serial console | exactly 0x68 | 3.7 V, 100 mA | missing or extra address |
| 2.1 | Bench supply off. USB into J1, no cell | `TP_VBAT` | the charger's no-cell behaviour from its datasheet | USB port | `VBAT` above the charger's float voltage |
| 2.2 | Reverse polarity on J2: bench supply reversed through a pigtail, 1.0 V | supply readout | under 1 mA | 1.0 V reversed, 20 mA | the supply hits its limit |

Stage 3 closes the `SD_3V3` link and fits a card; stage 4 closes the `SNS_3V3` link,
then scans for exactly 0x44, 0x5C and 0x68; stage 5 fits a cell.

## Written to specs.py

The four tables above, each handed to `add-check`, and `SHOTS` regions for the probe
points of stages 1 and 2.
