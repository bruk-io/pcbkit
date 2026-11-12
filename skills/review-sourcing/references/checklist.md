# Sourcing review - detailed checks

Read on demand from SKILL.md. Where a line below is already a check or a `pcbkit quote` number, the skill's step 1 says so: read the result instead of redoing it. Nothing here is a number to trust: every limit, price or surcharge comes from the assembler's current pages, read at review time.

## Where to look, and what to record

| Need | Look at | Record |
|------|---------|--------|
| Can the assembler source it | The assembler's parts library / sourcing service, searched by exact manufacturer part number | Found or not, stock, whether it is a stocked line or sourced on demand, date |
| Market stock and price | At least one authorised distributor (for example Digi-Key, Mouser, LCSC, Arrow) | Stock, price break at needed quantity, lead time if out of stock, date |
| Lifecycle | Manufacturer product page first; distributor lifecycle field second | Active / NRND / last-time-buy / obsolete, and any PCN date |
| Assembler limits | The assembler's current assembly capabilities page and an actual quote for this BOM | Page URL and date; quote number |
| Fab standard service | The fab's current capabilities page and instant quote with this board's parameters | Which parameters move the quote off the standard price |

Aggregators (Octopart, FindChips) are fine for finding sources, but record the distributor figure, not the aggregator summary.

Quantity needed per line = quantity per board x boards in batch + the assembler's attrition allowance for that package (from their page or quote). Compare that to stock and MOQ. Reel-only or cut-tape-unavailable parts at a five-board batch are a cost finding.

## Risk sort - what makes a line HIGH

- Single source with no named alternate.
- Lifecycle not Active.
- Assembler cannot source it and it must be consigned.
- Stock under roughly ten batches' worth at the assembler and distributors combined, or a quoted lead time.
- Connector whose exact variant (entry direction, mount, pin count, plating) has not been matched to the footprint.
- Leadless or fine-pitch package with no second source.

## Second sources

For each critical line state:

- The named alternate part number and manufacturer.
- Compatibility class: drop-in pin-compatible / pin-compatible with value changes / footprint-compatible with different pinout (not usable without a respin) / functional only.
- What changes if fitted: feedback or compensation values, inductor, current limit, switching frequency, enable thresholds, thermal pad, firmware or register differences, height.
- Whether the alternate is itself in stock at the assembler (look it up; same rules).

A pin-compatible family sibling with a higher current rating is a good alternate only if the fitted inductor, input/output capacitance and layout support it; say which of these were checked against the datasheet and which were not.

## Consolidation

Candidates:

- Resistors in the same package whose values could be one value without changing function (pull-ups, LED current within a tolerance the design accepts, series damping).
- Capacitors that could share one value and voltage rating, with the higher rating, same dielectric, same package - check DC-bias derating does not cut effective capacitance below what the circuit needs.
- The same value in two packages (0402 and 0603) where one package would do.
- Connectors of the same family in different pin counts where one count with unused pins would work - only if mis-plug risk does not increase (`review-misplug` owns that judgement; flag it, do not decide it).

Not candidates: anything where merging changes timing, filtering, regulation or current limit; deliberate consolidation already made (same regulator repeated by design); parts chosen for a mechanical or thermal reason.

Each proposal states unique lines saved and asks whether the current split is deliberate.

## Assembly cost drivers

Read the assembler's current pricing and capability pages for each of these, and report which ones this board triggers:

- Parts on both sides (second-side setup, second stencil).
- Leadless or fine-pitch packages (QFN, DFN, LGA, BGA, wafer-level): whether the assembler requires or charges for X-ray inspection, and minimum pitch on the standard service.
- Through-hole parts: hand soldering vs selective or wave; per-joint or per-part charges; whether to DNP them and solder by hand instead.
- Stencil needs: step stencil, thickness change, or aperture changes for a mix of fine-pitch and large thermal pads; paste on exposed pads with vias (tenting or plugging).
- Consigned parts, parts needing baking (moisture sensitivity level), and parts with odd packaging (tray, tube, loose).
- Unique line count, if the quote prices per unique part or feeder.
- Board size and panelisation if the assembler requires rails or a panel at small batch.

## Assembly correctness

- **Polarity and pin-1 visible after placement.** The mark must be outside the part body or it disappears under the part. For connectors, mark pin 1 on silkscreen and confirm it matches the mating cable convention.
- **Footprint matches the exact ordered part number.** Open the datasheet for the ordered MPN, not the family page. Check the package variant suffix: pad count, exposed pad size and paste pattern, pitch, body size, height, and for connectors entry direction (top vs side), mount (SMD vs THT), locating pegs or bosses, and retention tabs.
- **Placement orientation against the assembler's convention.** KiCad rotation follows the footprint's own zero orientation; assemblers use their own convention (often based on the tape orientation or an industry zero-orientation standard). Check whether the assembler expects you to correct rotations or corrects them itself, and whether they send a placement preview for approval. Ask for the preview on every first order of a footprint and record which parts needed correction.
- **Placement origin and units.** Placement file origin matches what the assembler expects (board origin, auxiliary origin), units are stated, bottom-side X is or is not mirrored per their convention.
- **Fiducials.** Present, on each assembled side, with the clearance the assembler asks for; local fiducials near fine-pitch parts if they require them.
- **Exposed pads.** Thermal pad paste reduced (windowpane) where the datasheet recommends it.
- **Height and keep-outs.** Tall parts near board edges or under mating parts.

## Connector variant traps

Connector families have many near-identical part numbers. Confirm, for each connector line, against the manufacturer datasheet:

- Entry: top (vertical) or side (right-angle). JST uses the part-number prefix for this in several families (for example a leading `S` for side entry and `B` for top entry in the PH and SH ranges); check the family's ordering-code table rather than relying on the pattern.
- Mount: SMD or through-hole, and whether SMD versions have solder tabs that need their own pads.
- Pin count and pitch match the footprint and the mating housing.
- Locating bosses or pegs and whether the footprint has the holes.
- Mating housing and crimp terminal part numbers recorded, even though they are not assembled - otherwise cables cannot be made.
- Current rating of the variant actually ordered (some families differ by plating or terminal).
- Polarity of high-current connectors (battery): footprint orientation and silkscreen `+` / `-` checked against the physical part.

## Variants and DNP

- Express every variant as a DNP list by reference designator, in the form the assembler accepts (separate BOMs per variant, or one BOM with a DNP column - check their page).
- For each part group that can be absent, confirm: nothing left floating that must be driven (enable pins, feedback nodes, bus lines), no rail is shorted or back-fed, solder jumpers or straps have a stated default and that default matches the variant, test points remain reachable.
- Solder jumpers: state whether each is open or bridged by default, whether the assembler will bridge it (paste on the jumper) or the designer will, and which variant needs which state. A jumper whose default suits only one variant is a finding.
- If one batch mixes variants, confirm the assembler supports it on one order and how it is priced; otherwise order per variant.

## Hand-soldered parts

- Pads extend beyond the part enough for an iron tip.
- Iron access is not blocked by tall neighbours.
- Joints on large copper pours or planes have thermal relief, or the designer accepts needing a hotter iron and preheat.
- THT holes sized for the actual lead (from the ordered part's datasheet) and annular ring adequate.
- The parts and any mating housings are on the designer's own order.

## Fab service tier

Extract from the fab outputs or board definition and compare with the fab's current standard-service limits:

- Layer count.
- Finished copper weight, outer and inner.
- Minimum trace and space.
- Minimum drill and annular ring; via-in-pad, plugged or filled vias.
- Board thickness.
- Impedance control requested or not.
- Surface finish (and whether fine-pitch parts need a flatter finish than HASL).
- Solder mask and silkscreen colour (non-standard colours often change price or lead time).
- Board outline features: castellations, slots, edge plating.

Each parameter outside standard service is a COST finding with the quote delta, or a question if it may not be needed.
