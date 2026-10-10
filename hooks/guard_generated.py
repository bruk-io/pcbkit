#!/usr/bin/env python3
"""Keep Claude's Edit and Write tools out of the folders pcbkit generates.

A pcbkit board project keeps its source in Python and ``pcbkit.toml``. The folders
``kicad/``, ``out/``, ``golden/`` and ``fab/`` are written by pcbkit, so a change made
there by hand is overwritten by the next build. This is a Claude Code PreToolUse hook
for the Edit and Write tools (see hooks/hooks.json). It reads the hook event from stdin
and, when ``tool_input.file_path`` lies under one of those folders of a directory that
holds a ``pcbkit.toml``, exits 2 with the reason on stderr: Claude Code then blocks the
tool call and shows Claude the reason. Everything else is allowed.

It also blocks any edit of a datasheet record (``<part>.datasheet.json``), in a project
or not: ``pcbkit datasheet find`` writes those and ``confirm`` is the only way to
change one, because it checks the evidence first.

It only sees the Edit and Write tools. A change made through Bash (a heredoc,
``sed -i``) never reaches it.

Exit codes: 0 allow, 2 block, 1 the event could not be read. Exit 1 is a non-blocking
error: the tool call goes ahead and Claude Code reports the hook error, so a guard that
has stopped working is noticed instead of silently off.

Standard library only: it runs under whichever ``python3`` is on PATH, without pcbkit
installed.
"""

from __future__ import annotations

import json
import os
import sys

# The folders of a project that pcbkit regenerates.
GENERATED = ("kicad", "out", "golden", "fab")
# The file that makes a folder a pcbkit project.
MARKER = "pcbkit.toml"
# The tools the hook is registered for.
TOOLS = ("Edit", "Write")
# The end of a datasheet record's name (pcbkit.datasheet_find.RECORD_SUFFIX).
RECORD_SUFFIX = ".datasheet.json"

ALLOW = 0
UNREADABLE = 1
BLOCK = 2


def project_root(path: str) -> str | None:
    """Return the nearest folder at or above ``path`` that holds a pcbkit.toml."""
    folder = os.path.dirname(path)
    while True:
        if os.path.isfile(os.path.join(folder, MARKER)):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def generated_folder(path: str) -> tuple[str, str] | None:
    """Return the project root and generated folder that ``path`` lies in, or None.

    ``path`` is absolute and normalised. Names are compared without regard to case,
    because the default macOS file system does not tell ``Kicad/`` from ``kicad/``.
    """
    root = project_root(path)
    if root is None:
        return None
    parts = os.path.relpath(path, root).split(os.sep)
    first = parts[0].casefold()
    if len(parts) > 1 and first in GENERATED:
        return root, first
    return None


def blocked_by(file_path: str, cwd: str) -> tuple[str, str] | None:
    """Return the project root and generated folder an edit of ``file_path`` hits.

    A relative path is taken from ``cwd``, ``..`` is resolved, and so is every symbolic
    link: an edit through a link into ``kicad/`` is an edit of ``kicad/``. The path as
    written is checked as well, so a link out of a generated folder does not hide it.
    """
    written = os.path.normpath(os.path.join(cwd, os.path.expanduser(file_path)))
    forms = [written]
    resolved = os.path.realpath(written)
    if resolved != written:
        forms.append(resolved)
    for form in forms:
        found = generated_folder(form)
        if found is not None:
            return found
    return None


def is_record(file_path: str, cwd: str) -> bool:
    """Say whether ``file_path``, as written or through a link, is a record."""
    written = os.path.normpath(os.path.join(cwd, os.path.expanduser(file_path)))
    names = {os.path.basename(written), os.path.basename(os.path.realpath(written))}
    return any(name.casefold().endswith(RECORD_SUFFIX) for name in names)


def record_reason(file_path: str) -> str:
    """Return what Claude is told when an edit of a datasheet record is blocked."""
    return (
        f"Blocked: {file_path} is a datasheet record, which only pcbkit writes. Run "
        "`pcbkit datasheet find <part>` to look again, or `pcbkit datasheet confirm "
        "<part> --page <n> --quote '<text>'` to confirm a candidate: confirm checks "
        "the quote against the PDF, an edit would not."
    )


def reason(file_path: str, folder: str) -> str:
    """Return what Claude is told when an edit of ``file_path`` is blocked."""
    return (
        f"Blocked: {file_path} is in {folder}/, which pcbkit generates, so an edit "
        "there is lost on the next build. Change the project's Python or pcbkit.toml "
        "instead (design.py for parts and nets, layout.py for positions and the "
        "outline, routing.py for routing, silk.py for labels), then rerun pcbkit "
        "build, route and finalize as needed."
    )


def decide(event: object, fallback_cwd: str) -> tuple[int, str]:
    """Return the exit code and the stderr message for one hook event."""
    if not isinstance(event, dict):
        return UNREADABLE, "guard_generated.py: the hook event is not a JSON object"
    tool_input = event.get("tool_input")
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str) or not path:
        if event.get("tool_name") in TOOLS:
            return UNREADABLE, (
                f"guard_generated.py: no tool_input.file_path in this "
                f"{event.get('tool_name')} event, so it was not checked"
            )
        return ALLOW, ""
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        cwd = fallback_cwd
    if is_record(path, cwd):
        return BLOCK, record_reason(path)
    found = blocked_by(path, cwd)
    if found is None:
        return ALLOW, ""
    return BLOCK, reason(path, found[1])


def say(message: str) -> None:
    """Write ``message`` and a newline to stderr, whatever its encoding is."""
    sys.stderr.buffer.write((message + "\n").encode("utf-8", "replace"))
    sys.stderr.flush()


def main() -> int:
    """Read one hook event from stdin; return the exit code for it."""
    if sys.stdin.isatty():
        say("guard_generated.py: a Claude Code hook; it reads its event from stdin")
        return UNREADABLE
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError as error:
        say(f"guard_generated.py: could not read the hook event as JSON: {error}")
        return UNREADABLE
    code, message = decide(event, os.getcwd())
    if message:
        say(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
