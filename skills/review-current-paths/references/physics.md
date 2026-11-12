# What to judge, and why

Background for the verdicts in the skill. Numbers are orders of magnitude for reasoning,
not limits: a limit belongs in `specs.py`, with its source, where a check reads it.

## What to judge

- **Hot loops.** For each switching converter: input capacitor, high-side switch,
  low-side switch, ground, back to the capacitor. It carries the full switched current
  with nanosecond edges; its area is the antenna. Compare its shape, side and vias with
  the datasheet's layout example. A side change inside the loop adds via inductance and
  usually area: call it out unless the datasheet does the same.
- **Return paths.** Low-frequency return takes the lowest-resistance route;
  high-frequency return runs right under the signal in the nearest reference. On a
  pcbkit board that is the ground pour on the other side, so find where a track on that
  side, a slot or a row of vias cuts the pour under a fast net.
- **Shared impedance.** Trace where load return current (motors, LEDs, heaters) flows
  through ground copper that a sensor, an ADC reference or a current-sense amplifier
  also uses as its reference. Find where grounds meet and whether a star point or a
  split is intended and actually works.
- **Aggressors and victims.** Inductors (stray field), switch nodes (dV/dt) and hot loops
  near an ADC input, crystal, oscillator or antenna: the distance and what lies between.
  A solid pour helps; an unshielded inductor right over the victim on the other side
  does not.
- **Bulk and decoupling.** Is bulk capacitance at the load, where the transient demand
  is, or at the source? What path lies between, and does the load's transient current
  return the same way?
- **Heat.** Where current makes heat (switches, inductors, shunts, narrow necks,
  connector contacts), and where the copper and vias spread it. A cluster over thin
  copper, or next to a temperature-sensitive part, is a finding.
- **Connector contacts.** Current entering through a connector leaves through an equally
  capable return: as many contacts, close to the supply contacts so the loop is small.

## Physics in brief

**Return current follows inductance, not resistance, once edges are fast.** Below a few
kHz it spreads by resistance. Above about 100 kHz, and for every fast edge, it crowds
under the signal in the nearest reference, because that path encloses the least area.
A converter switching at a few hundred kHz with nanosecond edges is firmly in the second regime.

**A gap in the reference forces a detour.** A slot, a cut pour, or a row of close vias
whose clearances merge into a slot, under a fast net sends the return around it. The
detour is a loop antenna and a shared path with whatever else crosses there.

**Loop area is the emitter and the receiver.** Radiated and coupled noise scale with
loop area times dI/dt, and a victim with a large loop picks up more.

**Ground copper has resistance and inductance.** 1 oz (35 um) copper is about 0.5
milliohm per square; a via is around 1 nH. So 5 A through 1 milliohm of shared ground is
5 mV of ground shift, large next to an ADC step or a sense signal of tens of millivolts;
and 5 A switched in 5 ns through 1 nH is a 1 V spike, which is why a hot loop stays on
one side with the input capacitor tight to the switch pins.

**Switch nodes couple capacitively; inductors couple magnetically.** A switch node swings
the full input at high dV/dt: keep its copper small and away from high-impedance
victims. Inductors leak field, more so unshielded ones, and it reaches through the board.

**Heat goes where copper goes.** A heat source in thin or broken copper runs hot; a
temperature-sensitive part (a sensor, a reference, a crystal) on the same copper sees
the gradient.

## Failure signatures

| Layout cause | Symptom on the bench |
|---|---|
| Large hot loop, or a side change inside it | Switch-node ringing, EMI, instability, noise on everything nearby |
| Return crossing a cut in its reference | Emissions, crosstalk, intermittent digital errors at the crossing |
| Load return through sensor ground | A reading that moves with load current |
| Inductor or switch node near a crystal | Clock jitter, spurious tones, failed start-up |
| Switch node or hot loop near an antenna | Lost radio range, worse on some channels |
| Bulk capacitance only at the source | Brown-outs or resets on load steps |
| Heat source beside a sensor | Offset drift with load and time |
| Fewer return contacts than supply contacts | Hot return contact, ground shift across the connector |
