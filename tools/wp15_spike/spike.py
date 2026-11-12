#!/usr/bin/env python3
"""Run the WP15 spike experiments (K1 to K17) and the two-layer byte baseline (K0).

Run it with KiCad's own Python, from the root of a pcbkit checkout, so that `pcbnew`
imports (make `.venv-kicad` first: see tools/wp15_spike/README.md):

    .venv-kicad/bin/python tools/wp15_spike/spike.py run --out DIR
    .venv-kicad/bin/python tools/wp15_spike/spike.py baseline --out DIR_A
    .venv-kicad/bin/python tools/wp15_spike/spike.py baseline-diff DIR_A DIR_B

``run`` builds a small generic four-layer board (`my-board`: six plain parts, a GND
zone on In1, a +5V zone on In2, SMD pads on both plane nets) in a temporary folder and
runs one experiment per question in "Needs real KiCad" of plans/wp15-four-layer.md.
Every experiment runs in its own child Python, so a pcbnew call that does not exist, or
one that takes the process down, is recorded against that experiment and the rest still
run. It writes `wp15-spike-report.md` (a summary table, one section per experiment with
the raw output, and the D3 question answered from the evidence) and a `wp15-spike/`
folder of artefacts (boards, DSN, SES, job file, file listings, DRC reports) into --out,
a folder it makes (default: a new temporary folder, printed at the end).

K6 and K7 run Freerouting 1.9.0 for real (Java 17 or newer and the jar, found the way
pcbkit finds them). On macOS Freerouting opens a window while it routes: leave it be, it
closes itself. Each run is watched with pcbkit's own watchdog (killed if it has not
printed "Starting auto-routing" in 90 s, or is still running after 900 s). K6 runs the
router three times and K7 once, so allow about ten minutes; K17 renders in 3D and is
slow too. `--skip-freerouting` leaves K6 and K7 out.

`baseline` (K0) is separate: it makes a fresh `pcbkit new my-board` project and runs
`build`, `pre` alone, `finalize`, `check` and `quote` in it, saving the normalised
outputs. Run it on main now and again after later slices, then `baseline-diff` the two
folders. Run it twice on the same commit and diff those to see how much is noise.

K18 is not a KiCad experiment: the report lists it as "ask PCBWay", with the questions.

Nothing here imports pcbnew at module level, so the pure parts (verdicts, report,
normalisers, folder diff) are unit-tested without KiCad: tests/unit/test_wp15_spike.py.
Python 3.9 floor, like the rest of pcbkit.
"""

from __future__ import annotations

import difflib
import filecmp
import json
import math
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import click

# --- names and constants --------------------------------------------------------------

REPORT_NAME = "wp15-spike-report.md"
ARTEFACT_DIR = "wp15-spike"
RESULT_TAG = "WP15-RESULT"

MATCHES = "matches"
DIFFERS = "differs"
FAILED = "failed"
SKIPPED = "skipped"
VERDICTS = (MATCHES, DIFFERS, FAILED, SKIPPED)

STEM = "my_board"
BOARD_NAME = "my-board"
WIDTH_MM, HEIGHT_MM, CORNER_MM = 40.0, 30.0, 2.0
THICKNESS_MM = 1.6
COPPER_MM = 0.035
PREPREG_MM = 0.2
PLANE_NETS = ("GND", "+5V")
INNER_LAYERS = ("In1.Cu", "In2.Cu")
GERBER_LAYERS_4 = (
    "F.Cu",
    "In1.Cu",
    "In2.Cu",
    "B.Cu",
    "F.Paste",
    "B.Paste",
    "F.SilkS",
    "B.SilkS",
    "F.Mask",
    "B.Mask",
    "Edge.Cuts",
)

# pcbkit's own defaults for [route] freerouting_passes and stall_timeout_s.
FREEROUTING_PASSES = 40
STALL_TIMEOUT_S = 90

# The most lines of raw output one report section shows; the artefacts hold the rest.
MAX_OBSERVED_LINES = 300

K18_QUESTIONS = (
    "What is PCBWay's standard 1.6 mm four-layer stack (prepreg and core thicknesses)?",
    "What is the default inner copper weight?",
    "What are the minimum inner track and space?",
    "What is the minimum inner annular ring?",
    "What is the minimum hole to inner copper clearance?",
    "What does the order form ask for when the board has four layers?",
)


# --- data model (pure) ----------------------------------------------------------------


class Skip(Exception):
    """Raised by an experiment that cannot run here, with the reason as its text."""


class MissingName(Exception):
    """A pcbnew (or kicad-cli wrapper) name that does not exist as the plan assumed."""

    def __init__(self, owner: str, name: str, close: Sequence[str]) -> None:
        """Keep the owner, the name and the close alternatives found with dir()."""
        self.owner = owner
        self.name = name
        self.close = list(close)
        found = ", ".join(self.close) if self.close else "none"
        super().__init__(f"{owner} has no {name!r}; close names found: {found}")


@dataclass
class Outcome:
    """What one experiment found.

    ``line`` is the one-line answer for the summary table (for a skip, the reason).
    ``observed`` is the raw output; left empty it is filled from the recorder.
    ``facts`` holds machine-readable findings that other parts of the report use.
    """

    verdict: str
    line: str
    observed: str = ""
    artefacts: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Spec:
    """One experiment: the plan's question and assumed answer, and how to run it.

    ``run`` is None for an item that is not a KiCad experiment; ``static`` is then the
    fixed outcome shown for it. ``needs`` names the tools it cannot do without
    ("pcbnew", "kicad-cli", "java", "jar").
    """

    k: str
    title: str
    question: str
    assumed: str
    run: Callable[[Ctx], Outcome] | None = None
    needs: tuple[str, ...] = ()
    timeout: float = 600.0
    static: Outcome | None = None


@dataclass
class Result:
    """A spec with its outcome and how long it took."""

    spec: Spec
    outcome: Outcome
    seconds: float = 0.0


def alternatives(name: str, available: Iterable[str], limit: int = 10) -> list[str]:
    """Return the names in ``available`` closest to ``name``, best first.

    A name is close if difflib finds it similar, or if it shares a word with ``name``
    (``SetLayerType`` and ``GetLayerType`` share "layer" and "type"). Names that start
    with an underscore are left out. This list is the answer a missing name gives.
    """
    pool = sorted({a for a in available if not a.startswith("_") and a != name})
    score: dict[str, float] = {}
    for found in difflib.get_close_matches(name, pool, n=limit, cutoff=0.6):
        ratio = difflib.SequenceMatcher(None, name.lower(), found.lower()).ratio()
        score[found] = 1.0 + ratio
    words = [
        w.lower()
        for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)
        if len(w) >= 3
    ]
    for candidate in pool:
        low = candidate.lower()
        hits = sum(1 for w in words if w in low)
        if hits and candidate not in score:
            ratio = difflib.SequenceMatcher(None, name.lower(), low).ratio()
            score[candidate] = 0.3 * hits + ratio
    return sorted(score, key=lambda c: (-score[c], c))[:limit]


class Checks:
    """The assumptions of one experiment, each held, broken or not observed.

    ``add(what, ok)`` takes True (held), False (broken) or None (could not be
    observed). The verdict follows: any broken one is ``differs``; else any not
    observed one is ``failed``; else ``matches``. No checks at all is ``failed``.
    """

    def __init__(self) -> None:
        """Start with no assumptions recorded."""
        self.items: list[tuple[str, bool | None]] = []

    def add(self, what: str, ok: bool | None) -> bool | None:
        """Record one assumption and return its state."""
        self.items.append((what, ok))
        return ok

    def verdict(self) -> str:
        """Return MATCHES, DIFFERS or FAILED from what was recorded."""
        states = [ok for _, ok in self.items]
        if not states:
            return FAILED
        if any(ok is False for ok in states):
            return DIFFERS
        if any(ok is None for ok in states):
            return FAILED
        return MATCHES

    def summary(self) -> str:
        """Return one line saying which assumptions held, broke or were not observed."""
        held = [w for w, ok in self.items if ok is True]
        broke = [w for w, ok in self.items if ok is False]
        unseen = [w for w, ok in self.items if ok is None]
        parts = []
        if broke:
            parts.append("differs: " + "; ".join(broke))
        if unseen:
            parts.append("not observed: " + "; ".join(unseen))
        if held:
            parts.append("held: " + "; ".join(held))
        return " | ".join(parts) if parts else "nothing was checked"

    def outcome(
        self,
        facts: dict[str, Any] | None = None,
        line: str | None = None,
        observed: str = "",
    ) -> Outcome:
        """Return the Outcome these checks add up to."""
        return Outcome(
            self.verdict(), line or self.summary(), observed, (), facts or {}
        )


def run_experiment(func: Callable[[Ctx], Outcome], ctx: Ctx) -> Outcome:
    """Run ``func`` and return its outcome; an exception becomes a recorded failure.

    A Skip is a skipped experiment, a MissingName a failure that names the missing name
    and its close alternatives, any other exception a failure with its traceback. The
    raw output the experiment recorded is kept in every case.
    """
    try:
        out = func(ctx)
    except Skip as err:
        out = Outcome(SKIPPED, str(err) or "skipped")
    except MissingName as err:
        out = Outcome(
            FAILED,
            f"missing name {err.owner}.{err.name}",
            facts={"missing": [err.owner + "." + err.name], "close": err.close},
        )
        ctx.rec.line("")
        ctx.rec.line(str(err))
    except Exception as err:  # the point: record it and let the rest run
        out = Outcome(FAILED, f"{type(err).__name__}: {err}")
        ctx.rec.line("")
        ctx.rec.line(traceback.format_exc().rstrip())
    if out.verdict not in VERDICTS:
        out = Outcome(FAILED, f"experiment returned the verdict {out.verdict!r}")
    if not out.observed:
        out.observed = ctx.rec.text()
    if not out.artefacts:
        out.artefacts = tuple(ctx.saved)
    return out


def collect(spec: Spec, runner: Callable[[Spec], Outcome]) -> Result:
    """Run one spec through ``runner``; whatever it raises becomes a failed result."""
    start = time.monotonic()
    if spec.static is not None:
        out = spec.static
    else:
        try:
            out = runner(spec)
        except Exception as err:
            out = Outcome(
                FAILED,
                f"the runner raised {type(err).__name__}: {err}",
                traceback.format_exc().rstrip(),
            )
    return Result(spec, out, time.monotonic() - start)


def run_all(specs: Sequence[Spec], runner: Callable[[Spec], Outcome]) -> list[Result]:
    """Run every spec in order; a failure in one never stops the rest."""
    return [collect(spec, runner) for spec in specs]


def unmet_reason(needs: Sequence[str], tools: Mapping[str, str | None]) -> str:
    """Return why a spec cannot run (a needed tool is missing), or "" if it can."""
    return "; ".join(
        f"{n} not found: {HOW_TO_GET.get(n, 'install it')}"
        for n in needs
        if not tools.get(n)
    )


HOW_TO_GET = {
    "pcbnew": "run with .venv-kicad/bin/python (KiCad's Python)",
    "kicad-cli": "install KiCad 10 or put kicad-cli on PATH",
    "java": "install Java 17 or newer (brew install openjdk@21)",
    "jar": "run `pcbkit setup`, or set FREEROUTING_JAR",
}


def describe_exit(code: int) -> str:
    """Return how a child ended: "exit 1", or "killed by SIGSEGV" for a signal."""
    if code < 0:
        try:
            return f"killed by {signal.Signals(-code).name}"
        except ValueError:
            return f"killed by signal {-code}"
    return f"exit {code}"


def parse_child_output(stdout: str) -> Outcome | None:
    """Return the Outcome a child printed on its last RESULT line, or None."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_TAG + " "):
            try:
                data = json.loads(line[len(RESULT_TAG) + 1 :])
                return Outcome(
                    data["verdict"],
                    data["line"],
                    data.get("observed", ""),
                    tuple(data.get("artefacts", ())),
                    data.get("facts", {}),
                )
            except (ValueError, KeyError, TypeError):
                return None
    return None


def run_child(
    argv: Sequence[str], timeout: float, partial: Path | None = None
) -> Outcome:
    """Run a child Python and return its Outcome, or a failure saying how it ended.

    The child writes its raw output to ``partial`` as it goes, so that a crash (a
    segfault, a timeout) still leaves what it had seen.
    """

    def seen() -> str:
        if partial is not None and partial.is_file():
            return partial.read_text(encoding="utf-8", errors="replace")
        return ""

    try:
        done = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Outcome(FAILED, f"timed out after {timeout:g} s", seen())
    parsed = parse_child_output(done.stdout)
    if parsed is not None:
        return parsed
    tail = "\n".join(done.stderr.strip().splitlines()[-15:])
    why = describe_exit(done.returncode)
    return Outcome(
        FAILED,
        f"the child ended without a result ({why})",
        seen() + f"\n--- the child's standard error (tail), {why} ---\n{tail}",
    )


# --- pure parsers and judgements ------------------------------------------------------


def _unquote(token: str) -> str:
    """Return a DSN or SES token without its quotes."""
    return token.strip('"')


def dsn_summary(text: str) -> dict[str, Any]:
    """Return the layers (with types), planes, via padstacks and keep-outs of a DSN."""
    layers: list[list[str]] = []
    for found in re.finditer(r'\(layer\s+("[^"]+"|[^\s()]+)\s*\(type\s+(\w+)\)', text):
        pair = [_unquote(found.group(1)), found.group(2)]
        if pair not in layers:
            layers.append(pair)
    planes = [
        [_unquote(m.group(1)), _unquote(m.group(2))]
        for m in re.finditer(
            r'\(plane\s+("[^"]+"|[^\s()]+)\s*\(polygon\s+("[^"]+"|[^\s()]+)', text
        )
    ]
    vias = sorted(set(re.findall(r"Via\[[^\]]*\]_\d+:\d+_um", text)))
    keepouts = len(re.findall(r"\(keepout\b", text))
    return {
        "layers": layers,
        "planes": planes,
        "via_padstacks": vias,
        "keepouts": keepouts,
    }


def ses_summary(text: str) -> dict[str, dict[str, Any]]:
    """Return, per net of a session file, its wires by layer and its vias."""
    from pcbkit.kicad.sexp import find, parse, walk

    tree = parse(text)
    root = tree if isinstance(tree, list) else []
    out: dict[str, dict[str, Any]] = {}
    for node in walk(root):
        if not node or node[0] != "network_out":
            continue
        for net in node:
            if not (isinstance(net, list) and net and net[0] == "net"):
                continue
            name = str(net[1])
            entry = out.setdefault(name, {"wires": {}, "vias": 0})
            for child in net:
                if not (isinstance(child, list) and child):
                    continue
                if child[0] == "wire":
                    path = find(child, "path")
                    layer = str(path[1]) if path and len(path) > 1 else "?"
                    entry["wires"][layer] = entry["wires"].get(layer, 0) + 1
                elif child[0] == "via":
                    entry["vias"] += 1
    return out


def ses_layer_use(
    summary: Mapping[str, Mapping[str, Any]], nets: Sequence[str]
) -> dict[str, int]:
    """Return the wires per layer and the via count of the named nets, summed.

    A net matches with or without KiCad's leading "/".
    """
    wires: dict[str, int] = {}
    vias = 0
    for name, entry in summary.items():
        if name.lstrip("/") not in nets:
            continue
        vias += int(entry["vias"])
        for layer, count in entry["wires"].items():
            wires[layer] = wires.get(layer, 0) + int(count)
    return {"vias": vias, **{f"wires:{k}": v for k, v in sorted(wires.items())}}


def classify_plane_connection(
    inner_tracks: int,
    plane_vias: int,
    plane_outer_tracks: int,
    smd_plane_pads: int,
    unconnected: int | None,
) -> str:
    """Say how the router connected the plane nets, from counts read off the result.

    "tracks on a plane layer" if any track sits on an inner layer; "vias to the planes"
    if every SMD plane pad could have its own via and there are no plane-net tracks;
    "vias and tracks" for a mix; "tracks on the outer layers" if there are tracks and
    no vias; "not connected" if pads are left unconnected; else "nothing routed".
    """
    if inner_tracks:
        return "tracks on a plane layer"
    if unconnected:
        return "not connected"
    if plane_vias and not plane_outer_tracks and plane_vias >= smd_plane_pads:
        return "vias to the planes"
    if plane_vias and plane_outer_tracks:
        return "vias and tracks"
    if plane_vias:
        return "vias to the planes"
    if plane_outer_tracks:
        return "tracks on the outer layers"
    return "nothing routed"


def judge_k6(facts: Mapping[str, Any]) -> Checks:
    """Return the assumptions of K6 judged from one run's facts (variant A)."""
    checks = Checks()
    ran = bool(facts.get("ses_written"))
    checks.add("Freerouting did not stall", None if facts.get("skipped") else ran)
    if not ran:
        return checks
    checks.add("no tracks on the power layers", facts.get("inner_wires") == 0)
    checks.add(
        "plane-net pads connected with vias",
        facts.get("mode") == "vias to the planes",
    )
    checks.add("DRC unconnected is 0", facts.get("unconnected") == 0)
    same = facts.get("repeat_identical")
    checks.add("same route on repeat", same if same is not None else None)
    return checks


def judge_k10(
    names: Sequence[str], job: Mapping[str, Any], stem: str
) -> list[tuple[str, bool | None]]:
    """Return the K10 assumptions judged from the Gerber file names and the job file."""
    have = set(names)
    out: list[tuple[str, bool | None]] = [
        (f"{stem}-In1_Cu.gbr written", f"{stem}-In1_Cu.gbr" in have),
        (f"{stem}-In2_Cu.gbr written", f"{stem}-In2_Cu.gbr" in have),
        ("LayerNumber is 4", job.get("layer_number") == 4 if job else None),
    ]
    stack = job.get("stackup", []) if job else []
    inner = [s for s in stack if s[1] in INNER_LAYERS and str(s[0]).lower() == "copper"]
    out.append(("MaterialStackup has In1.Cu and In2.Cu as Copper", len(inner) == 2))
    out.append(
        (
            "MaterialStackup has dielectric entries",
            any("copper" not in str(s[0]).lower() for s in stack),
        )
    )
    return out


def parse_job(text: str) -> dict[str, Any]:
    """Return the layer count, thickness, size and stackup of a Gerber job file."""
    data = json.loads(text)
    general = data.get("GeneralSpecs", {})
    stack = [
        [str(m.get("Type", "")), str(m.get("Name", "")), m.get("Thickness")]
        for m in data.get("MaterialStackup", [])
    ]
    return {
        "layer_number": general.get("LayerNumber"),
        "board_thickness": general.get("BoardThickness"),
        "size": general.get("Size"),
        "stackup": stack,
    }


def classify_connection(hits: int, samples: int) -> str:
    """Say how a zone meets a pad or via from the share of ring points that are copper.

    A ring just outside the pad is all copper where the join is solid, mostly empty
    (a gap broken by spokes) where it is a thermal relief, and empty if not joined.
    """
    if samples <= 0:
        return "unknown"
    if hits == samples:
        return "solid"
    if hits == 0:
        return "not joined"
    return f"spokes or partial ({hits} of {samples} points copper)"


def judge_k16(seconds_01: float, rss_mb_01: float, area_ratio: float) -> bool:
    """Return True if a 0.1 mm solve, scaled to the plan's 100 x 80 mm, fits its claim.

    The claim is under a minute and a few GB. The scaling is linear in area, which is
    optimistic for a sparse direct solve, so a True is a floor, not a promise.
    """
    return seconds_01 * area_ratio < 60.0 and rss_mb_01 * area_ratio < 8000.0


def insert_after_setup(text: str, block: str) -> str:
    """Return board text with ``block`` after its "(setup" line; raise if none."""
    start = text.find("(setup")
    if start < 0:
        raise ValueError("the board text has no (setup section")
    after = text.index("\n", start) + 1
    return text[:after] + block + text[after:]


def four_layer_stackup_text(core_mm: float | None = None) -> str:
    """Return a symmetric four-layer stackup block for the board file.

    A stand-in for slice 2's text, in the form place.py writes for two layers. Copper
    plus dielectric add up to THICKNESS_MM when ``core_mm`` is left out; pass another
    core to make the sum differ (K4). The masks are outside the sum.
    """
    if core_mm is None:
        core_mm = round(THICKNESS_MM - 4 * COPPER_MM - 2 * PREPREG_MM, 4)
    diel = '(material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))\n'
    return (
        "\t(stackup\n"
        '\t\t(layer "F.SilkS" (type "Top Silk Screen"))\n'
        '\t\t(layer "F.Paste" (type "Top Solder Paste"))\n'
        '\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))\n'
        f'\t\t(layer "F.Cu" (type "copper") (thickness {COPPER_MM:g}))\n'
        f'\t\t(layer "dielectric 1" (type "prepreg") (thickness {PREPREG_MM:g}) {diel}'
        f'\t\t(layer "In1.Cu" (type "copper") (thickness {COPPER_MM:g}))\n'
        f'\t\t(layer "dielectric 2" (type "core") (thickness {core_mm:g}) {diel}'
        f'\t\t(layer "In2.Cu" (type "copper") (thickness {COPPER_MM:g}))\n'
        f'\t\t(layer "dielectric 3" (type "prepreg") (thickness {PREPREG_MM:g}) {diel}'
        f'\t\t(layer "B.Cu" (type "copper") (thickness {COPPER_MM:g}))\n'
        '\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))\n'
        '\t\t(layer "B.Paste" (type "Bottom Solder Paste"))\n'
        '\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))\n'
        '\t\t(copper_finish "HAL lead-free")\n'
        "\t\t(dielectric_constraints no)\n"
        "\t)\n"
    )


def stackup_rows(board_text: str) -> list[tuple[str, str, str]]:
    """Return (name, type, thickness) of each stackup layer in a board file's text.

    A dielectric's name is blanked (KiCad may renumber them), so rows compare on what
    matters: order, type and thickness.
    """
    from pcbkit.kicad.sexp import find, findall, parse

    tree = parse(board_text)
    setup = find(tree, "setup") if isinstance(tree, list) else None
    stackup = find(setup, "stackup") if setup else None
    rows: list[tuple[str, str, str]] = []
    for node in findall(stackup, "layer") if stackup else []:
        kind = find(node, "type")
        thick = find(node, "thickness")
        name = str(node[1])
        is_copper = bool(kind) and str(kind[1]).lower() == "copper"
        rows.append(
            (
                name if is_copper or not name.startswith("dielectric") else "",
                str(kind[1]) if kind else "",
                str(thick[1]) if thick else "",
            )
        )
    return rows


# --- normalisers and the baseline diff (pure) -----------------------------------------

# A Gerber, drill or job-file line that holds the date the file was made on.
_DATE_LINE = re.compile(
    r"CreationDate|\bdate\s*:?\s*\d{4}-\d{2}-\d{2}|Created by .*\d{4}-"
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def normalise_gerber(data: bytes) -> bytes:
    """Return Gerber, drill or job-file bytes with their creation-date lines removed.

    Bytes in and out (read as Latin-1, which loses nothing), so a file with odd bytes
    is carried through untouched apart from the lines that name a date.
    """
    lines = data.decode("latin-1").splitlines(keepends=True)
    return "".join(line for line in lines if not _DATE_LINE.search(line)).encode(
        "latin-1"
    )


def _drop_seconds(value: Any) -> Any:
    """Return JSON data with every "seconds" key removed, at any depth."""
    if isinstance(value, dict):
        return {k: _drop_seconds(v) for k, v in value.items() if k != "seconds"}
    if isinstance(value, list):
        return [_drop_seconds(v) for v in value]
    return value


def normalise_results(data: bytes) -> bytes:
    """Return a results.json with its "seconds" removed; keep key order, indent 2."""
    parsed = _drop_seconds(json.loads(data.decode("utf-8")))
    return (json.dumps(parsed, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class FileChange:
    """A file that is in both folders and differs: where, and whether only in UUIDs."""

    path: str
    first_line: int
    a_line: str
    b_line: str
    uuid_only: bool
    unified: str


@dataclass(frozen=True)
class TreeDiff:
    """The difference of two baseline folders, as relative paths."""

    only_a: tuple[str, ...]
    only_b: tuple[str, ...]
    changed: tuple[FileChange, ...]
    same: int

    @property
    def identical(self) -> bool:
        """Return True if nothing is missing, extra or changed."""
        return not (self.only_a or self.only_b or self.changed)


def mask_uuids(text: str) -> str:
    """Return text with every UUID replaced by a fixed one."""
    return _UUID.sub("00000000-0000-0000-0000-000000000000", text)


def diff_trees(
    a: Mapping[str, bytes], b: Mapping[str, bytes], context_lines: int = 12
) -> TreeDiff:
    """Compare two folders held as {relative path: bytes}, and say what differs."""
    changed: list[FileChange] = []
    same = 0
    for path in sorted(set(a) & set(b)):
        if a[path] == b[path]:
            same += 1
            continue
        la = a[path].decode("latin-1").splitlines()
        lb = b[path].decode("latin-1").splitlines()
        first = next(
            (
                i
                for i in range(max(len(la), len(lb)))
                if (la[i] if i < len(la) else None) != (lb[i] if i < len(lb) else None)
            ),
            0,
        )
        unified = list(
            difflib.unified_diff(la, lb, "A/" + path, "B/" + path, n=1, lineterm="")
        )
        changed.append(
            FileChange(
                path,
                first + 1,
                la[first] if first < len(la) else "(end of file)",
                lb[first] if first < len(lb) else "(end of file)",
                mask_uuids("\n".join(la)) == mask_uuids("\n".join(lb)),
                "\n".join(unified[: context_lines + 2]),
            )
        )
    return TreeDiff(
        tuple(sorted(set(a) - set(b))),
        tuple(sorted(set(b) - set(a))),
        tuple(changed),
        same,
    )


def format_diff(diff: TreeDiff, label_a: str = "A", label_b: str = "B") -> str:
    """Return the text `baseline-diff` prints for a TreeDiff."""
    if diff.identical:
        return f"identical: {diff.same} files compared, no difference"
    out = [
        f"{diff.same} files identical, {len(diff.changed)} changed, "
        f"{len(diff.only_a)} only in {label_a}, {len(diff.only_b)} only in {label_b}"
    ]
    for path in diff.only_a:
        out.append(f"only in {label_a}: {path}")
    for path in diff.only_b:
        out.append(f"only in {label_b}: {path}")
    for change in diff.changed:
        note = " (differs only in UUIDs)" if change.uuid_only else ""
        out.append(f"changed: {change.path}, first at line {change.first_line}{note}")
        out.append(f"    {label_a}: {change.a_line[:120]}")
        out.append(f"    {label_b}: {change.b_line[:120]}")
    return "\n".join(out)


# --- report rendering (pure) ----------------------------------------------------------


def fence_for(text: str) -> str:
    """Return a backtick fence longer than any run of backticks inside ``text``."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def clip(text: str, max_lines: int = MAX_OBSERVED_LINES) -> str:
    """Return text cut to ``max_lines`` lines, saying how many were left out."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    more = len(lines) - max_lines
    return "\n".join(
        lines[:max_lines] + [f"... ({more} more lines; see the artefacts)"]
    )


def one_line(text: str, width: int = 160) -> str:
    """Return text on one line, cut with an ellipsis, with table pipes escaped."""
    flat = " ".join(text.split()).replace("|", "\\|")
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def summary_table(results: Sequence[Result]) -> str:
    """Return the Markdown summary table: K number, verdict, one line."""
    rows = ["| K | Verdict | One line |", "|---|---|---|"]
    for r in results:
        rows.append(
            f"| {r.spec.k} | {r.outcome.verdict} | {one_line(r.outcome.line)} |"
        )
    return "\n".join(rows)


def section(result: Result) -> str:
    """Return one experiment's report section."""
    spec, out = result.spec, result.outcome
    parts = [
        f"## {spec.k}: {spec.title}",
        "",
        f"**Question.** {spec.question}",
        "",
        f"**The plan assumes.** {spec.assumed}",
        "",
        f"**Verdict: {out.verdict}.** {out.line}",
        "",
    ]
    if out.observed.strip():
        body = clip(out.observed.rstrip())
        fence = fence_for(body)
        parts += ["**Observed.**", "", fence, body, fence, ""]
    if out.artefacts:
        parts.append("**Artefacts.** " + ", ".join(f"`{a}`" for a in out.artefacts))
        parts.append("")
    if result.seconds >= 1:
        parts += [f"_Took {result.seconds:.0f} s._", ""]
    return "\n".join(parts)


def by_k(results: Sequence[Result]) -> dict[str, Outcome]:
    """Return the outcomes keyed by K number."""
    return {r.spec.k: r.outcome for r in results}


D3_QUESTION = (
    "Does Freerouting 1.9.0, given the DSN KiCad exports for a four-layer board with "
    "power-type inner layers and plane zones, connect plane-net pads with vias and "
    "stay off the power layers, or not?"
)


def d3_section(results: Sequence[Result]) -> str:
    """Return the closing section: the D3 question and what the evidence says."""
    outs = by_k(results)
    k5, k6, k7 = outs.get("K5"), outs.get("K6"), outs.get("K7")
    lines = ["## D3: the question the spike exists to answer", "", D3_QUESTION, ""]
    lines.append("**What the evidence says.**")
    lines.append("")
    if k5 and k5.verdict in (MATCHES, DIFFERS) and k5.facts:
        unfilled = k5.facts.get("unfilled", {})
        lines.append(
            "- K5 (the DSN): layers "
            + (", ".join(f"{n} ({t})" for n, t in unfilled.get("layers", [])) or "?")
            + "; planes "
            + (
                ", ".join(f"{n} on {lay}" for n, lay in unfilled.get("planes", []))
                or "none"
            )
            + "."
        )
    elif k5:
        lines.append(
            f"- K5 did not give a reading ({k5.verdict}: {one_line(k5.line)})."
        )
    if k6 is None or k6.verdict in (SKIPPED, FAILED) and not k6.facts.get("a"):
        why = k6.line if k6 else "K6 was not part of this run"
        state = k6.verdict if k6 else "absent"
        lines += [
            f"- K6 gave no reading ({state}: {one_line(why)}).",
            "",
            "**Reading.** There is no evidence yet: D3 stays open until K6 has run on "
            "the Mac with Java and the Freerouting jar.",
        ]
        return "\n".join(lines) + "\n"
    a = k6.facts.get("a", {})
    b = k6.facts.get("b")
    lines.append(
        f"- K6, variant A (power-type layers, planes as zones): {describe_run(a)}"
    )
    if b:
        lines.append(
            f"- K6, variant B (the same plus a rule area banning tracks on In1 and "
            f"In2): {describe_run(b)}"
        )
    if k7:
        inc = k7.facts.get("inc_in_help")
        lines.append(
            "- K7: "
            + (
                "the `-inc` option is listed by `-help`."
                if inc
                else "`-inc` is not in `-help` (see K7)."
                if inc is False
                else f"no reading ({k7.verdict}: {one_line(k7.line)})."
            )
        )
    lines.append("")
    lines.append("**Reading.** " + d3_reading(a, b, k7.facts if k7 else {}))
    lines.append("")
    lines.append(
        "This is the evidence, not the decision: D3 is signed off after the spike by "
        "the owner."
    )
    return "\n".join(lines) + "\n"


def describe_run(facts: Mapping[str, Any]) -> str:
    """Return a sentence for one Freerouting variant's facts."""
    if not facts:
        return "did not run."
    if not facts.get("ses_written"):
        return (
            f"{facts.get('outcome', 'no result')}: no session file was written "
            f"({facts.get('detail', '')})."
        )
    return (
        f"routed in {facts.get('seconds', 0):.0f} s; "
        f"wires on In1.Cu/In2.Cu: {facts.get('inner_wires')}; "
        f"plane-net vias: {facts.get('plane_vias')}; "
        f"plane-net tracks on the outer layers: {facts.get('plane_outer_tracks')}; "
        f"DRC unconnected: {facts.get('unconnected')}; "
        f"connection: {facts.get('mode')}; "
        f"normalisation failures in the log: {facts.get('normalization_failures')}."
    )


def d3_reading(
    a: Mapping[str, Any], b: Mapping[str, Any] | None, k7: Mapping[str, Any]
) -> str:
    """Return the sentence that says which D3 option the measurements point at."""
    if not a.get("ses_written"):
        return (
            "Freerouting did not produce a route from the plane DSN (stalled, timed "
            "out or failed): that is the case for (b), pcbkit fans out plane nets "
            "itself in `pre`. "
        ) + _inc_note(k7)
    clean = (
        a.get("inner_wires") == 0
        and a.get("mode") == "vias to the planes"
        and a.get("unconnected") == 0
    )
    if clean and a.get("repeat_identical") is not False:
        return (
            "Freerouting connected the plane-net pads with vias and kept off the "
            "power layers, with no stall: build option (a)."
        )
    problems = []
    if a.get("inner_wires"):
        problems.append(f"it put {a['inner_wires']} wires on a power layer")
    if a.get("mode") not in ("vias to the planes",):
        problems.append(f"it connected the plane nets as: {a.get('mode')}")
    if a.get("unconnected"):
        problems.append(f"{a['unconnected']} pads were left unconnected")
    if a.get("repeat_identical") is False:
        problems.append("a repeat run gave a different route")
    text = "Not clean: " + "; ".join(problems or ["see K6"]) + ". "
    if b is not None and b.get("ses_written"):
        fixed = b.get("inner_wires") == 0 and b.get("unconnected") == 0
        text += "With the track-ban rule areas added the result " + (
            "is clean (the rule areas are what keeps it off the planes). "
            if fixed
            else "is still not clean. "
        )
    return text + "That points at (b), a fan-out in `pre`. " + _inc_note(k7)


def _inc_note(k7: Mapping[str, Any]) -> str:
    """Return a sentence on whether (b)'s `-inc` flag exists, from K7's facts."""
    inc = k7.get("inc_in_help")
    if inc is None:
        return "Whether `-inc` exists (needed for (b)) is not known: see K7."
    return (
        "`-inc` exists in 1.9.0, so (b) can ignore a net class."
        if inc
        else "`-inc` is not in 1.9.0's help, so (b) cannot use it as planned."
    )


def render_report(
    results: Sequence[Result], meta: Mapping[str, str], work_note: str = ""
) -> str:
    """Return the whole Markdown report: title, summary table, sections, D3."""
    head = ["# WP15 spike report", ""]
    head += [f"- {k}: {v}" for k, v in meta.items()]
    if work_note:
        head.append(f"- {work_note}")
    parts = ["\n".join(head), "", summary_table(results), ""]
    parts += [section(r) for r in results]
    parts.append(d3_section(results))
    return "\n".join(parts)


# --- the recorder and the context an experiment gets ----------------------------------


class Recorder:
    """Collect an experiment's raw output, and write it through to a file as it goes."""

    def __init__(self, partial: Path | None = None) -> None:
        """Start empty; if ``partial`` is given, every line is also appended there."""
        self.lines: list[str] = []
        self.partial = partial
        if partial is not None:
            partial.write_text("", encoding="utf-8")

    def line(self, text: str = "") -> None:
        """Record text (which may have several lines)."""
        for piece in str(text).split("\n") if text else [""]:
            self.lines.append(piece)
            if self.partial is not None:
                with open(self.partial, "a", encoding="utf-8") as handle:
                    handle.write(piece + "\n")

    def text(self) -> str:
        """Return everything recorded."""
        return "\n".join(self.lines)

    def attr(self, obj: Any, name: str, owner: str | None = None) -> Any:
        """Return ``obj.name``; if it is missing, record the close names and raise.

        The raise is a MissingName, which ends the experiment as a failure that names
        exactly what was missing. The close names come from ``dir(obj)``.
        """
        try:
            return getattr(obj, name)
        except AttributeError:
            label = owner or getattr(obj, "__name__", None) or type(obj).__name__
            close = alternatives(name, dir(obj))
            self.line(
                f"MISSING {label}.{name}; close names: {', '.join(close) or 'none'}"
            )
            raise MissingName(label, name, close) from None

    def probe(
        self, obj: Any, name: str, *args: Any, label: str = ""
    ) -> tuple[bool, Any]:
        """Call ``obj.name(*args)`` if it exists, record the result, never raise.

        Return (True, value) or (False, None). A missing name is recorded with its
        close names and does not end the experiment.
        """
        owner = getattr(obj, "__name__", None) or type(obj).__name__
        shown = label or f"{owner}.{name}({', '.join(map(repr, args))})"
        if not hasattr(obj, name):
            close = alternatives(name, dir(obj))
            self.line(f"{shown}: MISSING; close names: {', '.join(close) or 'none'}")
            return False, None
        try:
            value = getattr(obj, name)
            value = value(*args) if callable(value) else value
        except Exception as err:
            self.line(f"{shown}: raised {type(err).__name__}: {err}")
            return False, None
        self.line(f"{shown}: {value!r}")
        return True, value


@dataclass
class Ctx:
    """What an experiment gets: folders, the recorder, the tools found, the options."""

    work: Path
    art: Path
    rec: Recorder
    tools: Mapping[str, str | None] = field(default_factory=dict)
    opts: Mapping[str, Any] = field(default_factory=dict)
    saved: list[str] = field(default_factory=list)

    def pcbnew(self) -> Any:
        """Return the pcbnew module (quieted as pcbkit does); raise Skip if absent."""
        try:
            from pcbkit.kicad import env

            return env.import_pcbnew()
        except ImportError as err:
            raise Skip(f"pcbnew does not import here ({err})") from None

    def scratch(self, name: str) -> Path:
        """Return a fresh folder under the work folder."""
        path = self.work / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return path

    def fixture(self, variant: str) -> Path:
        """Return the fixture board of this variant; raise Skip if it was not built."""
        path = self.work / "fixture" / f"four_{variant}.kicad_pcb"
        if not path.is_file():
            raise Skip(f"the fixture board {path.name} was not built (see 'setup')")
        return path

    def save(self, src: Path, name: str = "") -> str:
        """Copy a file into the artefact folder and return its report-relative name."""
        self.art.mkdir(parents=True, exist_ok=True)
        target = name or src.name
        shutil.copy(src, self.art / target)
        label = f"{ARTEFACT_DIR}/{target}"
        if label not in self.saved:
            self.saved.append(label)
        return label

    def save_text(self, text: str, name: str) -> str:
        """Write text into the artefact folder and return its report-relative name."""
        self.art.mkdir(parents=True, exist_ok=True)
        (self.art / name).write_text(text, encoding="utf-8")
        label = f"{ARTEFACT_DIR}/{name}"
        if label not in self.saved:
            self.saved.append(label)
        return label


# --- process helpers (Freerouting) ----------------------------------------------------


def peak_rss_mb() -> float:
    """Return this process's peak memory in MB (macOS counts bytes, Linux KB)."""
    import resource

    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def find_router_or_skip() -> Any:
    """Return pcbkit's Router (Java and jar); raise Skip with the reason if absent."""
    import click as _click

    from pcbkit.route import freerouting

    try:
        return freerouting.find_router()
    except _click.ClickException as err:
        raise Skip(err.message) from None


def run_watched(
    command: Sequence[str], cwd: Path, log: Path, stall_s: float, hard_s: float
) -> tuple[str, int | None, str, float]:
    """Run a command under pcbkit's Freerouting watchdog; return how it ended.

    The result is (outcome, return code, detail, seconds). It runs in its own process
    group with its output in ``log``, as `pcbkit route` does.
    """
    from pcbkit.route import freerouting

    start = time.monotonic()
    with open(log, "wb") as handle:
        proc = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        outcome, code, detail = freerouting.watch(proc, log, stall_s, hard_s)
    return outcome, code, detail, time.monotonic() - start


def run_freerouting(
    folder: Path,
    dsn_name: str,
    router: Any,
    extra: Sequence[str] = (),
    passes: int = FREEROUTING_PASSES,
    stall_s: float = STALL_TIMEOUT_S,
) -> dict[str, Any]:
    """Run Freerouting once on ``folder/dsn_name`` and return what happened.

    The command is pcbkit's `build_command`, plus ``extra`` arguments. The session file
    is ``<stem>.ses`` and the console log ``freerouting.log``, both in ``folder``.
    """
    from pcbkit.route import freerouting

    ses = folder / f"{STEM}.ses"
    log = folder / "freerouting.log"
    ses.unlink(missing_ok=True)
    command = freerouting.build_command(
        router, dsn_name, ses.name, passes, freerouting.xvfb_command()
    ) + list(extra)
    outcome, code, detail, seconds = run_watched(
        command, folder, log, stall_s, freerouting.HARD_TIMEOUT_S
    )
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    parsed = freerouting.parse_log(text)
    return {
        "command": command,
        "outcome": outcome,
        "code": code,
        "detail": detail,
        "seconds": seconds,
        "ses": ses,
        "ses_written": ses.is_file(),
        "log": log,
        "log_text": text,
        "started": parsed.started_routing,
        "routing_seconds": parsed.routing_seconds,
        "normalization_failures": len(parsed.normalization_failures),
    }


def log_highlights(text: str, limit: int = 25) -> str:
    """Return the log lines that mention layers, planes, power or normalisation."""
    keep = [
        ln.strip()
        for ln in text.splitlines()
        if re.search(r"(?i)plane|power|normali|layer|warn|error", ln)
    ]
    return "\n".join(keep[:limit])


# --- the generic four-layer fixture ---------------------------------------------------

# ref, library, footprint, x and y in layout mm. Six plain parts on a 40 x 30 mm board.
PARTS = (
    ("J1", "Connector_PinHeader_2.54mm", "PinHeader_1x02_P2.54mm_Vertical", 6.0, 8.0),
    ("R1", "Resistor_SMD", "R_0603_1608Metric", 14.0, 8.0),
    ("D1", "LED_SMD", "LED_0603_1608Metric", 22.0, 8.0),
    ("R2", "Resistor_SMD", "R_0603_1608Metric", 14.0, 20.0),
    ("R3", "Resistor_SMD", "R_0603_1608Metric", 22.0, 20.0),
    ("C1", "Capacitor_SMD", "C_0603_1608Metric", 32.0, 18.0),
)
PAD_NETS = {
    ("J1", "1"): "+5V",
    ("J1", "2"): "GND",
    ("R1", "1"): "+5V",
    ("R1", "2"): "LED_A",
    ("D1", "1"): "GND",
    ("D1", "2"): "LED_A",
    ("R2", "1"): "+5V",
    ("R2", "2"): "SENSE",
    ("R3", "1"): "SENSE",
    ("R3", "2"): "GND",
    ("C1", "1"): "+5V",
    ("C1", "2"): "GND",
}
# SMD pads on a plane net: R1.1, D1.1, R2.1, R3.2, C1.1, C1.2 (J1 is through-hole).
SMD_PLANE_PADS = 6
GND_VIA = (20.0, 14.0)
V5_VIA = (30.0, 24.0)


def plain_hooks() -> Any:
    """Return pcbkit Hooks with one net class ("Planes") for the two plane nets."""
    from pcbkit.route.hooks import Hooks

    return Hooks(
        netclasses={"Planes": (0.4, 0.2, 0.7, 0.3, ("/GND", "/+5V"))},
        design_rules=None,
        prerouted=lambda board, kb: None,
        keepouts=None,
        gnd_links=None,
        zones=None,
        solid_pad_refs=frozenset(),
    )


def make_board(ctx: Ctx, path: Path, parts: bool = True) -> Any:
    """Make a four-layer board with NewBoard (nets, parts, outline); do not save it."""
    from pcbkit.kicad import board as kb
    from pcbkit.kicad import env

    pcbnew = ctx.pcbnew()
    board = pcbnew.NewBoard(str(path))
    board.SetCopperLayerCount(4)
    board.GetDesignSettings().SetBoardThickness(kb.mm(THICKNESS_MM))
    w, h, r = WIDTH_MM, HEIGHT_MM, CORNER_MM
    kb.add_line(board, r, 0, w - r, 0)
    kb.add_line(board, w, r, w, h - r)
    kb.add_line(board, w - r, h, r, h)
    kb.add_line(board, 0, h - r, 0, r)
    kb.add_arc(board, w - r, r, w - r, 0, w, r)
    kb.add_arc(board, w - r, h - r, w, h - r, w - r, h)
    kb.add_arc(board, r, h - r, r, h, 0, h - r)
    kb.add_arc(board, r, r, 0, r, r, 0)
    if not parts:
        return board
    nets: dict[str, Any] = {}
    for name in sorted(set(PAD_NETS.values())):
        nets[name] = pcbnew.NETINFO_ITEM(board, "/" + name)
        board.Add(nets[name])
    for ref, lib, name, x, y in PARTS:
        folder = env.footprints_dir() / f"{lib}.pretty"
        footprint = pcbnew.FootprintLoad(str(folder), name)
        if footprint is None:
            raise RuntimeError(f"footprint {lib}:{name} not found in {folder}")
        footprint.SetFPID(pcbnew.LIB_ID(lib, name))
        footprint.SetReference(ref)
        footprint.SetValue(name.split("_")[0])
        board.Add(footprint)
        footprint.SetPosition(kb.pt(x, y))
        footprint.SetOrientationDegrees(0)
        for pad in footprint.Pads():
            net = PAD_NETS.get((ref, pad.GetNumber()))
            if net:
                pad.SetNet(nets[net])
    return board


def save_with_stackup(
    pcbnew: Any, board: Any, path: Path, core_mm: float | None = None
) -> None:
    """Save a board and write the four-layer stackup text into the saved file."""
    pcbnew.SaveBoard(str(path), board)
    text = path.read_text(encoding="utf-8")
    block = four_layer_stackup_text(core_mm)
    path.write_text(insert_after_setup(text, block), encoding="utf-8", newline="")


def add_plane_zones(ctx: Ctx, board: Any) -> None:
    """Add the GND zone on In1 (thermal) and the +5V zone on In2 (solid)."""
    from pcbkit.kicad import board as kb

    pcbnew = ctx.pcbnew()
    outline = kb.rect(0.3, 0.3, WIDTH_MM - 0.3, HEIGHT_MM - 0.3)
    kb.zone(board, "GND", pcbnew.In1_Cu, outline, full=False)
    kb.zone(board, "+5V", pcbnew.In2_Cu, outline, full=True)


def fill(ctx: Ctx, board: Any) -> None:
    """Fill every zone of the board."""
    pcbnew = ctx.pcbnew()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def exp_setup(ctx: Ctx) -> Outcome:
    """Build the generic four-layer fixture boards that the experiments share."""
    from pcbkit.kicad import board as kb
    from pcbkit.route.pre import edge_strips
    from pcbkit.route.rules import apply_rules

    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    fx = ctx.scratch("fixture")
    built: list[str] = []

    def stage(variant: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            built.append(variant)
            rec.line(f"built four_{variant}.kicad_pcb")
        except Exception as err:
            rec.line(f"FAILED to build four_{variant}: {type(err).__name__}: {err}")
            rec.line(traceback.format_exc().rstrip())

    def bare() -> None:
        board = make_board(ctx, fx / "four_bare.kicad_pcb")
        ok, _ = rec.probe(
            board, "SetLayerType", pcbnew.In1_Cu, getattr(pcbnew, "LT_POWER", None)
        )
        rec.probe(
            board, "SetLayerType", pcbnew.In2_Cu, getattr(pcbnew, "LT_POWER", None)
        )
        if not ok:
            rec.line("the inner layers stay 'signal': SetLayerType did not work")
        save_with_stackup(pcbnew, board, fx / "four_bare.kicad_pcb")

    def unfilled() -> None:
        board = pcbnew.LoadBoard(str(fx / "four_bare.kicad_pcb"))
        apply_rules(board, plain_hooks(), 4)
        add_plane_zones(ctx, board)
        for x0, y0, x1, y1 in edge_strips(WIDTH_MM, HEIGHT_MM):
            kb.keepout(board, x0, y0, x1, y1, pours=False)
        pcbnew.SaveBoard(str(fx / "four_unfilled.kicad_pcb"), board)

    def banned() -> None:
        board = pcbnew.LoadBoard(str(fx / "four_unfilled.kicad_pcb"))
        kb.keepout(
            board,
            0,
            0,
            WIDTH_MM,
            HEIGHT_MM,
            tracks=True,
            vias=False,
            pours=False,
            layers=INNER_LAYERS,
        )
        pcbnew.SaveBoard(str(fx / "four_banned.kicad_pcb"), board)

    def filled() -> None:
        board = pcbnew.LoadBoard(str(fx / "four_unfilled.kicad_pcb"))
        fill(ctx, board)
        pcbnew.SaveBoard(str(fx / "four_filled.kicad_pcb"), board)

    def vias() -> None:
        board = pcbnew.LoadBoard(str(fx / "four_unfilled.kicad_pcb"))
        kb.via(board, *GND_VIA, "GND", d=0.7, drill=0.3)
        kb.via(board, *V5_VIA, "+5V", d=0.7, drill=0.3)
        outline = kb.rect(0.3, 0.3, WIDTH_MM - 0.3, HEIGHT_MM - 0.3)
        kb.zone(board, "GND", pcbnew.F_Cu, outline)
        kb.zone(board, "GND", pcbnew.B_Cu, outline)
        fill(ctx, board)
        pcbnew.SaveBoard(str(fx / "four_vias.kicad_pcb"), board)

    stage("bare", bare)
    if "bare" in built:
        stage("unfilled", unfilled)
    if "unfilled" in built:
        stage("banned", banned)
        stage("filled", filled)
        stage("vias", vias)
    for variant in built:
        ctx.save(fx / f"four_{variant}.kicad_pcb")
    need = {"bare", "unfilled", "filled", "vias", "banned"}
    missing = sorted(need - set(built))
    if missing:
        return Outcome(
            FAILED, "fixture boards not built: " + ", ".join(missing), facts={}
        )
    return Outcome(
        MATCHES,
        f"{BOARD_NAME}: 6 parts, GND zone on In1, +5V zone on In2, 5 board variants",
    )


# --- experiments K1 to K17 ------------------------------------------------------------


def exp_k1(ctx: Ctx) -> Outcome:
    """Run K1: the ids and names of the inner copper layers."""
    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    board = pcbnew.NewBoard(str(ctx.scratch("k1") / "f.kicad_pcb"))
    board.SetCopperLayerCount(4)
    ids = [rec.attr(pcbnew, n) for n in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu")]
    rec.line(f"F_Cu, In1_Cu, In2_Cu, B_Cu = {ids}")
    names = [board.GetLayerName(i) for i in (ids[1], ids[2])]
    rec.line(f"GetLayerName(In1_Cu, In2_Cu) = {names}")
    rec.probe(board, "GetCopperLayerCount")
    inner = [n for n in dir(pcbnew) if re.fullmatch(r"In\d+_Cu", n)]
    rec.line(f"inner layer symbols in pcbnew: {len(inner)} ({inner[0]}..{inner[-1]})")
    checks = Checks()
    checks.add("ids are 0, 4, 6, 2", ids == [0, 4, 6, 2])
    checks.add('names are "In1.Cu", "In2.Cu"', names == ["In1.Cu", "In2.Cu"])
    return checks.outcome({"ids": ids, "names": names})


def exp_k2(ctx: Ctx) -> Outcome:
    """Run K2: SetLayerType, and how a power layer is saved."""
    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    path = ctx.scratch("k2") / "f.kicad_pcb"
    board = pcbnew.NewBoard(str(path))
    board.SetCopperLayerCount(4)
    rec.line(
        "LT_ names in pcbnew: "
        + ", ".join(n for n in dir(pcbnew) if n.startswith("LT_"))
    )
    power = rec.attr(pcbnew, "LT_POWER")
    setter = rec.attr(board, "SetLayerType")
    setter(pcbnew.In1_Cu, power)
    setter(pcbnew.In2_Cu, power)
    rec.line("SetLayerType(In1_Cu, LT_POWER) and (In2_Cu, LT_POWER): no error")
    rec.probe(board, "GetLayerType", pcbnew.In1_Cu)
    pcbnew.SaveBoard(str(path), board)
    text = path.read_text(encoding="utf-8")
    rec.line("grep -n 'In[12].Cu' on the saved board:")
    for number, line in enumerate(text.splitlines(), 1):
        if re.search(r"In[12]\.Cu", line):
            rec.line(f"  {number}: {line.strip()}")
    types = dict(re.findall(r'\(\d+\s+"In([12])\.Cu"\s+(\w+)', text))
    rec.line(f"types read from the (layers ...) section: {types}")
    again = pcbnew.LoadBoard(str(path))
    ok, back = rec.probe(
        again, "GetLayerType", pcbnew.In1_Cu, label="after reload, GetLayerType(In1_Cu)"
    )
    ctx.save(path, "k2_power_layers.kicad_pcb")
    checks = Checks()
    checks.add("SetLayerType exists and takes (layer, LT_POWER)", True)
    checks.add(
        'the saved layers section says "power" for In1.Cu and In2.Cu',
        (types == {"1": "power", "2": "power"}) if types else None,
    )
    checks.add("the type survives a reload", (back == power) if ok else None)
    return checks.outcome({"types": types})


def exp_k3(ctx: Ctx) -> Outcome:
    """Run K3: a hand-inserted four-layer stackup through LoadBoard and SaveBoard."""
    from pcbkit.kicad import board as kb

    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    folder = ctx.scratch("k3")
    first, second = folder / "a.kicad_pcb", folder / "b.kicad_pcb"
    board = pcbnew.NewBoard(str(first))
    board.SetCopperLayerCount(4)
    save_with_stackup(pcbnew, board, first)
    loaded = pcbnew.LoadBoard(str(first))
    if loaded is None:
        raise RuntimeError("LoadBoard returned None for the board with the stackup")
    pcbnew.SaveBoard(str(second), loaded)
    before = stackup_rows(first.read_text(encoding="utf-8"))
    after_text = second.read_text(encoding="utf-8")
    after = stackup_rows(after_text)
    rec.line("stackup rows before (name, type, thickness; dielectric names blank):")
    for row in before:
        rec.line(f"  {row}")
    rec.line("stackup rows after LoadBoard + SaveBoard:")
    for row in after:
        rec.line(f"  {row}")
    start = after_text.find("(stackup")
    rec.line("the saved stackup block, first 40 lines:")
    rec.line(
        "\n".join(after_text[start:].splitlines()[:40]) if start >= 0 else "(none)"
    )
    inner = re.findall(r'\(layer "In\d\.Cu"\s*\(type "copper"\)', after_text)
    try:
        kb.set_copper_text(after_text, 0.035)
        outer_ok: bool | None = True
    except ValueError as err:
        rec.line(f"set_copper_text refused the saved text: {err}")
        outer_ok = False
    ctx.save(second, "k3_roundtrip.kicad_pcb")
    checks = Checks()
    checks.add("stackup kept with order, types and thicknesses", before == after)
    checks.add("inner copper layers match the multi-line pattern", len(inner) == 2)
    checks.add("set_copper_text still accepts the saved text", outer_ok)
    return checks.outcome()


def exp_k4(ctx: Ctx) -> Outcome:
    """Run K4: board thickness against the stackup sum, in the job file and DRC."""
    from pcbkit.kicad import cli

    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    folder = ctx.scratch("k4")
    seen: dict[str, Any] = {}
    for tag, core in (("control", None), ("mismatch", round(1.06 + 0.1, 4))):
        pcb = folder / f"{tag}.kicad_pcb"
        save_with_stackup(pcbnew, make_board(ctx, pcb, parts=False), pcb, core)
        out = folder / f"{tag}_gerbers"
        run = cli.export_gerbers(pcb, out, layers=GERBER_LAYERS_4)
        job_path = next(f for f in run.files if f.endswith(".gbrjob"))
        job = parse_job(Path(job_path).read_text(encoding="utf-8"))
        total = sum(
            float(s[2]) for s in job["stackup"] if isinstance(s[2], (int, float))
        )
        drc = cli.drc(pcb, folder / f"{tag}.rpt").report
        complaints = [
            v.message
            for v in drc.violations
            if re.search(r"(?i)thick|stackup|dielectric", v.message + v.category)
        ]
        rec.line(
            f"{tag}: SetBoardThickness {THICKNESS_MM}, stackup sum on the board "
            f"{round(THICKNESS_MM - 1.06 + (core or 1.06), 4)}, job BoardThickness "
            f"{job['board_thickness']}, MaterialStackup sum {round(total, 4)}"
        )
        rec.line(f"{tag}: DRC counts {drc.counts}; thickness complaints {complaints}")
        seen[tag] = (job["board_thickness"], dict(drc.counts), complaints)
    ctx.save(folder / "mismatch.rpt", "k4_mismatch_drc.rpt")
    checks = Checks()
    thickness = seen["mismatch"][0]
    checks.add(
        "the job file reports SetBoardThickness",
        None if thickness is None else abs(float(thickness) - THICKNESS_MM) < 1e-6,
    )
    checks.add(
        "no DRC complaint about the mismatch",
        not seen["mismatch"][2] and seen["mismatch"][1] == seen["control"][1],
    )
    return checks.outcome({"job_thickness": thickness})


def exp_k5(ctx: Ctx) -> Outcome:
    """Run K5: the DSN KiCad exports for a board with inner zones."""
    from pcbkit.route.pre import export_dsn

    rec = ctx.rec
    root = ctx.scratch("k5")
    facts: dict[str, Any] = {}
    for variant in ("unfilled", "filled"):
        source = ctx.fixture(variant)
        copy = root / f"{variant}.kicad_pcb"
        shutil.copy(source, copy)
        shutil.copy(source.with_suffix(".kicad_pro"), copy.with_suffix(".kicad_pro"))
        dsn = root / f"{variant}.dsn"
        export_dsn(copy, dsn, root)
        text = dsn.read_text(encoding="utf-8", errors="replace")
        facts[variant] = dsn_summary(text)
        rec.line(f"--- {variant} ---")
        rec.line(f"grep -n 'layer In\\|type power\\|plane\\|Via\\[' {variant}.dsn:")
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"layer In|type power|\(plane|Via\[", line):
                rec.line(f"  {number}: {line.strip()[:150]}")
        rec.line(f"summary: {json.dumps(facts[variant])}")
        ctx.save(dsn, f"k5_{variant}.dsn")
    checks = Checks()
    for variant, data in facts.items():
        layers = dict(map(tuple, data["layers"]))
        checks.add(
            f"{variant}: (type power) on In1.Cu and In2.Cu",
            layers.get("In1.Cu") == "power" and layers.get("In2.Cu") == "power",
        )
        planes = {tuple(p) for p in data["planes"]}
        checks.add(
            f"{variant}: (plane ...) for /GND on In1.Cu and /+5V on In2.Cu",
            {("/GND", "In1.Cu"), ("/+5V", "In2.Cu")} <= planes,
        )
        checks.add(
            f"{variant}: via padstack Via[0-3]_700:300_um",
            "Via[0-3]_700:300_um" in data["via_padstacks"],
        )
    return checks.outcome(facts)


def route_variant(ctx: Ctx, tag: str, source: Path, repeat: bool) -> dict[str, Any]:
    """Route one fixture board with Freerouting, import the result, and measure it."""
    from pcbkit.kicad import cli
    from pcbkit.route.pre import export_dsn
    from pcbkit.route.rules import apply_rules

    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    router = find_router_or_skip()
    root = ctx.scratch(f"k6{tag}")
    kicad = root / "kicad"
    kicad.mkdir()
    prerouted = kicad / "prerouted.kicad_pcb"
    shutil.copy(source, prerouted)
    shutil.copy(source.with_suffix(".kicad_pro"), prerouted.with_suffix(".kicad_pro"))
    dsn = kicad / f"{STEM}.dsn"
    export_dsn(prerouted, dsn, root)
    rec.line(f"--- variant {tag.upper()}: {source.name} ---")
    run = run_freerouting(kicad, dsn.name, router)
    rec.line("command: " + " ".join(run["command"]))
    rec.line(
        f"outcome: {run['outcome']} (code {run['code']}) in {run['seconds']:.0f} s; "
        f"started routing: {run['started']}; routing seconds: "
        f"{run['routing_seconds']}; normalisation failures: "
        f"{run['normalization_failures']}"
    )
    if run["detail"]:
        rec.line("detail: " + run["detail"])
    rec.line("log highlights:\n" + log_highlights(run["log_text"]))
    rec.line("log tail:\n" + "\n".join(run["log_text"].strip().splitlines()[-8:]))
    facts: dict[str, Any] = {
        "outcome": run["outcome"],
        "detail": run["detail"],
        "seconds": run["seconds"],
        "ses_written": run["ses_written"],
        "normalization_failures": run["normalization_failures"],
    }
    ctx.save(dsn, f"k6{tag}.dsn")
    ctx.save(run["log"], f"k6{tag}_freerouting.log")
    if not run["ses_written"]:
        return facts
    ctx.save(run["ses"], f"k6{tag}.ses")
    ses = ses_summary(run["ses"].read_text(encoding="utf-8", errors="replace"))
    ses_inner = sum(
        n
        for entry in ses.values()
        for layer, n in entry["wires"].items()
        if layer in INNER_LAYERS
    )
    rec.line("SES per net (wires by layer, vias): " + json.dumps(ses, sort_keys=True))
    board = pcbnew.LoadBoard(str(prerouted))
    apply_rules(board, plain_hooks(), 4)
    pcbnew.SaveBoard(str(prerouted), board)
    board = pcbnew.LoadBoard(str(prerouted))
    imported = pcbnew.ImportSpecctraSES(board, str(run["ses"]))
    rec.line(f"ImportSpecctraSES returned {imported!r}")
    plane = {"/" + n for n in PLANE_NETS}
    vias = inner = outer_plane = 0
    plane_vias = 0
    for item in board.GetTracks():
        if item.GetClass() == "PCB_VIA":
            vias += 1
            plane_vias += item.GetNetname() in plane
            continue
        name = board.GetLayerName(item.GetLayer())
        inner += name in INNER_LAYERS
        outer_plane += item.GetNetname() in plane and name not in INNER_LAYERS
    rec.line(
        f"imported: {vias} vias ({plane_vias} on plane nets), tracks on inner layers "
        f"{inner}, plane-net tracks on outer layers {outer_plane}"
    )
    fill(ctx, board)
    routed = kicad / "routed.kicad_pcb"
    pcbnew.SaveBoard(str(routed), board)
    drc = cli.drc(routed, kicad / "drc.rpt").report
    rec.line(f"DRC counts: {drc.counts}; categories: {drc.categories}")
    ctx.save(routed, f"k6{tag}_routed.kicad_pcb")
    ctx.save(kicad / "drc.rpt", f"k6{tag}_drc.rpt")
    unconnected = drc.unconnected
    inner_all = max(ses_inner, inner)
    facts.update(
        {
            "inner_wires": inner_all,
            "plane_vias": plane_vias,
            "plane_outer_tracks": outer_plane,
            "unconnected": unconnected,
            "drc_counts": dict(drc.counts),
            "mode": classify_plane_connection(
                inner_all, plane_vias, outer_plane, SMD_PLANE_PADS, unconnected
            ),
            "repeat_identical": None,
        }
    )
    rec.line(f"connection of the plane nets: {facts['mode']}")
    if repeat:
        again = ctx.scratch(f"k6{tag}2") / "kicad"
        again.mkdir()
        shutil.copy(dsn, again / dsn.name)
        second = run_freerouting(again, dsn.name, router)
        same = second["ses_written"] and filecmp.cmp(
            run["ses"], second["ses"], shallow=False
        )
        facts["repeat_identical"] = bool(same) if second["ses_written"] else None
        rec.line(
            f"repeat run: {second['outcome']} in {second['seconds']:.0f} s; "
            f"session file identical to the first: {facts['repeat_identical']}"
        )
    return facts


def exp_k6(ctx: Ctx) -> Outcome:
    """Run K6: Freerouting 1.9.0 on the plane DSN (variant B adds track bans)."""
    rec = ctx.rec
    a = route_variant(ctx, "a", ctx.fixture("unfilled"), repeat=True)
    b: dict[str, Any] | None = None
    try:
        b = route_variant(ctx, "b", ctx.fixture("banned"), repeat=False)
    except Skip:
        raise
    except Exception as err:
        rec.line(f"variant B failed: {type(err).__name__}: {err}")
        rec.line(traceback.format_exc().rstrip())
    checks = judge_k6(a)
    line = describe_run(a) + "  [" + checks.summary() + "]"
    return Outcome(checks.verdict(), line, facts={"a": a, "b": b})


def exp_k7(ctx: Ctx) -> Outcome:
    """Run K7: does `-inc` exist in Freerouting 1.9.0, and does it ignore a class."""
    from pcbkit.route.pre import export_dsn

    rec = ctx.rec
    router = find_router_or_skip()
    root = ctx.scratch("k7")
    kicad = root / "kicad"
    kicad.mkdir()
    source = ctx.fixture("unfilled")
    pcb = kicad / "prerouted.kicad_pcb"
    shutil.copy(source, pcb)
    shutil.copy(source.with_suffix(".kicad_pro"), pcb.with_suffix(".kicad_pro"))
    dsn = kicad / f"{STEM}.dsn"
    export_dsn(pcb, dsn, root)
    classes = re.findall(
        r'\(class\s+("[^"]+"|[^\s()]+)', dsn.read_text(errors="replace")
    )
    rec.line(f"net classes in the DSN: {classes}")
    help_cmd = [router.java, "-jar", router.jar, "-help"]
    log = kicad / "help.log"
    outcome, code, detail, seconds = run_watched(help_cmd, kicad, log, 60, 60)
    help_text = log.read_text(encoding="utf-8", errors="replace")
    rec.line("$ " + " ".join(help_cmd))
    rec.line(f"ended: {outcome} (code {code}) {detail} in {seconds:.0f} s")
    rec.line(help_text.strip()[:3000])
    in_help = bool(re.search(r"(^|\s)-inc\b", help_text))
    ctx.save_text(help_text, "k7_help.txt")
    checks = Checks()
    checks.add("-inc is listed by -help", in_help)
    facts: dict[str, Any] = {"inc_in_help": in_help, "classes": classes}
    target = "Planes" if "Planes" in [c.strip('"') for c in classes] else None
    if target is None:
        rec.line("no 'Planes' net class in the DSN: the -inc run is not possible")
        checks.add("a run with -inc ignores the net class", None)
        return checks.outcome(facts)
    run = run_freerouting(kicad, dsn.name, router, extra=["-inc", target])
    rec.line("command: " + " ".join(run["command"]))
    rec.line(
        f"outcome: {run['outcome']} in {run['seconds']:.0f} s, session written: "
        f"{run['ses_written']}"
    )
    rec.line("log highlights:\n" + log_highlights(run["log_text"]))
    ctx.save(run["log"], "k7_inc_freerouting.log")
    if run["ses_written"]:
        ctx.save(run["ses"], "k7_inc.ses")
        summary = ses_summary(run["ses"].read_text(encoding="utf-8", errors="replace"))
        use = ses_layer_use(summary, PLANE_NETS)
        rec.line(f"plane nets in the session (wires by layer, vias): {use}")
        facts["inc_run_plane_use"] = use
        checks.add(
            "a run with -inc leaves the plane nets unrouted", not any(use.values())
        )
    else:
        checks.add("a run with -inc leaves the plane nets unrouted", None)
    return checks.outcome(facts)


def exp_k8(ctx: Ctx) -> Outcome:
    """Run K8: import K6's session on the four-layer board; vias and their layers."""
    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    kicad = ctx.work / "k6a" / "kicad"
    prerouted, ses = kicad / "prerouted.kicad_pcb", kicad / f"{STEM}.ses"
    if not (prerouted.is_file() and ses.is_file()):
        raise Skip(
            "K6 left no session file to import (K6 did not run or did not route)"
        )
    board = pcbnew.LoadBoard(str(prerouted))
    zones_before = [(z.GetNetname(), z.GetFirstLayer()) for z in board.Zones()]
    rec.line(
        f"ImportSpecctraSES returned {pcbnew.ImportSpecctraSES(board, str(ses))!r}"
    )
    zones_after = [(z.GetNetname(), z.GetFirstLayer()) for z in board.Zones()]
    pairs: dict[str, int] = {}
    for item in board.GetTracks():
        if item.GetClass() != "PCB_VIA":
            continue
        _, top = rec.probe(item, "TopLayer", label="via.TopLayer()")
        _, bottom = rec.probe(item, "BottomLayer", label="via.BottomLayer()")
        key = f"{board.GetLayerName(top)} to {board.GetLayerName(bottom)}"
        pairs[key] = pairs.get(key, 0) + 1
        if sum(pairs.values()) >= 3:
            break
    rec.line("(first three vias shown above)")
    total = sum(1 for t in board.GetTracks() if t.GetClass() == "PCB_VIA")
    rec.line(f"vias after import: {total}; layer pairs of the first three: {pairs}")
    rec.line(f"zones before {zones_before}; after {zones_after}")
    checks = Checks()
    checks.add(
        "vias run F.Cu to B.Cu",
        None if not pairs else set(pairs) == {"F.Cu to B.Cu"},
    )
    checks.add("zones untouched by the import", zones_before == zones_after)
    return checks.outcome({"via_pairs": pairs, "vias": total})


def ring_coverage(
    pcbnew: Any, rec: Recorder, zone: Any, layer: int, centre: Any, radius_mm: float
) -> tuple[int, int]:
    """Count how many of 16 points on a ring about ``centre`` lie in the zone's fill."""
    polys = zone.GetFilledPolysList(layer)
    collide = rec.attr(polys, "Collide", "SHAPE_POLY_SET")
    radius = pcbnew.FromMM(radius_mm)
    hits = 0
    for i in range(16):
        angle = 2 * math.pi * i / 16
        point = pcbnew.VECTOR2I(
            int(centre.x + radius * math.cos(angle)),
            int(centre.y + radius * math.sin(angle)),
        )
        hits += bool(collide(point, 0))
    return hits, 16


def exp_k9(ctx: Ctx) -> Outcome:
    """Run K9: how filled inner planes meet vias and pads; the via padstack defaults."""
    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    board = pcbnew.LoadBoard(str(ctx.fixture("vias")))
    layers = {"/GND": pcbnew.In1_Cu, "/+5V": pcbnew.In2_Cu}
    vias = {t.GetNetname(): t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"}
    zone_settings = {
        "FULL": getattr(pcbnew, "ZONE_CONNECTION_FULL", None),
        "THERMAL": getattr(pcbnew, "ZONE_CONNECTION_THERMAL", None),
    }
    checks = Checks()
    facts: dict[str, Any] = {}
    for net, layer in layers.items():
        via = vias[net]
        lname = board.GetLayerName(layer)
        rec.line(f"--- {net} via on {lname} ---")
        on_layer = via.IsOnLayer(layer)
        rec.line(f"IsOnLayer({lname}) = {on_layer}")
        _, width = rec.probe(via, "GetWidth", layer, label=f"GetWidth({lname})")
        _, width_f = rec.probe(via, "GetWidth", pcbnew.F_Cu, label="GetWidth(F.Cu)")
        checks.add(
            f"{net} via has its ring on {lname}",
            None if width is None else width == width_f,
        )
        for name in (
            "GetRemoveUnconnected",
            "GetKeepStartEndLayers",
            "GetZoneConnection",
            "Padstack",
        ):
            rec.probe(via, name)
        ok, stack = rec.probe(via, "Padstack", label="via.Padstack()")
        if ok:
            rec.line(
                "padstack names about unconnected layers: "
                + ", ".join(n for n in dir(stack) if re.search("nconnect|Remove", n))
            )
            rec.probe(
                stack, "UnconnectedLayerMode", label="Padstack().UnconnectedLayerMode()"
            )
        rec.line(
            "via names with Zone/Remove/Unconnected: "
            + ", ".join(n for n in dir(via) if re.search("Zone|Remove|nconnect", n))
        )
        zone = next(
            z
            for z in board.Zones()
            if not z.GetIsRuleArea() and z.GetNetname() == net and z.IsOnLayer(layer)
        )
        setting = zone.GetPadConnection()
        rec.line(
            "zone pad connection setting: "
            + next((k for k, v in zone_settings.items() if v == setting), repr(setting))
        )
        hits, n = ring_coverage(
            pcbnew,
            rec,
            zone,
            layer,
            via.GetPosition(),
            pcbnew.ToMM(width or 0) / 2 + 0.2,
        )
        kind = classify_connection(hits, n)
        rec.line(f"the {lname} fill around the via: {kind}")
        facts[f"via{net}"] = kind
        checks.add(f"{net} via joins the {lname} fill solid", kind == "solid")
    for ref, num, net in (("J1", "2", "/GND"), ("J1", "1", "/+5V")):
        layer = layers[net]
        pad = next(
            p
            for p in board.FindFootprintByReference(ref).Pads()
            if p.GetNumber() == num
        )
        rec.line(
            f"--- {ref}.{num} (through-hole pad, {net}) on "
            f"{board.GetLayerName(layer)} ---"
        )
        rec.line(f"IsOnLayer = {pad.IsOnLayer(layer)}")
        ok, size = rec.probe(pad, "GetSizeX", label="GetSizeX()")
        zone = next(
            z
            for z in board.Zones()
            if not z.GetIsRuleArea() and z.GetNetname() == net and z.IsOnLayer(layer)
        )
        radius = (pcbnew.ToMM(size) if ok else 1.7) / 2 + 0.2
        hits, n = ring_coverage(pcbnew, rec, zone, layer, pad.GetPosition(), radius)
        kind = classify_connection(hits, n)
        rec.line(f"fill around the pad: {kind}")
        facts[f"pad{net}"] = kind
        expect_solid = net == "/+5V"
        checks.add(
            f"{ref}.{num} pad: " + ("solid" if expect_solid else "thermal"),
            (kind == "solid") if expect_solid else kind.startswith("spokes"),
        )
    return checks.outcome(facts)


def exp_k10(ctx: Ctx) -> Outcome:
    """Run K10: Gerber names and the job file on four layers."""
    from pcbkit.kicad import cli

    rec = ctx.rec
    pcb = ctx.fixture("vias")
    out = ctx.scratch("k10") / "gerbers"
    run = cli.export_gerbers(pcb, out, layers=GERBER_LAYERS_4)
    rec.line("$ " + " ".join(run.args))
    names = sorted(Path(f).name for f in run.files)
    rec.line("files:\n" + "\n".join(f"  {n}" for n in names))
    job_path = next((f for f in run.files if f.endswith(".gbrjob")), None)
    if job_path is None:
        raise RuntimeError("no .gbrjob among the exported files")
    job_text = Path(job_path).read_text(encoding="utf-8")
    job = parse_job(job_text)
    rec.line(
        f"LayerNumber {job['layer_number']}, BoardThickness {job['board_thickness']}"
    )
    rec.line("MaterialStackup:")
    for row in job["stackup"]:
        rec.line(f"  {row}")
    ctx.save_text(
        "\n".join(f"{n}  {(out / n).stat().st_size} bytes" for n in names) + "\n",
        "k10_gerber_listing.txt",
    )
    ctx.save(Path(job_path), "k10.gbrjob")
    checks = Checks()
    for what, ok in judge_k10(names, job, pcb.stem):
        checks.add(what, ok)
    return checks.outcome({"names": names})


def exp_k11(ctx: Ctx) -> Outcome:
    """Run K11: the drill export on a board with through vias only."""
    from pcbkit.kicad import cli

    rec = ctx.rec
    pcb = ctx.fixture("vias")
    out = ctx.scratch("k11") / "drill"
    run = cli.export_drill(pcb, out)
    rec.line("$ " + " ".join(run.args))
    names = sorted(Path(f).name for f in run.files)
    rec.line("files:\n" + "\n".join(f"  {n}" for n in names))
    drills = [n for n in names if n.endswith(".drl")]
    extra = [n for n in drills if not re.search(r"-N?PTH\.drl$", n)]
    ctx.save_text("\n".join(names) + "\n", "k11_drill_listing.txt")
    checks = Checks()
    checks.add(
        "a -PTH.drl file",
        any(n.endswith("-PTH.drl") and "NPTH" not in n for n in drills),
    )
    checks.add("no layer-pair drill files", not extra)
    return checks.outcome({"names": names, "extra": extra})


def exp_k12(ctx: Ctx) -> Outcome:
    """Run K12: DRC on four layers with a planted clearance error on In1.Cu."""
    from pcbkit.kicad import board as kb
    from pcbkit.kicad import cli

    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    folder = ctx.scratch("k12")
    planted = folder / "planted.kicad_pcb"
    shutil.copy(ctx.fixture("vias"), planted)
    shutil.copy(
        ctx.fixture("vias").with_suffix(".kicad_pro"), planted.with_suffix(".kicad_pro")
    )
    board = pcbnew.LoadBoard(str(planted))
    kb.track(board, [(15, 15), (25, 15)], 0.25, "LED_A", layer=pcbnew.In1_Cu)
    kb.track(board, [(15, 15.4), (25, 15.4)], 0.25, "SENSE", layer=pcbnew.In1_Cu)
    fill(ctx, board)
    pcbnew.SaveBoard(str(planted), board)
    control = cli.drc(ctx.fixture("vias"), folder / "control.rpt")
    result = cli.drc(planted, folder / "planted.rpt")
    for tag, res in (("control (no planted error)", control), ("planted", result)):
        rec.line(
            f"{tag}: counts {res.report.counts}; categories {res.report.categories}"
        )
    rec.line("planted entries:")
    for v in result.report.violations:
        rec.line(f"  [{v.category}] {v.message} | {'; '.join(v.items)}")
    ctx.save(folder / "planted.rpt", "drc_four_layer_planted.rpt")
    ctx.save(folder / "control.rpt", "drc_four_layer_unrouted.rpt")
    clearance = [v for v in result.report.violations if v.category == "clearance"]
    checks = Checks()
    checks.add("parse_drc reads the four-layer report unchanged", True)
    checks.add(
        'a clearance item says "In1.Cu"',
        any("In1.Cu" in " ".join((v.message, *v.items)) for v in clearance),
    )
    checks.add(
        "the control has no clearance violation",
        control.report.categories.get("clearance", 0) == 0,
    )
    return checks.outcome({"clearance": len(clearance)})


def exp_k13(ctx: Ctx) -> Outcome:
    """Run K13: polygon contours of an inner layer, with and without the fill."""
    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    areas: dict[str, float] = {}
    for variant in ("unfilled", "vias"):
        board = pcbnew.LoadBoard(str(ctx.fixture(variant)))
        polys = pcbnew.SHAPE_POLY_SET()
        rec.attr(board, "ConvertBrdLayerToPolygonalContours", "BOARD")(
            pcbnew.In1_Cu, polys
        )
        polys.Simplify()
        areas[variant] = abs(polys.Area()) / 1e12
        rec.line(f"In1.Cu copper area, {variant}: {areas[variant]:.1f} mm2")
    checks = Checks()
    checks.add(
        "the contours include zone fills",
        areas["vias"] - areas["unfilled"] > 100.0,
    )
    return checks.outcome(areas)


def exp_k14(ctx: Ctx) -> Outcome:
    """Run K14: SVG and PDF export of inner layers."""
    from pcbkit.kicad import cli

    rec = ctx.rec
    pcb = ctx.fixture("vias")
    folder = ctx.scratch("k14")
    checks = Checks()
    svg = folder / "in1.svg"
    run = cli.export_svg(pcb, svg, ("In1.Cu", "Edge.Cuts"))
    text = svg.read_text(encoding="utf-8", errors="replace")
    rec.line("$ " + " ".join(run.args))
    rec.line(f"svg: {svg.stat().st_size} bytes, {text.count('<path')} paths")
    checks.add(
        "SVG export of In1.Cu and Edge.Cuts", "<svg" in text and text.count("<path") > 0
    )
    pdf = folder / "inner.pdf"
    run = cli.export_pcb_pdf(pcb, pdf, ("In1.Cu", "In2.Cu", "Edge.Cuts"))
    head = pdf.read_bytes()[:5]
    rec.line("$ " + " ".join(run.args))
    rec.line(f"pdf: {pdf.stat().st_size} bytes, starts {head!r}")
    checks.add("PDF export of In1.Cu, In2.Cu, Edge.Cuts", head == b"%PDF-")
    ctx.save(svg, "k14_in1.svg")
    ctx.save(pdf, "k14_inner.pdf")
    return checks.outcome()


def exp_k15(ctx: Ctx) -> Outcome:
    """Run K15: which items are on the inner layers, and their effective shapes."""
    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    board = pcbnew.LoadBoard(str(ctx.fixture("vias")))
    in1 = pcbnew.In1_Cu
    via = next(t for t in board.GetTracks() if t.GetClass() == "PCB_VIA")
    ok, shape = rec.probe(
        via, "GetEffectiveShape", in1, label="via.GetEffectiveShape(In1_Cu)"
    )
    if ok:
        rec.line(f"  shape type {type(shape).__name__}")
    pth = board.FindFootprintByReference("J1").Pads()[0]
    smd = board.FindFootprintByReference("R1").Pads()[0]
    got = {
        "via": via.IsOnLayer(in1),
        "pth": pth.IsOnLayer(in1),
        "smd": smd.IsOnLayer(in1),
    }
    rec.line(
        f"IsOnLayer(In1_Cu): via {got['via']}, PTH pad J1.1 {got['pth']}, "
        f"SMD pad R1.1 {got['smd']}"
    )
    rec.probe(pth, "GetEffectiveShape", in1, label="PTH pad GetEffectiveShape(In1_Cu)")
    checks = Checks()
    checks.add("the via is on In1.Cu", got["via"])
    checks.add("the PTH pad is on In1.Cu", got["pth"])
    checks.add("the SMD pad is not on In1.Cu", not got["smd"])
    checks.add("GetEffectiveShape(In1_Cu) works on a via", ok)
    return checks.outcome(got)


def laplace_stand_in(nx: int, ny: int, layers: int) -> tuple[float, float]:
    """Solve a unit-current Laplace problem on ``layers`` grids; return (s, peak MB)."""
    import numpy as np
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla

    def path(n: int) -> Any:
        return sp.diags([-1.0, 2.0, -1.0], [-1, 0, 1], shape=(n, n))

    start = time.monotonic()
    plane = sp.kron(sp.eye(ny), path(nx)) + sp.kron(path(ny), sp.eye(nx))
    n2 = nx * ny
    total = sp.block_diag([plane] * layers, format="csr")
    rng = np.random.default_rng(0)
    sites = rng.integers(0, n2, 200)
    rows, cols, vals = [], [], []
    for layer in range(layers - 1):
        a = sites + layer * n2
        b = sites + (layer + 1) * n2
        rows += [a, b, a, b]
        cols += [a, b, b, a]
        vals += [np.ones(200), np.ones(200), -np.ones(200), -np.ones(200)]
    link = sp.coo_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
        shape=total.shape,
    )
    matrix = (total + link).tocsc()
    rhs = np.zeros(matrix.shape[0])
    rhs[matrix.shape[0] // 2] = 1.0
    spla.spsolve(matrix, rhs)
    return time.monotonic() - start, peak_rss_mb()


def exp_k16(ctx: Ctx) -> Outcome:
    """Run K16: time and memory of NetCopper, and a three-layer stand-in solve."""
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import scipy  # noqa: F401
    except ImportError as err:
        raise Skip(f"numpy, scipy and Pillow are needed here ({err})") from None
    from pcbkit.check.copper import NetCopper

    pcbnew = ctx.pcbnew()
    rec = ctx.rec
    board = pcbnew.LoadBoard(str(ctx.fixture("vias")))
    for res in (0.2, 0.1):
        start = time.monotonic()
        copper = NetCopper(board, "/GND", res=res)
        built = time.monotonic() - start
        start = time.monotonic()
        copper.solve(
            [(copper.pad_cells("R3", 2), 0.1), (copper.pad_cells("J1", 2), 0.0)]
        )
        rec.line(
            f"existing NetCopper (two layers only), res {res}: raster {built:.1f} s, "
            f"solve {time.monotonic() - start:.1f} s, peak {peak_rss_mb():.0f} MB"
        )
    nx0 = int((WIDTH_MM + 1) / 0.1) + 1
    ny0 = int((HEIGHT_MM + 1) / 0.1) + 1
    sizes = [
        ("this board, 0.2 mm", nx0 // 2, ny0 // 2),
        ("this board, 0.1 mm", nx0, ny0),
    ]
    sizes.append(("100 x 80 mm, 0.2 mm", 501, 401))
    if ctx.opts.get("k16_big"):
        sizes.append(("100 x 80 mm, 0.1 mm", 1001, 801))
    timings: dict[str, tuple[float, float]] = {}
    for label, nx, ny in sizes:
        seconds, rss = laplace_stand_in(nx, ny, 3)
        timings[label] = (seconds, rss)
        rec.line(
            f"3-layer stand-in solve, {label} ({nx} x {ny} x 3): {seconds:.1f} s, "
            f"peak {rss:.0f} MB"
        )
    ratio = (100 * 80) / ((WIDTH_MM + 1) * (HEIGHT_MM + 1))
    s01, rss01 = timings["this board, 0.1 mm"]
    rec.line(f"scaling this board to 100 x 80 mm: x{ratio:.1f} in area")
    checks = Checks()
    checks.add(
        "under a minute and a few GB at 0.1 mm on 100 x 80 mm (scaled)",
        judge_k16(s01, rss01, ratio),
    )
    return checks.outcome({k: list(v) for k, v in timings.items()})


def exp_k17(ctx: Ctx) -> Outcome:
    """Run K17: the three 3D renders of the four-layer board."""
    from pcbkit.shots import render_views

    rec = ctx.rec
    out = ctx.scratch("k17")
    made = render_views(ctx.fixture("vias"), out)
    checks = Checks()
    for png in made:
        head = png.read_bytes()[:8]
        rec.line(f"{png.name}: {png.stat().st_size} bytes")
        checks.add(f"{png.name} is a PNG", head == b"\x89PNG\r\n\x1a\n")
        ctx.save(png, f"k17_{png.name}")
    rec.line("Open the PNGs: this only says they were made, not that they look right.")
    return checks.outcome()


# --- the experiment list --------------------------------------------------------------

_P, _C, _J, _R = "pcbnew", "kicad-cli", "java", "jar"

SPECS: tuple[Spec, ...] = (
    Spec(
        "setup",
        "The generic four-layer fixture",
        "Can a small four-layer board (my-board: six plain parts, a GND zone on In1, a "
        "+5V zone on In2, SMD pads on plane nets) be built with pcbnew?",
        "Yes: every later experiment reads boards built here.",
        exp_setup,
        (_P,),
    ),
    Spec(
        "K0",
        "Two-layer byte baseline",
        "What do build, pre, finalize, check and quote write on a fresh two-layer "
        "project, byte for byte?",
        "Byte-identical after every slice.",
        static=Outcome(
            SKIPPED,
            "separate subcommand: run `spike.py baseline --out DIR` (see the README)",
        ),
    ),
    Spec(
        "K1",
        "Inner layer ids and names",
        "What are the ids and names of In1.Cu and In2.Cu on a four-layer board?",
        'Ids 0, 4, 6, 2 for F_Cu, In1_Cu, In2_Cu, B_Cu; names "In1.Cu", "In2.Cu". '
        "Code uses the symbols only.",
        exp_k1,
        (_P,),
    ),
    Spec(
        "K2",
        "Layer type setter and how it is saved",
        "Does SetLayerType(In1_Cu, LT_POWER) exist, and what does the saved file say?",
        "SetLayerType exists and writes `power` in the (layers ...) section.",
        exp_k2,
        (_P,),
    ),
    Spec(
        "K3",
        "A hand-inserted four-layer stackup survives LoadBoard and SaveBoard",
        "Is a four-layer stackup block inserted after (setup kept through a load and "
        "a save?",
        "Kept with its order, types (prepreg, core) and thicknesses, in the multi-line "
        "form set_copper_text already matches.",
        exp_k3,
        (_P,),
    ),
    Spec(
        "K4",
        "Board thickness against the stackup sum",
        "With a stackup sum 0.1 mm off SetBoardThickness, what does the job file say, "
        "and does DRC complain?",
        "The job file reports SetBoardThickness; no DRC complaint.",
        exp_k4,
        (_P, _C),
    ),
    Spec(
        "K5",
        "DSN export with inner zones",
        "What does ExportSpecctraDSN write for a GND zone on In1 and a +5V zone on "
        "In2, "
        "unfilled and filled?",
        "(type power) on both inner layers; each zone a (plane NET (polygon In1.Cu "
        "...)) from its outline; the via padstack Via[0-3]_700:300_um.",
        exp_k5,
        (_P,),
    ),
    Spec(
        "K6",
        "Freerouting 1.9.0 with plane layers",
        "Given the K5 DSN of a six-part board with plane-net SMD pads, does "
        "Freerouting "
        "connect plane-net pads with vias, stay off the power layers, not stall and "
        "give the same route on repeat? (Variant B adds rule areas that ban tracks on "
        "In1 and In2.)",
        "No tracks on power layers; plane-net pads connected with vias; no stall; same "
        "route on repeat. If not, D3 goes to (b).",
        exp_k6,
        (_P, _C, _J, _R),
        timeout=3 * 3600.0,
    ),
    Spec(
        "K7",
        "Freerouting ignores a net class from the command line",
        "Does `-inc` exist in 1.9.0, and does a run with `-inc Planes` leave the plane "
        "nets alone?",
        "`-inc` exists in 1.9.0 (needed for D3(b) only).",
        exp_k7,
        (_P, _J, _R),
        timeout=2400.0,
    ),
    Spec(
        "K8",
        "SES import on four layers",
        "After importing K6's session, which layer pair do the vias join, and are the "
        "zones untouched?",
        "Through vias F to B; zones untouched.",
        exp_k8,
        (_P,),
    ),
    Spec(
        "K9",
        "Zone fill on inner planes",
        "How does a filled In1 or In2 plane meet a via and a through-hole pad (solid "
        "or spokes), and what are the via padstack's unconnected-layer defaults?",
        "Vias join zones solid; PTH pads get thermals per zone setting; vias keep "
        "their "
        "ring on every layer (no removal of unconnected rings by default). (Here the "
        "GND zone is set thermal and the +5V zone solid, to see both.)",
        exp_k9,
        (_P,),
    ),
    Spec(
        "K10",
        "Gerber names and job file on four layers",
        "What does kicad-cli write for F.Cu, In1.Cu, In2.Cu, B.Cu and the rest, and "
        "what does the job file say?",
        "Files <stem>-In1_Cu.gbr and <stem>-In2_Cu.gbr; LayerNumber 4; MaterialStackup "
        'entries with Name "In1.Cu", Type "Copper", Thickness, plus dielectric '
        "entries.",
        exp_k10,
        (_P, _C),
    ),
    Spec(
        "K11",
        "Drill export with through vias only",
        "Which drill files does a four-layer board with through vias write?",
        "Still -PTH.drl and -NPTH.drl; no layer-pair files.",
        exp_k11,
        (_P, _C),
    ),
    Spec(
        "K12",
        "DRC report on four layers",
        "Does a planted In1.Cu clearance error come out in the same report format, "
        'with "In1.Cu" in the item, and does parse_drc read it unchanged?',
        'Same format; items say "on In1.Cu"; parse_drc reads it unchanged.',
        exp_k12,
        (_P, _C),
    ),
    Spec(
        "K13",
        "Polygon contours of inner layers (compare)",
        "Does ConvertBrdLayerToPolygonalContours(In1_Cu, ps) work, and include zone "
        "fills?",
        "Works and includes zone fills.",
        exp_k13,
        (_P,),
    ),
    Spec(
        "K14",
        "SVG and PDF export of inner layers",
        "Do `pcb export svg` and `pcb export pdf` accept In1.Cu and In2.Cu?",
        "Both work.",
        exp_k14,
        (_C,),
    ),
    Spec(
        "K15",
        "Item shapes on inner layers",
        "Which items are on In1.Cu, and does GetEffectiveShape(In1_Cu) work?",
        "Through items are on inner layers; SMD pads are not.",
        exp_k15,
        (_P,),
    ),
    Spec(
        "K16",
        "Solver size and time",
        "How long and how much memory do NetCopper and a three-layer solve take at "
        "res 0.2 and 0.1?",
        "Under a minute and a few GB at 0.1 on a 100 x 80 mm board; otherwise expose "
        "`res`.",
        exp_k16,
        (_P,),
        timeout=1800.0,
    ),
    Spec(
        "K17",
        "3D render of a four-layer board",
        "Do the three render views still work on a four-layer board?",
        "Unchanged.",
        exp_k17,
        (_C,),
        timeout=1800.0,
    ),
    Spec(
        "K18",
        "PCBWay four-layer facts (not KiCad)",
        "What are PCBWay's four-layer stack, inner copper, inner limits and order-form "
        "fields?",
        "Prepreg 0.2 mm; inner 1 oz; inner track and space at the outer 2 oz limits; "
        "hole to inner copper 0.25 mm. All unconfirmed: slice 5a's defaults stay out "
        "of "
        "the template until confirmed.",
        static=Outcome(
            SKIPPED,
            "ask PCBWay (not a KiCad experiment)",
            "Ask PCBWay (their capability page, or support):\n"
            + "\n".join(f"- {q}" for q in K18_QUESTIONS),
        ),
    ),
)
SPEC_BY_K = {spec.k: spec for spec in SPECS}


# --- the run (parent process) ---------------------------------------------------------


def detect_tools() -> dict[str, str | None]:
    """Return what this machine has: pcbnew, kicad-cli, Java and the Freerouting jar."""
    from pcbkit.kicad import env

    tools: dict[str, str | None] = {}
    cli_tool = env.find_kicad_cli()
    tools["kicad-cli"] = f"{cli_tool.path} {cli_tool.version}" if cli_tool else None
    java = env.find_java()
    major = env.java_major(java.version) if java else None
    tools["java"] = (
        f"{java.path} {java.version}"
        if java and major is not None and major >= env.MIN_JAVA
        else None
    )
    jar = env.find_freerouting_jar()
    tools["jar"] = f"{jar.path} {jar.version}" if jar and env.is_jar(jar.path) else None
    try:
        done = subprocess.run(
            [sys.executable, "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines = [ln for ln in done.stdout.splitlines() if ln.strip()]
        tools["pcbnew"] = lines[-1] if done.returncode == 0 and lines else None
    except (OSError, subprocess.TimeoutExpired):
        tools["pcbnew"] = None
    return tools


def repo_root() -> Path:
    """Return the root of the pcbkit checkout this script lives in."""
    return Path(__file__).resolve().parents[2]


def git_line(*args: str) -> str:
    """Return the output of a git command in the checkout, or "unknown"."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return done.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def selected(spec: Spec, only: Sequence[str], skip_freerouting: bool) -> str:
    """Return why a spec is left out of this run, or "" if it runs."""
    if only and spec.k not in only and spec.k != "setup":
        return "not selected (--only)"
    if skip_freerouting and spec.k in ("K6", "K7"):
        return "left out by --skip-freerouting"
    return ""


def execute_run(
    out: Path,
    only: Sequence[str] = (),
    skip_freerouting: bool = False,
    keep_work: bool = False,
    k16_big: bool = False,
    say: Callable[[str], None] = click.echo,
) -> Path:
    """Run every experiment in its own child and write the report; return its path."""
    out.mkdir(parents=True, exist_ok=True)
    art = out / ARTEFACT_DIR
    art.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="wp15-work-"))
    tools = detect_tools()
    say("tools: " + json.dumps(tools))
    script = Path(__file__).resolve()

    def runner(spec: Spec) -> Outcome:
        why = selected(spec, only, skip_freerouting) or unmet_reason(spec.needs, tools)
        if why:
            return Outcome(SKIPPED, why)
        argv = [sys.executable, str(script), "_child", spec.k]
        argv += ["--work", str(work), "--art", str(art)]
        if k16_big:
            argv.append("--k16-big")
        say(f"{spec.k}: running ({spec.title}) ...")
        return run_child(argv, spec.timeout, work / f"{spec.k}.partial.txt")

    results = []
    for spec in SPECS:
        result = collect(spec, runner)
        results.append(result)
        say(f"{spec.k}: {result.outcome.verdict}: {one_line(result.outcome.line, 110)}")
    meta = {
        "Generated": datetime.now().isoformat(timespec="seconds"),
        "pcbkit": f"{git_line('rev-parse', '--short', 'HEAD')} on "
        f"{git_line('rev-parse', '--abbrev-ref', 'HEAD')}",
        "Python": sys.version.split()[0] + " at " + sys.executable,
        "pcbnew": tools.get("pcbnew") or "not importable here",
        "kicad-cli": tools.get("kicad-cli") or "not found",
        "Java": tools.get("java") or "not found",
        "Freerouting jar": tools.get("jar") or "not found",
        "Fixture stackup": "a stand-in written by this script (symmetric, 1.6 mm), "
        "not slice 2's text",
    }
    note = f"work folder kept at {work}" if keep_work else ""
    report = out / REPORT_NAME
    report.write_text(render_report(results, meta, note), encoding="utf-8")
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return report


# --- K0: the two-layer byte baseline --------------------------------------------------

PRE_CODE = (
    "from pcbkit.project import load_project; from pcbkit.route.pre import pre; "
    "pre(load_project('.'))"
)


def pcbkit_argv(*args: str) -> list[str]:
    """Return the command line that runs pcbkit's CLI in this Python."""
    return [sys.executable, "-c", "from pcbkit.cli import cli; cli()", *args]


def stage_files(stage: str, project: Path) -> dict[str, bytes]:
    """Return the files a baseline keeps after ``stage``, normalised, by path."""
    files: dict[str, bytes] = {}
    if stage in ("build", "pre", "finalize"):
        for pattern in ("*.kicad_pcb", "*.dsn"):
            for path in sorted((project / "kicad").glob(pattern)):
                files[f"kicad/{path.name}"] = path.read_bytes()
    if stage == "finalize":
        for path in sorted((project / "out" / "fab" / "gerbers").glob("*")):
            if path.is_file():
                files[f"gerbers/{path.name}"] = normalise_gerber(path.read_bytes())
    if stage == "check":
        results = project / "out" / "checks" / "results.json"
        if results.is_file():
            files["results.json"] = normalise_results(results.read_bytes())
    return files


def run_logged(argv: Sequence[str], cwd: Path, log: list[str], timeout: float) -> str:
    """Run a command, log its command line, exit and output; return the output."""
    log.append("$ " + " ".join(argv[:3] if argv[1:2] == ["-c"] else argv))
    try:
        done = subprocess.run(
            list(argv),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        log.append(f"({describe_exit(done.returncode)})")
        log.append(done.stdout)
        return done.stdout
    except subprocess.TimeoutExpired:
        log.append(f"(timed out after {timeout:g} s)")
        return ""


def execute_baseline(out: Path, say: Callable[[str], None] = click.echo) -> None:
    """Make a fresh `pcbkit new my-board`, run the five commands, save the outputs."""
    out.mkdir(parents=True, exist_ok=True)
    parent = Path(tempfile.mkdtemp(prefix="wp15-baseline-"))
    log: list[str] = []
    run_logged(
        pcbkit_argv("new", BOARD_NAME, "--pcbkit-source", str(repo_root())),
        parent,
        log,
        300,
    )
    project = parent / BOARD_NAME
    stages = (
        ("build", pcbkit_argv("build")),
        ("pre", [sys.executable, "-c", PRE_CODE]),
        ("finalize", pcbkit_argv("finalize")),
        ("check", pcbkit_argv("check")),
        ("quote", pcbkit_argv("quote")),
    )
    for number, (stage, argv) in enumerate(stages, 1):
        say(f"baseline: {stage} ...")
        text = run_logged(argv, project, log, 1800)
        files = stage_files(stage, project)
        if stage == "quote":
            files["quote.txt"] = text.encode("utf-8")
        for name, data in files.items():
            target = out / f"{number:02d}-{stage}" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        say(f"  kept {len(files)} files")
    (out / "commands.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    meta = {
        "pcbkit_commit": git_line("rev-parse", "HEAD"),
        "branch": git_line("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": git_line("status", "--porcelain") not in ("unknown", ""),
        "python": sys.version.split()[0],
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(parent, ignore_errors=True)


NOT_COMPARED = ("commands.log", "meta.json")


def read_tree(root: Path) -> dict[str, bytes]:
    """Return every file under a baseline folder by relative path, minus the logs."""
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and rel not in NOT_COMPARED:
            files[rel] = path.read_bytes()
    return files


# --- the command line -----------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Run the WP15 spike experiments on a Mac with KiCad 10.0.6.

    Use KiCad's Python (.venv-kicad/bin/python). See the module help and
    tools/wp15_spike/README.md.
    """


@main.command("run")
@click.option(
    "--out",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where to write wp15-spike-report.md and wp15-spike/ (default: a new "
    "temporary folder, printed at the end).",
)
@click.option("--only", help="Run only these experiments, comma separated: K5,K6.")
@click.option(
    "--skip-freerouting",
    is_flag=True,
    help="Leave out K6 and K7 (they run Freerouting).",
)
@click.option("--keep-work", is_flag=True, help="Keep the scratch folder, for digging.")
@click.option(
    "--k16-big", is_flag=True, help="Also time a 100 x 80 mm stand-in solve at 0.1 mm."
)
def run_cmd(
    out: Path | None,
    only: str | None,
    skip_freerouting: bool,
    keep_work: bool,
    k16_big: bool,
) -> None:
    """Run K1 to K17 on a generic four-layer board and write the report.

    K6 and K7 run Freerouting 1.9.0 for real; on macOS it opens a window while it
    routes, which closes by itself. Plan on about ten minutes in all.
    """
    target = out or Path(tempfile.mkdtemp(prefix="wp15-spike-out-"))
    wanted = [k.strip().upper() for k in only.split(",")] if only else []
    report = execute_run(target, wanted, skip_freerouting, keep_work, k16_big)
    click.echo(f"\nreport:    {report}\nartefacts: {target / ARTEFACT_DIR}")


@main.command("baseline")
@click.option(
    "--out",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="The folder to save the normalised outputs in.",
)
def baseline_cmd(out: Path) -> None:
    """K0: run build, pre, finalize, check, quote on a fresh my-board; save them."""
    execute_baseline(out)
    click.echo(f"baseline saved in {out}")


@main.command("baseline-diff")
@click.argument("a", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("b", type=click.Path(exists=True, file_okay=False, path_type=Path))
def baseline_diff_cmd(a: Path, b: Path) -> None:
    """Compare two baseline folders A and B and print what differs (exit 1 if any)."""
    for label, folder in (("A", a), ("B", b)):
        meta = folder / "meta.json"
        if meta.is_file():
            commit = json.loads(meta.read_text(encoding="utf-8")).get("pcbkit_commit")
            click.echo(f"{label}: {folder} (pcbkit {commit})")
    diff = diff_trees(read_tree(a), read_tree(b))
    click.echo(format_diff(diff))
    if not diff.identical:
        raise SystemExit(1)


@main.command("_child", hidden=True)
@click.argument("k")
@click.option("--work", required=True, type=click.Path(path_type=Path))
@click.option("--art", required=True, type=click.Path(path_type=Path))
@click.option("--k16-big", is_flag=True)
def child_cmd(k: str, work: Path, art: Path, k16_big: bool) -> None:
    """Run one experiment in this process and print its outcome as a RESULT line."""
    spec = SPEC_BY_K[k]
    assert spec.run is not None
    ctx = Ctx(work, art, Recorder(work / f"{k}.partial.txt"), {}, {"k16_big": k16_big})
    outcome = run_experiment(spec.run, ctx)
    click.echo(RESULT_TAG + " " + json.dumps(asdict(outcome)))


if __name__ == "__main__":
    main()
