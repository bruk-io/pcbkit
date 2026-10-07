"""Hide the wxWidgets noise that pcbnew writes to standard error.

On macOS, KiCad's pcbnew module prints lines that look like errors and are not. Measured
on KiCad 10.0.6 (macOS), they come from two calls and nowhere else:

* The first board a process makes or loads (``NewBoard`` or ``LoadBoard``) prints one
  assertion line, once per process::

      ./src/common/stdpbase.cpp(59): assert ""traits"" failed in Get(): create wxApp ...

* Every ``LoadBoard`` after the first prints twelve lines, one per image format, even
  for a file that does not exist::

      11:44:35 PM: Debug: Adding duplicate image handler for 'PNG file'

  (The space before PM is U+202F, a narrow no-break space, not an ASCII space.)

``import pcbnew``, ``SaveBoard``, ``FootprintLoad``, the Specctra import and export, the
zone filler and every kicad-cli subcommand were measured too, and print nothing. A
``pcbkit finalize`` loads boards four or five times, which is where its 49 noise lines
came from.

The lines are written by C++ straight to file descriptor 2, so ``sys.stderr`` cannot
catch them. ``quiet_stderr`` points descriptor 2 at a pipe for the length of one call.
A small filter process reads the pipe and writes every line that is not one of the
known patterns to the real standard error. It is a process, and not a thread or a
temporary file, so that whatever pcbnew prints just before it aborts still arrives:
a thread or a file dies with the process that owns it, and the filter outlives it and
drains the pipe.

Only ``LoadBoard`` and ``NewBoard`` are wrapped (``quiet_pcbnew``), only for the length
of the call, and only the patterns below are dropped: any other line, from pcbnew or
from Python, passes through untouched. A pattern that does not match shows the line, so
a build of wxWidgets that words the noise differently is noisy again, never silent about
a real error. If the filter cannot be started the call runs unfiltered.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, Any, TypeVar, cast

# One entry per kind of noise: a regular expression on a line's bytes, without its line
# ending. Bytes, because the time stamp is not ASCII and a line need not be UTF-8.
NOISE_PATTERNS: tuple[bytes, ...] = (
    # wxStandardPaths::Get() finding no wxApp. The path before the file name and the
    # line number change with the wxWidgets build; the function and the message do not.
    rb'^\S*stdpbase\.cpp\(\d+\): assert "+traits"+ failed in Get\(\): '
    rb"create wxApp before calling this$",
    # wxImage::AddHandler, once per image format. The message is exact. The time stamp
    # before it follows the locale (%X: "11:44:35 PM", "23:44:35", "23時44分35秒"),
    # so it is only bounded: at most 40 bytes, ending in ": ", with a digit before its
    # first colon.
    rb"^(?:(?=[^\r\n:]*\d)[^\r\n]{1,40}: )?"
    rb"Debug: Adding duplicate image handler for '[^'\r\n]*'$",
)

# The calls of the pcbnew module that print noise. Wrapped in place by quiet_pcbnew.
NOISY_CALLS = ("LoadBoard", "NewBoard")

# Seconds to wait for the filter to finish after the call. It ends when its input does,
# so this is a ceiling for something gone wrong, such as a child of the call that
# inherited the pipe and is still running.
FILTER_WAIT_S = 10.0

# What the filter process runs: python -I -S -c <this>. It reads the pipe, drops the
# noise, and writes the rest to the saved real standard error. A line is written whole
# and flushed at once, so a message printed just before a crash is not held back. If
# the real standard error closes it keeps reading and discards, so the process that
# writes into the pipe never sees a broken pipe.
_FILTER_SOURCE = """\
import re, sys
noise = [re.compile(p) for p in %r]
out = sys.stdout.buffer
for raw in sys.stdin.buffer:
    line = raw.rstrip(b"\\r\\n")
    if any(p.match(line) for p in noise):
        continue
    if out is not None:
        try:
            out.write(raw)
            out.flush()
        except OSError:
            out = None
"""

_MARK = "_pcbkit_quiet"
_NOISE = tuple(re.compile(pattern) for pattern in NOISE_PATTERNS)

F = TypeVar("F", bound=Callable[..., Any])

# True while a quiet section is open: a section opened inside it just runs its body.
_in_section = False


# --- what is noise ----------------------------------------------------------------


def is_noise(line: bytes) -> bool:
    """Return True if ``line`` (with or without its line ending) is known wx noise."""
    text = line.rstrip(b"\r\n")
    return any(pattern.match(text) for pattern in _NOISE)


def strip_noise(text: str) -> str:
    """Return ``text`` without its noise lines; every other line is kept as it is."""
    kept = [
        line
        for line in text.splitlines(keepends=True)
        if not is_noise(line.encode("utf-8", "replace"))
    ]
    return "".join(kept)


# --- hiding it --------------------------------------------------------------------


def _flush_stderr() -> None:
    """Flush Python's own buffers of standard error, whatever they are wrapped in."""
    for stream in (sys.stderr, sys.__stderr__):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):  # no stream, or a closed one
            pass


@dataclass(frozen=True)
class _Section:
    """What `_end` needs to put things back: the real standard error and the filter."""

    saved: int  # a copy of the real descriptor 2
    child: subprocess.Popen[bytes]  # the filter process
    pipe: IO[bytes]  # its input, which descriptor 2 points at meanwhile


def _begin() -> _Section | None:
    """Point descriptor 2 at a filter process; return None if that cannot be done."""
    if not sys.executable:
        return None
    try:
        saved = os.dup(2)
    except OSError:  # no standard error at all: nothing to filter
        return None
    _flush_stderr()
    try:
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _FILTER_SOURCE % (NOISE_PATTERNS,)],
            stdin=subprocess.PIPE,
            stdout=saved,
            # Its own session: Ctrl-C must reach pcbkit and not the filter, which then
            # sees the end of its input and finishes what it was sent.
            start_new_session=True,
        )
    except (OSError, ValueError):  # no interpreter to run: show the noise
        os.close(saved)
        return None
    pipe = cast("IO[bytes]", child.stdin)  # a PIPE was asked for, so it is there
    try:
        os.dup2(pipe.fileno(), 2)
    except OSError:
        pipe.close()
        child.wait()
        os.close(saved)
        return None
    return _Section(saved, child, pipe)


def _end(section: _Section) -> None:
    """Put the real standard error back and wait for the filter to finish."""
    _flush_stderr()
    try:
        os.dup2(section.saved, 2)
    finally:
        os.close(section.saved)
        try:
            section.pipe.close()
        except OSError:
            pass
        try:
            section.child.wait(timeout=FILTER_WAIT_S)
        except subprocess.TimeoutExpired:
            section.child.kill()
            section.child.wait()


@contextmanager
def quiet_stderr() -> Iterator[None]:
    """Hide the known wx noise written to descriptor 2 while the body runs.

    Every other line is written to the real standard error, in order, before the block
    returns, and an exception in the body goes on to propagate once standard error has
    been put back. Python-level writes to ``sys.stderr`` inside the block are filtered
    too, since they reach descriptor 2 like everything else. A block opened inside
    another one runs without a second filter.
    """
    global _in_section
    section = None if _in_section else _begin()
    if section is None:
        yield
        return
    _in_section = True
    try:
        yield
    finally:
        _in_section = False
        _end(section)


def quieted(call: F) -> F:
    """Return ``call`` run inside `quiet_stderr`, its arguments and result unchanged."""

    @functools.wraps(call)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with quiet_stderr():
            return call(*args, **kwargs)

    setattr(wrapper, _MARK, True)
    return cast(F, wrapper)


def quiet_pcbnew(module: Any) -> Any:
    """Quiet the noisy calls of a pcbnew module in place, once, and return the module.

    Every pcbkit module calls ``pcbnew.LoadBoard`` through the module at the time it
    needs it, so wrapping the attribute covers all of them without touching them. A
    call the module lacks is skipped, and one that is already wrapped is left alone.
    """
    for name in NOISY_CALLS:
        call = getattr(module, name, None)
        if callable(call) and not getattr(call, _MARK, False):
            setattr(module, name, quieted(call))
    return module
