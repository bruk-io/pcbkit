#!/usr/bin/env python3
"""Keep the check-writer agent's Edit and Write tools inside the project's checks/.

The check-writer agent (agents/check-writer.md) writes one check under ``checks/`` and
shows it failing in a scratch copy of the project. A plugin agent cannot carry hooks of
its own, but this plugin-level PreToolUse hook (see hooks/hooks.json) sees every Edit
and Write, and Claude Code adds ``agent_type`` to the event when a subagent makes the
call: ``pcbkit:check-writer`` for this one. When it is check-writer and the file lies in
one of the session's projects but not under that project's ``checks/``, the hook exits 2
with the reason on stderr, and Claude Code blocks the tool call.

Which project is "the session's" does not depend on where the agent happens to be
working: it is a project (a folder holding a ``pcbkit.toml``) that contains, or lies
inside, ``$CLAUDE_PROJECT_DIR``, the folder the session was started for (the event's
``cwd`` stands in when the variable is missing). A scratch copy made elsewhere is
another project and may be edited freely, and so may any other agent, the main session
and files that belong to no project.

Folders are compared by what they are, not by what they are called. The names are tried
first; when they differ, the file system is asked (device and inode, links followed), so
``REAL/my-board`` is ``real/my-board`` on a case-insensitive file system, which is the
macOS default, and a link is the folder it leads to. A folder that does not exist, or
cannot be asked about, counts by its name alone. The same goes for ``checks/``: a
spelling that only differs in capitals is that folder where the file system says so, and
a project with no ``checks/`` yet starts one under that exact name.

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

# The agent this hook confines, and the one folder of a project it may write in.
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


def same_folder(first: str, second: str) -> bool:
    """Return True if the file system says ``first`` and ``second`` are one folder.

    On a case-insensitive file system ``REAL/`` and ``real/`` are one folder, and a link
    is the folder it leads to. A folder that does not exist, or that the file system
    will not answer for, is the same as no folder.
    """
    try:
        return os.path.samefile(first, second)
    except (OSError, ValueError):
        return False


def inside_by_name(path: str, base: str) -> bool:
    """Return True if ``path`` is ``base`` itself or lies below it, by their names."""
    relative = os.path.relpath(path, base)
    return relative != os.pardir and not relative.startswith(os.pardir + os.sep)


def inside(path: str, base: str) -> bool:
    """Return True if ``path`` is ``base`` itself or lies below it.

    The names are compared first. When they do not say so, ``path`` and each folder
    above it are compared with ``base`` by what they are (see ``same_folder``): another
    spelling of ``base``, such as other capitals on a case-insensitive file system, or
    a link to it, is still ``base``. Where the file system has nothing to say, the
    names have had the last word already.
    """
    if inside_by_name(path, base):
        return True
    folder = path
    while True:
        if same_folder(folder, base):
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def strays(path: str, root: str) -> bool:
    """Return True if ``path``, inside project ``root``, is not under its checks/.

    The folder is called ``checks``. A name that only differs from it in capitals is
    that folder where the file system says so, as the default macOS one does for
    ``Checks/``; a case-sensitive one does not, and there ``Checks/`` is another folder.
    With no checks/ in the project to compare with, only the exact name starts one. Any
    other name, a link to checks/ included, is outside it.
    """
    parts = os.path.relpath(path, root).split(os.sep)
    if len(parts) == 1:
        return True
    first = parts[0]
    if first == FOLDER:
        return False
    if first.casefold() != FOLDER:
        return True
    return not same_folder(os.path.join(root, first), os.path.join(root, FOLDER))


def confined(path: str, session: str) -> bool:
    """Return True if ``path`` is in a project of the session, outside its checks/."""
    root = project_root(os.path.dirname(path))
    if root is None:
        return False  # it belongs to no project
    if not (inside(root, session) or inside(session, root)):
        return False  # another project altogether: a scratch copy
    return strays(path, root)


def blocked_path(file_path: str, cwd: str, session: str) -> bool:
    """Return True if check-writer may not write ``file_path``.

    A relative path is taken from ``cwd`` and ``..`` is resolved. Symbolic links are
    followed as well as the path as written: a link inside checks/ that leads to
    design.py is a write to design.py. The followed path is always judged against the
    session folder with its links followed too, even when the path has no link of its
    own: the session may have been started in a link that leads into the middle of the
    project (a shortcut to checks/), and only the folder it leads to has the project
    above it. Which folders are one is `inside`'s business, whatever they are called.
    """
    written = os.path.normpath(os.path.join(cwd, os.path.expanduser(file_path)))
    if confined(written, os.path.normpath(session)):
        return True
    return confined(os.path.realpath(written), os.path.realpath(session))


def session_folder(event: dict, fallback_cwd: str) -> str:
    """Return the folder the session was started for: the project dir, else the cwd."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir and os.path.isabs(project_dir):
        return project_dir
    cwd = event.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isabs(cwd) else fallback_cwd


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
    if blocked_path(path, cwd, session_folder(event, fallback_cwd)):
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
