# Recurring fixes

Suggest the one that fits the trace; choose values for the design.py in front of you.
Each comes with the kind of product it is often seen on, only to make it concrete.

## A pull-down on an enable that defaults on

A regulator or driver whose enable turns it on when floating (an internal pull-up, or
an input that floats high) gets an external pull-down to ground, strong enough to beat
the internal pull, so the part is off until something drives the enable high. Ground is
present in every condition; a pull to a rail is gone when the rail is. A heater
controller's gate driver is the usual case.

## A supervisor on the power stage

A voltage supervisor or reset IC with an open-drain output on the enable holds a power
stage off while its supply is below threshold, through brownout and recovery, whatever
the controller is doing. A bench supply's output stage, or a charger's, often has one.

## Safety input in the enable chain

Put the safety input (a lid interlock, a door switch, an over-temperature trip) in the
hardware path of the enable, so that tripping it, a cut wire or an unplugged connector
removes the enable with no firmware involved. Firmware may read the input too, but
safety does not depend on that read. A laser cutter's lid interlock is the classic case.

## Output-enable held inactive by default

A shift register or driver with an active-low output-enable gets that pin pulled to its
inactive level, so its outputs are off while the controller boots or sits in reset.
Then check what the outputs do while disabled: a high-impedance output leaves the
stage it drives floating, so a relay or LED driver behind it needs its own pull to off.
A relay board's shift register is the usual case.

## Diode OR-ing or a power mux for USB

USB should reach only the rails it is meant to power, usually the logic rail. Feed that
rail from USB and from the main supply through diodes or a power-mux part, so neither
source back-feeds the other and USB cannot reach a load rail. Handheld instruments that
also run from an adapter do this.

## Series resistance on lines to a separately powered device

A UART or GPIO line to a device with its own supply (a host computer's adapter, a
programmer) gets a series resistor sized to keep injected current inside the receiving
pin's limit, or a buffer that is high impedance when unpowered. Shared bus pull-ups go
to the rail that is present whenever the bus is driven.

## A limit that firmware cannot override

Where an output must stop when firmware hangs, give it a hardware limit that does not
depend on the controller: a thermal cut-out in series with a heating element, a
current limit in the driver, an external watchdog that resets the controller into a
safe reset state. A hot plate's thermal fuse is the classic case.
