"""Unit tests for pcbkit.route.freerouting: the log parser, the watchdog, the loop.

Nothing real runs: the process is a fake that "prints" on a fake clock, so a stall of
90 seconds takes no time and a test can say exactly when each line appears.
"""

from __future__ import annotations

import signal
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import click
import pytest

from pcbkit.project import load_project
from pcbkit.route import freerouting as fr
from pcbkit.route.files import RouteFiles, route_files
from tests.board_files import TOML, write_file
from tests.fake_machine import FakeMachine

# --- a Freerouting log, as the console shows it ------------------------------------

STAMP = "2026-01-01 10:00:{sec:06.3f} [{thread}] {level:5} "


def line(sec: float, text: str, thread: str = "Thread-20", level: str = "INFO") -> str:
    """Return one console line of the router's log, as it prints them."""
    return STAMP.format(sec=sec, thread=thread, level=level) + text + "\n"


HEALTHY = (
    line(0.0, "Freerouting v1.9.0 (build-date: 2023-10-30)", "main")
    + line(1.0, "Opening 'my_board.dsn'...", "main")
    + line(1.3, "New version available: v2.5.0", "pool-1")
    + line(23.0, "Starting auto-routing...")
    + line(50.0, "CalcFromSide: start corner was not found", level="WARN")
    + line(80.0, "Auto-routing was completed in 1 minute(s) 7.5 seconds.")
    + line(80.1, "Starting route optimization on 1 thread...")
    + line(90.0, "Route optimization was completed in 9.90 seconds.")
    + line(90.1, "Saving 'my_board.ses'...")
)

LOADING_ONLY = (
    line(0.0, "Freerouting v1.9.0 (build-date: 2023-10-30)", "main")
    + line(1.0, "Opening 'my_board.dsn'...", "main")
    + line(2.0, "normalization of net '/NET_A' failed", "main", "WARN")
)


# --- the parser ---------------------------------------------------------------------


def test_the_parser_reads_a_healthy_run() -> None:
    """Report that routing started, how long it took, and that the session was saved."""
    log = fr.parse_log(HEALTHY)
    assert log.started_routing
    assert log.routing_seconds == pytest.approx(67.5)  # 1 minute(s) 7.5 seconds
    assert log.optimized
    assert log.saved
    assert log.warnings == (
        "2026-01-01 10:00:50.000 [Thread-20] WARN  CalcFromSide: start corner "
        "was not found",
    )
    assert log.normalization_failures == ()


def test_the_parser_reads_seconds_without_minutes() -> None:
    """Read "completed in 33.48 seconds" as 33.48."""
    text = "x INFO  Auto-routing was completed in 33.48 seconds.\n"
    assert fr.parse_log(text).routing_seconds == pytest.approx(33.48)


def test_the_parser_reads_a_run_that_hung_while_loading() -> None:
    """Report no routing, no session, and the line that marks the hang."""
    log = fr.parse_log(LOADING_ONLY)
    assert not log.started_routing
    assert log.routing_seconds is None
    assert not log.optimized and not log.saved
    assert log.normalization_failures == (
        "2026-01-01 10:00:02.000 [main] WARN  normalization of net '/NET_A' failed",
    )


def test_the_parser_reads_an_empty_log() -> None:
    """Say nothing happened, without failing."""
    log = fr.parse_log("")
    assert not log.started_routing and log.routing_seconds is None
    assert log.warnings == () and log.normalization_failures == ()


# --- the command line ---------------------------------------------------------------


def test_the_command_line_is_the_one_route_sh_used() -> None:
    """Pass the DSN, the session file and the pass limit, in that order."""
    router = fr.Router("/jdk/bin/java", "/jars/freerouting-1.9.0.jar")
    assert fr.build_command(router, "b.dsn", "b.ses", 40) == [
        "/jdk/bin/java",
        "-jar",
        "/jars/freerouting-1.9.0.jar",
        "-de",
        "b.dsn",
        "-do",
        "b.ses",
        "-mp",
        "40",
    ]


def test_the_command_runs_under_xvfb_when_asked() -> None:
    """Put xvfb-run -a in front of Java."""
    router = fr.Router("java", "r.jar")
    command = fr.build_command(router, "b.dsn", "b.ses", 5, xvfb="/usr/bin/xvfb-run")
    assert command[:3] == ["/usr/bin/xvfb-run", "-a", "java"]
    assert command[-2:] == ["-mp", "5"]


def test_xvfb_is_used_on_linux_when_installed(machine: FakeMachine) -> None:
    """Find xvfb-run on a Linux machine that has it."""
    machine.os_name = "linux"
    machine.path_tools["xvfb-run"] = "/usr/bin/xvfb-run"
    assert fr.xvfb_command() == "/usr/bin/xvfb-run"


def test_xvfb_is_not_used_on_linux_without_it(machine: FakeMachine) -> None:
    """Return None where xvfb-run is not installed."""
    machine.os_name = "linux"
    assert fr.xvfb_command() is None


def test_xvfb_is_never_used_on_macos(machine: FakeMachine) -> None:
    """Show the Freerouting window on a Mac, even if xvfb-run happens to exist."""
    machine.os_name = "macos"
    machine.path_tools["xvfb-run"] = "/opt/homebrew/bin/xvfb-run"
    assert fr.xvfb_command() is None


# --- finding Java and the jar -------------------------------------------------------

JAVA_17 = 'openjdk version "17.0.20.1" 2026-08-18\nOpenJDK Runtime Environment\n'
JAVA_11 = 'openjdk version "11.0.2" 2019-01-15\nOpenJDK Runtime Environment\n'


def make_jar(path: Path) -> Path:
    """Write a real, tiny zip archive at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    return path


def test_the_router_is_java_and_the_jar(machine: FakeMachine) -> None:
    """Return the Java that was found and the jar that was found."""
    java = machine.exe(machine.root / "jdk" / "bin" / "java", JAVA_17, on_path="java")
    jar = make_jar(machine.home / ".local/share/pcbkit/freerouting-1.9.0.jar")
    assert fr.find_router() == fr.Router(str(java), str(jar))


def test_no_java_is_an_error_that_says_how_to_get_it(machine: FakeMachine) -> None:
    """Name the Java version and the install command."""
    make_jar(machine.home / ".local/share/pcbkit/freerouting-1.9.0.jar")
    with pytest.raises(click.ClickException, match=r"Java 17 or newer.*openjdk@21"):
        fr.find_router()


def test_an_old_java_is_an_error(machine: FakeMachine) -> None:
    """Refuse Java 11 and say which one was found."""
    machine.exe(machine.root / "old" / "bin" / "java", JAVA_11, on_path="java")
    make_jar(machine.home / ".local/share/pcbkit/freerouting-1.9.0.jar")
    with pytest.raises(click.ClickException, match=r"Java 11\.0\.2 .*too old"):
        fr.find_router()


def test_no_jar_is_an_error_that_points_at_setup(machine: FakeMachine) -> None:
    """Name `pcbkit setup` and the override variable."""
    machine.exe(machine.root / "jdk" / "bin" / "java", JAVA_17, on_path="java")
    with pytest.raises(click.ClickException, match=r"pcbkit setup.*FREEROUTING_JAR"):
        fr.find_router()


def test_a_truncated_jar_is_an_error(machine: FakeMachine) -> None:
    """Refuse a file that is not a complete archive."""
    machine.exe(machine.root / "jdk" / "bin" / "java", JAVA_17, on_path="java")
    bad = machine.home / ".local/share/pcbkit/freerouting-1.9.0.jar"
    write_file(bad, "PK\x03\x04 cut short")
    with pytest.raises(click.ClickException, match="not a complete jar"):
        fr.find_router()


# --- a fake process on a fake clock -------------------------------------------------


class World:
    """A fake clock, and a fake process that prints to its log as the clock moves.

    ``prints`` maps a time (seconds after the start) to the text written then. The
    process exits by itself at ``exit_at`` with ``code``, or runs for ever if that is
    None, and ``kill`` ends it at once.
    """

    pid = 424242  # no real process has this: the fake kill functions never signal it

    def __init__(
        self,
        log: Path,
        prints: dict[float, str],
        exit_at: float | None = None,
        code: int = 0,
        on_exit: Any = None,
    ) -> None:
        """Start at time 0 with an empty log."""
        self.log = log
        self.prints = sorted(prints.items())
        self.exit_at = exit_at
        self.code = code
        self.on_exit = on_exit
        self.t = 0.0
        self.killed = False
        self.sleeps = 0
        log.write_text("", encoding="utf-8")
        self._emit()

    def _emit(self) -> None:
        """Write whatever the process would have printed by now."""
        while self.prints and self.prints[0][0] <= self.t:
            _, text = self.prints.pop(0)
            with open(self.log, "a", encoding="utf-8") as handle:
                handle.write(text)
        if self.on_exit and self.poll() is not None:
            self.on_exit()
            self.on_exit = None

    def clock(self) -> float:
        """Return the fake time."""
        return self.t

    def sleep(self, seconds: float) -> None:
        """Move the fake time on."""
        self.sleeps += 1
        assert self.sleeps < 10_000, "the watchdog never ended"
        self.t += seconds
        self._emit()

    def poll(self) -> int | None:
        """Return the exit code once the process has exited or been killed."""
        if self.killed:
            return -9
        if self.exit_at is not None and self.t >= self.exit_at:
            return self.code
        return None

    def kill(self, proc: Any = None) -> None:
        """End the process, as the watchdog's kill does."""
        self.killed = True


def watch(world: World, stall: float = 90.0, hard: float = 900.0) -> Any:
    """Run the watchdog on ``world`` and return its result."""
    return fr.watch(
        world,
        world.log,
        stall,
        hard,
        clock=world.clock,
        sleep=world.sleep,
        kill=world.kill,
    )


def test_a_run_that_never_starts_routing_is_killed_and_reported(tmp_path: Path) -> None:
    """Kill it at the stall timeout, not before, and say it stalled."""
    world = World(
        tmp_path / "fr.log",
        {0.0: "INFO Freerouting v1.9.0\n", 1.0: "INFO Opening 'my_board.dsn'...\n"},
    )
    outcome, code, detail = watch(world, stall=90)
    assert outcome == fr.STALLED
    assert code is None
    assert world.killed
    assert 90 <= world.t < 91
    assert detail == (
        'no "Starting auto-routing" in 90 s: Freerouting stalled while loading '
        "the board"
    )


def test_the_stall_report_quotes_the_normalization_failure(tmp_path: Path) -> None:
    """Add the log's own explanation when it has one."""
    world = World(tmp_path / "fr.log", {2.0: LOADING_ONLY})
    outcome, _, detail = watch(world, stall=30)
    assert outcome == fr.STALLED
    assert "normalization of net '/NET_A' failed" in detail


def test_a_run_that_starts_routing_is_left_alone(tmp_path: Path) -> None:
    """Do not kill a run that prints the marker, however long it then takes."""
    world = World(tmp_path / "fr.log", {22.0: HEALTHY}, exit_at=400.0, code=0)
    outcome, code, detail = watch(world, stall=90, hard=900)
    assert (outcome, code, detail) == (fr.FINISHED, 0, "")
    assert not world.killed
    assert world.t >= 400


def test_a_marker_at_the_deadline_still_counts(tmp_path: Path) -> None:
    """Look at the log before the clock: a run that starts at 90 s is not a stall."""
    world = World(
        tmp_path / "fr.log",
        {90.0: "INFO  Starting auto-routing...\n"},
        exit_at=120.0,
    )
    outcome, _, _ = watch(world, stall=90)
    assert outcome == fr.FINISHED
    assert not world.killed


def test_a_run_that_routes_for_ever_is_killed_at_the_hard_timeout(
    tmp_path: Path,
) -> None:
    """Kill a run that started routing and never exits, and say it timed out."""
    world = World(tmp_path / "fr.log", {20.0: "INFO  Starting auto-routing...\n"})
    outcome, code, detail = watch(world, stall=90, hard=300)
    assert outcome == fr.TIMED_OUT
    assert code is None
    assert world.killed
    assert 300 <= world.t < 301
    assert detail == "still running after 300 s"


def test_a_run_that_exits_before_routing_is_not_a_stall(tmp_path: Path) -> None:
    """Report an early exit as finished, with its exit code, and kill nothing."""
    world = World(
        tmp_path / "fr.log",
        {0.0: "ERROR could not read the DSN\n"},
        exit_at=3.0,
        code=1,
    )
    outcome, code, _ = watch(world, stall=90)
    assert (outcome, code) == (fr.FINISHED, 1)
    assert not world.killed


def test_a_missing_log_is_waited_for(tmp_path: Path) -> None:
    """Treat a log that does not exist yet as empty, not as an error."""
    world = World(tmp_path / "fr.log", {}, exit_at=5.0)
    (tmp_path / "fr.log").unlink()
    outcome, _, _ = watch(world, stall=90)
    assert outcome == fr.FINISHED


# --- ending the process -------------------------------------------------------------


class Stubborn:
    """A process that only leaves when it is sent ``dies_on``."""

    pid = 424243

    def __init__(self, dies_on: int) -> None:
        """Run until the signal ``dies_on`` arrives."""
        self.dies_on = dies_on
        self.sent: list[int] = []
        self.waited = False
        self.t = 0.0

    def poll(self) -> int | None:
        """Return an exit code once the fatal signal has been sent."""
        return -1 if self.dies_on in self.sent else None

    def wait(self) -> int:
        """Record that the process was reaped."""
        self.waited = True
        return -1

    def send(self, proc: Any, sig: int) -> None:
        """Record a signal instead of delivering it."""
        self.sent.append(sig)

    def clock(self) -> float:
        """Return the fake time."""
        return self.t

    def sleep(self, seconds: float) -> None:
        """Move the fake time on."""
        self.t += seconds


def test_stop_asks_politely_first(tmp_path: Path) -> None:
    """Send SIGTERM, and nothing harder when that is enough."""
    proc = Stubborn(dies_on=signal.SIGTERM)
    fr.stop(proc, proc.send, 5.0, proc.clock, proc.sleep)
    assert proc.sent == [signal.SIGTERM]
    assert proc.waited


def test_stop_insists_after_the_grace_period() -> None:
    """Send SIGKILL to a process that ignores SIGTERM, after waiting 5 s."""
    proc = Stubborn(dies_on=signal.SIGKILL)
    fr.stop(proc, proc.send, 5.0, proc.clock, proc.sleep)
    assert proc.sent == [signal.SIGTERM, signal.SIGKILL]
    assert 5.0 <= proc.t < 6.0
    assert proc.waited


# --- one run, with the process faked ------------------------------------------------


def project_files(tmp_path: Path) -> RouteFiles:
    """Return the routing file names of a throwaway project, with kicad/ made."""
    write_file(tmp_path / "pcbkit.toml", TOML)
    files = route_files(load_project(tmp_path))
    files.kicad.mkdir()
    return files


class Spawner:
    """Stands in for subprocess.Popen: records the call, returns a ``World``."""

    def __init__(self, files: RouteFiles, **world: Any) -> None:
        """Remember what the fake process should do."""
        self.files = files
        self.world_args = world
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.world: World | None = None

    def __call__(self, command: list[str], **kwargs: Any) -> World:
        """Start the fake process: its log is the file the caller opened."""
        self.calls.append((command, kwargs))
        self.world = World(self.files.log, **self.world_args)
        return self.world


def run(files: RouteFiles, spawner: Spawner, **kwargs: Any) -> fr.RouterRun:
    """Run ``run_router`` with the fake process and clock."""
    router = fr.Router("/jdk/bin/java", "/jars/fr.jar")
    options: dict[str, Any] = {"stall_timeout_s": 90.0}
    options.update(kwargs)
    return fr.run_router(
        files,
        router,
        40,
        spawn=spawner,
        clock=lambda: spawner.world.t if spawner.world else 0.0,
        sleep=lambda dt: spawner.world.sleep(dt),  # type: ignore[union-attr]
        kill=lambda proc: spawner.world.kill(),  # type: ignore[union-attr]
        **options,
    )


def test_a_run_starts_java_in_its_own_group_in_the_kicad_folder(
    tmp_path: Path, machine: FakeMachine
) -> None:
    """Run the documented command in kicad/, in a process group of its own."""
    files = project_files(tmp_path)
    spawner = Spawner(
        files,
        prints={20.0: "INFO  Starting auto-routing...\n"},
        exit_at=60.0,
        on_exit=lambda: files.ses.write_text("(session)", encoding="utf-8"),
    )
    result = run(files, spawner)
    ((command, kwargs),) = spawner.calls
    assert command == [
        "/jdk/bin/java",
        "-jar",
        "/jars/fr.jar",
        "-de",
        "my_board.dsn",
        "-do",
        "my_board.ses",
        "-mp",
        "40",
    ]
    assert kwargs["cwd"] == str(files.kicad)
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert result.outcome == fr.FINISHED
    assert result.ses_written
    assert result.log == files.log
    assert result.seconds == pytest.approx(60.0, abs=1.0)


def test_a_run_deletes_the_old_session_first(
    tmp_path: Path, machine: FakeMachine
) -> None:
    """Make sure a session file found afterwards was written by this run."""
    files = project_files(tmp_path)
    files.ses.write_text("(session from an earlier run)", encoding="utf-8")
    spawner = Spawner(files, prints={}, exit_at=2.0)  # exits at once, writes nothing
    result = run(files, spawner)
    assert not files.ses.exists()
    assert not result.ses_written


def test_a_stalled_run_reports_the_stall_and_no_session(
    tmp_path: Path, machine: FakeMachine
) -> None:
    """Pass the watchdog's verdict through."""
    files = project_files(tmp_path)
    spawner = Spawner(files, prints={1.0: LOADING_ONLY})
    result = run(files, spawner, stall_timeout_s=30.0)
    assert result.outcome == fr.STALLED
    assert not result.ses_written
    assert "Starting auto-routing" in result.detail
    assert result.returncode is None


def test_a_run_that_exits_without_a_session_says_so_with_the_log_tail(
    tmp_path: Path, machine: FakeMachine
) -> None:
    """Explain a clean exit that left no session, and show the end of the log."""
    files = project_files(tmp_path)
    spawner = Spawner(
        files, prints={0.0: "ERROR could not read my_board.dsn\n"}, exit_at=2.0, code=1
    )
    result = run(files, spawner)
    assert result.outcome == fr.FINISHED
    assert not result.ses_written
    assert result.returncode == 1
    assert "exited with code 1 without writing my_board.ses" in result.detail
    assert "ERROR could not read my_board.dsn" in result.detail


def test_the_run_uses_xvfb_on_linux(tmp_path: Path, machine: FakeMachine) -> None:
    """Start the command under xvfb-run when the machine has it."""
    machine.os_name = "linux"
    machine.path_tools["xvfb-run"] = "/usr/bin/xvfb-run"
    files = project_files(tmp_path)
    spawner = Spawner(files, prints={}, exit_at=1.0)
    run(files, spawner)
    assert spawner.calls[0][0][:3] == ["/usr/bin/xvfb-run", "-a", "/jdk/bin/java"]


# --- the retry loop -----------------------------------------------------------------


def finished(ses: bool = True, outcome: str = fr.FINISHED, detail: str = "") -> Any:
    """Return a RouterRun, by default a normal one that wrote its session."""
    return fr.RouterRun(outcome, 0, 60.0, Path("fr.log"), ses, detail)


class Rig:
    """The loop's callables, as fakes that record what the loop did with them.

    ``runs`` are the router results in order; ``drcs`` are the category counts the
    DRC reports for each try that made a board, in order.
    """

    def __init__(self, runs: list[Any], drcs: list[dict[str, int]]) -> None:
        """Queue the fakes' answers."""
        self.runs = list(runs)
        self.drcs = list(drcs)
        self.log: list[str] = []
        self.said: list[str] = []
        self.fell_back = 0
        self.kept: list[int] = []
        self.restored: list[int] = []

    def run(self) -> Any:
        """Return the next router result."""
        self.log.append("run")
        return self.runs.pop(0)

    def post(self) -> str:
        """Pretend to finish the board."""
        self.log.append("post")
        return "post done"

    def drc(self) -> dict[str, int]:
        """Return the next try's DRC category counts."""
        self.log.append("drc")
        return self.drcs.pop(0)

    def fall_back(self) -> None:
        """Record the fall-back to a full route."""
        self.log.append("fall back")
        self.fell_back += 1

    def keep(self, attempt: fr.Attempt) -> None:
        """Record a try being set aside as the best so far."""
        self.kept.append(attempt.number)

    def restore(self, attempt: fr.Attempt) -> None:
        """Record the best try being put back."""
        self.restored.append(attempt.number)

    def loop(self, tries: int = 3, eco: bool = False) -> fr.LoopResult:
        """Run the loop against the fakes."""
        return fr.route_loop(
            tries,
            self.run,
            self.post,
            self.drc,
            fall_back=self.fall_back if eco else None,
            keep=self.keep,
            restore=self.restore,
            say=self.said.append,
            clock=lambda: 0.0,
        )


def test_the_loop_fails_twice_then_succeeds_and_stops_at_the_first_clean_try() -> None:
    """Run the router three times, keep the clean third try and run no fourth."""
    rig = Rig(
        [finished(), finished(), finished(), finished()],
        [{"clearance": 2}, {"unconnected_items": 1, "shorting_items": 1}, {}, {}],
    )
    result = rig.loop(tries=6)
    assert result.clean
    assert [a.status for a in result.attempts] == [
        fr.PROBLEMS,
        fr.PROBLEMS,
        fr.CLEAN,
    ]
    assert result.best is result.attempts[-1]
    assert rig.log == ["run", "post", "drc"] * 3  # the fourth run never happened
    assert rig.restored == []  # the clean board is the one in place
    assert rig.said == [
        "post done",
        "try 1/6: 2 copper problems (clearance 2), 0 s",
        "post done",
        "try 2/6: 2 copper problems (unconnected_items 1, shorting_items 1), 0 s",
        "post done",
        "try 3/6: clean, 0 s",
    ]


def test_every_copper_category_is_counted_and_nothing_else() -> None:
    """Count the six route.sh categories; ignore silk and footprint entries."""
    counts = {
        "unconnected_items": 1,
        "clearance": 2,
        "shorting_items": 3,
        "tracks_crossing": 4,
        "hole_clearance": 5,
        "copper_edge_clearance": 6,
        "silk_overlap": 40,
        "lib_footprint_issues": 9,
    }
    assert fr.copper_problems(counts) == {
        "unconnected_items": 1,
        "clearance": 2,
        "shorting_items": 3,
        "tracks_crossing": 4,
        "hole_clearance": 5,
        "copper_edge_clearance": 6,
    }
    assert sum(fr.copper_problems({"silk_overlap": 66}).values()) == 0


def test_a_board_with_only_silk_warnings_is_clean() -> None:
    """Judge a try by copper alone: silk warnings are fixed later, by the silkscreen."""
    rig = Rig([finished()], [{"silk_overlap": 33, "silk_over_copper": 32}])
    result = rig.loop(tries=3)
    assert result.clean and len(result.attempts) == 1


def test_the_loop_gives_up_with_the_best_try_and_puts_it_back() -> None:
    """After the last try, restore the best one: here the second, not the last."""
    rig = Rig(
        [finished(), finished(), finished()],
        [{"clearance": 5}, {"clearance": 1}, {"clearance": 3}],
    )
    result = rig.loop(tries=3)
    assert not result.clean
    assert result.best is result.attempts[1]
    assert rig.kept == [1, 2]  # the third was no better than the second
    assert rig.restored == [2]
    assert rig.said[-1] == (
        "gave up after 3 tries: the best was try 2: 1 copper problem (clearance 1)"
    )


def test_the_loop_does_not_restore_when_the_last_try_is_the_best() -> None:
    """Leave the board alone if the board in place is already the best."""
    rig = Rig([finished(), finished()], [{"clearance": 4}, {"clearance": 2}])
    result = rig.loop(tries=2)
    assert not result.clean
    assert result.best is result.attempts[-1]
    assert rig.restored == []


def test_equal_tries_keep_the_earliest() -> None:
    """Prefer the first of two equally bad tries: it was set aside already."""
    rig = Rig([finished(), finished()], [{"clearance": 2}, {"hole_clearance": 2}])
    result = rig.loop(tries=2)
    assert result.best is result.attempts[0]
    assert rig.kept == [1]
    assert rig.restored == [1]


def test_a_stall_is_a_failed_try_in_a_full_route() -> None:
    """Count a stalled run as a try, skip post and DRC for it, and carry on."""
    stalled = finished(ses=False, outcome=fr.STALLED, detail="no start in 90 s")
    rig = Rig([stalled, finished()], [{}])
    result = rig.loop(tries=3)
    assert result.clean
    assert [a.status for a in result.attempts] == [fr.STALLED, fr.CLEAN]
    assert rig.log == ["run", "run", "post", "drc"]
    assert rig.said[0] == "try 1/3: stalled: no start in 90 s"
    assert not result.fell_back


def test_a_run_with_no_session_is_a_failed_try() -> None:
    """Call a run that finished but wrote nothing "no result", with its reason."""
    nothing = finished(ses=False, detail="exited with code 1 without writing b.ses")
    rig = Rig([nothing, finished()], [{}])
    result = rig.loop(tries=2)
    assert [a.status for a in result.attempts] == [fr.NO_RESULT, fr.CLEAN]
    assert rig.said[0] == (
        "try 1/2: no result: exited with code 1 without writing b.ses"
    )


def test_a_timed_out_run_is_a_failed_try() -> None:
    """Report a killed-at-the-limit run by its outcome."""
    slow = finished(ses=False, outcome=fr.TIMED_OUT, detail="still running after 900 s")
    rig = Rig([slow], [])
    result = rig.loop(tries=1)
    assert not result.clean
    assert result.attempts[0].status == fr.TIMED_OUT
    assert rig.said[-1] == "gave up after 1 tries: none of them made a routed board"
    assert result.best is None
    assert rig.restored == []


def test_an_eco_stall_falls_back_to_a_full_route_without_using_a_try() -> None:
    """On --eco, route the whole board after a stall; the stall costs no try."""
    stalled = finished(ses=False, outcome=fr.STALLED, detail="no start in 90 s")
    rig = Rig([stalled, finished()], [{}])
    result = rig.loop(tries=1, eco=True)  # one try only: the fall-back must not eat it
    assert result.clean
    assert result.fell_back
    assert rig.fell_back == 1
    assert rig.log == ["run", "fall back", "run", "post", "drc"]
    assert rig.said[:2] == [
        "the eco run stalled: no start in 90 s",
        "routing the whole board instead of keeping the old route",
    ]
    assert len(result.attempts) == 1


def test_a_second_stall_after_the_fall_back_is_a_failed_try() -> None:
    """Fall back once only: a stall in the full route fails a try as usual."""
    stalled = finished(ses=False, outcome=fr.STALLED, detail="no start")
    rig = Rig([stalled, stalled, finished()], [{}])
    result = rig.loop(tries=2, eco=True)
    assert rig.fell_back == 1
    assert [a.status for a in result.attempts] == [fr.STALLED, fr.CLEAN]


def test_a_non_eco_loop_never_falls_back() -> None:
    """Leave fall_back alone when none was given."""
    stalled = finished(ses=False, outcome=fr.STALLED, detail="no start")
    rig = Rig([stalled], [])
    result = rig.loop(tries=1, eco=False)
    assert rig.fell_back == 0
    assert not result.fell_back


def test_the_loop_uses_exactly_the_tries_it_is_given() -> None:
    """Run the router once per try, no more, when none is clean."""
    rig = Rig([finished()] * 4, [{"clearance": 1}] * 4)
    rig.loop(tries=4)
    assert rig.log.count("run") == 4


def test_a_one_problem_try_reads_in_the_singular() -> None:
    """Say "1 copper problem", and list the category."""
    attempt = fr.Attempt(1, fr.PROBLEMS, fr.copper_problems({"clearance": 1}), 61.0)
    assert fr.describe(attempt, 3) == "try 1/3: 1 copper problem (clearance 1), 61 s"
