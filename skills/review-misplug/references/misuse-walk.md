# Misuse walk - questions per misuse

The questions to ask at each step of the walk, and the physical reasoning behind
them. The answers need the parts, the cables and the enclosure; where a question
reduces to "which net lands on which pin", that is a check's job (`MATING`, proposed,
read with the netlist's pad nets) and this review starts from its answer.

## Contents

- Building the fit matrix
- Reversed
- Wrong port
- Offset by one
- Hot-plugged while powered
- Left unplugged
- Shorted by a wire or tool
- Reach inside the case
- Cables leaving the case
- Labels
- Buttons, test points, programming port
- Yank load
- Remedies, with examples

## Building the fit matrix

- List every plug that will ever exist near the board, not only the ones in the
  BOM: the user's own harnesses, third-party modules with their stock cables, a
  bench supply lead, a spare cable from the parts drawer.
- A plug with fewer pins than a socket of the same family usually fits:
  in a shroud it can sit at either end; on an unshrouded header it can sit
  anywhere along the row, and on a 2-row field it can sit along either axis.
- A polarising ramp or shroud notch stops reversal in the intended socket only.
  Ask whether it also stops the plug sitting offset in a wider one.
- Different pitch is not proof of no fit. Small-pitch plugs can be pushed into
  a larger-pitch socket and make contact on some pins. Mark it `test`.
- Third-party cables in a "standard" family are not all wired the same. A cable
  sold for the family can arrive reversed end to end. Treat the far end of a
  third-party cable as unknown until checked.

## Reversed

- Is reversal mechanically possible? Polarised housing, shroud with notch,
  latch on one face, or nothing.
- If possible: the mating check gives the pin-by-pin result. Name what the
  plugged device does with the voltage that reaches each wire.
- A reversal that lands each wire on a pin of the same kind (a symmetric
  pinout, such as a differential pair with a ground at each end) is harmless.
  Where reversal puts a device's ground on a signal pin, ask what current flows
  through that pin's driver as the device's return.
- The harmful reversal: supply and ground swap. Ask whether the device or the
  board survives reverse supply, and for how long.

## Wrong port

- For each `fits` or `forced` cell the mating check lands the plug pin by pin;
  judge what drives what. Look for
  supply onto an output, a higher voltage onto a lower-voltage input, two
  outputs fighting, and an interlock loop closed by something that is not the
  interlock.
- A swap that is electrically harmless but functionally wrong still counts when
  it defeats a safety function (a lid interlock that no longer interlocks) or when it
  fails silently (a sensor reads nothing and the firmware acts on it).

## Offset by one

- Unshrouded headers only (and wide shrouds holding narrow plugs). Shift the
  plug one pin each way along its row; on a 2-row header, also one row across.
- A pin hanging off the end of a header is a floating wire on the device side.
- Ask which shift puts supply on the device's ground or signal pin.

## Hot-plugged while powered

- Which pin touches first? Most small headers and wire-to-board families have
  equal-length pins, so the order is set by the angle of insertion and is random.
  Ask what happens when signal arrives before ground, and when supply arrives
  before ground.
- Signal before ground: the device returns its supply current through the signal
  pin's driver and protection diodes. Series resistance limits it.
- Supply into a device with bulk capacitance: inrush sags the shared rail.
  Does anything else on that rail reset or brown out?
- Power inputs (a DC jack, a 24 V terminal block, a USB port feeding a large
  bulk capacitor): inrush on mating, and the arc it draws. Is there a
  soft-start or a load switch so the input never mates into a hard load?
- Latch-up: a powered signal line into an unpowered CMOS device, or the reverse,
  through its protection diodes. Is there a series resistor or a buffer?

## Left unplugged

- What does each input read with nothing connected? A pull resistor or nothing.
- Is that state safe? An interlock input must read "open" when unplugged; a
  power-switch input must read "off".
- If the answer depends on firmware or on what the circuit does next, record it
  and hand it to `walk-fault-states`, which writes the unplugged state as a
  `FAULT_STATES` condition.

## Shorted by a wire or tool

- Adjacent pins at different voltages on any user-reachable connector, header or
  test point. A screwdriver or a stray strand of wire bridges 2.54mm easily.
- Worst pairs: an unfused supply next to anything, a supply next to a logic pin,
  a supply next to ground with no current limit.
- Also bridges between neighbouring connectors on a dense edge, not only within
  one connector.

## Reach inside the case

- Clearance in front of every mating face for the plug body plus the cable's
  strain relief or bend, plus a finger and thumb to grip the plug.
- Latching families (latch on top) need room above the plug to press the latch.
- Anything above the connector row (a lid, a shelf, another board) closes the
  space a plug and a hand need. Compare the gap with plug height plus grip room.
- Case wall: does the cut-out line up with the mating face once the board is
  mounted, and is the wall thin enough for the plug to seat fully?

## Cables leaving the case

- Do cables cross moving parts: arms, wheels, fans, the lid hinge?
- Bend radius at the case wall and where a cable turns hard after leaving the
  connector. Small-gauge wire-to-board cables fatigue at the crimp.
- Do cables pile up in front of other connectors and block them?

## Labels

- Is every port labelled on the silkscreen and on the case, with the same name?
- Does the label say what plugs in and which way (pin 1 or the ground side)?
- Is the silkscreen label still visible once the plug is in and the case is
  closed? If not, the case label is the one that counts.

## Buttons, test points, programming port

- Reset and boot reachable with the case closed: through a hole, a light pipe or
  a flexure button printed into the case.
- Programming port reachable with the case closed and the board mounted.
- Test points needed during bring-up reachable without removing a cover, a
  shield or a plug-in module.
- Can a button be pressed by accident when gripping the case or mating a nearby
  connector?

## Yank load

- For each cable: what takes the force first? Solder joints and SMD pads are the
  wrong answer. Through-hole pins, hold-down tabs, a housing locked into the case
  wall, or a cable anchor in the case are right.
- Heavy or stiff cables (mains leads, thick power leads) need an anchor in the
  case so the connector never carries their weight.
- Does a yank pull the plug out cleanly (fine) or rip the socket off the board
  (not fine)?

## Remedies, with examples

1. Design-out:
   - One family or pin count per function, so nothing else fits.
   - Pins that must never meet at opposite ends of the connector, so no
     offset or narrower plug can bridge them.
   - A presence pin that mates last (shorter, as in card-edge and USB-C
     connectors), so the board sees the plug only when it is fully seated.
   - Shrouded or polarised sockets instead of bare headers.
   - Different pin counts for functions that share a family.
2. Protection parts:
   - Series resistor on each signal that leaves the board, sized for the
     current when the signal becomes someone's ground return.
   - Polyfuse or current limit on supply pins of user ports.
   - A current-limited switch on supply pins a user hot-plugs (a USB host
     port, a sensor port).
3. Labels: silkscreen and case, matching, saying which way round.
