#!/usr/bin/env python3
"""Keep the check-writer agent's Edit and Write tools inside the project's checks/.

The check-writer agent (agents/check-writer.md) writes one check under ``checks/`` and
shows it failing in a scratch copy of the project. A plugin agent cannot carry hooks of
its own, but this plugin-level PreToolUse hook (see hooks/hooks.json) sees every Edit
and Write, and Claude Code adds ``agent_type`` to the event when a subagent makes the
call: ``pcbkit:check-writer`` for this one. When it is check-writer, and the file lies
in the project the session works in but not under ``checks/``, the hook exits 2 with the
reason on stderr, and Claude Code blocks the tool call.

Everything else is allowed: any other agent, the main session, a scratch copy (a
different project, outside this one) and files that belong to no project. The project is
the nearest folder at or above the event's ``cwd`` that holds a ``pcbkit.toml``.

It only sees the Edit and Write tools: a change made through Bash never reaches it.

Exit codes: 0 allow, 2 block, 1 the event could not be read (a non-blocking error that
Claude Code reports, so a guard that has stopped working is noticed).

Standard library only: it runs under whichever ``python3`` is on PATH, without pcbkit
installed.
"""

from __future__ import annotations

import json
import os
import sys

# The agent this hook confines, and the one folder of the project it may write in.
AGENT = "check-writer"
FOLDER = "checks"
# The file that makes a folder a pcbkit project.
MARKER = "pcbkit.toml"
# The tools the hook is registered for.
TOOLS = ("Edit", "Write")

ALLOW = 0
UNREADABLE = 1
BLOCK = 2


def is_check_writer(agent_type: object) -> bool:
    """Return True if ``agent_type`` names the check-writer agent, namespaced or not."""
    return isinstance(agent_type, str) and agent_type.rsplit(":", 1)[-1] == AGENT


def project_root(folder: str) -> str | None:
    """Return the nearest folder at or above ``folder`` that holds a pcbkit.toml."""
    while True:
        if os.path.isfile(os.path.join(folder, MARKER)):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def strays(path: str, root: str) -> bool:
    """Return True if ``path`` is inside ``root`` but not under its checks/ folder.

    Names are compared without regard to case, because the default macOS file system
    does not tell ``Checks/`` from ``checks/``.
    """
    relative = os.path.relpath(path, root)
    parts = relative.split(os.sep)
    if parts[0] in (os.curdir, os.pardir):
        return False  # the root itself, or somewhere else altogether
    return len(parts) == 1 or parts[0].casefold() != FOLDER


def blocked_path(file_path: str, cwd: str) -> bool:
    """Return True if check-writer may not write ``file_path``.

    A relative path is taken from ``cwd`` and ``..`` is resolved. Symbolic links are
    followed as well as the path as written: a link inside checks/ that leads to
    design.py is a write to design.py.
    """
    root = project_root(cwd)
    if root is None:
        return False
    written = os.path.normpath(os.path.join(cwd, os.path.expanduser(file_path)))
    if strays(written, os.path.normpath(root)):
        return True
    resolved = os.path.realpath(written)
    return resolved != written and strays(resolved, os.path.realpath(root))


def reason(file_path: str) -> str:
    """Return what the agent is told when a write of ``file_path`` is blocked."""
    return (
        f"Blocked: {file_path} is outside checks/. The check-writer agent may "
        "write only under the project's checks/ folder. Keep a limit and its source "
        "at the top of the check module, work on a scratch copy of the project "
        "outside the project folder, and return anything else, such as a MUTANTS "
        "entry, to the caller."
    )


def decide(event: object, fallback_cwd: str) -> tuple[int, str]:
    """Return the exit code and the stderr message for one hook event."""
    if not isinstance(event, dict):
        return (
            UNREADABLE,
            "confine_check_writer.py: the hook event is not a JSON object",
        )
    if not is_check_writer(event.get("agent_type")):
        return ALLOW, ""
    tool_input = event.get("tool_input")
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str) or not path:
        if event.get("tool_name") in TOOLS:
            return UNREADABLE, (
                f"confine_check_writer.py: no tool_input.file_path in this "
                f"{event.get('tool_name')} event, so it was not checked"
            )
        return ALLOW, ""
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        cwd = fallback_cwd
    if blocked_path(path, cwd):
        return BLOCK, reason(path)
    return ALLOW, ""


def say(message: str) -> None:
    """Write ``message`` and a newline to stderr, whatever its encoding is."""
    sys.stderr.buffer.write((message + "\n").encode("utf-8", "replace"))
    sys.stderr.flush()


def main() -> int:
    """Read one hook event from stdin; return the exit code for it."""
    if sys.stdin.isatty():
        say(
            "confine_check_writer.py: a Claude Code hook; it reads its event from stdin"
        )
        return UNREADABLE
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError as error:
        say(f"confine_check_writer.py: could not read the hook event as JSON: {error}")
        return UNREADABLE
    code, message = decide(event, os.getcwd())
    if message:
        say(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
