#!/bin/bash
# Make the smallest pcbkit project a case can edit in: a pcbkit.toml and one generated
# board file. Runs in the empty workspace before Claude starts, under --scaffold.
set -euo pipefail
mkdir -p kicad
cat > pcbkit.toml <<'TOML'
[board]
stem = "x"
title = "Demo"
rev = "A"
fab_name = "Demo_revA"
TOML
cat > kicad/x.kicad_pcb <<'PCB'
(kicad_pcb (version 20240108) (generator "pcbnew")
  (title_block (title "Demo"))
)
PCB
