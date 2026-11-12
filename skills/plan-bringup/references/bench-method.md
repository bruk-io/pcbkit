# Bench method reference

Detail behind the method in SKILL.md. Assumes hobbyist equipment: a multimeter,
a current-limited bench supply (typically 30 V / 3-5 A), possibly a basic
two-channel scope.

## Contents

- Stage structure
- Pre-power inspection
- Resistance checks
- Choosing the current limit
- Rail voltage tolerance
- Isolation means
- Firmware ladder
- Exercising protection
- Steps that need the scope
- Moving to the product's own supply

## Stage structure

Use these stages unless the power tree forces another order. Drop stages the
board does not have.

| Stage | Live | Typical limit |
|-------|------|---------------|
| 0 | Nothing. Inspection and resistance checks | - |
| 1 | The lowest-energy section that runs alone, from the bench supply on its own rail | 2-3x its recorded idle current |
| 2 | The input stage alone, everything after it held off | a few times its leakage and bias currents |
| 3 | Each further rail in dependency order, with no load | idle plus margin for soft-start into output capacitors |
| 4 | Real loads, smallest first | above the load's running draw, with a reason in the cell |
| 5 | The product's own supply, after every earlier step has passed | what that supply provides, attended |

## Pre-power inspection

- Orientation of every polarised part: diodes (including Schottky OR-ing
  diodes and TVS), electrolytic and tantalum caps, ICs and modules (pin 1),
  connector keys. Compare against the assembly drawing, not memory.
- Solder bridges on fine-pitch and QFN parts, especially regulator switch
  nodes next to input or ground pins. Use a loupe or microscope.
- Joints of power parts and connectors: full fillets.
- Set every isolation element to its first-power state and write that state in
  the plan's "Initial state" line.

## Resistance checks

Measure each rail to ground, both meter polarities (semiconductor junctions make
readings polarity-dependent), and let the reading settle (bulk capacitance
charges and the reading climbs). Also measure adjacent rails to each other to
catch bridges between them.

The expected reading per rail is the one the readings check recorded (a DC solve:
it misses paths through IC inputs, so the bench can read lower). The usual orders of
magnitude, to sanity-check it:

| Net | Usual reading | Stop if |
|-----|---------------|---------|
| Main input after its protection | climbs past 10 kOhm (dividers, bias networks, regulator inputs in shutdown) | under 1 kOhm |
| Regulated rail with only a feedback divider and IC inputs | 1 kOhm to 100 kOhm, set by the divider | under 100 Ohm |
| Logic rail feeding a microcontroller and many ICs | hundreds of Ohm to tens of kOhm | under 50 Ohm |
| Rail to adjacent rail | over 100 kOhm or a diode-like reading | under 1 kOhm |

Under 10 Ohm on any rail is a short until proved otherwise. With several boards
from the same order, compare them: a board ten times lower than its siblings on
the same rail is suspect even if it is inside the table.

## Choosing the current limit

1. Take the stage's idle current from the readings check's record: the
   `BRINGUP_STAGES` scenario for the stage carries the datasheets' quiescent or
   typical supply currents, LEDs, pull-ups, dividers and bias resistors, and a
   regulator's output load referred to its input. Never add it up in the conversation.
2. Set the limit at 2-3x that figure, at least 20 mA above it, and below the
   level at which the most fragile part in the path is damaged if a short sits
   behind it.
3. Expect the supply to touch the limit briefly while bulk capacitors charge.
   Constant current that persists more than about a second is a stop condition.
4. Write the reason in the cell, for example "200 mA = 3 x ~60 mA idle (recorded)".

Microcontroller rule of thumb: a blank or minimal-firmware microcontroller with
radios off draws tens of mA; enabling a radio adds hundreds of mA peak. The
bring-up firmware keeps radios off.

## Rail voltage tolerance

The expected window is the rail's `SUPPLY_PINS` row, or the set-point row
`review-derating` wrote in `SUPPLY_LIMITS` (reference accuracy, divider
tolerance and load regulation together). State the absolute value above which
the most sensitive part on the rail is at risk and make that the stop condition,
with "remove power immediately".

## Isolation means

In order of preference for first power:

- Enable pin held off by a pull-down, released by firmware. Isolates without
  rework only where each regulator has an enable net of its own.
- Zero-ohm link or normally-closed solder jumper in a regulator's enable line,
  a rail's output, or an open-drain power-good line feeding a shared net.
  Cutting a solder jumper or lifting a 0603 link is acceptable bench rework.
- Normally-open solder jumper, closed only after the section passes.
- Part left unpopulated in the first order (`dnp=True` in design.py), only if
  fitting it by hand later is realistic for the package.

A section is isolated only if it can be powered and verified while everything
downstream of it stays off, and powered off while everything upstream stays up.

## Firmware ladder

Each rung proves one thing; do not move to the next rung until it passes.

1. Programming interface enumerates or responds (chip ID, flash ID).
2. Heartbeat: LED and serial message with reset reason, running for several
   minutes without brownout resets.
3. Bus scan per bus: exactly the `I2C_EXPECTED` set, which its check holds equal
   to what the design answers. Record extra addresses as well as missing ones.
   Some parts answer an all-call or general-call address as well as their own;
   the datasheet says, and the expected set includes it.
4. Identity register of each device, read and compared against the datasheet
   value. For parts without one, read a register with a documented reset value,
   or write and read back a harmless register.
5. Each peripheral's minimal function with its power stage still off (for
   example PWM out on a pin, checked with scope or meter frequency mode).
6. Control of power stages: enable lines, power-good inputs, and any voltage or
   current the firmware reads, compared against the bench supply readout.

## Exercising protection

Test only the protection this board has, each from its datasheet or the
user's intent, on the bench supply at a low voltage and current limit first.

- **Safety inputs** (an interlock, a supervisor, a thermal cut-out): trip each
  with rails up and no load. Check the outputs drop and what happens when the
  cause clears. The intended behaviour comes from the user (and `FAULT_STATES`,
  if `walk-fault-states` wrote it); the plan only checks it.
- **Input protection** (reverse polarity, an over-voltage clamp): at the bottom
  of the input range and a low limit first, after the forward input stage has
  passed; raise it only if the first test draws next to nothing.
- **Current limits** (an eFuse, a load switch's limit, a polyfuse): with a power
  resistor or an electronic load, never a dead short, and only if the user asks.
- **Default-off states**: controller unpowered, held in reset, and rebooting
  (scope the enable net across a reset for glitches). Load rails must stay off in
  all three.

## Steps that need the scope

- Rail start-up shape and overshoot (soft-start, monotonic, overshoot against the
  load's maximum rating).
- Switching ripple, measured with a spring ground tip on a nearby ground pad.
- Enable and reset glitches across microcontroller boot.
- Rail droop when a load steps.
- Bus waveforms when a scan fails (pull-ups, rise time, stuck lines).
- PWM or other signal outputs (a meter with frequency and duty mode can stand in).

## Moving to the product's own supply

Only after every bench-supply step passes, on every board to be used. Check the
supply's voltage and polarity at its plug with a meter first, and stay with the
board for the first runs. Full-load tests beyond the bench supply's current belong
to `review-derating`'s measurement list, not this plan.
