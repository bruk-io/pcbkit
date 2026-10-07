"""Unit tests for hooks/confine_check_writer.py, the guard on the check-writer agent.

Like the guard on generated files, it is a program Claude Code runs before every Edit
and Write, fed one JSON event on stdin; the tests run it that way on a throwaway
project. What it adds is the agent: an event carries ``agent_type`` when a subagent
makes the call, and for the check-writer agent every write inside the project must land
under ``checks/``.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "confine_check_writer.py"

# How Claude Code names a plugin's agent in `agent_type`, and the bare name.
NAMESPACED = "pcbkit:check-writer"
BARE = "check-writer"


def run_hook(event: object | bytes, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Run the hook on one event (JSON-encoded unless given as bytes)."""
    data = event if isinstance(event, bytes) else json.dumps(event).encode("utf-8")
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=data,
        capture_output=True,
        cwd=cwd,
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


def test_letter_case_of_checks_does_not_matter(project: Path) -> None:
    """The default macOS file system treats Checks/ and checks/ as one folder."""
    done = run_hook(write(project / "Checks" / "test_x.py", cwd=project), project)
    assert done.returncode == 0, stderr(done)


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


def test_dotdot_segments_are_resolved_before_judging(project: Path) -> None:
    out_of = project / "checks" / ".." / "design.py"
    into = project / "docs" / ".." / "checks" / "test_x.py"
    assert run_hook(write(out_of, cwd=project), project).returncode == 2
    assert run_hook(write(into, cwd=project), project).returncode == 0


def test_a_link_inside_checks_that_leads_out_is_a_write_outside(project: Path) -> None:
    (project / "checks" / "link.py").symlink_to(project / "design.py")
    done = run_hook(write(project / "checks" / "link.py", cwd=project), project)
    assert done.returncode == 2, stderr(done)


def test_a_generated_folder_is_outside_checks_too(project: Path) -> None:
    done = run_hook(write(project / "kicad" / "x.kicad_pcb", cwd=project), project)
    assert done.returncode == 2


# --- what is not the agent's business -------------------------------------------------


@pytest.mark.parametrize("agent", [None, "general-purpose", "pcbkit:parts-researcher"])
def test_everyone_else_may_write_anywhere_in_the_project(
    project: Path, agent: str | None
) -> None:
    done = run_hook(write(project / "design.py", agent, project), project)
    assert done.returncode == 0, stderr(done)
    assert done.stderr == b""


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
    ],
    ids=[
        "empty",
        "garbled",
        "array",
        "not-utf8",
        "write-without-path",
        "edit-string-input",
        "numeric-path",
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
