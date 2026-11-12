# What to judge

Background for the verdicts in the skill. A limit belongs in `specs.py`, with its
source, where a check reads it; nothing here is a limit.

- **Hot loops.** For each switching converter: input capacitor, high-side switch,
  low-side switch, ground, back to the capacitor. It carries the full switched current
  with nanosecond edges; its area is the antenna. Compare its shape, side and vias with
  the datasheet's layout example. A side change inside the loop adds via inductance and
  usually area: call it out unless the datasheet does the same.
- **Return paths.** Low-frequency return takes the lowest-resistance route;
  high-frequency return runs right under the signal in the nearest reference, on a
  pcbkit board the ground pour on the other side. At placement, judge whether the
  parts let a fast net's return run straight under it: a connector or converter placed
  so that bottom-side tracks must cross under the net is the finding. Whether the
  routed copper actually cuts the pour there is `review-board`'s look.
- **Shared impedance.** Trace where load return current (motors, pumps, LEDs, heaters)
  flows through ground copper that a sensor, an ADC reference or a current-sense
  amplifier also uses as its reference. Find where grounds meet and whether a star
  point or a split is intended and actually works.
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
