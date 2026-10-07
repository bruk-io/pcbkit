"""Unit tests for hooks/confine_check_writer.py, the guard on the check-writer agent.

Like the guard on generated files, it is a program Claude Code runs before every Edit
and Write, fed one JSON event on stdin; the tests run it that way on a throwaway
project. What it adds is the agent: an event carries ``agent_type`` when a subagent
makes the call, and for the check-writer agent every write inside the project must land
under ``checks/``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import pty
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "confine_check_writer.py"

# How Claude Code names a plugin's agent in `agent_type`, and the bare name.
NAMESPACED = "pcbkit:check-writer"
BARE = "check-writer"


def run_hook(
    event: object | bytes,
    cwd: Path,
    session: Path | str | None = None,
    home: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run the hook on one event (JSON-encoded unless given as bytes).

    ``session`` is what Claude Code puts in $CLAUDE_PROJECT_DIR for a hook; without it
    the variable is removed, so a test never sees the one of the session it runs in.
    ``home`` is the child's $HOME, which a path that starts with ``~`` is read against;
    without it the child gets the unit test's fake home.
    """
    data = event if isinstance(event, bytes) else json.dumps(event).encode("utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if session is not None:
        env["CLAUDE_PROJECT_DIR"] = str(session)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=data,
        capture_output=True,
        cwd=cwd,
        env=env,
        timeout=60,
    )


def write(
    path: str | Path,
    agent: object = NAMESPACED,
    cwd: Path | None = None,
    tool: str = "Write",
) -> dict:
    """Return the PreToolUse event for a tool call on ``path`` made by ``agent``."""
    event: dict = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(path), "content": "x"},
    }
    if agent is not None:
        event["agent_id"] = "a123"
        event["agent_type"] = agent
    if cwd is not None:
        event["cwd"] = str(cwd)
    return event


def stderr(done: subprocess.CompletedProcess[bytes]) -> str:
    """Return what the hook printed on stderr."""
    return done.stderr.decode("utf-8")


def one_folder(first: Path, second: Path) -> bool:
    """Return True if the file system says ``first`` and ``second`` are one folder.

    The tests below that spell a folder with other capitals take what the hook must do
    from this, so they are right on whichever file system they run on. On a
    case-insensitive one (the macOS default) ``REAL/`` is ``real/`` and the hook has to
    treat it so. On a case-sensitive one ``REAL/`` is no folder at all, so this is False
    and a path under it belongs to no project.
    """
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def load_hook() -> ModuleType:
    """Return the hook script as a module, to call one of its functions directly."""
    spec = importlib.util.spec_from_file_location("confine_check_writer", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a board project: pcbkit.toml, checks/, and the usual source files."""
    root = tmp_path / "my-board"
    for name in ("checks/sub", "kicad", "docs"):
        (root / name).mkdir(parents=True)
    for name in ("pcbkit.toml", "design.py", "specs.py", "mutants.py"):
        (root / name).write_text("x = 1\n", encoding="utf-8")
    return root


# --- inside checks/ -------------------------------------------------------------------


@pytest.mark.parametrize("agent", [NAMESPACED, BARE])
@pytest.mark.parametrize(
    "relative",
    ["checks/test_led.py", "checks/sub/helper.py", "checks/new/deeper/test_x.py"],
)
def test_the_agent_may_write_under_checks(
    project: Path, agent: str, relative: str
) -> None:
    done = run_hook(write(project / relative, agent, project), project)
    assert done.returncode == 0, stderr(done)
    assert done.stderr == b""


@pytest.mark.parametrize("spelled", ["Checks", "CHECKS", "cHeCkS"])
def test_checks_with_other_capitals_is_checks_only_where_the_file_system_says_so(
    project: Path, spelled: str
) -> None:
    """The default macOS file system treats Checks/ and checks/ as one folder.

    A case-sensitive one does not: there Checks/ is another folder (here one that does
    not even exist), so a write to it is a write outside checks/. The file system is
    asked, not assumed, so the test is honest on both.
    """
    same = one_folder(project / spelled, project / "checks")
    done = run_hook(write(project / spelled / "test_x.py", cwd=project), project.parent)
    assert done.returncode == (0 if same else 2), (same, stderr(done))


def test_a_project_with_no_checks_folder_yet_starts_one_under_its_exact_name(
    tmp_path: Path,
) -> None:
    """With no checks/ for the file system to compare it with, Checks/ is not it."""
    root = tmp_path / "my-board"
    root.mkdir()
    (root / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    exact = run_hook(write(root / "checks" / "test_x.py", cwd=root), tmp_path)
    capitals = run_hook(write(root / "Checks" / "test_x.py", cwd=root), tmp_path)
    assert (exact.returncode, capitals.returncode) == (0, 2), stderr(capitals)


@pytest.mark.parametrize("name", ["chk", "checks_old", "checks2"])
def test_a_link_to_checks_under_another_name_is_not_checks(
    project: Path, name: str
) -> None:
    """Only a spelling that differs in capitals is compared by what it is.

    A name that merely starts with ``checks`` is another name.
    """
    (project / name).symlink_to("checks", target_is_directory=True)
    done = run_hook(write(project / name / "test_x.py", cwd=project), project)
    assert done.returncode == 2, stderr(done)


# --- outside checks/, in the project --------------------------------------------------


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("agent", [NAMESPACED, BARE])
@pytest.mark.parametrize(
    "relative",
    [
        "design.py",
        "specs.py",
        "mutants.py",
        "pcbkit.toml",
        "layout.py",
        "docs/notes.md",
        "new_folder/x.py",
        "checks_old/test_x.py",
        "checks",
    ],
)
def test_the_agent_may_not_write_elsewhere_in_the_project(
    project: Path, tool: str, agent: str, relative: str
) -> None:
    done = run_hook(write(project / relative, agent, project, tool), project)
    assert done.returncode == 2, stderr(done)
    assert done.stdout == b""
    assert "outside checks/" in stderr(done)
    assert str(project / relative) in stderr(done)


def test_a_path_that_utf8_cannot_encode_is_still_blocked_and_reported(
    project: Path,
) -> None:
    """JSON can carry a lone surrogate; printing the reason must not crash on it.

    A crash is exit 1, which lets the write through.
    """
    done = run_hook(write(project / "design\ud800.py", cwd=project), project)
    assert done.returncode == 2, stderr(done)
    assert "outside checks/" in stderr(done)


def test_the_block_says_what_to_do_instead(project: Path) -> None:
    done = run_hook(write(project / "design.py", cwd=project), project)
    message = stderr(done)
    assert "scratch copy" in message
    assert "MUTANTS" in message


def test_a_relative_path_is_taken_from_the_events_cwd(project: Path) -> None:
    elsewhere = project.parent
    blocked = run_hook(write("design.py", cwd=project), elsewhere)
    allowed = run_hook(write("checks/test_x.py", cwd=project), elsewhere)
    assert (blocked.returncode, allowed.returncode) == (2, 0)


def test_a_relative_path_without_a_cwd_uses_the_process_cwd(project: Path) -> None:
    assert run_hook(write("design.py"), project).returncode == 2
    assert run_hook(write("checks/test_x.py"), project).returncode == 0


@pytest.mark.parametrize("with_session", [False, True], ids=["no-session", "session"])
@pytest.mark.parametrize(
    "cwd",
    ["checks", "", 7, None, ["x"]],
    ids=["relative", "empty", "number", "null", "list"],
)
def test_an_event_cwd_that_is_no_absolute_path_is_ignored(
    project: Path, cwd: object, with_session: bool
) -> None:
    """The process's own folder stands in, as when the event has no cwd at all.

    A relative cwd that was trusted would turn design.py into checks/design.py, which
    is allowed, and let the write through.
    """
    event = write("design.py")
    event["cwd"] = cwd
    done = run_hook(event, project, session=project if with_session else None)
    assert done.returncode == 2, stderr(done)


def test_without_a_session_variable_the_events_cwd_is_the_session_folder(
    project: Path, tmp_path: Path
) -> None:
    """Not the folder the hook happens to run in: that is somewhere else entirely."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    done = run_hook(write(project / "design.py", cwd=project), elsewhere)
    assert done.returncode == 2, stderr(done)


def test_a_path_that_starts_with_a_tilde_is_read_against_the_home_folder(
    tmp_path: Path,
) -> None:
    """~/my-board/design.py is in the session's project when $HOME holds my-board.

    The event's cwd is outside every project, so a ``~`` taken as a plain folder name
    (``<cwd>/~/my-board/...``) would find no project and let the write through.
    """
    home = tmp_path / "home"
    board = home / "my-board"
    (board / "checks").mkdir(parents=True)
    (board / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    blocked = run_hook(
        write("~/my-board/design.py", cwd=elsewhere),
        elsewhere,
        session=board,
        home=home,
    )
    allowed = run_hook(
        write("~/my-board/checks/test_x.py", cwd=elsewhere),
        elsewhere,
        session=board,
        home=home,
    )
    assert blocked.returncode == 2, stderr(blocked)
    assert "~/my-board/design.py" in stderr(blocked)
    assert allowed.returncode == 0, stderr(allowed)


def test_dotdot_segments_are_resolved_before_judging(project: Path) -> None:
    out_of = project / "checks" / ".." / "design.py"
    into = project / "docs" / ".." / "checks" / "test_x.py"
    assert run_hook(write(out_of, cwd=project), project).returncode == 2
    assert run_hook(write(into, cwd=project), project).returncode == 0


def test_a_path_that_climbs_out_of_the_project_with_dotdot_is_not_the_projects(
    project: Path,
) -> None:
    """The project's folder is not a place to write to, but its parent is no project."""
    for climbing in (
        project / ".." / "notes.txt",
        project / "checks" / ".." / ".." / "notes.txt",
    ):
        done = run_hook(write(climbing, cwd=project), project)
        assert done.returncode == 0, (str(climbing), stderr(done))


def test_dotdot_after_a_link_to_another_project_is_read_as_written(
    project: Path, tmp_path: Path
) -> None:
    """other/../design.py is project/design.py as written, not what the link says.

    The session was started in checks/, so a project found through the link (other/ is
    a project of its own) is not the session's and would let the write through.
    """
    scratch = tmp_path / "scratch" / "my-board"
    scratch.mkdir(parents=True)
    (scratch / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    (project / "other").symlink_to(scratch, target_is_directory=True)
    inside = project / "checks"
    done = run_hook(
        write(project / "other" / ".." / "design.py", cwd=inside),
        inside,
        session=inside,
    )
    assert done.returncode == 2, stderr(done)


def test_a_link_inside_checks_that_leads_out_is_a_write_outside(project: Path) -> None:
    (project / "checks" / "link.py").symlink_to(project / "design.py")
    done = run_hook(write(project / "checks" / "link.py", cwd=project), project)
    assert done.returncode == 2, stderr(done)


def test_a_generated_folder_is_outside_checks_too(project: Path) -> None:
    done = run_hook(write(project / "kicad" / "x.kicad_pcb", cwd=project), project)
    assert done.returncode == 2


# --- links: in the session folder, in the file's path, in the project's own folders ---


@pytest.fixture
def linked(tmp_path: Path) -> tuple[Path, Path]:
    """Return a project and the same project reached through a link: (real, link).

    pytest's tmp_path is already free of links, so the link has to be made here. On a
    Mac, /tmp and /var are links, which is how a session folder comes to be one.
    ``checks/link.py`` is a link to ``design.py``, the way an agent might sneak a write
    out of checks/.
    """
    real = tmp_path / "real" / "my-board"
    (real / "checks").mkdir(parents=True)
    for name in ("pcbkit.toml", "design.py"):
        (real / name).write_text("x = 1\n", encoding="utf-8")
    (real / "checks" / "link.py").symlink_to("../design.py")
    (tmp_path / "link").symlink_to("real", target_is_directory=True)
    return real, tmp_path / "link" / "my-board"


@pytest.mark.parametrize(
    "session_by_link", [False, True], ids=["session-real", "session-link"]
)
@pytest.mark.parametrize("file_by_link", [False, True], ids=["file-real", "file-link"])
@pytest.mark.parametrize(
    ("relative", "expected"),
    [("design.py", 2), ("checks/link.py", 2), ("checks/test_x.py", 0)],
    ids=["design", "link-to-design", "new-check"],
)
def test_the_session_folder_and_the_file_may_each_be_named_through_a_link(
    linked: tuple[Path, Path],
    session_by_link: bool,
    file_by_link: bool,
    relative: str,
    expected: int,
) -> None:
    """The same file is judged the same way however the session and file are named."""
    real, link = linked
    session = link if session_by_link else real
    target = (link if file_by_link else real) / relative
    done = run_hook(write(target, cwd=session), session, session=session)
    assert done.returncode == expected, stderr(done)


@pytest.mark.parametrize(
    "session_by_link", [False, True], ids=["session-real", "session-link"]
)
@pytest.mark.parametrize("file_by_link", [False, True], ids=["file-real", "file-link"])
def test_a_folder_of_the_project_that_is_a_link_elsewhere_is_still_the_projects(
    linked: tuple[Path, Path],
    tmp_path: Path,
    session_by_link: bool,
    file_by_link: bool,
) -> None:
    """docs/ may be a link to a shared folder: the path as written still counts.

    That holds when the session and the file name the project differently, one of them
    through a link. Followed to the end the path lands in the shared folder, which is in
    no project, so it is the project's own folder, found by its identity, that stops it.
    """
    real, link = linked
    shared = tmp_path / "shared"
    shared.mkdir()
    (real / "docs").symlink_to(shared, target_is_directory=True)
    session = link if session_by_link else real
    target = (link if file_by_link else real) / "docs" / "notes.md"
    done = run_hook(write(target, cwd=session), session, session=session)
    assert done.returncode == 2, stderr(done)


@pytest.mark.parametrize(
    "session_by_link", [False, True], ids=["file-by-link", "session-by-link"]
)
def test_a_link_that_is_the_project_itself_is_the_project(
    project: Path, tmp_path: Path, session_by_link: bool
) -> None:
    """A link to the project folder is a second name for it, whichever side uses it.

    notes/ leads out of the project, so followed to the end the file is in no project:
    only the project folder, found through the link, stops it. The link is the last
    part of the name here, which a link above the project would not test: the file
    system looks through that one whichever way it is asked.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (project / "notes").symlink_to(shared, target_is_directory=True)
    mirror = tmp_path / "mirror"
    mirror.symlink_to(project, target_is_directory=True)
    session, folder = (mirror, project) if session_by_link else (project, mirror)
    done = run_hook(
        write(folder / "notes" / "todo.md", cwd=session), session, session=session
    )
    assert done.returncode == 2, stderr(done)


def test_a_session_started_in_a_folder_of_the_project_that_is_a_link_elsewhere(
    project: Path, tmp_path: Path
) -> None:
    """Started in notes/, which leads out of the project, the session is in it still.

    The followed folder is the shared one, in no project, so only the name the session
    was started under ties it to the project.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (project / "notes").symlink_to(shared, target_is_directory=True)
    session = project / "notes"
    blocked = run_hook(
        write(project / "design.py", cwd=session), session, session=session
    )
    allowed = run_hook(
        write(project / "checks" / "test_x.py", cwd=session), session, session=session
    )
    assert (blocked.returncode, allowed.returncode) == (2, 0), stderr(blocked)


def test_a_checks_folder_that_is_a_link_elsewhere_is_still_checks(
    project: Path, tmp_path: Path
) -> None:
    shared = tmp_path / "shared-checks"
    shared.mkdir()
    (project / "checks" / "sub").rmdir()
    (project / "checks").rmdir()
    (project / "checks").symlink_to(shared, target_is_directory=True)
    done = run_hook(write(project / "checks" / "test_x.py", cwd=project), project)
    assert done.returncode == 0, stderr(done)


# --- one folder under two names: capitals, a link into the project --------------------


@pytest.fixture
def cased(tmp_path: Path) -> Path:
    """Return a folder that holds the project ``real/my-board``.

    The tests spell its folders with other capitals. Whether ``REAL/my-board`` is then
    the project or no folder at all depends on the file system, so each expectation is
    taken from ``one_folder``, never assumed.
    """
    root = tmp_path / "real" / "my-board"
    (root / "checks").mkdir(parents=True)
    (root / "kicad").mkdir()
    for name in ("pcbkit.toml", "design.py"):
        (root / name).write_text("x = 1\n", encoding="utf-8")
    (root / "checks" / "link.py").symlink_to("../design.py")
    return tmp_path


# (session, the project the file is in), both below the test folder. Every row spells
# exactly one of them with other capitals, at or below the session folder, so no part of
# the file's path starts with the session's own spelling and only the file system can
# tie the two together. The session starts at the project, inside it and above it, and
# so does the spelling that differs. A row with the capitals above the session folder
# would test nothing: the names already match.
CAPITALS = [
    pytest.param("real/my-board", "REAL/my-board", id="file-ancestor"),
    pytest.param("real/my-board", "real/MY-BOARD", id="file-project"),
    pytest.param("real/my-board", "REAL/MY-BOARD", id="file-both"),
    pytest.param("REAL/my-board", "real/my-board", id="session-ancestor"),
    pytest.param("real/MY-BOARD", "real/my-board", id="session-project"),
    pytest.param("real/my-board/checks", "REAL/my-board", id="inside-file"),
    pytest.param("REAL/my-board/checks", "real/my-board", id="inside-session"),
    pytest.param("real", "REAL/my-board", id="above-file"),
    pytest.param("REAL", "real/my-board", id="above-session"),
]


@pytest.mark.parametrize(("session", "project"), CAPITALS)
def test_a_project_spelled_with_other_capitals_is_the_sessions_where_it_is_one_folder(
    cased: Path, session: str, project: str
) -> None:
    """Block a write to design.py exactly when the file system calls the two one folder.

    A string comparison sees ``REAL/my-board`` and ``real/my-board`` as two projects and
    lets the write through. On a case-sensitive file system the capitals name a folder
    that does not exist, which is no project, so the write is the hook's to ignore.
    """
    same = all(one_folder(cased / s, cased / s.lower()) for s in (session, project))
    real = cased / "real" / "my-board"
    done = run_hook(
        write(cased / project / "design.py", cwd=real), real, session=cased / session
    )
    assert done.returncode == (2 if same else 0), (same, stderr(done))


@pytest.mark.parametrize(("session", "project"), CAPITALS)
def test_the_checks_folder_is_writable_however_the_project_is_spelled(
    cased: Path, session: str, project: str
) -> None:
    real = cased / "real" / "my-board"
    done = run_hook(
        write(cased / project / "checks" / "test_x.py", cwd=real),
        real,
        session=cased / session,
    )
    assert done.returncode == 0, stderr(done)


@pytest.mark.parametrize(
    ("relative", "outside"),
    [
        ("new_folder/deeper/x.py", True),
        ("kicad/x.kicad_pcb", True),
        ("checks/link.py", True),
        ("checks/new/deeper/test_x.py", False),
    ],
    ids=["new-folders", "generated-folder", "link-out-of-checks", "new-check"],
)
def test_everything_else_is_judged_the_same_under_the_other_spelling(
    cased: Path, relative: str, outside: bool
) -> None:
    """Folders that do not exist yet, and a link that leads out of checks/, included."""
    real = cased / "real" / "my-board"
    variant = cased / "REAL" / "my-board"
    same = one_folder(variant, real)
    done = run_hook(write(variant / relative, cwd=real), real, session=real)
    assert done.returncode == (2 if same and outside else 0), (same, stderr(done))


def test_an_event_cwd_spelled_with_other_capitals_is_the_same_folder(
    cased: Path,
) -> None:
    """A relative path is taken from the event's cwd, which may be spelled otherwise."""
    real = cased / "real" / "my-board"
    variant = cased / "REAL" / "my-board"
    same = one_folder(variant, real)
    blocked = run_hook(write("design.py", cwd=variant), real, session=real)
    allowed = run_hook(write("checks/test_x.py", cwd=variant), real, session=real)
    assert (blocked.returncode, allowed.returncode) == (2 if same else 0, 0), (
        same,
        stderr(blocked),
    )


def test_a_scratch_copy_spelled_with_other_capitals_is_still_another_project(
    cased: Path,
) -> None:
    """What the file system calls one folder is the same project, not a lookalike."""
    scratch = cased / "scratch" / "my-board"
    scratch.mkdir(parents=True)
    (scratch / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    real = cased / "real" / "my-board"
    done = run_hook(
        write(cased / "SCRATCH" / "my-board" / "design.py", cwd=real),
        real,
        session=real,
    )
    assert done.returncode == 0, stderr(done)


def test_a_project_whose_name_starts_like_the_sessions_is_another_project(
    project: Path, tmp_path: Path
) -> None:
    sibling = tmp_path / "my-board-2"
    sibling.mkdir()
    (sibling / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    done = run_hook(write(sibling / "design.py", cwd=project), project, session=project)
    assert done.returncode == 0, stderr(done)


def test_a_session_reached_through_a_link_into_the_project_is_inside_it(
    project: Path, tmp_path: Path
) -> None:
    """The link jumps into the middle of the project: only the followed path shows it.

    The file is named by its real location, with no link in it, and the session folder
    is the link, so neither name starts with the other. Folders above the link are
    nowhere near the project.
    """
    shortcut = tmp_path / "shortcut"
    shortcut.symlink_to(project / "checks", target_is_directory=True)
    blocked = run_hook(
        write(project / "design.py", cwd=shortcut), shortcut, session=shortcut
    )
    allowed = run_hook(
        write(project / "checks" / "test_x.py", cwd=shortcut),
        shortcut,
        session=shortcut,
    )
    assert (blocked.returncode, allowed.returncode) == (2, 0), stderr(blocked)


def test_a_session_folder_that_does_not_exist_is_judged_by_its_name(
    project: Path,
) -> None:
    """A folder that is gone cannot be compared with anything but its spelling.

    Below the project, it still puts the project in the session. Elsewhere it puts
    nothing in it: a missing folder is not every folder.
    """
    inside = project / "checks" / "not-yet"
    elsewhere = project.parent / "missing"
    target = write(project / "design.py", cwd=project)
    assert run_hook(target, project, session=inside).returncode == 2
    assert run_hook(target, project, session=elsewhere).returncode == 0


def test_a_session_folder_with_a_null_character_in_it_does_not_stop_the_comparison(
    project: Path,
) -> None:
    """A path the file system cannot take is no folder: judged by name, not a crash.

    A crash is exit 1, which lets the write through. JSON can carry the character, and
    this is the event's cwd, the session folder when there is no session variable.
    """
    event = write(project / "design.py")
    event["cwd"] = f"{project}/checks/not\x00yet"
    done = run_hook(event, project)
    assert done.returncode == 2, stderr(done)


def test_a_folder_that_does_not_exist_is_judged_by_its_name(tmp_path: Path) -> None:
    """``inside`` falls back on the spelling when the file system has nothing to say."""
    hook = load_hook()
    gone = str(tmp_path / "gone")
    assert hook.inside(gone, gone)
    assert hook.inside(f"{gone}/a/b", gone)
    assert not hook.inside(gone, f"{gone}/a")
    assert not hook.inside(f"{gone}-2/a", gone)


# --- what is not the agent's business -------------------------------------------------


@pytest.mark.parametrize("agent", [None, "general-purpose", "pcbkit:parts-researcher"])
def test_everyone_else_may_write_anywhere_in_the_project(
    project: Path, agent: str | None
) -> None:
    done = run_hook(write(project / "design.py", agent, project), project)
    assert done.returncode == 0, stderr(done)
    assert done.stderr == b""


def test_the_agent_name_is_what_follows_the_last_colon(project: Path) -> None:
    """A plugin may nest its agents: the name is the part after the last colon."""
    for agent in ("pcbkit:check-writer", "pcbkit:nested:check-writer"):
        done = run_hook(write(project / "design.py", agent, project), project)
        assert done.returncode == 2, (agent, stderr(done))


@pytest.mark.parametrize(
    "agent",
    [
        "check-writer-two",
        "my-check-writer",
        "other:check-writer:x",
        "",
        5,
        ["check-writer"],
    ],
)
def test_only_that_agent_is_confined(project: Path, agent: object) -> None:
    done = run_hook(write(project / "design.py", agent, project), project)
    assert done.returncode == 0, stderr(done)


# --- which project is the session's -------------------------------------------------


def test_the_session_decides_the_project_not_where_the_agent_is_working(
    project: Path, tmp_path: Path
) -> None:
    """An agent that works inside its scratch copy does not unprotect the project."""
    scratch = tmp_path / "scratch" / "my-board"
    scratch.mkdir(parents=True)
    (scratch / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    real = run_hook(write(project / "design.py", cwd=scratch), scratch, session=project)
    copy = run_hook(write(scratch / "design.py", cwd=scratch), scratch, session=project)
    assert (real.returncode, copy.returncode) == (2, 0), stderr(real)


def test_a_session_started_above_the_board_still_confines_it(tmp_path: Path) -> None:
    started = tmp_path / "repo"
    board = started / "boards" / "my-board"
    (board / "checks").mkdir(parents=True)
    (board / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    (board / "design.py").write_text("x = 1\n", encoding="utf-8")
    scratch = tmp_path / "scratch" / "my-board"
    scratch.mkdir(parents=True)
    (scratch / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    results = {
        name: run_hook(write(path, cwd=started), started, session=started).returncode
        for name, path in {
            "design.py": board / "design.py",
            "checks": board / "checks" / "test_x.py",
            "scratch": scratch / "design.py",
        }.items()
    }
    assert results == {"design.py": 2, "checks": 0, "scratch": 0}


def test_a_session_started_inside_the_project_confines_it(project: Path) -> None:
    inside = project / "checks"
    blocked = run_hook(write(project / "design.py", cwd=inside), inside, session=inside)
    allowed = run_hook(write(inside / "test_x.py", cwd=inside), inside, session=inside)
    assert (blocked.returncode, allowed.returncode) == (2, 0)


@pytest.mark.parametrize("session", ["relative/folder", ""], ids=["relative", "empty"])
def test_a_session_variable_that_is_no_absolute_path_is_ignored(
    project: Path, session: str
) -> None:
    """The event's cwd stands in, as when the variable is missing.

    The hook runs somewhere else, so a relative variable that was trusted would be read
    against that folder and would not lead to the project.
    """
    done = run_hook(
        write(project / "design.py", cwd=project), project.parent, session=session
    )
    assert done.returncode == 2, stderr(done)


def test_a_relative_event_cwd_does_not_move_the_session_folder(tmp_path: Path) -> None:
    """Use the hook's own folder as the session folder when the event's cwd is useless.

    There is no session variable here, and the folder the hook runs in is the one above
    the board.
    """
    started = tmp_path / "repo"
    board = started / "boards" / "my-board"
    board.mkdir(parents=True)
    (board / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    event = write(board / "design.py")
    event["cwd"] = "scratch"
    done = run_hook(event, started)
    assert done.returncode == 2, stderr(done)


def test_a_scratch_copy_outside_the_project_may_be_edited(
    project: Path, tmp_path: Path
) -> None:
    scratch = tmp_path / "scratch" / "my-board"
    scratch.mkdir(parents=True)
    (scratch / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    (scratch / "design.py").write_text("x = 1\n", encoding="utf-8")
    for name in ("design.py", "mutants.py", "checks/test_x.py"):
        done = run_hook(write(scratch / name, cwd=project), project)
        assert done.returncode == 0, (name, stderr(done))


def test_a_file_that_belongs_to_no_project_may_be_written(
    project: Path, tmp_path: Path
) -> None:
    done = run_hook(write(tmp_path / "notes.txt", cwd=project), project)
    assert done.returncode == 0, stderr(done)


def test_a_session_outside_any_project_is_not_confined(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-board"
    plain.mkdir()
    done = run_hook(write(plain / "design.py", cwd=plain), plain)
    assert done.returncode == 0, stderr(done)


def test_a_sibling_project_is_not_this_project(project: Path, tmp_path: Path) -> None:
    sibling = tmp_path / "other-board"
    sibling.mkdir()
    (sibling / "pcbkit.toml").write_text("x = 1\n", encoding="utf-8")
    done = run_hook(write(sibling / "design.py", cwd=project), project)
    assert done.returncode == 0, stderr(done)


# --- events it cannot read: never block, and say so -----------------------------------


@pytest.mark.parametrize(
    "event",
    [
        b"",
        b"not json",
        b"[]",
        b"\xff\xfe",
        {"agent_type": NAMESPACED, "tool_name": "Write", "tool_input": {}},
        {"agent_type": NAMESPACED, "tool_name": "Edit", "tool_input": "design.py"},
        {
            "agent_type": NAMESPACED,
            "tool_name": "Write",
            "tool_input": {"file_path": 3},
        },
        {
            "agent_type": NAMESPACED,
            "tool_name": "Write",
            "tool_input": {"file_path": ""},
        },
    ],
    ids=[
        "empty",
        "garbled",
        "array",
        "not-utf8",
        "write-without-path",
        "edit-string-input",
        "numeric-path",
        "write-empty-path",
    ],
)
def test_an_event_it_cannot_read_is_reported_but_never_blocks(
    project: Path, event: object
) -> None:
    done = run_hook(event, project)
    assert done.returncode == 1, stderr(done)
    assert stderr(done).startswith("confine_check_writer.py:")


@pytest.mark.parametrize(
    "event",
    [
        {},
        {
            "agent_type": NAMESPACED,
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        },
        {"tool_name": "Write", "tool_input": {}},
    ],
    ids=["empty-object", "bash-by-the-agent", "main-session-no-path"],
)
def test_an_event_with_nothing_to_check_is_allowed(
    project: Path, event: object
) -> None:
    done = run_hook(event, project)
    assert done.returncode == 0, stderr(done)
    assert done.stderr == b""


# --- the script itself ---------------------------------------------------------------


def test_run_by_hand_in_a_terminal_it_says_what_it_is_instead_of_waiting(
    tmp_path: Path,
) -> None:
    """A person who runs the hook from a shell gets a hint, not a silent wait."""
    master, slave = pty.openpty()
    try:
        done = subprocess.run(
            [sys.executable, str(HOOK)],
            stdin=slave,
            capture_output=True,
            cwd=tmp_path,
            timeout=15,
        )
    finally:
        os.close(master)
        os.close(slave)
    assert done.returncode == 1, stderr(done)
    assert stderr(done).startswith("confine_check_writer.py: a Claude Code hook")
    assert stderr(done).endswith("\n")


def test_the_script_imports_only_the_standard_library() -> None:
    """It runs under the system python3, where pcbkit is not installed."""
    tree = ast.parse(HOOK.read_text(encoding="utf-8"), filename=str(HOOK))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "json", "os", "sys"}, imported
