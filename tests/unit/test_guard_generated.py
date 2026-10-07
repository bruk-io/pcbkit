"""Unit tests for hooks/guard_generated.py, the plugin's guard on generated files.

The guard is a program Claude Code runs before every Edit and Write: it reads the event
as JSON on stdin and answers with an exit code and a reason on stderr. The tests run it
the same way, as a child process on a throwaway project folder, so the contract Claude
Code depends on is what is tested: exit 2 blocks, exit 0 allows, exit 1 means "could not
read the event" and must never block.
"""

from __future__ import annotations

import ast
import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "guard_generated.py"

# What the guard promises to leave alone and to block.
GENERATED = ["kicad", "out", "golden", "fab"]


def run_guard(
    event: object | bytes,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run the guard on one event (JSON-encoded unless given as bytes)."""
    data = event if isinstance(event, bytes) else json.dumps(event).encode("utf-8")
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=data,
        capture_output=True,
        cwd=cwd,
        env=env,
        timeout=60,
    )


def edit(path: str | Path, tool: str = "Edit", cwd: Path | None = None) -> dict:
    """Return the PreToolUse event Claude Code sends for a tool call on ``path``."""
    event: dict = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(path), "content": "x"},
    }
    if cwd is not None:
        event["cwd"] = str(cwd)
    return event


def stderr(done: subprocess.CompletedProcess[bytes]) -> str:
    """Return what the guard printed on stderr."""
    return done.stderr.decode("utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a board project: a pcbkit.toml, the generated folders and a checks/."""
    root = tmp_path / "my-board"
    for name in ("kicad", "out/fab", "golden", "fab", "checks", "archive/rev_a/kicad"):
        (root / name).mkdir(parents=True)
    (root / "pcbkit.toml").write_text('[board]\nstem = "my_board"\n', encoding="utf-8")
    return root


# --- what is blocked ------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["Edit", "Write"])
@pytest.mark.parametrize(
    "relative",
    [
        "kicad/my_board.kicad_pcb",
        "out/fab/my_board_BOM.csv",
        "golden/my_board.ses",
        "fab/my_board_gerbers.zip",
    ],
)
def test_a_file_in_a_generated_folder_is_blocked(
    project: Path, tool: str, relative: str
) -> None:
    done = run_guard(edit(project / relative, tool), project)
    assert done.returncode == 2, stderr(done)
    assert done.stdout == b""
    folder = relative.split("/")[0]
    assert f"in {folder}/" in stderr(done)


def test_every_generated_folder_is_covered(project: Path) -> None:
    """Guard the parametrised test above: it must name each folder the guard knows."""
    blocked = []
    for name in GENERATED:
        done = run_guard(edit(project / name / "x.txt"), project)
        blocked.append(done.returncode)
    assert blocked == [2] * len(GENERATED)


def test_the_block_names_the_file_and_what_to_change_instead(project: Path) -> None:
    target = project / "kicad" / "my_board.kicad_pcb"
    done = run_guard(edit(target), project)
    message = stderr(done)
    assert str(target) in message
    for source in ("design.py", "layout.py", "routing.py", "silk.py", "pcbkit.toml"):
        assert source in message
    assert "pcbkit build" in message


def test_a_new_file_in_a_new_subfolder_is_blocked(project: Path) -> None:
    """Write can create folders: the guard cannot rely on the file existing."""
    done = run_guard(
        edit(project / "kicad" / "new" / "deep" / "x.txt", "Write"), project
    )
    assert done.returncode == 2, stderr(done)


# --- what is allowed ------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "design.py",
        "layout.py",
        "routing.py",
        "specs.py",
        "pcbkit.toml",
        "checks/test_led.py",
        "archive/rev_a/kicad/old.kicad_pcb",
        "README.md",
    ],
)
def test_the_projects_own_files_are_allowed(project: Path, relative: str) -> None:
    done = run_guard(edit(project / relative), project)
    assert done.returncode == 0, stderr(done)
    assert done.stderr == b""


@pytest.mark.parametrize(
    "relative",
    [
        "kicad_notes.md",
        "kicad.txt",
        "output/report.txt",
        "outline/board.svg",
        "fabric/swatch.txt",
        "golden_old/x.ses",
        "checks/kicad/helper.py",
        "out",
    ],
)
def test_names_that_only_start_like_a_generated_folder_are_allowed(
    project: Path, relative: str
) -> None:
    done = run_guard(edit(project / relative), project)
    assert done.returncode == 0, stderr(done)


@pytest.mark.parametrize("relative", ["kicad/x.kicad_pcb", "out/x.csv", "fab/x.zip"])
def test_the_same_paths_are_allowed_outside_a_project(
    tmp_path: Path, relative: str
) -> None:
    plain = tmp_path / "not-a-board"
    (plain / relative).parent.mkdir(parents=True)
    done = run_guard(edit(plain / relative), plain)
    assert done.returncode == 0, stderr(done)


def test_the_nearest_project_decides(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    other = outer / "other"
    for folder in (outer, inner):
        (folder / "kicad").mkdir(parents=True)
        (folder / "pcbkit.toml").write_text("[board]\n", encoding="utf-8")
    (other / "kicad").mkdir(parents=True)
    results = {
        "inner source": run_guard(edit(inner / "design.py"), outer).returncode,
        "inner kicad": run_guard(edit(inner / "kicad" / "x"), outer).returncode,
        # `other` has no pcbkit.toml, so it is a plain folder of outer's
        "other kicad": run_guard(edit(other / "kicad" / "x"), outer).returncode,
        "outer kicad": run_guard(edit(outer / "kicad" / "x"), outer).returncode,
    }
    assert results == {
        "inner source": 0,
        "inner kicad": 2,
        "other kicad": 0,
        "outer kicad": 2,
    }


# --- paths that are not plain absolute ones -------------------------------------------


def test_a_relative_path_is_taken_from_the_events_cwd(project: Path) -> None:
    results = {
        "kicad": run_guard(edit("kicad/x.kicad_pcb", cwd=project), project.parent),
        "source": run_guard(edit("design.py", cwd=project), project.parent),
        "from checks": run_guard(
            edit("../kicad/x.kicad_pcb", cwd=project / "checks"), project.parent
        ),
        "elsewhere": run_guard(edit("../other/kicad/x", cwd=project), project.parent),
    }
    assert {k: v.returncode for k, v in results.items()} == {
        "kicad": 2,
        "source": 0,
        "from checks": 2,
        "elsewhere": 0,
    }


def test_a_relative_path_without_a_cwd_in_the_event_uses_the_process_cwd(
    project: Path,
) -> None:
    assert run_guard(edit("kicad/x.kicad_pcb"), project).returncode == 2
    assert run_guard(edit("design.py"), project).returncode == 0


@pytest.mark.parametrize(
    "cwd",
    ["checks", "", 7, None, ["x"]],
    ids=["relative", "empty", "number", "null", "list"],
)
def test_an_event_cwd_that_is_no_absolute_path_is_ignored(
    project: Path, cwd: object
) -> None:
    """The process's own folder stands in, as when the event has no cwd at all.

    A relative cwd that was trusted would turn kicad/x.kicad_pcb into
    checks/kicad/x.kicad_pcb, which is not a generated folder, and let the edit through.
    """
    event = edit("kicad/x.kicad_pcb")
    event["cwd"] = cwd
    done = run_guard(event, project)
    assert done.returncode == 2, stderr(done)


def test_a_path_that_starts_with_a_tilde_is_read_against_the_home_folder(
    tmp_path: Path,
) -> None:
    """~/my-board/kicad/x is in the project when $HOME holds my-board.

    The event's cwd is outside every project, so a ``~`` taken as a plain folder name
    (``<cwd>/~/my-board/...``) would find no project and let the edit through.
    """
    home = tmp_path / "home"
    (home / "my-board" / "kicad").mkdir(parents=True)
    (home / "my-board" / "pcbkit.toml").write_text("[board]\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {**os.environ, "HOME": str(home)}
    generated = run_guard(
        edit("~/my-board/kicad/x.kicad_pcb", cwd=elsewhere), elsewhere, env=env
    )
    source = run_guard(edit("~/my-board/design.py", cwd=elsewhere), elsewhere, env=env)
    assert generated.returncode == 2, stderr(generated)
    assert "~/my-board/kicad/x.kicad_pcb" in stderr(generated)
    assert source.returncode == 0, stderr(source)


def test_dotdot_segments_are_resolved_before_judging(project: Path) -> None:
    into = project / "checks" / ".." / "kicad" / "x.kicad_pcb"
    out_of = project / "kicad" / ".." / "design.py"
    assert run_guard(edit(into), project).returncode == 2
    assert run_guard(edit(out_of), project).returncode == 0


def test_letter_case_does_not_hide_a_generated_folder(project: Path) -> None:
    """The default macOS file system treats Kicad/ and kicad/ as one folder."""
    for name in ("KiCad", "OUT", "Golden"):
        done = run_guard(edit(project / name / "x.txt"), project)
        assert done.returncode == 2, name


def test_a_link_into_a_generated_folder_is_followed(project: Path) -> None:
    (project / "board").symlink_to(project / "kicad", target_is_directory=True)
    done = run_guard(edit(project / "board" / "x.kicad_pcb"), project)
    assert done.returncode == 2, stderr(done)


def test_a_generated_folder_that_is_a_link_elsewhere_is_still_generated(
    project: Path, tmp_path: Path
) -> None:
    """kicad/ may be a link to a scratch disk: the path as written still counts."""
    (project / "kicad").rmdir()
    target = tmp_path / "scratch-disk"
    target.mkdir()
    (project / "kicad").symlink_to(target, target_is_directory=True)
    done = run_guard(edit(project / "kicad" / "x.kicad_pcb"), project)
    assert done.returncode == 2, stderr(done)


def test_a_non_ascii_path_is_blocked_even_when_stderr_is_ascii(project: Path) -> None:
    """Printing the reason must not fail: a crash would let the edit through."""
    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    done = run_guard(edit(project / "kicad" / "café.kicad_pcb"), project, env=env)
    assert done.returncode == 2, stderr(done)
    assert "café" in stderr(done)


# --- events the guard cannot read: never block, and say so ----------------------------


@pytest.mark.parametrize(
    "event",
    [
        b"",
        b"not json",
        b'{"tool_name": "Edit"',
        b"[]",
        b"null",
        b"\xff\xfe",
        {"tool_name": "Edit", "tool_input": {}},
        {"tool_name": "Write", "tool_input": {"file_path": ""}},
        {"tool_name": "Write", "tool_input": {"file_path": 7}},
        {"tool_name": "Edit", "tool_input": "kicad/x"},
    ],
    ids=[
        "empty",
        "garbled",
        "truncated",
        "array",
        "null",
        "not-utf8",
        "edit-without-path",
        "write-empty-path",
        "write-numeric-path",
        "edit-string-input",
    ],
)
def test_an_event_it_cannot_read_is_reported_but_never_blocks(
    project: Path, event: object
) -> None:
    done = run_guard(event, project)
    assert done.returncode == 1, stderr(done)
    assert stderr(done).startswith("guard_generated.py:")


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        {"tool_name": "Read", "tool_input": {"file_path": "/kicad/x"}},
    ],
    ids=["empty-object", "bash", "read-outside-a-project"],
)
def test_an_event_with_nothing_to_check_is_allowed(
    project: Path, event: object
) -> None:
    done = run_guard(event, project)
    assert done.returncode == 0, stderr(done)
    assert done.stderr == b""


# --- the script itself ----------------------------------------------------------------


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
    assert stderr(done).startswith("guard_generated.py: a Claude Code hook")
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
