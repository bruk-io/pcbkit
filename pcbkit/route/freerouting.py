"""Run Freerouting, watch it, and try again until the board is clean.

Freerouting is the autorouter: ``java -jar freerouting.jar -de board.dsn -do board.ses
-mp PASSES`` reads the Specctra DSN that ``pre`` wrote and writes a session file that
``post`` imports. Two things about it cost hours before they became code here:

* **Its result differs from run to run.** The same DSN once left a net unrouted and
  routed fully on the next run. ``route_loop`` runs it again until a try's copper is
  clean (no unconnected pad, clearance, short, crossing, hole or edge problem), keeps
  the first such try, prints the problems of every try, and gives up after a set
  number of tries with the best one it saw.
* **It can hang after loading a board**, with "normalization of net ... failed" in its
  log and no routing at all. The router prints "Starting auto-routing" when it begins
  to route, so a run that has not printed it within ``[route] stall_timeout_s`` is
  killed (with everything it started) and reported. For ``pcbkit route --eco`` a stall
  falls back to routing the whole board.

The loop (``route_loop``) takes plain callables, so it can be run against fakes; the
process handling (``run_router``) takes its clock, its sleep and its spawner for the
same reason. ``pcbkit.route.flow`` wires them to the real stages.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from pcbkit.kicad import env
from pcbkit.route.files import RouteFiles

# Freerouting prints this when it begins to route (not when it merely loads the board).
STARTED_MARKER = "Starting auto-routing"

# A run is killed after this long whether or not it routed: route.sh's `timeout 900`.
HARD_TIMEOUT_S = 900.0
# How often the watchdog looks at the process and its log, and how long a killed
# process gets to leave before it is killed harder.
POLL_S = 0.5
KILL_GRACE_S = 5.0

# What a DRC report entry must not be, for a try's copper to count as clean: route.sh's
# list, in its order.
COPPER_CATEGORIES = (
    "unconnected_items",
    "clearance",
    "shorting_items",
    "tracks_crossing",
    "hole_clearance",
    "copper_edge_clearance",
)

# How a router run ended.
FINISHED = "finished"
STALLED = "stalled"
TIMED_OUT = "timed out"

# How a try ended.
CLEAN = "clean"
PROBLEMS = "problems"
NO_RESULT = "no result"


# --- finding the router -------------------------------------------------------------


@dataclass(frozen=True)
class Router:
    """The Java and the Freerouting jar that will be run."""

    java: str
    jar: str


def find_router() -> Router:
    """Return the Java and Freerouting jar to use, or raise with how to get them."""
    java = env.find_java()
    if java is None:
        raise click.ClickException(
            f"Java {env.MIN_JAVA} or newer not found: install it "
            "(brew install openjdk@21); `pcbkit doctor` shows what is missing"
        )
    major = env.java_major(java.version)
    if major is not None and major < env.MIN_JAVA:
        raise click.ClickException(
            f"Java {java.version} at {java.path} is too old: Freerouting needs "
            f"{env.MIN_JAVA} or newer (brew install openjdk@21)"
        )
    jar = env.find_freerouting_jar()
    if jar is None:
        raise click.ClickException(
            "Freerouting jar not found: run `pcbkit setup`, or set "
            f"FREEROUTING_JAR=/path/to/{env.FREEROUTING_JAR_NAME}"
        )
    if not env.is_jar(jar.path):
        raise click.ClickException(
            f"{jar.path} is not a complete jar (a truncated download?): delete it "
            "and run `pcbkit setup`"
        )
    return Router(java.path, jar.path)


def xvfb_command() -> str | None:
    """Return xvfb-run on Linux when it is installed, else None.

    Without a display, Freerouting needs one made for it. macOS has a screen, and shows
    the Freerouting window while it routes.
    """
    if env.host_os() != "linux":
        return None
    return env._which("xvfb-run")


def build_command(
    router: Router, dsn: str, ses: str, passes: int, xvfb: str | None = None
) -> list[str]:
    """Return the command line of one Freerouting run (``dsn`` and ``ses`` by name)."""
    command = [router.java, "-jar", router.jar]
    command += ["-de", dsn, "-do", ses, "-mp", str(passes)]
    return [xvfb, "-a", *command] if xvfb else command


# --- reading its log ----------------------------------------------------------------

_DONE = re.compile(
    r"Auto-routing was completed in (?:(\d+) minute\(s\) )?(\d+(?:\.\d+)?) seconds"
)
_NORMALIZATION = re.compile(r"normalization of net .*? failed")


@dataclass(frozen=True)
class FreeroutingLog:
    """What a Freerouting console log says about how far the run got.

    ``routing_seconds`` is the router's own figure for how long the routing took, or
    None if it never finished. ``normalization_failures`` are the lines that mark the
    hang described in the module docstring.
    """

    started_routing: bool
    routing_seconds: float | None
    optimized: bool
    saved: bool
    warnings: tuple[str, ...]
    normalization_failures: tuple[str, ...]


def parse_log(text: str) -> FreeroutingLog:
    """Read a Freerouting console log (one line per event, timestamped)."""
    seconds = None
    for done in _DONE.finditer(text):
        minutes = int(done.group(1) or 0)
        seconds = minutes * 60 + float(done.group(2))
    lines = text.splitlines()
    return FreeroutingLog(
        started_routing=STARTED_MARKER in text,
        routing_seconds=seconds,
        optimized="Route optimization was completed" in text,
        saved="Saving '" in text,
        warnings=tuple(line.strip() for line in lines if " WARN " in line),
        normalization_failures=tuple(
            line.strip() for line in lines if _NORMALIZATION.search(line)
        ),
    )


def _read(path: Path) -> str:
    """Return a log file's text, or "" if it is not there (yet)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _last_lines(text: str, count: int = 4) -> str:
    """Return the last few non-empty lines of a log, indented, for a message."""
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join("    " + line for line in kept[-count:])


# --- running it ---------------------------------------------------------------------


@dataclass(frozen=True)
class RouterRun:
    """One Freerouting run: how it ended and whether it left a session file.

    ``outcome`` is FINISHED (the process exited, whatever its code), STALLED (killed:
    no "Starting auto-routing" within the stall timeout) or TIMED_OUT (killed: still
    running at the hard timeout). ``detail`` says why, for a message.
    """

    outcome: str
    returncode: int | None
    seconds: float
    log: Path
    ses_written: bool
    detail: str = ""


def signal_group(proc: Any, sig: int) -> None:
    """Send ``sig`` to the whole process group of ``proc``.

    The router is started in a group of its own (``start_new_session``), so this reaches
    Java and, under xvfb-run, the X server too; neither is left behind.
    """
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass  # it left already


def stop(
    proc: Any,
    send: Callable[[Any, int], None] = signal_group,
    grace_s: float = KILL_GRACE_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """End ``proc`` and all it started: ask first, then insist after ``grace_s``."""
    send(proc, signal.SIGTERM)
    deadline = clock() + grace_s
    while proc.poll() is None and clock() < deadline:
        sleep(0.1)
    if proc.poll() is None:
        send(proc, signal.SIGKILL)
    proc.wait()


def watch(
    proc: Any,
    log: Path,
    stall_timeout_s: float,
    hard_timeout_s: float = HARD_TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    kill: Callable[[Any], None] | None = None,
) -> tuple[str, int | None, str]:
    """Wait for ``proc``, killing it if it stalls or runs too long.

    Return (outcome, returncode, detail). ``kill`` ends the process; it defaults to
    ``stop`` on the real process group.
    """
    end = kill or stop
    start = clock()
    started = False
    while True:
        code = proc.poll()
        if code is not None:
            return FINISHED, code, ""
        elapsed = clock() - start
        text = _read(log)
        started = started or STARTED_MARKER in text
        if not started and elapsed >= stall_timeout_s:
            end(proc)
            detail = (
                f'no "{STARTED_MARKER}" in {stall_timeout_s:g} s: Freerouting '
                "stalled while loading the board"
            )
            hint = parse_log(text).normalization_failures
            if hint:
                detail += f' (its log says "{hint[0]}")'
            return STALLED, None, detail
        if elapsed >= hard_timeout_s:
            end(proc)
            return TIMED_OUT, None, f"still running after {hard_timeout_s:g} s"
        sleep(POLL_S)


def run_router(
    files: RouteFiles,
    router: Router,
    passes: int,
    stall_timeout_s: float,
    hard_timeout_s: float = HARD_TIMEOUT_S,
    spawn: Callable[..., Any] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    kill: Callable[[Any], None] | None = None,
) -> RouterRun:
    """Run Freerouting once on the project's DSN and report how it went.

    It runs in ``kicad/``, as route.sh did, with the console output in ``files.log``.
    An old session file is deleted first, so one that exists afterwards was written by
    this run.
    """
    files.ses.unlink(missing_ok=True)
    command = build_command(
        router, files.dsn.name, files.ses.name, passes, xvfb_command()
    )
    start = clock()
    with open(files.log, "wb") as log:
        proc = spawn(
            command,
            cwd=str(files.kicad),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        outcome, code, detail = watch(
            proc, files.log, stall_timeout_s, hard_timeout_s, clock, sleep, kill
        )
    seconds = clock() - start
    written = files.ses.is_file()
    if outcome == FINISHED and not written:
        why = "exited" if code == 0 else f"exited with code {code}"
        detail = f"Freerouting {why} without writing {files.ses.name}"
        tail = _last_lines(_read(files.log))
        if tail:
            detail += f"; the end of its log:\n{tail}"
    return RouterRun(outcome, code, seconds, files.log, written, detail)


# --- the retry loop -----------------------------------------------------------------


def copper_problems(counts: Mapping[str, int]) -> dict[str, int]:
    """Return the copper categories of a DRC report's counts, zeros included."""
    return {name: counts.get(name, 0) for name in COPPER_CATEGORIES}


@dataclass(frozen=True)
class Attempt:
    """One try of the loop.

    ``status`` is CLEAN, PROBLEMS (a board with copper problems), STALLED, TIMED_OUT or
    NO_RESULT. ``categories`` holds the copper problem counts of a try that made a
    board, and is empty for one that did not.
    """

    number: int
    status: str
    categories: Mapping[str, int]
    seconds: float
    note: str = ""

    @property
    def problems(self) -> int | None:
        """Return the number of copper problems, or None if the try made no board."""
        return sum(self.categories.values()) if self.categories else None


@dataclass(frozen=True)
class LoopResult:
    """How the loop ended: every try, and the best of them.

    ``best`` is the clean try if there is one, else the try with the fewest copper
    problems (the earliest of equals), or None if no try made a board. ``fell_back``
    is True if an eco run stalled and the whole board was routed instead.
    """

    attempts: tuple[Attempt, ...]
    clean: bool
    best: Attempt | None
    fell_back: bool = False


def _problem_text(attempt: Attempt) -> str:
    """Return "2 copper problems (clearance 1, unconnected_items 1)" for a try."""
    found = ", ".join(f"{k} {v}" for k, v in attempt.categories.items() if v)
    total = attempt.problems or 0
    noun = "problem" if total == 1 else "problems"
    return f"{total} copper {noun} ({found})"


def describe(attempt: Attempt, tries: int) -> str:
    """Return the line printed for one try: its problems by category, or why not."""
    head = f"try {attempt.number}/{tries}"
    if attempt.status == CLEAN:
        return f"{head}: clean, {attempt.seconds:.0f} s"
    if attempt.status == PROBLEMS:
        return f"{head}: {_problem_text(attempt)}, {attempt.seconds:.0f} s"
    return f"{head}: {attempt.status}: {attempt.note}"


def route_loop(
    tries: int,
    run: Callable[[], RouterRun],
    post: Callable[[], str],
    drc: Callable[[], Mapping[str, int]],
    fall_back: Callable[[], None] | None = None,
    keep: Callable[[Attempt], None] | None = None,
    restore: Callable[[Attempt], None] | None = None,
    say: Callable[[str], None] = click.echo,
    clock: Callable[[], float] = time.monotonic,
) -> LoopResult:
    """Run the router, finish the board and check it, until a try's copper is clean.

    Each try calls ``run`` (one Freerouting run), then ``post`` (import its result and
    finish the board; it returns a line to show) and ``drc`` (the DRC category counts
    of that board). The first try with no copper problem ends the loop. After
    ``tries`` tries without one it gives up, and ``restore`` is called with the best
    try if the board left behind is not that one. ``keep`` is called with each try
    that is the best so far, so its files can be set aside before the next try
    overwrites them.

    A run that makes no session file (stalled, timed out, or exited without one) is a
    failed try and is not post-processed. If ``fall_back`` is given, the first stall
    calls it instead, and does not use up a try: it is for ``pcbkit route --eco``,
    whose board can stall the router, to route the whole board instead.
    """
    attempts: list[Attempt] = []
    best: Attempt | None = None
    fell_back = False
    while len(attempts) < tries:
        number = len(attempts) + 1
        began = clock()
        ran = run()
        if ran.outcome == STALLED and fall_back is not None:
            say(f"the eco run stalled: {ran.detail}")
            say("routing the whole board instead of keeping the old route")
            fall_back()
            fall_back = None
            fell_back = True
            continue
        if not ran.ses_written:
            status = ran.outcome if ran.outcome != FINISHED else NO_RESULT
            attempt = Attempt(number, status, {}, clock() - began, ran.detail)
            attempts.append(attempt)
            say(describe(attempt, tries))
            continue
        say(post())
        categories = copper_problems(drc())
        problems = sum(categories.values())
        status = CLEAN if problems == 0 else PROBLEMS
        attempt = Attempt(number, status, categories, clock() - began)
        attempts.append(attempt)
        say(describe(attempt, tries))
        if problems == 0:
            return LoopResult(tuple(attempts), True, attempt, fell_back)
        if best is None or problems < (best.problems or 0):
            best = attempt
            if keep is not None:
                keep(attempt)
    if best is None:
        say(f"gave up after {tries} tries: none of them made a routed board")
    else:
        say(
            f"gave up after {tries} tries: the best was try {best.number}: "
            f"{_problem_text(best)}"
        )
        if attempts[-1] is not best and restore is not None:
            restore(best)
    return LoopResult(tuple(attempts), False, best, fell_back)
