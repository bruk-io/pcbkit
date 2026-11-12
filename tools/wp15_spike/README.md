# WP15 spike script

Runs the "Needs real KiCad" experiments of `plans/wp15-four-layer.md` (K1 to K17) on a
generic four-layer board, and captures the two-layer byte baseline (K0). Needs KiCad
10.0.6, and for K6 and K7 Java 17 or newer and the Freerouting 1.9.0 jar
(`pcbkit setup` fetches it). K18 is not a KiCad experiment: the report lists it as "ask
PCBWay", with the questions.

## One-off: make `.venv-kicad`

From the root of the checkout (the `wp15-four-layer` branch):

```
KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
uv venv --python $KICAD_PY --system-site-packages .venv-kicad
VIRTUAL_ENV=.venv-kicad uv pip install -e . pytest
```

K16 also needs numpy, scipy and Pillow in that environment. pcbkit depends on them, so
the install above brings them in.

## Run the experiments

```
.venv-kicad/bin/python tools/wp15_spike/spike.py run --out ~/wp15-spike-out
```

About ten minutes. Freerouting opens a window while K6 and K7 run (three router runs in
K6, one in K7); leave it be, it closes itself. Useful options: `--only K5,K6` (the
fixture is always built), `--skip-freerouting`, `--keep-work`, `--k16-big`.

It writes `wp15-spike-report.md` and the `wp15-spike/` folder (boards, DSN, SES, job file,
Gerber and drill listings, DRC reports) into `--out`. Without `--out` it makes a
temporary folder and prints the path at the end.

## Baseline (K0)

Run on main now, and again after slices 2, 3, 4 and 6. Each run makes a fresh
`pcbkit new my-board` project in a temporary folder and runs `build`, `pre` alone,
`finalize`, `check` and `quote` in it.

```
git switch main
.venv-kicad/bin/python tools/wp15_spike/spike.py baseline --out ~/baseline-main-1
.venv-kicad/bin/python tools/wp15_spike/spike.py baseline --out ~/baseline-main-2
.venv-kicad/bin/python tools/wp15_spike/spike.py baseline-diff ~/baseline-main-1 ~/baseline-main-2
```

The last command compares two runs of the same commit: anything it reports (random
UUIDs in a board, say) is noise the real comparison has to allow for. Later, from the
branch under test:

```
.venv-kicad/bin/python tools/wp15_spike/spike.py baseline --out ~/baseline-slice2
.venv-kicad/bin/python tools/wp15_spike/spike.py baseline-diff ~/baseline-main-1 ~/baseline-slice2
```

`baseline-diff` prints what is only in one folder and what changed (first differing
line, and whether the change is only in UUIDs), and exits 1 if anything differs. Gerber
creation-date lines and the `seconds` in the results JSON are stripped when a baseline is
saved.

## What to send back

- `wp15-spike-report.md` and the whole `wp15-spike/` folder from `--out`.
- The baseline folder from main (`~/baseline-main-1`), and the baseline-diff output
  between two runs of main.
