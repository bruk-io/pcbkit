---
description: >-
  Write a new design check for a pcbkit board, with a sourced limit, and prove it fails.
  Use when the user says "add a check", "make sure X can never happen", "verify that ...",
  or "test that ... stays under/over ..." (for example "add a check that the LED current
  is under 20 mA"), or wants a datasheet limit, derating rule, pin rule, polarity rule or
  layout rule enforced on the board. Also for changing a limit or understanding why a check
  exists. Not for running the checks that exist and reading their results (board-workflow).
---

# Adding a check

A check is a pytest function under `checks/` that fails when the board breaks a rule. It is
not worth having until you have watched it fail on a planted mistake.

## 1. Is it already covered?

The built-in checks are switched on by `[checks] groups` in pcbkit.toml and read their
numbers from specs.py. This lists them: a file's name gives its group (`test_circuit.py` is
the `circuit` group), and its docstring says which specs.py names it reads.

```
grep -n '^def test_' "$(.venv/bin/python -c 'import pcbkit.check.builtin as m; print(m.__path__[0])')"/*.py
```

If one covers the hazard, switch its group on and fill in specs.py. Do not write a second
check for the same thing.

## 2. Pin down the rule and its source

State the hazard in one sentence. Give the limit as a number with a unit, and write its
source beside it in specs.py: the datasheet, the page or table, the URL. If you cannot find
a source, ask the user or dispatch the `parts-researcher` agent. A limit you made up is
worse than no check, because it looks like evidence.

## 3. Write it

- One hazard per check, in `checks/test_<topic>.py`. The fixtures a check can ask for
  (`nl`, `board`, `specs`, `circuits`, `record`, ...) are documented at the top of
  `pcbkit.check.plugin`: `.venv/bin/python -c 'import pcbkit.check.plugin as p; print(p.__doc__)'`.
  `test_led_currents` in the built-in `test_circuit` module is a worked example.
- Parametrise from specs.py with `spec_params`, so a missing spec fails the check by name
  instead of making it vanish.
- The failure message names the part, the value and the limit: `R3 at 21.4 mA, limit
  20 mA`. Hand the numbers a report should show to `record(name, value)`.
- Run just that check: `pcbkit check -k <name>`.

## 4. Prove it fails

- A netlist or circuit check: add an entry to `MUTANTS` in mutants.py (the mistake in words,
  a list of `(text in design.py, replacement)` edits, and the `-k` expression that must
  then fail), then run `pcbkit mutants`. It must say CAUGHT, and the control run must pass.
- A board or copper check: the mutant runner edits design.py only and leaves the layout
  alone. Copy the project to a scratch folder, plant the mistake there (move the part, narrow
  the track), rebuild what the check reads, and run the check in the copy. Never plant it in
  the project itself.
- Quote the failing output in your answer. "It passes" proves nothing.

## 5. A failing check is information

Never loosen a limit, widen a tolerance, delete, xfail or skip a check to get a green run.
If the board is wrong, fix the design, layout or routing. If the limit is wrong, change it
only with a source for the new number, and say so. Skip only when the check does not apply
to this board, with `pytest.skip("<reason naming the part or group>")`: the reason is
listed in out/checks/results.json.
