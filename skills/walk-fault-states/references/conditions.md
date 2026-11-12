# Conditions to walk

Every board gets all ten rows. Add the board's own rows after them. For each condition:
which rails are present, what the controller is doing, and where the trace usually goes
wrong. Each row becomes a set of keyword arguments for `circuits.scenario` when the
intent is written to `FAULT_STATES`.

## 1. Main supply connected, everything else idle

- Rails: the input rail up; the logic rail up if it comes from the input.
- Controller: in power-on reset, then booting, then running with outputs not yet set.
- Trace: every enable from the instant the supply connects, before any firmware runs.
  This row catches regulators that start on their own and driver chips whose outputs
  are live out of reset.

## 2. Logic rail absent, input rail present

- Rails: the input rail up; the logic regulator failed, disabled or its fuse open.
- Controller: unpowered. Every pin it drives floats or is clamped through its ESD diodes.
- Trace: what pulls each enable when its driver is dead. A pull-up to the logic rail is
  a pull-down when that rail is at 0 V, and a pull to the input rail is still live.
  Check whether driver chips have their own UVLO on the logic supply and what their
  outputs do below it.

## 3. Controller in reset or its bootloader

- Rails: all present.
- Controller: held in reset (button, debugger, supervisor), or in the ROM bootloader
  after a strapping pin or a failed flash.
- Trace: the datasheet's reset and boot state of every GPIO on an output path: input,
  input with a weak pull, or briefly driven. Strapping pins are sampled at reset and
  may be pulled by the design; a strapping pin wired to an enable gets the strapping
  pull. Some pins toggle or put out a clock during boot.

## 4. Firmware crashed or hung

- Rails: all present.
- Controller: pins frozen at the last state written, or reset by a watchdog and back in
  row 3.
- Trace: both outcomes. If an output stays on when firmware stops updating it, the
  hardware has to remove it (a watchdog-driven enable, a pulse-required enable, the stop
  loop). A PWM peripheral keeps running when the CPU hangs.

## 5. Powered from USB only

- Rails: whatever USB can reach. The main supply absent.
- Controller: running, often on a bench with the mechanism attached.
- Trace: which rails USB can reach and through what. If USB reaches a load rail, the
  actuators can move on bench power and the port can be overloaded. Check that firmware
  cannot tell the difference and command motion anyway.

## 6. Brownout during a load spike

- Rails: the input sags under stall or inrush current; the logic rail may follow if it
  shares the source.
- Controller: may reset, run through a corrupt state, or land in row 3.
- Trace: the order in which parts cross their thresholds as the rail falls and recovers.
  Does the logic reset before or after the enables lose their hold? What do the outputs
  do on the way back up: row 1 again, with the mechanism mid-motion?

## 7. Stop input opened

- Rails: all present, unless the stop cuts a rail directly.
- Controller: running, possibly commanding motion.
- Trace: whether the stop acts in hardware on the enable path or is only read by
  firmware. A stop that only firmware reads is row 4 waiting to happen. Check that a
  broken wire or an unplugged stop connector reads as open.

## 8. Another device powered while this board is not

- Rails: this board's rails at 0 V; a separately powered device (a host computer, a
  second board, a programmer) driving UART, I2C, SPI or GPIO lines into it.
- Controller: unpowered.
- Trace: current from each driven pin through input ESD diodes into the unpowered logic
  rail. That rail can rise far enough to half-start the controller or a driver chip.
  Check pull-ups to the other device's rail on shared buses too.

## 9. Supply removed while outputs are active

- Rails: the input collapsing from full load; bulk capacitance discharging.
- Controller: may outlive the load rails on its own hold-up, or die first.
- Trace: whether any output glitches on as rails collapse at different rates, and
  whether stored energy (bulk capacitors, motor back-EMF) holds a rail up long enough to
  keep an output undefined.

## 10. Hot-plugging a peripheral

- Rails: all present; a connector mating live.
- Controller: running.
- Trace: what the peripheral's pins do to shared enables and signal lines in the first
  milliseconds: inrush pulling a shared rail into row 6, an unpowered peripheral loading
  a bus, a peripheral that back-feeds this board (row 8 the other way). Plugs mated
  wrongly or reversed belong to `review-misplug`.

## The board's own rows

Ask the user before filling the table. Common additions: a charger connected with the
battery removed, a debug probe powering the board, a second supply, a radio module with
its own regulator, a port that supplies power outward.
