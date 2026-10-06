"""Integration: the watchdog against real processes (a stand-in for Freerouting).

The unit tests run the watchdog on a fake clock. Here the clock, the log file, the
process and the signals are real; only the program is not: a short Python script that
prints what Freerouting prints, and sleeps, exits or ignores signals as a test needs.
Nothing here needs KiCad or Java, but it uses POSIX process groups, as pcbkit does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pcbkit.project import load_project
from pcbkit.route import freerouting as fr
from pcbkit.route.files import RouteFiles, route_files
from tests.board_files import TOML, write_file

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the watchdog uses POSIX process groups"
)

ROUTER = fr.Router("python", "freerouting-stand-in.jar")


@pytest.fixture
def files(tmp_path: Path) -> RouteFiles:
    """Return the routing file names of a throwaway project, with kicad/ made."""
    write_file(tmp_path / "pcbkit.toml", TOML)
    found = route_files(load_project(tmp_path))
    found.kicad.mkdir()
    return found


def stand_in(script: str) -> Callable[..., Any]:
    """Return a ``spawn`` that starts ``script`` under Python in Freerouting's place.

    It takes the call ``run_router`` makes for Java, keeps its working directory,
    session, standard streams and process group, and swaps the command.
    """

    def spawn(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        code = "import sys, time\n" + textwrap.dedent(script)
        return subprocess.Popen([sys.executable, "-u", "-c", code], **kwargs)

    return spawn


def alive(pid: int) -> bool:
    """Return True if a process with this id still exists (a zombie counts)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def run(files: RouteFiles, script: str, **kwargs: Any) -> fr.RouterRun:
    """Run the stand-in under ``run_router`` with short real timeouts."""
    options: dict[str, Any] = {"stall_timeout_s": 1.0, "hard_timeout_s": 30.0}
    options.update(kwargs)
    return fr.run_router(files, ROUTER, 40, spawn=stand_in(script), **options)


def test_a_run_that_never_starts_routing_is_killed_after_the_stall_timeout(
    files: RouteFiles,
) -> None:
    """Kill a process that loads and then goes quiet, in about a second."""
    began = time.monotonic()
    result = run(
        files,
        """
        print("INFO  Opening 'my_board.dsn'...")
        print("WARN  normalization of net '/NET_A' failed")
        time.sleep(60)
        """,
    )
    assert result.outcome == fr.STALLED
    assert result.returncode is None
    assert not result.ses_written
    assert 1.0 <= time.monotonic() - began < 10.0
    assert "normalization of net '/NET_A' failed" in result.detail
    assert "Opening" in files.log.read_text("utf-8")


def test_the_whole_process_group_goes_not_just_the_first_process(
    files: RouteFiles,
) -> None:
    """Leave no grandchild behind: Java under xvfb-run is one too."""
    pid_file = files.kicad / "grandchild.pid"
    result = run(
        files,
        f"""
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        print("INFO  Opening 'my_board.dsn'...")
        time.sleep(60)
        """,
    )
    assert result.outcome == fr.STALLED
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(grandchild)


def test_a_process_that_ignores_sigterm_is_killed_after_the_grace_period(
    files: RouteFiles,
) -> None:
    """Send SIGKILL when SIGTERM was not enough, and still reap the process."""
    ready = files.kicad / "ready"
    began = time.monotonic()
    result = run(
        files,
        f"""
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        open({str(ready)!r}, "w").write("up")
        print("INFO  Opening 'my_board.dsn'...")
        time.sleep(60)
        """,
        kill=lambda proc: fr.stop(proc, grace_s=0.5),
    )
    assert ready.exists()
    assert result.outcome == fr.STALLED
    assert time.monotonic() - began < 10.0


def test_a_run_that_routes_and_saves_is_finished_with_its_session(
    files: RouteFiles,
) -> None:
    """Let a healthy run alone; report the session it wrote."""
    result = run(
        files,
        f"""
        print("INFO  Opening 'my_board.dsn'...")
        time.sleep(0.3)
        print("INFO  Starting auto-routing...")
        time.sleep(1.5)  # past the 1 s stall timeout, but routing has started
        print("INFO  Saving 'my_board.ses'...")
        open({str(files.ses)!r}, "w").write("(session my_board)")
        """,
    )
    assert result.outcome == fr.FINISHED
    assert result.returncode == 0
    assert result.ses_written
    assert fr.parse_log(files.log.read_text("utf-8")).started_routing


def test_a_run_that_starts_but_never_ends_is_killed_at_the_hard_timeout(
    files: RouteFiles,
) -> None:
    """Kill a run that has started routing and runs on, and say it timed out."""
    began = time.monotonic()
    result = run(
        files,
        """
        print("INFO  Starting auto-routing...")
        time.sleep(60)
        """,
        stall_timeout_s=30.0,
        hard_timeout_s=1.5,
    )
    assert result.outcome == fr.TIMED_OUT
    assert result.detail == "still running after 1.5 s"
    assert 1.5 <= time.monotonic() - began < 10.0


def test_a_run_that_exits_at_once_with_an_error_is_not_a_stall(
    files: RouteFiles,
) -> None:
    """Report the exit code and the end of the log, and kill nothing."""
    result = run(
        files,
        """
        print("ERROR could not read my_board.dsn")
        sys.exit(3)
        """,
    )
    assert result.outcome == fr.FINISHED
    assert result.returncode == 3
    assert not result.ses_written
    assert "exited with code 3 without writing my_board.ses" in result.detail
    assert "ERROR could not read my_board.dsn" in result.detail


def test_an_old_session_file_is_not_mistaken_for_the_runs(files: RouteFiles) -> None:
    """Delete the session file first, so a run that writes none reports none."""
    files.ses.write_text("(session from yesterday)", encoding="utf-8")
    result = run(files, "sys.exit(0)")
    assert not result.ses_written
    assert not files.ses.exists()


def test_a_killed_process_is_reaped_and_not_left_a_zombie(files: RouteFiles) -> None:
    """Wait for the killed process, so nothing is left in the process table."""
    pid_file = files.kicad / "child.pid"
    result = run(
        files,
        f"""
        import os
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        time.sleep(60)
        """,
    )
    assert result.outcome == fr.STALLED
    assert not alive(int(pid_file.read_text()))
