"""Unit tests for pcbkit.kicad.quiet: hiding pcbnew's wx noise, and nothing else.

pcbnew writes two kinds of noise straight to file descriptor 2 (see the module's
docstring for the measurements). The tests use stand-ins that write the same lines with
``os.write(2, ...)``, and read them back with ``capfd``, which captures at the
descriptor like a terminal would. A test that needs a process of its own (a crash, a
closed standard error) runs a fresh Python and reads its real standard error.

The filter is a real child process: that is the thing under test, because a crash's last
words reach the terminal only if something outside the crashing process writes them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pcbkit.kicad import board as kb
from pcbkit.kicad import cli, env, quiet
from pcbkit.kicad.env import Run
from tests.fake_machine import FakeMachine

NNBSP = "\u202f"  # the space macOS writes before AM or PM: not an ASCII space
FORMATS = [
    "PNG file",
    "JPEG file",
    "TIFF file",
    "GIF file",
    "PNM file",
    "PCX file",
    "IFF file",
    "Windows icon file",
    "Windows cursor file",
    "Windows animated cursor file",
    "TGA file",
    "XPM file",
]
# The assertion exactly as KiCad 10.0.6 on macOS prints it.
ASSERT = (
    b'./src/common/stdpbase.cpp(59): assert ""traits"" failed in Get(): '
    b"create wxApp before calling this"
)


def handler(stamp: str, name: str = "PNG file") -> bytes:
    """Return the debug line wx prints for one image format, after ``stamp``."""
    prefix = f"{stamp}: " if stamp else ""
    return f"{prefix}Debug: Adding duplicate image handler for '{name}'".encode()


MAC = handler(f"11:44:35{NNBSP}PM")  # what this Mac writes, byte for byte


def write(*lines: bytes) -> None:
    """Write each line and a newline straight to descriptor 2, as C++ would."""
    for line in lines:
        os.write(2, line + b"\n")


def run_child(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh Python that can import pcbkit, and capture its output."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=120,
    )


# --- which lines are noise ------------------------------------------------------------

NOISE = [
    pytest.param(ASSERT, id="assertion as measured"),
    pytest.param(ASSERT + b"\r\n", id="assertion with a CRLF ending"),
    pytest.param(
        b'/Users/runner/work/wx/src/common/stdpbase.cpp(61): assert "traits" failed '
        b"in Get(): create wxApp before calling this",
        id="assertion: absolute path, other line number and quoting",
    ),
    pytest.param(
        ASSERT.replace(b"./src", b"../src"), id="assertion: path that climbs a folder"
    ),
    pytest.param(
        ASSERT.replace(b"./src/common/", b""), id="assertion: file name alone"
    ),
    pytest.param(MAC, id="12-hour stamp with the narrow no-break space"),
    pytest.param(handler("11:44:35 PM"), id="12-hour stamp with an ASCII space"),
    pytest.param(handler("23:44:35"), id="24-hour stamp"),
    pytest.param(handler("23.44.35"), id="dotted stamp"),
    pytest.param(handler("23時44分35秒"), id="stamp with CJK units"),
    pytest.param(handler("오후 11:47:53"), id="stamp with a leading day-part word"),
    pytest.param(handler(""), id="no stamp at all"),
    *[pytest.param(handler("23:44:35", name), id=name) for name in FORMATS],
    pytest.param(MAC + b"\n", id="debug line with its newline"),
]

REAL = [
    pytest.param(b"", id="empty line"),
    pytest.param(
        b"Failed to load board: Expecting '(' in 'x.kicad_pcb', line 1, offset 1.",
        id="kicad-cli's own error",
    ),
    pytest.param(b"Traceback (most recent call last):", id="a traceback"),
    pytest.param(
        b"libc++abi: terminating due to uncaught exception of type std::bad_alloc",
        id="a C++ crash message",
    ),
    pytest.param(b"Fatal Python error: Aborted", id="a Python crash message"),
    pytest.param(
        b"11:44:35 PM: Debug: Adding image handler for 'PNG file'",
        id="another debug message",
    ),
    pytest.param(
        b"11:44:35 PM: Debug: Loading board /tmp/x.kicad_pcb", id="another debug line"
    ),
    pytest.param(
        b"11:44:35 PM: Error: Adding duplicate image handler for 'PNG file'",
        id="same words, but an error",
    ),
    pytest.param(
        b"Adding duplicate image handler for 'PNG file'", id="same words, no Debug"
    ),
    pytest.param(MAC + b" (and more)", id="debug line with text after it"),
    pytest.param(
        b"11:44:35 PM: Debug: Adding duplicate image handler for ", id="no format named"
    ),
    pytest.param(
        b"oops: Debug: Adding duplicate image handler for 'PNG file'",
        id="stamp-like prefix with no digit",
    ),
    pytest.param(
        b"PM: Debug: Adding duplicate image handler for 'PNG file'",
        id="stamp-like prefix with letters only",
    ),
    pytest.param(
        b"1" * 41 + b": Debug: Adding duplicate image handler for 'PNG file'",
        id="prefix too long to be a stamp",
    ),
    pytest.param(b"progress 50% " + MAC, id="text glued to the front of a debug line"),
    pytest.param(
        b"progress 50% " + ASSERT, id="text glued to the front of the assertion"
    ),
    pytest.param(
        ASSERT.replace(b"stdpbase", b"other"), id="assertion from another file"
    ),
    pytest.param(
        ASSERT.replace(b'""traits""', b'"width"'), id="assertion about another thing"
    ),
    pytest.param(
        ASSERT.replace(b"create wxApp before calling this", b"it went wrong"),
        id="assertion with another message",
    ),
    pytest.param(ASSERT + b" (really)", id="assertion with text after it"),
    pytest.param(
        ASSERT.replace(b"(59)", b""), id="assertion with no line number to match"
    ),
]


@pytest.mark.parametrize("line", NOISE)
def test_known_noise_is_recognised(line: bytes) -> None:
    assert quiet.is_noise(line)


@pytest.mark.parametrize("line", REAL)
def test_anything_else_is_kept(line: bytes) -> None:
    assert not quiet.is_noise(line)


def emit(line: bytes) -> None:
    """Write ``line`` to descriptor 2 with a line ending, unless it has one already."""
    os.write(2, line if line.endswith(b"\n") else line + b"\n")


def test_the_filter_drops_every_known_noise_line(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Send them through the filter process, which matches with code of its own."""
    with quiet.quiet_stderr():
        for param in NOISE:
            emit(param.values[0])
    assert capfd.readouterr().err == ""


def test_the_filter_keeps_every_other_line(capfd: pytest.CaptureFixture[str]) -> None:
    with quiet.quiet_stderr():
        for param in REAL:
            emit(param.values[0])
    kept = [param.values[0].decode() + "\n" for param in REAL]
    assert capfd.readouterr().err == "".join(kept)


def test_strip_noise_keeps_every_other_line_as_it_is() -> None:
    text = (
        MAC.decode() + "\n"
        "first real line\n" + ASSERT.decode() + "\n"
        "  indented real line  \n"
        "last line without a newline"
    )
    assert quiet.strip_noise(text) == (
        "first real line\n  indented real line  \nlast line without a newline"
    )


# --- hiding it at the file descriptor ------------------------------------------------


def test_noise_is_dropped_and_other_lines_keep_their_order(
    capfd: pytest.CaptureFixture[str],
) -> None:
    write(b"before the call")
    with quiet.quiet_stderr():
        write(ASSERT, b"first real line", MAC, handler("23:44:35", "XPM file"))
        write(b"second real line", MAC)
    write(b"after the call")
    assert capfd.readouterr().err == (
        "before the call\nfirst real line\nsecond real line\nafter the call\n"
    )


def test_a_last_line_without_a_newline_is_kept(
    capfd: pytest.CaptureFixture[str],
) -> None:
    with quiet.quiet_stderr():
        write(MAC)
        os.write(2, b"no newline at the end")
    assert capfd.readouterr().err == "no newline at the end"


def test_more_output_than_a_pipe_holds_does_not_stall_the_call(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Write 400 KB of noise (a pipe holds 64 KB) between two real lines."""
    with quiet.quiet_stderr():
        write(b"first")
        for _ in range(6000):
            write(MAC)
        write(b"last")
    assert capfd.readouterr().err == "first\nlast\n"


def test_it_does_not_decode_what_it_keeps() -> None:
    """Pass bytes that are not UTF-8 through unchanged."""
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os\n"
            "from pcbkit.kicad import quiet\n"
            "with quiet.quiet_stderr():\n"
            "    os.write(2, b'caf\\xe9 is latin-1\\n')\n",
        ],
        capture_output=True,
        timeout=120,
    )
    assert (done.returncode, done.stderr) == (0, b"caf\xe9 is latin-1\n")


@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_an_exception_propagates_after_standard_error_is_back(
    capfd: pytest.CaptureFixture[str], error: type[BaseException]
) -> None:
    before = os.fstat(2)
    with pytest.raises(error):
        with quiet.quiet_stderr():
            write(MAC, b"the real error, written before the exception")
            raise error("planted")
    after = os.fstat(2)
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    write(b"written after the block")
    assert capfd.readouterr().err == (
        "the real error, written before the exception\nwritten after the block\n"
    )


def test_standard_error_is_back_after_every_block(
    capfd: pytest.CaptureFixture[str],
) -> None:
    for round_ in range(3):
        with quiet.quiet_stderr():
            write(MAC)
        write(f"line {round_}".encode())
    assert capfd.readouterr().err == "line 0\nline 1\nline 2\n"


class Recorder:
    """Stands in for quiet's ``subprocess`` module, and keeps each child it starts."""

    PIPE = subprocess.PIPE
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self) -> None:
        """Start with no children."""
        self.children: list[Any] = []

    def Popen(self, *args: Any, **kwargs: Any) -> Any:  # noqa: N802
        """Start the real child and remember it."""
        child = subprocess.Popen(*args, **kwargs)
        self.children.append(child)
        return child


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Make quiet start its filters through a recorder, which still starts them."""
    rec = Recorder()
    monkeypatch.setattr(quiet, "subprocess", rec)
    return rec


def test_the_filter_has_finished_when_the_block_returns(recorder: Recorder) -> None:
    with quiet.quiet_stderr():
        (child,) = recorder.children
        assert child.poll() is None
    assert child.returncode == 0


def test_the_filter_runs_in_its_own_session(recorder: Recorder) -> None:
    """Keep Ctrl-C from reaching the filter before pcbkit has put things back."""
    with quiet.quiet_stderr():
        (child,) = recorder.children
        assert os.getsid(child.pid) == child.pid
        assert os.getsid(child.pid) != os.getsid(0)


def test_a_block_inside_a_block_starts_no_second_filter(
    capfd: pytest.CaptureFixture[str], recorder: Recorder
) -> None:
    with quiet.quiet_stderr():
        with quiet.quiet_stderr():
            write(MAC, b"inner real line")
        write(MAC, b"outer real line")
    assert len(recorder.children) == 1
    assert capfd.readouterr().err == "inner real line\nouter real line\n"


def test_a_block_after_a_failed_one_still_works(
    capfd: pytest.CaptureFixture[str], recorder: Recorder
) -> None:
    """Reset the nesting flag even when the body raised."""
    with pytest.raises(RuntimeError):
        with quiet.quiet_stderr():
            raise RuntimeError("planted")
    with quiet.quiet_stderr():
        write(MAC, b"still filtered")
    assert len(recorder.children) == 2
    assert capfd.readouterr().err == "still filtered\n"


# --- when it cannot hide anything: show everything, break nothing ---------------------


@pytest.mark.parametrize("executable", ["", "/nonexistent/python"])
def test_without_a_usable_interpreter_the_noise_shows(
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    executable: str,
) -> None:
    monkeypatch.setattr(sys, "executable", executable)
    with quiet.quiet_stderr():
        write(MAC, b"real line")
    assert capfd.readouterr().err == MAC.decode() + "\nreal line\n"


def test_a_closed_standard_error_is_left_alone() -> None:
    done = run_child(
        """
        import os
        from pcbkit.kicad import quiet

        os.close(2)
        with quiet.quiet_stderr():
            print("ran")
        """
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, "ran\n", "")


# --- the reason it is a process: a crash's own messages ------------------------------

CRASH = """
import os
from pcbkit.kicad import quiet

with quiet.quiet_stderr():
    os.write(2, {noise!r} + b"\\n")
    os.write(2, b"libc++abi: terminating due to uncaught exception\\n")
    os.abort()
"""


def test_a_crashs_last_words_still_reach_standard_error() -> None:
    """Abort inside the block and read what the terminal would have shown.

    The last line is what pcbnew's C++ runtime writes just before it aborts. A filter
    that lived in the crashing process, or a temporary file read back afterwards, would
    lose it.
    """
    done = run_child(CRASH.format(noise=MAC))
    assert done.returncode != 0
    assert done.stderr == "libc++abi: terminating due to uncaught exception\n"


def test_python_level_writes_inside_the_block_are_filtered_in_order() -> None:
    done = run_child(
        f"""
        import sys
        from pcbkit.kicad import quiet

        print("before", file=sys.stderr)
        with quiet.quiet_stderr():
            print({MAC.decode()!r}, file=sys.stderr)
            print("a python error", file=sys.stderr)
        print("after", file=sys.stderr)
        """
    )
    assert done.returncode == 0
    assert done.stderr == "before\na python error\nafter\n"


# --- wrapping calls ------------------------------------------------------------------


def test_quieted_passes_arguments_results_and_exceptions_through() -> None:
    seen: list[Any] = []

    def load(path: str, *, flag: bool = False) -> Any:
        """Load something."""
        seen.append((path, flag))
        return None if path == "missing" else ("board", path)

    wrapped = quiet.quieted(load)
    assert wrapped("a.kicad_pcb", flag=True) == ("board", "a.kicad_pcb")
    assert wrapped("missing") is None  # an unreadable board is None, and stays None
    assert seen == [("a.kicad_pcb", True), ("missing", False)]
    assert (wrapped.__name__, wrapped.__doc__) == ("load", "Load something.")
    with pytest.raises(ZeroDivisionError):
        quiet.quieted(lambda: 1 / 0)()


def stand_in_pcbnew() -> types.ModuleType:
    """Return a pcbnew whose calls write the noise that the real ones write."""
    module = types.ModuleType("pcbnew")

    def LoadBoard(path: str) -> Any:  # noqa: N802
        write(MAC, ASSERT, f"loading {path}".encode())
        return None if path == "missing" else ("board", path)

    def NewBoard(path: str) -> Any:  # noqa: N802
        write(ASSERT, f"new {path}".encode())
        return ("new", path)

    def SaveBoard(path: str, board: Any) -> bool:  # noqa: N802
        write(MAC, f"saving {path}".encode())
        return True

    module.LoadBoard = LoadBoard  # type: ignore[attr-defined]
    module.NewBoard = NewBoard  # type: ignore[attr-defined]
    module.SaveBoard = SaveBoard  # type: ignore[attr-defined]
    return module


def test_quiet_pcbnew_wraps_the_two_calls_that_print_noise(
    capfd: pytest.CaptureFixture[str],
) -> None:
    module = stand_in_pcbnew()
    save = module.SaveBoard  # type: ignore[attr-defined]
    assert quiet.quiet_pcbnew(module) is module
    assert module.LoadBoard("a") == ("board", "a")  # type: ignore[attr-defined]
    assert module.LoadBoard("missing") is None  # type: ignore[attr-defined]
    assert module.NewBoard("b") == ("new", "b")  # type: ignore[attr-defined]
    assert capfd.readouterr().err == "loading a\nloading missing\nnew b\n"
    # Only what was measured to print noise is wrapped; the rest is the module's own.
    assert module.SaveBoard is save  # type: ignore[attr-defined]
    module.SaveBoard("c", None)  # type: ignore[attr-defined]
    assert capfd.readouterr().err == MAC.decode() + "\nsaving c\n"


def test_quiet_pcbnew_is_idempotent(
    capfd: pytest.CaptureFixture[str], recorder: Recorder
) -> None:
    module = stand_in_pcbnew()
    original = module.LoadBoard  # type: ignore[attr-defined]
    quiet.quiet_pcbnew(module)
    once = module.LoadBoard  # type: ignore[attr-defined]
    quiet.quiet_pcbnew(module)
    assert module.LoadBoard is once  # type: ignore[attr-defined]
    assert once.__wrapped__ is original
    module.LoadBoard("a")  # type: ignore[attr-defined]
    assert len(recorder.children) == 1  # one filter, not one per layer of wrapping
    assert capfd.readouterr().err == "loading a\n"


def test_quiet_pcbnew_skips_a_call_the_module_lacks() -> None:
    empty = types.ModuleType("pcbnew")
    assert quiet.quiet_pcbnew(empty) is empty
    assert not hasattr(empty, "LoadBoard")


# --- who applies it ------------------------------------------------------------------


@pytest.fixture
def pcbnew_stand_in(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Put the noisy stand-in where ``import pcbnew`` finds it."""
    module = stand_in_pcbnew()
    monkeypatch.setitem(sys.modules, "pcbnew", module)
    return module


def test_import_pcbnew_returns_the_module_with_its_calls_quieted(
    capfd: pytest.CaptureFixture[str], pcbnew_stand_in: types.ModuleType
) -> None:
    assert env.import_pcbnew() is pcbnew_stand_in
    pcbnew_stand_in.LoadBoard("a")  # type: ignore[attr-defined]
    assert capfd.readouterr().err == "loading a\n"


def test_import_pcbnew_raises_import_error_when_there_is_no_pcbnew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    with pytest.raises(ImportError):
        env.import_pcbnew()


def test_require_pcbnew_quiets_the_calls_for_the_rest_of_the_process(
    capfd: pytest.CaptureFixture[str], pcbnew_stand_in: types.ModuleType
) -> None:
    env.require_pcbnew()
    pcbnew_stand_in.NewBoard("a")  # type: ignore[attr-defined]
    assert capfd.readouterr().err == "new a\n"


def test_the_board_helpers_get_pcbnew_with_its_calls_quieted(
    capfd: pytest.CaptureFixture[str], fake_pcbnew: types.ModuleType
) -> None:
    """Quiet pcbnew the first time any helper in pcbkit.kicad.board asks for it."""
    loud = stand_in_pcbnew()
    for name in ("LoadBoard", "NewBoard"):
        setattr(fake_pcbnew, name, getattr(loud, name))
    assert kb.mm(1.0) == 1_000_000
    assert kb.mm(2.0) == 2_000_000  # a second call must not wrap again
    fake_pcbnew.LoadBoard("a")  # type: ignore[attr-defined]
    assert capfd.readouterr().err == "loading a\n"
    assert fake_pcbnew.LoadBoard.__wrapped__ is loud.LoadBoard  # type: ignore[attr-defined]


# --- kicad-cli output ----------------------------------------------------------------


def test_a_failed_kicad_cli_run_shows_the_error_and_not_the_noise(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep wx noise from filling the tail and pushing the real error out of it."""
    kicad = str(
        machine.exe(machine.usr_bin / "kicad-cli", "10.0.6", on_path="kicad-cli")
    )
    error = "Failed to load board: Expecting '(' in 'b.kicad_pcb', line 1, offset 1."
    noise = [ASSERT.decode()] + [handler("11:44:35", n).decode() for n in FORMATS]
    output = (
        "\n".join([error, *noise]) + "\n"
    )  # more noise after it than the tail holds

    def run(args: list[str], timeout: float = 20.0) -> Run:
        if args == [kicad, "--version"]:
            return Run(0, "10.0.6\n")
        return Run(3, output)

    monkeypatch.setattr(env, "_run", run)
    with pytest.raises(cli.KicadCliError) as caught:
        cli.drc(tmp_path / "b.kicad_pcb", tmp_path / "drc.rpt")
    assert caught.value.message == f"kicad-cli pcb drc failed (exit 3)\n{error}"
    assert caught.value.output == output  # the whole output stays available


@pytest.mark.parametrize("lines", [1, 2, 6])
def test_the_tail_counts_only_lines_that_are_not_noise(lines: int) -> None:
    real = [f"real {n}" for n in range(8)]
    mixed: list[str] = []
    for line in real:
        mixed += [line, MAC.decode(), ASSERT.decode()]
    expected: Callable[[int], str] = lambda n: "\n".join(real[-n:])  # noqa: E731
    assert cli._tail("\n".join(mixed), lines) == expected(lines)
