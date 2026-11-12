# Part classes - what to check and what to look up

House targets below are starting defaults. Where the user has set a different target for a board, use theirs. The last line of each class says where its result is written in `specs.py`.

## Ceramic capacitors (MLCC)

- **DC bias.** Class II dielectrics (X5R, X7R, X6S) lose capacitance with applied DC voltage. Smaller cases and higher capacitance per volume lose more. A 10uF 25V part at 12V can deliver well under half its nominal; at 20V less again.
- **Temperature.** The code gives the band: X7R and X5R allow +/-15% over their range (-55 to +125 C and -55 to +85 C). Y5V and Z5U allow far more loss; treat them as unfit for regulator duty. C0G is flat but rarely available above a few nF in small cases.
- **AC ripple and ageing** reduce it further; ageing is a few percent per decade-hour for class II.
- **Look up:** the exact part number in the manufacturer's simulation tool (Murata SimSurfing, TDK SEAT, Samsung Electro-Mechanics' characterisation tool, KEMET K-SIM) at the worst-case DC voltage and hottest ambient. Record the tool and operating point in the table. If the BOM gives only a generic value, the finding is "part not specified tightly enough to derate".
- **What the loss does:** compare the effective capacitance against the regulator datasheet's minimum effective (not nominal) output capacitance. Below it, loop stability and transient response are not as designed. Output ripple scales roughly inversely with effective capacitance. Input capacitors that lose capacitance raise input ripple current and the ripple seen by everything on that rail.
- **Target:** effective capacitance at worst case meets the regulator's minimum with margin; rated voltage at least the worst-case transient.
- **Written to:** `CAP_VRATED` (rated volts per part number); a `SUPPLY_LIMITS` row for effective against minimum capacitance, the value and the tool's operating point beside it.

## Other capacitors

- **Tantalum (MnO2):** fails short on surge. House target: working voltage at most 50% of rating on any rail that can be hot-plugged or sees surges.
- **Polymer tantalum, polymer aluminium, aluminium electrolytic:** target at most 80% of rating at worst-case voltage. Check ripple current rating at the hottest ambient; electrolytic life halves roughly every 10 C rise.
- **Look up:** the ripple current rating and its temperature multiplier in the datasheet.
- **Written to:** `CAP_VRATED`, always the datasheet's true rated voltage. The built-in check holds every capacitor to 80 % and has no per-part factor yet (WP17 plans one), so a tantalum between 50 % and 80 % passes it: judge that in the review as a `MARGINAL` or `FAIL` finding against the 50 % target, and never write a lowered rating into the table to make the check stricter.

## FETs and diodes

- **Voltage:** VDS, VGS and diode VRRM against the worst-case net voltage including transients, not the nominal supply. Hot-plugging a lithium pack into low-ESR ceramic input capacitance can ring well above the pack voltage (up to nearly twice in the undamped case). Motor braking can pump a rail above the battery if nothing absorbs the energy (fuse open, battery disconnected, ideal-diode blocking reverse current).
- **Current and heat:** RDS(on) rises with junction temperature; use the datasheet's normalised RDS(on) versus temperature curve at the expected junction temperature, not the 25 C figure. Check the SOA curve for linear-mode use (inrush limiting, hot-swap).
- **Target:** worst-case voltage at most 80% of rating. If the transient is unknown, the verdict is `UNSTATED` and the fix is a TVS, bulk damping capacitor, or a measurement on the bench.
- **Written to:** `SEMI_VRATED` (proposed: `add-check` writes the check until pcbkit has it); the transient as a `STRESS_SCENARIOS` row.

## Connectors, headers and wire

- **Per contact.** Find the contact rating, the wire gauge it assumes, and the temperature rise it is specified at (often 30 C). Many datasheets derate when all contacts are loaded at once.
- **Divide honestly.** If power and ground each have one contact, the per-contact current is the full load current. Extra contacts on the same net only share current if the mating cable has them wired and crimped.
- **Wire gauge:** the crimp terminal's rating applies only with the gauge the datasheet names. Thinner wire lowers the real rating and adds drop.
- **Names are not ratings.** A number in a connector's name is not necessarily its continuous rating at your ambient. Read the datasheet's continuous figure.
- **Target:** worst-case per-contact current at most 80% of the derated contact rating.
- **Written to:** `CONTACT_RATINGS` (proposed); the copper behind the connector as `COPPER_SEGMENTS` terminals.

## Inductors

- **Peak, not average.** Peak inductor current = load current + half the ripple current, at the worst-case input voltage (highest Vin for a buck gives the most ripple). During startup, overload or a short the inductor carries the regulator's switch current limit, so Isat must exceed the regulator's maximum current limit, not the load.
- **Temperature.** Ferrite saturation current drops as temperature rises; powdered-iron and composite cores degrade more softly. Use the datasheet's Isat at the hottest ambient plus self-heating.
- **Two ratings.** Isat (inductance drops by a stated percentage, often 20 or 30%) and Irms (heating, often a 40 C rise). Check both and note which definition the datasheet uses.
- **Look up:** the inductance versus current curve at temperature for the exact part.
- **Written to:** a `SUPPLY_LIMITS` row: the regulator's maximum current limit `<` Isat at the hottest ambient.

## Resistors and shunts

- **Power:** dissipation at worst-case current or voltage against the rating after the datasheet's temperature derating curve (most derate linearly above 70 C). House target: at most 50% of the derated rating.
- **Pulse:** for inrush limiting, snubbers and gate resistors, check the pulse load curve; a resistor that survives the average can fail on the pulse.
- **Shunts:** self-heating shifts the reading through the TCR. Compute dissipation at stall current, not running current, and check the reading error at that temperature against what the firmware needs.
- **Written to:** `RESISTOR_POWER_W` (the built-in check holds half the rating); the stall case as a `STRESS_SCENARIOS` row.

## Fuses

- **Hold versus trip.** A fuse rated 25A is not a 25A continuous part. Cartridge and blade fuses need rerating (commonly run at 75% or less of rating); polyfuse hold current falls sharply with ambient temperature. Use the manufacturer's temperature rerating curve.
- **Trip time** at modest overload can be minutes. A fuse protects the wiring and the pack from a hard fault; it does not protect individual rails from overload. Check each rail has its own limit.
- **Inrush I²t.** On connection the input capacitance charges through only the pack's internal resistance, connector, fuse and copper. For an RC charge, I²t = V²C / (2R). Compare it against the fuse's melting I²t after the manufacturer's pulse-cycle derating factor (a fuse that survives one plug-in can fatigue over many). R is small and uncertain; state the value used and its source.
- **Look up:** melting I²t, time-current curve, temperature rerating curve and pulse derating factor from the fuse datasheet or selection guide.
- **Written to:** `SUPPLY_LIMITS` rows: the budget behind the fuse against its rerated hold current, and inrush I²t (a function of the netlist that sums the capacitance after the fuse) against the derated melting I²t.

## Set-point dividers

- Output = Vref x (1 + Rtop / Rbottom). Worst case combines the reference tolerance with both resistor tolerances. With a 1% reference and 1% resistors and a large ratio, the output can land close to 3% off nominal.
- Compare the worst-case high output against the load's absolute maximum, and the worst-case low against its minimum.
- This is arithmetic; do it, but only once the reference tolerance comes from the regulator datasheet.
- **Written to:** a `SUPPLY_LIMITS` row that is a function of the netlist, reading the divider's values from it, so a changed resistor is judged again.

## Supply edges

- **Low end:** lowest real voltage = cell cutoff (or the protection board's cutoff) minus sag under peak load through pack resistance, connector and fuse. At that point check every regulator's dropout or maximum duty cycle: a buck at maximum duty passes the input through minus the drop across the switch and inductor, and its output follows the battery down. Decide whether the loads tolerate that or need a buck-boost or a lower set-point.
- **High end:** highest real voltage is the fully charged pack plus transients. Regulator minimum on-time and UVLO behaviour belong to `walk-fault-states` (and later `review-datasheets`); here, check ratings against it.
- **Written to:** `SUPPLY_LIMITS` rows for dropout at the lowest input and for the highest input against each rating; `SUPPLY_PINS` for the regulator's input pin.
