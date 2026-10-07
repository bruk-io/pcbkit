---
type: llm
---

PASS if the reply tells the user to run pcbkit route (after pcbkit build), and says the
route may be promoted only when DRC is clean, meaning 0 violations, 0 unconnected pads and
0 footprint errors, and that pcbkit finalize follows pcbkit promote.
FAIL if it leaves out the clean-DRC condition, or describes commands that are not pcbkit's,
or claims to have run anything.
