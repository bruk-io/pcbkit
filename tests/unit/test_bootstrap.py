"""Unit tests for pcbkit.bootstrap and the `pcbkit setup` command.

The machine is fake: the `machine` fixture stands in for PATH, HOME, /Applications and
every command whose output pcbkit reads, and `bootstrap._call` (a command with its
output on the terminal: `uv venv`, `uv sync`) and `bootstrap._open` (the download) are
replaced here. So these tests never run uv, never touch the network, and show what
`pcbkit setup` does and says on a machine with and without each thing it needs.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import urllib.error
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner, Result

from pcbkit import bootstrap
from pcbkit.bootstrap import SetupError
from pcbkit.cli import cli
from pcbkit.kicad import env
from tests.board_files import TOML, write_file
from tests.fake_machine import FakeMachine

KICAD_OK = "pcbkit-probe 3.9.13 10.0.6\n"
VENV_OK = "pcbkit-setup 10.0.6\n"
NO_PCBNEW = (
    "Traceback (most recent call last):\n"
    "ModuleNotFoundError: No module named 'pcbnew'\n"
)
PYPROJECT = '[project]\nname = "my-board"\nversion = "0.1.0"\n'
JAR = env.FREEROUTING_JAR_NAME


def real_size_jar() -> bytes:
    """Return a zip file of exactly the size of the Freerouting 1.9.0 jar.

    One stored member (no compression) with a one-letter name: the local header, the
    central directory entry and the end record add 100 bytes to the member's data.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("a", b"\0" * (bootstrap.FREEROUTING_BYTES - 100))
    data = buffer.getvalue()
    assert len(data) == bootstrap.FREEROUTING_BYTES
    return data


GOOD_JAR = real_size_jar()


def small_zip() -> bytes:
    """Return a few hundred bytes that are a valid zip file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    return buffer.getvalue()


@dataclass
class World:
    """A fake machine with a board project on it, and a record of what ran."""

    machine: FakeMachine
    root: Path
    calls: list[tuple[list[str], Path]] = field(default_factory=list)
    uv_venv_code: int = 0
    uv_sync_code: int = 0
    probe: str = VENV_OK
    probe_code: int = 0
    downloads: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)

    @property
    def uv(self) -> Path:
        """Return where the fake uv is."""
        return self.machine.root / "bin" / "uv"

    @property
    def kicad_python(self) -> Path:
        """Return KiCad's Python inside the fake KiCad.app."""
        return (
            self.machine.mac_app
            / "Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
        )

    @property
    def venv(self) -> Path:
        """Return the project's .venv."""
        return self.root / ".venv"

    @property
    def jar_dir(self) -> Path:
        """Return pcbkit's own folder for the jar."""
        return self.machine.home / ".local" / "share" / "pcbkit"

    def make_venv(self, output: str = VENV_OK, code: int = 0) -> None:
        """Put a virtual environment in .venv whose Python answers with ``output``."""
        (self.venv / "bin").mkdir(parents=True, exist_ok=True)
        (self.venv / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
        self.machine.exe(self.venv / "bin" / "python", output=output, returncode=code)


@pytest.fixture
def world(
    machine: FakeMachine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> World:
    """Make a Mac with uv, KiCad 10 and Java 21, a project, and a fake network."""
    machine.os_name = "macos"
    root = tmp_path / "my-board"
    write_file(root / "pcbkit.toml", TOML)
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    w = World(machine, root)
    machine.exe(w.uv, output="uv 0.12.15", on_path="uv")
    machine.exe(w.kicad_python, output=KICAD_OK)
    jdk = machine.home / "jdk"
    machine.exe(jdk / "bin" / "java", output='openjdk version "21.0.1" 2026-01-01')
    monkeypatch.setenv("JAVA_HOME", str(jdk))

    def call(args: list[str], cwd: Path) -> int:
        """Pretend to be `uv venv` and `uv sync`."""
        w.calls.append((list(args), Path(cwd)))
        if args[1] == "venv":
            if w.uv_venv_code:
                return w.uv_venv_code
            target = Path(cwd) / args[-1]
            if target.exists():
                if "--clear" not in args:  # what the real uv does
                    return 2
                shutil.rmtree(target)
            w.make_venv(w.probe, w.probe_code)
            return 0
        assert args[1] == "sync", args
        return w.uv_sync_code

    monkeypatch.setattr(bootstrap, "_call", call)

    def refuse(url: str) -> Any:
        """Fail the test that reaches for the network without saying so."""
        raise AssertionError(f"unexpected download of {url}")

    monkeypatch.setattr(bootstrap, "_open", refuse)
    return w


def put_jar(folder: Path, data: bytes | None = None) -> Path:
    """Write a jar called freerouting-1.9.0.jar into ``folder``, and return its path.

    The default is a small valid zip: a jar that is found is only checked to be one.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / JAR
    path.write_bytes(small_zip() if data is None else data)
    return path


def serve(
    monkeypatch: pytest.MonkeyPatch, world: World, data: bytes | Exception
) -> None:
    """Make the download answer with ``data``, or raise it if it is an exception."""

    def opener(url: str) -> Any:
        world.downloads.append(url)
        if isinstance(data, Exception):
            raise data
        return io.BytesIO(data)

    monkeypatch.setattr(bootstrap, "_open", opener)


def run_setup(world: World) -> bootstrap.SetupResult:
    """Run setup_project on the world's project, keeping what it says."""
    world.said.clear()
    return bootstrap.setup_project(world.root, say=world.said.append)


def fails(world: World, text: str) -> SetupError:
    """Run setup, expect a SetupError whose message has ``text``, and return it."""
    with pytest.raises(SetupError) as caught:
        run_setup(world)
    assert text in caught.value.message, caught.value.message
    return caught.value


# --- a run from start to finish -------------------------------------------------------


def test_setup_makes_the_venv_on_kicads_python_then_syncs_and_proves_pcbnew(
    world: World,
) -> None:
    """Run `uv venv` with KiCad's Python and system site packages, then `uv sync`."""
    put_jar(world.machine.home / ".local" / "share" / "freerouting")
    result = run_setup(world)
    py = str(world.kicad_python)
    assert world.calls == [
        (
            [str(world.uv), "venv", "--python", py, "--system-site-packages", ".venv"],
            world.root,
        ),
        ([str(world.uv), "sync", "--python", py], world.root),
    ]
    assert result.root == world.root
    assert result.kicad_python.pcbnew_version == "10.0.6"
    assert result.venv_python == world.venv / "bin" / "python"
    assert result.java is not None and result.java.version == "21.0.1"
    said = "\n".join(world.said)
    for text in (
        "uv  0.12.15",
        "KiCad Python  3.9.13, pcbnew 10.0.6",
        ".venv  making it on KiCad's Python",
        ".venv  installing the dependencies",
        ".venv  imports pcbnew 10.0.6",
        "Java  21.0.1",
    ):
        assert text in said, text


def test_setup_runs_the_venv_before_the_sync_and_the_proof_after_it(
    world: World,
) -> None:
    """Make the order matter: sync needs the venv, and the proof needs the sync."""
    put_jar(world.jar_dir)
    run_setup(world)
    said = world.said
    order = [
        next(i for i, line in enumerate(said) if text in line)
        for text in ("making it", "installing", "imports pcbnew", "Freerouting")
    ]
    assert order == sorted(order)


def test_setup_keeps_a_venv_that_already_imports_pcbnew(world: World) -> None:
    """Not run `uv venv` over a good .venv (it would refuse), but still sync."""
    put_jar(world.jar_dir)
    world.make_venv()
    run_setup(world)
    assert [args[1] for args, _ in world.calls] == ["sync"]
    assert any(".venv  kept: it imports pcbnew 10.0.6" in line for line in world.said)


def test_setup_twice_does_no_harm(world: World) -> None:
    """Make the venv once, keep it the second time, and end in the same state."""
    put_jar(world.jar_dir)
    first = run_setup(world)
    second = run_setup(world)
    assert [args[1] for args, _ in world.calls] == ["venv", "sync", "sync"]
    assert first.venv_python == second.venv_python


def test_setup_makes_a_venv_that_cannot_import_pcbnew_again_with_clear(
    world: World,
) -> None:
    """Replace a venv built on another Python, saying so, and ask uv to clear it."""
    put_jar(world.jar_dir)
    world.make_venv(NO_PCBNEW, 1)
    run_setup(world)
    venv_call = world.calls[0][0]
    assert venv_call[1] == "venv" and "--clear" in venv_call
    assert any("does not import pcbnew" in line for line in world.said)
    assert any(".venv  imports pcbnew 10.0.6" in line for line in world.said)


def test_a_fresh_venv_is_made_without_clear(world: World) -> None:
    """Never pass --clear when there is nothing to clear."""
    put_jar(world.jar_dir)
    run_setup(world)
    assert "--clear" not in world.calls[0][0]


def test_setup_never_replaces_a_folder_that_is_not_a_venv(world: World) -> None:
    """Refuse a .venv with no pyvenv.cfg, and leave it and its files alone."""
    (world.venv / "stuff").mkdir(parents=True)
    (world.venv / "stuff" / "mine.txt").write_text("keep", encoding="utf-8")
    fails(world, "is not a virtual environment")
    assert world.calls == []
    assert (world.venv / "stuff" / "mine.txt").read_text(encoding="utf-8") == "keep"


def test_setup_never_replaces_a_file_called_venv(world: World) -> None:
    """Treat a plain file at .venv like any other thing that is not a venv."""
    world.venv.write_text("a file", encoding="utf-8")
    fails(world, "is not a virtual environment")
    assert world.calls == [] and world.venv.read_text(encoding="utf-8") == "a file"


# --- what is missing, and what to do about it -----------------------------------------


@pytest.mark.parametrize(
    ("os_name", "fix"),
    [
        ("macos", "brew install uv"),
        ("linux", "curl -LsSf https://astral.sh/uv/install.sh | sh"),
    ],
)
def test_no_uv_says_how_to_install_it_on_each_os(
    world: World, os_name: str, fix: str
) -> None:
    """Name the fix for this OS and run nothing."""
    world.machine.os_name = os_name
    del world.machine.path_tools["uv"]
    error = fails(world, "uv is not installed")
    assert f"Install it: {fix}" in error.message
    assert world.calls == []


def test_no_kicad_python_lists_what_was_tried_and_says_to_install_kicad(
    world: World,
) -> None:
    """Show why the interpreter that exists was no good, and the fix."""
    world.machine.exe(world.kicad_python, output="", error="no such module pcbnew")
    error = fails(world, "no Python that can import pcbnew was found")
    assert "no such module pcbnew" in error.message
    assert "brew install --cask kicad" in error.message
    assert "pcbkit doctor" in error.message
    assert world.calls == []


def test_no_kicad_at_all_says_there_was_nothing_to_try(world: World) -> None:
    """Say so when there is no interpreter to even try."""
    world.kicad_python.unlink()
    fails(world, "no interpreter to try")


def test_an_old_kicad_is_refused_with_its_version(world: World) -> None:
    """Name the version found and the one pcbkit needs."""
    world.machine.exe(world.kicad_python, output="pcbkit-probe 3.9.13 9.0.7\n")
    error = fails(world, "pcbnew 9.0.7")
    assert "older than the 10.0 pcbkit needs" in error.message
    assert world.calls == []


def test_a_project_with_no_pyproject_is_refused_before_anything_runs(
    world: World,
) -> None:
    """Say which file to add, and that `pcbkit new` writes one."""
    (world.root / "pyproject.toml").unlink()
    error = fails(world, "pyproject.toml not found")
    assert "pcbkit new" in error.message
    assert world.calls == []


def test_a_failing_uv_venv_stops_setup_and_reports_its_exit_code(
    world: World,
) -> None:
    """Report `uv venv` failing, and run nothing after it."""
    world.uv_venv_code = 2
    error = fails(world, "`uv venv` failed (exit 2)")
    assert "run `pcbkit setup` again" in error.message
    assert [args[1] for args, _ in world.calls] == ["venv"]


def test_a_failing_uv_sync_stops_setup_and_points_at_the_dependencies(
    world: World,
) -> None:
    """Report `uv sync` failing, and where to look."""
    world.uv_sync_code = 1
    error = fails(world, "`uv sync` failed (exit 1)")
    assert "[tool.uv.sources]" in error.message
    assert "imports pcbnew" not in "\n".join(world.said)


def test_pcbnew_missing_after_the_sync_is_reported_with_the_likely_cause(
    world: World,
) -> None:
    """Catch a sync that rebuilt .venv on another Python, and name .python-version."""
    world.probe, world.probe_code = NO_PCBNEW, 1
    error = fails(world, "pcbnew does not import in")
    assert ".python-version" in error.message and "UV_PYTHON" in error.message
    assert "No module named 'pcbnew'" in error.message
    assert "Freerouting" not in "\n".join(world.said)


def test_the_venv_probe_ignores_noise_before_its_line(world: World) -> None:
    """Find the answer among the wx and DeprecationWarning lines pcbnew writes."""
    world.make_venv("assert failed in Get()\nDeprecationWarning: SWIG\n" + VENV_OK)
    ok, detail = bootstrap.venv_imports_pcbnew(world.venv / "bin" / "python")
    assert (ok, detail) == (True, "10.0.6")


def test_the_venv_probe_says_why_when_the_python_is_missing_or_silent(
    world: World,
) -> None:
    """Report a missing interpreter, and a Python that exits 0 without answering."""
    python = world.venv / "bin" / "python"
    assert bootstrap.venv_imports_pcbnew(python) == (
        False,
        f"{python} does not exist",
    )
    world.make_venv(output="", code=0)
    assert bootstrap.venv_imports_pcbnew(python) == (False, "no output")


# --- the Freerouting jar --------------------------------------------------------------


def test_a_jar_that_is_already_there_is_used_and_nothing_is_downloaded(
    world: World,
) -> None:
    """Take the jar pcbkit's own folder has, and say where it is."""
    jar = put_jar(world.jar_dir)
    result = run_setup(world)
    assert (result.jar, result.downloaded) == (jar, False)
    assert f"Freerouting  {jar}" in world.said


def test_the_older_freerouting_folder_is_used_too(world: World) -> None:
    """Take a jar from ~/.local/share/freerouting, where the first setups put it."""
    jar = put_jar(world.machine.home / ".local" / "share" / "freerouting")
    assert run_setup(world).jar == jar


def test_freerouting_jar_names_the_jar_and_the_other_places_are_not_looked_at(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Use the jar FREEROUTING_JAR names, wherever it is."""
    put_jar(world.jar_dir, b"not a jar: and never looked at")
    mine = put_jar(tmp_path / "mine")
    monkeypatch.setenv("FREEROUTING_JAR", str(mine))
    assert run_setup(world).jar == mine


def test_freerouting_jar_that_does_not_exist_is_an_error_and_no_download(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never download somewhere else when FREEROUTING_JAR was set on purpose."""
    missing = tmp_path / "nowhere" / JAR
    monkeypatch.setenv("FREEROUTING_JAR", str(missing))
    error = fails(world, f"FREEROUTING_JAR={missing} does not exist")
    assert "Unset it" in error.message
    assert world.downloads == []
    assert not world.jar_dir.exists()


def test_a_damaged_jar_that_is_not_pcbkits_is_reported_and_left_alone(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Name a damaged jar in someone else's place, and do not overwrite it."""
    mine = put_jar(tmp_path / "mine", b"PK\x03\x04 cut off")
    monkeypatch.setenv("FREEROUTING_JAR", str(mine))
    error = fails(world, f"{mine} is not a valid jar")
    assert "Delete it" in error.message
    assert mine.read_bytes() == b"PK\x03\x04 cut off"


def test_a_damaged_jar_in_the_older_folder_is_reported_not_replaced(
    world: World,
) -> None:
    """Leave ~/.local/share/freerouting alone: pcbkit did not put the file there."""
    old = put_jar(world.machine.home / ".local" / "share" / "freerouting", b"junk")
    fails(world, "is not a valid jar")
    assert old.read_bytes() == b"junk"
    assert world.downloads == []


def test_a_damaged_jar_in_pcbkits_own_folder_is_downloaded_again(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replace a bad jar in the folder pcbkit downloads to."""
    jar = put_jar(world.jar_dir, b"junk")
    serve(monkeypatch, world, GOOD_JAR)
    result = run_setup(world)
    assert (result.jar, result.downloaded) == (jar, True)
    assert jar.read_bytes() == GOOD_JAR
    assert any("is damaged: downloading it again" in line for line in world.said)


def test_without_a_jar_setup_downloads_the_release_into_pcbkits_folder(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fetch Freerouting 1.9.0 from its GitHub release, check it, put it in place."""
    serve(monkeypatch, world, GOOD_JAR)
    result = run_setup(world)
    expected = world.jar_dir / JAR
    assert world.downloads == [bootstrap.FREEROUTING_URL]
    assert bootstrap.FREEROUTING_URL == (
        "https://github.com/freerouting/freerouting/releases/download/"
        "v1.9.0/freerouting-1.9.0.jar"
    )
    assert (result.jar, result.downloaded) == (expected, True)
    assert expected.read_bytes() == GOOD_JAR
    assert expected.stat().st_size == 5044336
    assert [p.name for p in world.jar_dir.iterdir()] == [JAR]
    assert any("downloading 1.9.0 (5.0 MB)" in line for line in world.said)


@pytest.mark.parametrize(
    "data",
    [b"", GOOD_JAR[:1], small_zip(), GOOD_JAR[:-1]],
    ids=["nothing", "one byte", "a small valid zip", "one byte short"],
)
def test_a_short_download_is_rejected_and_leaves_no_jar_behind(
    world: World, monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    """Refuse anything under 5044336 bytes, even a valid zip, and keep nothing."""
    serve(monkeypatch, world, data)
    error = fails(world, f"the Freerouting download was {len(data)} bytes")
    assert "should be 5044336" in error.message and "cut short" in error.message
    assert bootstrap.FREEROUTING_URL in error.message
    assert str(world.jar_dir / JAR) in error.message  # where to save it by hand
    assert list(world.jar_dir.iterdir()) == []


def test_a_download_that_is_too_long_is_rejected_too(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse one byte more as well, so it cannot be a different file."""
    serve(monkeypatch, world, GOOD_JAR + b"\0")
    error = fails(world, "bytes and should be 5044336")
    assert "longer than that" in error.message
    assert list(world.jar_dir.iterdir()) == []


def test_a_download_that_never_ends_is_cut_off(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop reading once there is more than the file's size, and not buffer forever."""
    reads = []

    class Endless:
        def read(self, n: int) -> bytes:
            reads.append(n)
            return b"\0" * n

        def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap, "_open", lambda url: Endless())
    fails(world, "longer than that")
    assert len(reads) <= bootstrap.FREEROUTING_BYTES // bootstrap.CHUNK + 2
    assert list(world.jar_dir.iterdir()) == []


def test_a_download_of_the_right_size_that_is_not_a_zip_is_rejected(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check the content as well as the length: an error page can be the right size."""
    serve(monkeypatch, world, b"\0" * bootstrap.FREEROUTING_BYTES)
    fails(world, "is not a jar")
    assert list(world.jar_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (urllib.error.URLError("no route to host"), "no route to host"),
        (
            urllib.error.HTTPError(
                bootstrap.FREEROUTING_URL,
                404,
                "Not Found",
                {},
                None,  # type: ignore[arg-type]
            ),
            "Not Found",
        ),
        (TimeoutError("timed out"), "timed out"),
    ],
    ids=["no network", "404", "timeout"],
)
def test_a_download_that_fails_says_why_and_how_to_do_it_by_hand(
    world: World, monkeypatch: pytest.MonkeyPatch, error: Exception, said: str
) -> None:
    """Turn a network error into a message with the URL and the file's place."""
    serve(monkeypatch, world, error)
    caught = fails(world, "could not download Freerouting from")
    assert said in caught.message
    assert bootstrap.FREEROUTING_URL in caught.message
    assert f"save it as {world.jar_dir / JAR}" in caught.message
    assert list(world.jar_dir.iterdir()) == []


def test_a_connection_that_drops_part_way_leaves_no_part_file(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove the .part file when the read fails after some bytes arrived."""

    class Drops:
        sent = False

        def read(self, n: int) -> bytes:
            if self.sent:
                raise ConnectionResetError(54, "Connection reset by peer")
            self.sent = True
            return b"PK" * 1000

        def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap, "_open", lambda url: Drops())
    fails(world, "Connection reset by peer")
    assert list(world.jar_dir.iterdir()) == []


def test_a_folder_that_cannot_be_made_is_reported_not_raised(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Say so when the jar's folder cannot be created, as a download failure."""
    serve(monkeypatch, world, GOOD_JAR)
    (world.machine.home / ".local").mkdir()
    (world.machine.home / ".local" / "share").write_text("a file", encoding="utf-8")
    fails(world, "could not download Freerouting")


def test_fetch_jar_takes_the_size_it_is_given(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Accept exactly the stated size, so a different release can state its own."""
    data = small_zip()
    serve(monkeypatch, world, data)
    dest = tmp_path / "jars" / "x.jar"
    bootstrap.fetch_jar("https://example.invalid/x.jar", dest, size=len(data))
    assert dest.read_bytes() == data and list(dest.parent.iterdir()) == [dest]
    with pytest.raises(SetupError, match="should be 5044336"):
        bootstrap.fetch_jar("https://example.invalid/x.jar", dest.with_name("y.jar"))


# --- java -----------------------------------------------------------------------------


def test_a_missing_java_is_a_warning_with_the_fix_and_setup_still_succeeds(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report Java missing, how to install it, and finish: only routing needs it."""
    put_jar(world.jar_dir)
    monkeypatch.delenv("JAVA_HOME")
    result = run_setup(world)
    assert result.java is None
    line = next(x for x in world.said if x.startswith("Java"))
    assert "needs 17 or newer" in line and "brew install openjdk@21" in line


def test_a_java_that_is_too_old_counts_as_missing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treat Java 11 as no Java, since Freerouting will not run on it."""
    put_jar(world.jar_dir)
    java = world.machine.home / "jdk" / "bin" / "java"
    world.machine.exe(java, output='openjdk version "11.0.2" 2019-01-15')
    assert run_setup(world).java is None


# --- the machine boundary -------------------------------------------------------------


def test_call_runs_a_command_with_no_input_and_without_virtual_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run in the given folder, with no stdin and no VIRTUAL_ENV; return the code."""
    seen: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> Any:
        seen["args"], seen["kwargs"] = args, kwargs
        return SimpleNamespace(returncode=3)

    monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
    monkeypatch.setenv("KEEP_ME", "yes")
    monkeypatch.setattr(subprocess, "run", run)
    code = bootstrap._call(["uv", "sync"], tmp_path)
    assert code == 3
    assert seen["args"] == ["uv", "sync"]
    kwargs = seen["kwargs"]
    assert kwargs["cwd"] == tmp_path and kwargs["stdin"] == subprocess.DEVNULL
    assert "VIRTUAL_ENV" not in kwargs["env"] and kwargs["env"]["KEEP_ME"] == "yes"
    assert "stdout" not in kwargs and "capture_output" not in kwargs  # on the terminal


def test_call_reports_a_command_that_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Turn a missing executable into a message."""

    def run(args: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SetupError, match="could not run uv: No such file"):
        bootstrap._call(["uv", "sync"], tmp_path)


def test_open_asks_for_the_url_with_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass the URL and a time limit to urlopen, so a stalled download ends."""
    seen: list[Any] = []

    def urlopen(url: str, timeout: float) -> str:
        seen.append((url, timeout))
        return "response"

    monkeypatch.setattr(bootstrap, "urlopen", urlopen)
    assert bootstrap._open("https://example.invalid/x") == "response"
    assert seen == [("https://example.invalid/x", bootstrap.DOWNLOAD_SECONDS)]


def test_the_jar_folder_is_under_the_home_the_machine_has(world: World) -> None:
    """Keep the jar in ~/.local/share/pcbkit of whoever is running."""
    assert bootstrap.jar_home() == world.machine.home / ".local" / "share" / "pcbkit"


# --- what it tells the user to type ---------------------------------------------------


@pytest.mark.parametrize("where", ["root", "parent", "inside", "elsewhere"])
def test_the_next_commands_say_where_to_run_them(world: World, where: str) -> None:
    """Add a `cd` to the project unless the user is already in it."""
    put_jar(world.jar_dir)
    result = run_setup(world)
    inside = world.root / "checks"
    here = {
        "root": world.root,
        "parent": world.root.parent,
        "inside": inside,
        "elsewhere": world.machine.root,
    }[where]
    prefix = {
        "root": "",
        "parent": "cd my-board && ",
        "inside": "cd .. && ",
        "elsewhere": "cd ../my-board && ",
    }[where]
    inside.mkdir()
    lines = bootstrap.format_next(result, here).splitlines()
    assert lines[:2] == ["", "Ready. Next:"]
    assert lines[2:] == [
        f"  {prefix}.venv/bin/pcbkit {name}"
        for name in ("doctor", "build", "route", "promote", "finalize", "check")
    ]


# --- the command ----------------------------------------------------------------------


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, *args: str) -> Result:
    """Run `pcbkit ...` from inside the world's project."""
    monkeypatch.chdir(world.root)
    return CliRunner().invoke(cli, list(args))


def test_the_command_runs_setup_in_the_project_and_prints_the_next_steps(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 0 with the board's title, each step, and the commands to run next."""
    put_jar(world.jar_dir)
    result = invoke(world, monkeypatch, "setup")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"pcbkit setup: My Board in {world.root.resolve()}"
    assert "Ready. Next:" in lines
    assert "  .venv/bin/pcbkit build" in lines
    assert [args[1] for args, _ in world.calls] == ["venv", "sync"]


def test_the_command_finds_the_project_from_a_folder_inside_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run in the project's root whatever folder pcbkit was started in."""
    put_jar(world.jar_dir)
    inside = world.root / "checks"
    inside.mkdir()
    monkeypatch.chdir(inside)
    result = CliRunner().invoke(cli, ["setup"])
    assert result.exit_code == 0, result.output
    assert world.calls and all(
        cwd.resolve() == world.root.resolve() for _, cwd in world.calls
    )
    assert "  cd .. && .venv/bin/pcbkit build" in result.output.splitlines()


def test_the_command_outside_a_project_says_how_to_start_one(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Report the missing pcbkit.toml, with `pcbkit new`, and run nothing."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = CliRunner().invoke(cli, ["setup"])
    assert result.exit_code == 1
    assert "pcbkit.toml" in result.output and "pcbkit new" in result.output
    assert world.calls == []


@pytest.mark.parametrize(
    ("break_it", "text"),
    [
        (lambda w, m: w.machine.path_tools.pop("uv"), "uv is not installed"),
        (lambda w, m: setattr(w, "uv_sync_code", 1), "`uv sync` failed"),
        (lambda w, m: m.setenv("FREEROUTING_JAR", "/nowhere"), "does not exist"),
    ],
    ids=["no uv", "sync fails", "bad jar variable"],
)
def test_the_command_turns_every_failure_into_an_error_line_and_exit_1(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    break_it: Callable[[World, pytest.MonkeyPatch], object],
    text: str,
) -> None:
    """Print `Error: ...` and exit 1: never a traceback."""
    break_it(world, monkeypatch)
    result = invoke(world, monkeypatch, "setup")
    assert result.exit_code == 1
    assert result.output.rstrip().splitlines()[-1].startswith("Error: ")
    assert text in result.output
    assert "Traceback" not in result.output


def test_the_help_explains_what_setup_does_and_where_the_jar_goes() -> None:
    """Say, in --help, what the command builds, checks and downloads."""
    output = CliRunner().invoke(cli, ["setup", "--help"]).output
    for text in (
        ".venv",
        "KiCad's own Python",
        "uv sync",
        "import pcbnew",
        "FREEROUTING_JAR",
        "~/.local/share/pcbkit",
        "pcbkit doctor",
    ):
        assert text in output, text
