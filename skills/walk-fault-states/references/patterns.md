# Recurring fixes

Suggest the one that fits the trace; choose values for the design.py in front of you.

## Hold-down on an enable that defaults on

A regulator or driver whose enable turns it on when floating cannot be made safe by a
pull to the logic rail: when that rail is missing, the pull is gone. Hold the enable low
with a transistor powered from the side that is always present, and have the controller
release the hold-down rather than assert the enable. Every failure of the controller or
the logic rail then leaves the hold-down in place.

## Stop loop in the enable chain

Route the controller's enable signal through the normally-closed stop loop before it
reaches the release transistor. An open stop, a cut wire or an unplugged connector then
removes the enable in hardware. Firmware may read the stop too, but safety does not
depend on that read.

## Output-enable held inactive by default

Driver chips with an active-low output-enable get that pin pulled to its inactive level,
so no pulses reach the actuators while the controller boots or sits in reset. Firmware
drives it active only after the driver is configured. Check what the outputs do while
disabled (high impedance, driven low, or configurable): some actuators read a floating
input as a command.

## Diode OR-ing or a power mux for USB

USB should reach only the rails it is meant to power, usually the logic rail. Feed that
rail from USB and from the main supply through diodes or an ideal-diode or power-mux
part, so neither source back-feeds the other and USB cannot reach a load rail.

## Series resistance on lines to a separately powered device

Signal lines to a device with its own supply get series resistors sized to keep injected
current inside the receiving pin's absolute maximum, or a buffer that is high impedance
when unpowered. Shared bus pull-ups go to the rail that is present whenever the bus is
driven.

## Watchdog or pulse-required enable

Where an output must drop when firmware hangs, make the enable depend on a signal
firmware must keep toggling (an external watchdog, a charge-pump enable), not a static
level the hung firmware leaves in place.
