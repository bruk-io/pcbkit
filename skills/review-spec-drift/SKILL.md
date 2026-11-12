---
description: >-
  Compare a pcbkit board as built (design.py, layout.py, pcbkit.toml, specs.py and the
  check results) against its written spec, report every disagreement in both
  directions, ask which side is stale, and move each claim a program can decide into
  the specs.py table or layout.py value a check reads. Use at every design gate -
  after the schematic, after placement, before ordering - and whenever the spec or the
  design changed in conversation, even a small change. Without it Claude checks only
  spec against design, misses parts the spec never mentions and open questions the
  design silently answered, trusts stale diagram captions, recounts what the checks
  already hold, and "fixes" one side itself instead of asking.
---

# Spec drift check

Spec and design drift apart because one gets edited and the other does not. This
review finds every place they disagree and asks the user which side is right. It does
not judge whether a claim is a good idea: ratings, current paths, fault states and plugs
have their own reviews. Gate: every gate.

In pcbkit the structured spec is the board project itself: each claim a program can
decide lives in a `specs.py` table, a `layout.py` value or `pcbkit.toml`, where a check
reads it. The prose spec holds intent, behaviour and open questions.

## 1. Start from what the checks decide

Run `.venv/bin/pcbkit check` (no `-k`) and read `out/checks/results.json`. For each claim
type, [references/claim-homes.md](references/claim-homes.md) says where it lives and
which check holds it. A claim whose check passed matches; one whose check failed
differs, with the check's message as evidence. Never recount by reading what a check
already decided.

## 2. Inputs

- The prose spec: ask where it is (a `README.md` or a spec file in the project, a doc
  elsewhere), and read all of it, captions, tables and notes included. design.py's
  `NOTES` and `COMMENT` are claims too.
- The design: design.py, layout.py, pcbkit.toml, specs.py, and for a later gate the
  files in `out/`. Say which you used. Before placement, claims that need the board are
  `cannot tell`, not skipped.

## 3. Method

1. **Extract claims.** Number every claim in the prose spec. Tag each `mechanical`
   (decidable from the netlist, footprints, BOM or geometry) or `behavioural` (needs
   reasoning: default states, enable chains, "X powers Y only", fail-safe claims).
   Exclusions and open questions are claims too.
2. **Check the spec against itself.** Captions, diagrams and summaries lag the body. Two
   parts of the spec that disagree are a finding before the design is opened.
3. **Mechanical claims**: from the check results (step 1). For a claim no check holds,
   read the design files and record how: which file, which ref or net, what you counted.
4. **Behavioural claims**: trace the circuit that implements each, from design.py:
   follow the enable net to every part it gates, each rail from source to loads. State
   the chain. When a part changed, re-check every claim that mentioned it, the feature
   it gave and anything it fed.
5. **Sweep the design for anything untraced.** Every connector, regulator, IC, switch,
   jumper, mounting hole, test point and `dnp` part must trace to a claim; one that does
   not is `not in spec`. Ask: scope creep, or a spec never updated?
6. **Settle open questions.** For each, say whether the design now answers it (and
   with what), silently assumed an answer, or leaves it open. An answered or assumed
   question is `differs` until the spec records the answer.

Never decide which side is right, and never edit the spec or the design during the
review. Sometimes the spec is the stale one.

Drift to hunt: a part swap that changed a rating in one place only; a consolidation or
split that quietly dropped a feature the spec still lists; a part that kept its name but
changed its job; captions describing an earlier layout. A generic worked example:
[references/example.md](references/example.md).

## Output

Always this format, nothing before it but one line naming the spec, the design files
and the gate.

**Claims**

| # | Claim | Type | Design evidence | Status |
|---|---|---|---|---|

Type is `mechanical` or `behavioural`. Design evidence names the check that settled it,
or the file and ref or net, or the chain traced. Status is one of `matches`, `differs`,
`missing in design`, `not in spec`, `cannot tell`. Conflicts inside the spec are
`differs`, with both spec locations as evidence.

**Decisions needed**: one numbered question per mismatch, each with its two options
("J1 is a terminal block in the design and a barrel jack in the port map. Update the
spec, or change J1 back?"). Order by consequence: what changes the order first.

**Moves to code**: each mechanical claim you checked by hand, with the table or value
it goes into (step 4).

## 4. Move claims to code

Once the user has decided, write each settled mechanical claim where a check reads it,
so the next gate reads a result instead of repeating this: `layout.W`, `H`, `HOLES` for
the outline and holes; `[stackup]` for layers and copper; `SUPPLY_PINS` for a rail at a
load; `I2C_BUSES` for bus devices; `PINOUT` for a part's pin functions; `DEVKIT_GPIO`
and `ANALOG_NETS` on an ESP32-S3 DevKit board. Claims with no table yet go into the
proposed ones (`PIN_NETS`, `PORT_EDGES`, `EXCLUSIONS`, `POWER_PATHS`, `I2C_EXPECTED`),
handed to `add-check`. Behavioural claims stay in the prose spec until a check can hold
them; `walk-fault-states` writes the default-state ones as `FAULT_STATES`.

A kind of claim checked by hand on a second board gets its table and check, and leaves
this skill.
