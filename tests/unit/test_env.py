"""Unit tests for pcbkit.kicad.env, against a fake machine (see fake_machine.py)."""

from __future__ import annotations

import plistlib
import subprocess
import sys
import textwrap
import types
import zipfile
from pathlib import Path
from typing import Any

import click
import pytest

from pcbkit.kicad import env
from pcbkit.kicad.env import KicadPython, Run, Tool
from tests.fake_machine import FakeMachine

# The real functions, captured before the autouse fixture replaces them on the module.
REAL_RUN = env._run
REAL_WHICH = env._which
REAL_HOST_OS = env.host_os

PROBE_OK = "pcbkit-probe 3.9.13 10.0.6\n"
NO_PCBNEW = (
    "Traceback (most recent call last):\n"
    '  File "<string>", line 1, in <module>\n'
    "ModuleNotFoundError: No module named 'pcbnew'\n"
)
JAVA_8 = 'java version "1.8.0_292"\nJava(TM) SE Runtime Environment\n'
JAVA_11 = 'openjdk version "11.0.2" 2019-01-15\nOpenJDK Runtime Environment\n'
JAVA_17 = 'openjdk version "17.0.20.1" 2026-08-18\nOpenJDK Runtime Environment\n'
JAVA_21 = 'openjdk version "21.0.11" 2026-10-20\nOpenJDK Runtime Environment\n'
JAVA_STUB = "The operation couldn't be completed. Unable to locate a Java Runtime.\n"


def kicad_python_in_app(machine: FakeMachine) -> Path:
    """Return where KiCad.app keeps its Python on macOS."""
    bundled = machine.mac_app / "Contents" / "Frameworks" / "Python.framework"
    return bundled / "Versions" / "Current" / "bin" / "python3"


def keg_java(machine: FakeMachine, keg: str, prefix: str = "arm") -> Path:
    """Return where a Homebrew keg-only JDK keeps java."""
    base = machine.brew_arm if prefix == "arm" else machine.brew_intel
    return base / "opt" / keg / "bin" / "java"


# --- no work at import time -------------------------------------------------------

GUARD = textwrap.dedent(
    """
    import os, pathlib, shutil, subprocess
    import click, plistlib, zipfile, pcbkit  # everything env imports, loaded first

    def refuse(*args, **kwargs):
        raise AssertionError("pcbkit.kicad.env touched the machine while importing")

    # Path.is_dir() reaches the disk through os.path on new Pythons and through
    # Path.stat on old ones, so cut off every route.
    os.path.isdir = os.path.isfile = os.path.exists = refuse
    pathlib.Path.stat = pathlib.Path.is_dir = refuse
    pathlib.Path.is_file = pathlib.Path.exists = refuse
    subprocess.Popen = shutil.which = refuse
    import pcbkit.kicad.env
    """
)


def test_importing_env_does_not_touch_the_machine(tmp_path: Path) -> None:
    """Import env in a child where any file test, PATH lookup or command raises.

    A module that looks for KiCad while it is being imported would break every command,
    doctor included, on a machine that has no KiCad.
    """
    done = subprocess.run(
        [sys.executable, "-c", GUARD],
        env={"PATH": "", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr


# --- the machine ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", "macos"), ("linux", "linux"), ("linux2", "linux"), ("win32", "win32")],
)
def test_host_os_names_the_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: str
) -> None:
    """Map sys.platform to the names pcbkit uses."""
    monkeypatch.setattr(sys, "platform", platform)
    assert REAL_HOST_OS() == expected


def test_which_looks_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delegate to shutil.which."""
    monkeypatch.setattr(env.shutil, "which", lambda name: f"/bin/{name}")
    assert REAL_WHICH("kicad-cli") == "/bin/kicad-cli"


def test_run_returns_output_and_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run with no stdin, stderr folded into stdout, and a time limit."""
    seen: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, 3, stdout="out\n")

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    run = REAL_RUN(["tool", "--version"], timeout=5.0)
    assert run == Run(3, "out\n")
    assert seen["args"] == ["tool", "--version"]
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["stderr"] == subprocess.STDOUT
    assert seen["timeout"] == 5.0


def test_run_reports_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn a timeout into an error, not an exception."""

    def fake_run(args: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    run = REAL_RUN(["tool"], timeout=5.0)
    assert run == Run(-1, "", "timed out after 5 s")


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (
            FileNotFoundError(2, "No such file or directory"),
            "No such file or directory",
        ),
        (PermissionError(13, "Permission denied"), "Permission denied"),
        (OSError("exec format error"), "exec format error"),
    ],
)
def test_run_reports_a_command_that_cannot_start(
    monkeypatch: pytest.MonkeyPatch, raised: OSError, message: str
) -> None:
    """Turn an OSError into an error, not an exception."""

    def fake_run(args: list[str], **kwargs: Any) -> None:
        raise raised

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    run = REAL_RUN(["tool"])
    assert run.returncode == -1
    assert run.error == message


# --- versions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10.0.6", (10, 0, 6)),
        ("9.0.9\n", (9, 0, 9)),
        ("(10.0.0-rc1)", (10, 0, 0)),
        ("kicad 11.2", (11, 2)),
        ("nightly", ()),
        ("", ()),
    ],
)
def test_version_tuple(text: str, expected: tuple[int, ...]) -> None:
    """Read the first dotted version out of free text."""
    assert env.version_tuple(text) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("10.0.6", True),
        ("10.0.0", True),
        ("11.2", True),
        ("9.0.9", False),
        ("9.99.0", False),
        ("", True),
        ("nightly", True),
    ],
)
def test_at_least_the_kicad_floor(version: str, expected: bool) -> None:
    """Compare against 10.0, letting a version it cannot read through."""
    assert env.at_least(version, env.MIN_KICAD) is expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("17.0.20.1", 17),
        ("21.0.11", 21),
        ("21-ea", 21),
        ("17", 17),
        ("11.0.2", 11),
        ("1.8.0_292", 8),
        ("9", 9),
        ("1", 1),
        ("", None),
        ("garbage", None),
    ],
)
def test_java_major(version: str, expected: int | None) -> None:
    """Read the major version, treating old "1.x" numbers as x."""
    assert env.java_major(version) == expected


# --- KiCad's libraries ------------------------------------------------------------


def test_share_uses_the_kicad_share_override(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prefer KICAD_SHARE over a KiCad that is installed in the usual place."""
    (machine.linux_share / "footprints").mkdir(parents=True)
    custom = tmp_path / "custom"
    (custom / "footprints").mkdir(parents=True)
    monkeypatch.setenv("KICAD_SHARE", str(custom))
    assert env.find_share() == custom
    assert env.share_dir() == custom


def test_share_override_expands_a_tilde(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expand ~ in KICAD_SHARE the way a shell would."""
    (machine.home / "kicad-share" / "footprints").mkdir(parents=True)
    monkeypatch.setenv("KICAD_SHARE", "~/kicad-share")
    assert env.find_share() == machine.home / "kicad-share"


def test_a_wrong_override_is_reported_not_replaced(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail on a KICAD_SHARE without footprints/ even if another KiCad is installed."""
    (machine.linux_share / "footprints").mkdir(parents=True)
    monkeypatch.setenv("KICAD_SHARE", str(tmp_path / "empty"))
    assert env.find_share() is None
    with pytest.raises(click.ClickException) as err:
        env.share_dir()
    wanted = f"KICAD_SHARE={tmp_path / 'empty'} has no footprints/ folder"
    assert wanted in err.value.message


def test_share_looks_in_linux_then_macos_locations(machine: FakeMachine) -> None:
    """Look in /usr/share/kicad first, then in KiCad.app's SharedSupport."""
    mac_share = machine.mac_app / "Contents" / "SharedSupport"
    (mac_share / "footprints").mkdir(parents=True)
    assert env.find_share() == mac_share
    (machine.linux_share / "footprints").mkdir(parents=True)
    assert env.find_share() == machine.linux_share


def test_share_needs_a_footprints_folder(machine: FakeMachine) -> None:
    """Ignore a share folder that has no footprints/."""
    machine.linux_share.mkdir(parents=True)
    assert env.find_share() is None


def test_missing_share_names_the_places_and_the_override(machine: FakeMachine) -> None:
    """Say where it looked and how to point it elsewhere."""
    with pytest.raises(click.ClickException) as err:
        env.share_dir()
    assert str(machine.linux_share) in err.value.message
    assert str(machine.mac_app / "Contents" / "SharedSupport") in err.value.message
    assert "set KICAD_SHARE" in err.value.message


def test_symbols_footprints_and_models_live_under_the_share(
    machine: FakeMachine,
) -> None:
    """Return the three stock library folders."""
    (machine.linux_share / "footprints").mkdir(parents=True)
    assert env.symbols_dir() == machine.linux_share / "symbols"
    assert env.footprints_dir() == machine.linux_share / "footprints"
    assert env.models_dir() == machine.linux_share / "3dmodels"


# --- KiCad.app --------------------------------------------------------------------


def test_kicad_app_version_comes_from_its_plist(machine: FakeMachine) -> None:
    """Read CFBundleShortVersionString on macOS."""
    machine.os_name = "macos"
    contents = machine.mac_app / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {"CFBundleShortVersionString": "10.0.6", "CFBundleName": "KiCad"}
        )
    )
    assert env.find_kicad_app() == Tool(str(machine.mac_app), "10.0.6")


@pytest.mark.parametrize(
    "plist",
    [None, b"this is not a plist", b"<?xml version='1.0'?><plist><dict>"],
    ids=["no-plist", "garbage", "truncated-xml"],
)
def test_kicad_app_without_a_readable_plist_has_no_version(
    machine: FakeMachine, plist: bytes | None
) -> None:
    """Still find the app when its version cannot be read."""
    machine.os_name = "macos"
    contents = machine.mac_app / "Contents"
    contents.mkdir(parents=True)
    if plist is not None:
        (contents / "Info.plist").write_bytes(plist)
    assert env.find_kicad_app() == Tool(str(machine.mac_app), "")


def test_kicad_app_plist_that_is_not_a_dict_has_no_version(
    machine: FakeMachine,
) -> None:
    """Ignore a plist whose top level is not a dictionary."""
    machine.os_name = "macos"
    contents = machine.mac_app / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(["10.0.6"]))
    assert env.find_kicad_app() == Tool(str(machine.mac_app), "")


def test_kicad_app_is_none_when_not_installed(machine: FakeMachine) -> None:
    """Report nothing when KiCad.app is absent."""
    machine.os_name = "macos"
    assert env.find_kicad_app() is None


def test_kicad_app_is_a_macos_idea(machine: FakeMachine) -> None:
    """Return None on Linux even if a folder is there."""
    (machine.mac_app / "Contents").mkdir(parents=True)
    machine.os_name = "linux"
    assert env.find_kicad_app() is None


# --- kicad-cli --------------------------------------------------------------------


def test_kicad_cli_on_path_wins(machine: FakeMachine) -> None:
    """Prefer the kicad-cli on PATH over the one in KiCad.app."""
    on_path = machine.exe(
        machine.root / "bin" / "kicad-cli", "10.0.6\n", on_path="kicad-cli"
    )
    machine.exe(machine.mac_app / "Contents/MacOS/kicad-cli", "9.0.9\n")
    assert env.find_kicad_cli() == Tool(str(on_path), "10.0.6")
    assert machine.calls == [[str(on_path), "--version"]]


def test_kicad_cli_falls_back_to_the_app_then_usr_bin(machine: FakeMachine) -> None:
    """Look in KiCad.app, then /usr/bin, when PATH has none."""
    in_usr = machine.exe(machine.usr_bin / "kicad-cli", "10.0.1\n")
    assert env.find_kicad_cli() == Tool(str(in_usr), "10.0.1")
    in_app = machine.exe(machine.mac_app / "Contents/MacOS/kicad-cli", "10.0.6\n")
    assert env.find_kicad_cli() == Tool(str(in_app), "10.0.6")


def test_an_old_kicad_cli_on_path_gives_way_to_a_supported_one(
    machine: FakeMachine,
) -> None:
    """Prefer a kicad-cli of 10.0 or newer over an older one found first."""
    machine.exe(machine.root / "bin" / "kicad-cli", "9.0.9\n", on_path="kicad-cli")
    in_app = machine.exe(machine.mac_app / "Contents/MacOS/kicad-cli", "10.0.6\n")
    assert env.find_kicad_cli() == Tool(str(in_app), "10.0.6")


def test_only_an_old_kicad_cli_is_returned_so_doctor_can_report_it(
    machine: FakeMachine,
) -> None:
    """Return the first old kicad-cli rather than None when there is nothing newer."""
    old = machine.exe(
        machine.root / "bin" / "kicad-cli", "9.0.9\n", on_path="kicad-cli"
    )
    machine.exe(machine.usr_bin / "kicad-cli", "8.0.1\n")
    assert env.find_kicad_cli() == Tool(str(old), "9.0.9")


def test_kicad_cli_missing_is_none(machine: FakeMachine) -> None:
    """Return None when there is no kicad-cli anywhere."""
    assert env.find_kicad_cli() is None
    assert machine.calls == []


def test_kicad_cli_with_unreadable_version_is_still_found(machine: FakeMachine) -> None:
    """Report the path even if --version prints nothing useful."""
    path = machine.exe(machine.usr_bin / "kicad-cli", "unknown build\n")
    assert env.find_kicad_cli() == Tool(str(path), "")


# --- KiCad's Python ---------------------------------------------------------------


def test_macos_uses_the_python_bundled_in_the_app(machine: FakeMachine) -> None:
    """Probe the interpreter inside KiCad.app and parse its answer."""
    machine.os_name = "macos"
    py = machine.exe(kicad_python_in_app(machine), PROBE_OK)
    found, reasons = env.probe_kicad_python()
    assert found == KicadPython(str(py), "3.9.13", "10.0.6")
    assert reasons == []
    assert env.find_kicad_python() == found
    command = machine.calls[0]
    assert command[:3] == [str(py), "-I", "-c"]
    assert "import sys, pcbnew" in command[3]


def test_probe_finds_its_line_among_noise(machine: FakeMachine) -> None:
    """Skip the debug chatter pcbnew prints on import."""
    machine.os_name = "macos"
    noisy = "Debug: something\n\nwarning: other\n" + PROBE_OK + "Debug: trailing\n"
    machine.exe(kicad_python_in_app(machine), noisy)
    found, _ = env.probe_kicad_python()
    assert found is not None
    assert found.pcbnew_version == "10.0.6"


def test_probe_keeps_a_build_string_with_spaces(machine: FakeMachine) -> None:
    """Keep what pcbnew reports when it is more than one word."""
    machine.os_name = "macos"
    machine.exe(
        kicad_python_in_app(machine),
        "pcbkit-probe 3.9.13 (10.0.0-rc1, release build)\n",
    )
    found, _ = env.probe_kicad_python()
    assert found is not None
    assert found.pcbnew_version == "10.0.0-rc1, release build"


def test_probe_accepts_an_interpreter_that_exits_badly_after_importing(
    machine: FakeMachine,
) -> None:
    """Trust the probe line: it is only printed once pcbnew has imported."""
    machine.os_name = "macos"
    machine.exe(kicad_python_in_app(machine), PROBE_OK, returncode=139)
    found, _ = env.probe_kicad_python()
    assert found is not None


def test_an_interpreter_without_pcbnew_is_reported_with_its_reason(
    machine: FakeMachine,
) -> None:
    """Say why the interpreter that exists did not qualify."""
    machine.os_name = "macos"
    py = machine.exe(kicad_python_in_app(machine), NO_PCBNEW, returncode=1)
    found, reasons = env.probe_kicad_python()
    assert found is None
    assert reasons == [f"{py}: ModuleNotFoundError: No module named 'pcbnew'"]
    assert env.find_kicad_python() is None


def test_an_interpreter_that_cannot_run_is_reported_with_its_error(
    machine: FakeMachine,
) -> None:
    """Report a timeout or exec failure as the reason."""
    machine.os_name = "macos"
    py = machine.exe(kicad_python_in_app(machine), error="timed out after 60 s")
    found, reasons = env.probe_kicad_python()
    assert found is None
    assert reasons == [f"{py}: timed out after 60 s"]


def test_an_interpreter_with_no_output_is_reported(machine: FakeMachine) -> None:
    """Report silence as "no output"."""
    machine.os_name = "macos"
    py = machine.exe(kicad_python_in_app(machine), "", returncode=1)
    assert env.probe_kicad_python() == (None, [f"{py}: no output"])


def test_no_interpreter_at_all(machine: FakeMachine) -> None:
    """Return nothing, and no reasons, when KiCad's Python is not installed."""
    machine.os_name = "macos"
    assert env.probe_kicad_python() == (None, [])
    assert machine.calls == []


def test_macos_does_not_try_pythons_from_linux_places(machine: FakeMachine) -> None:
    """Probe only KiCad.app's interpreter on macOS."""
    machine.os_name = "macos"
    machine.exe(machine.usr_bin / "python3", PROBE_OK, on_path="python3")
    assert env.probe_kicad_python() == (None, [])


def test_linux_tries_python3_on_path_then_usr_bin(machine: FakeMachine) -> None:
    """Take the first python3 that can import pcbnew."""
    machine.os_name = "linux"
    on_path = machine.exe(
        machine.root / "venv" / "bin" / "python3", NO_PCBNEW, 1, on_path="python3"
    )
    system = machine.exe(machine.usr_bin / "python3", PROBE_OK)
    found, reasons = env.probe_kicad_python()
    assert found == KicadPython(str(system), "3.9.13", "10.0.6")
    assert reasons == [f"{on_path}: ModuleNotFoundError: No module named 'pcbnew'"]


def test_linux_probes_a_python_that_is_in_both_places_once(
    machine: FakeMachine,
) -> None:
    """Skip a duplicate candidate."""
    machine.os_name = "linux"
    machine.exe(machine.usr_bin / "python3", PROBE_OK, on_path="python3")
    env.probe_kicad_python()
    assert len(machine.calls) == 1


def test_linux_does_not_look_inside_a_macos_app(machine: FakeMachine) -> None:
    """Ignore KiCad.app's interpreter on Linux."""
    machine.os_name = "linux"
    machine.exe(kicad_python_in_app(machine), PROBE_OK)
    assert env.probe_kicad_python() == (None, [])


# --- pcbnew in this interpreter ---------------------------------------------------


def test_pcbnew_importable_when_the_module_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass require_pcbnew when ``import pcbnew`` works."""
    monkeypatch.setitem(sys.modules, "pcbnew", types.ModuleType("pcbnew"))
    assert env.pcbnew_importable() is True
    env.require_pcbnew()


def test_require_pcbnew_says_to_run_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise the agreed message, not a traceback, when pcbnew cannot be imported."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    assert env.pcbnew_importable() is False
    with pytest.raises(click.ClickException) as err:
        env.require_pcbnew()
    assert err.value.message == (
        "pcbnew isn't importable here. In the board project, run: pcbkit setup"
    )
    assert err.value.exit_code == 1


# --- Java -------------------------------------------------------------------------


def test_java_on_path_that_is_new_enough_is_used(machine: FakeMachine) -> None:
    """Return the java on PATH when it is 17 or newer."""
    java = machine.exe(machine.root / "jdk" / "bin" / "java", JAVA_17, on_path="java")
    assert env.find_java() == Tool(str(java), "17.0.20.1")
    assert machine.calls == [[str(java), "-version"]]


def test_a_keg_only_homebrew_java_is_found_when_not_on_path(
    machine: FakeMachine,
) -> None:
    """Find `brew install openjdk@21` without a PATH or symlink step."""
    keg = machine.exe(keg_java(machine, "openjdk@21"), JAVA_21)
    assert env.find_java() == Tool(str(keg), "21.0.11")


def test_intel_homebrew_prefix_is_searched_too(machine: FakeMachine) -> None:
    """Look under /usr/local/opt as well as /opt/homebrew/opt."""
    keg = machine.exe(keg_java(machine, "openjdk@21", prefix="intel"), JAVA_21)
    assert env.find_java() == Tool(str(keg), "21.0.11")


def test_an_old_java_on_path_is_skipped_for_a_new_enough_keg(
    machine: FakeMachine,
) -> None:
    """Prefer any Java >= 17 over an older one earlier in the search."""
    machine.exe(machine.root / "old" / "bin" / "java", JAVA_11, on_path="java")
    keg = machine.exe(keg_java(machine, "openjdk@21"), JAVA_21)
    assert env.find_java() == Tool(str(keg), "21.0.11")


@pytest.mark.parametrize(
    ("output", "version"), [(JAVA_11, "11.0.2"), (JAVA_8, "1.8.0_292")]
)
def test_only_an_old_java_is_returned_so_doctor_can_report_it(
    machine: FakeMachine, output: str, version: str
) -> None:
    """Return the too-old Java instead of None."""
    java = machine.exe(machine.root / "old" / "bin" / "java", output, on_path="java")
    assert env.find_java() == Tool(str(java), version)
    assert (env.java_major(version) or 0) < env.MIN_JAVA


def test_java_home_is_tried_before_path(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Honour JAVA_HOME ahead of the java on PATH."""
    home = machine.root / "jvm" / "home"
    chosen = machine.exe(home / "bin" / "java", JAVA_21)
    machine.exe(machine.root / "other" / "bin" / "java", JAVA_17, on_path="java")
    monkeypatch.setenv("JAVA_HOME", str(home))
    assert env.find_java() == Tool(str(chosen), "21.0.11")


def test_a_java_found_twice_is_run_once(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip a duplicate candidate (JAVA_HOME and PATH pointing at one file)."""
    home = machine.root / "jvm"
    machine.exe(home / "bin" / "java", JAVA_17, on_path="java")
    monkeypatch.setenv("JAVA_HOME", str(home))
    env.find_java()
    assert len(machine.calls) == 1


def test_keg_order_prefers_openjdk_21_over_17(machine: FakeMachine) -> None:
    """Try openjdk@21 before openjdk@17."""
    machine.exe(keg_java(machine, "openjdk@17"), JAVA_17)
    newer = machine.exe(keg_java(machine, "openjdk@21"), JAVA_21)
    assert env.find_java() == Tool(str(newer), "21.0.11")


def test_a_java_stub_that_is_not_java_is_skipped(machine: FakeMachine) -> None:
    """Ignore macOS's /usr/bin/java stub when no JDK is installed behind it."""
    machine.exe(machine.usr_bin / "java", JAVA_STUB, returncode=1, on_path="java")
    assert env.find_java() is None


def test_a_java_that_cannot_run_is_skipped(machine: FakeMachine) -> None:
    """Move on from a java that fails to start."""
    machine.exe(machine.usr_bin / "java", error="exec format error", on_path="java")
    keg = machine.exe(keg_java(machine, "openjdk"), JAVA_21)
    assert env.find_java() == Tool(str(keg), "21.0.11")


def test_no_java_is_none(machine: FakeMachine) -> None:
    """Return None when there is no Java."""
    assert env.find_java() is None


# --- Freerouting ------------------------------------------------------------------


def make_jar(path: Path) -> Path:
    """Write a real, tiny zip archive at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    return path


def test_freerouting_override_is_used(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use FREEROUTING_JAR, and read the version from a standard file name."""
    make_jar(machine.home / ".local/share/pcbkit/freerouting-1.9.0.jar")
    mine = make_jar(machine.root / "elsewhere" / "freerouting-2.0.1.jar")
    monkeypatch.setenv("FREEROUTING_JAR", str(mine))
    assert env.find_freerouting_jar() == Tool(str(mine), "2.0.1")


def test_freerouting_override_with_an_odd_name_has_no_version(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report an unknown version rather than guess."""
    mine = make_jar(machine.root / "elsewhere" / "router.jar")
    monkeypatch.setenv("FREEROUTING_JAR", str(mine))
    assert env.find_freerouting_jar() == Tool(str(mine), "")


def test_a_wrong_freerouting_override_is_not_replaced(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return None for a missing override even when a default jar exists."""
    make_jar(machine.home / ".local/share/pcbkit/freerouting-1.9.0.jar")
    monkeypatch.setenv("FREEROUTING_JAR", str(machine.root / "nope.jar"))
    assert env.find_freerouting_jar() is None


def test_freerouting_override_expands_a_tilde(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expand ~ in FREEROUTING_JAR the way a shell would."""
    jar = make_jar(machine.home / "jars" / "freerouting-1.9.0.jar")
    monkeypatch.setenv("FREEROUTING_JAR", "~/jars/freerouting-1.9.0.jar")
    assert env.find_freerouting_jar() == Tool(str(jar), "1.9.0")


def test_freerouting_looks_in_pcbkit_then_freerouting_folders(
    machine: FakeMachine,
) -> None:
    """Prefer ~/.local/share/pcbkit over ~/.local/share/freerouting."""
    share = machine.home / ".local" / "share"
    other = make_jar(share / "freerouting" / "freerouting-1.9.0.jar")
    assert env.find_freerouting_jar() == Tool(str(other), "1.9.0")
    ours = make_jar(share / "pcbkit" / "freerouting-1.9.0.jar")
    assert env.find_freerouting_jar() == Tool(str(ours), "1.9.0")


def test_no_freerouting_jar_is_none(machine: FakeMachine) -> None:
    """Return None when no jar is anywhere."""
    assert env.find_freerouting_jar() is None


def test_is_jar_tells_a_zip_from_a_truncated_download(tmp_path: Path) -> None:
    """Accept a real zip and refuse junk or a missing file."""
    good = make_jar(tmp_path / "good.jar")
    bad = tmp_path / "bad.jar"
    bad.write_bytes(b"PK\x03\x04 truncated")
    assert env.is_jar(str(good)) is True
    assert env.is_jar(str(bad)) is False
    assert env.is_jar(str(tmp_path / "missing.jar")) is False


# --- ngspice, rsvg-convert, uv ----------------------------------------------------

NGSPICE_BANNER = (
    "******\n** ngspice-47 : Circuit level simulation program\n"
    "** Compiled with KLU Direct Linear Solver\n** The U. C. Berkeley CAD Group\n"
)


@pytest.mark.parametrize(
    ("finder", "name", "output", "version"),
    [
        (env.find_ngspice, "ngspice", NGSPICE_BANNER, "47"),
        (
            env.find_rsvg_convert,
            "rsvg-convert",
            "rsvg-convert version 2.60.0\n",
            "2.60.0",
        ),
        (
            env.find_uv,
            "uv",
            "uv 0.12.15 (d35f1f270 2026-09-15 aarch64-apple-darwin)\n",
            "0.12.15",
        ),
    ],
)
def test_simple_tools_are_found_on_path_with_their_version(
    machine: FakeMachine, finder: Any, name: str, output: str, version: str
) -> None:
    """Find a tool on PATH and read its version from --version."""
    assert finder() is None
    path = machine.exe(machine.root / "bin" / name, output, on_path=name)
    assert finder() == Tool(str(path), version)
    assert machine.calls == [[str(path), "--version"]]


@pytest.mark.parametrize(
    ("finder", "name"),
    [
        (env.find_ngspice, "ngspice"),
        (env.find_rsvg_convert, "rsvg-convert"),
        (env.find_uv, "uv"),
    ],
)
def test_a_simple_tool_with_unreadable_version_is_still_found(
    machine: FakeMachine, finder: Any, name: str
) -> None:
    """Report the path even if the version text is not what was expected."""
    path = machine.exe(machine.root / "bin" / name, "???\n", on_path=name)
    assert finder() == Tool(str(path), "")
