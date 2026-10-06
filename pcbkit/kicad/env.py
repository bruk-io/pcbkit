"""Find KiCad, its Python, Java, Freerouting and the other tools pcbkit drives.

Nothing here runs at import time, and a ``find_*`` function returns None rather than
raising when it finds nothing, so ``pcbkit doctor`` works on a machine with no KiCad.
``share_dir`` (and its three siblings) and ``require_pcbnew`` raise, because their
callers cannot carry on without the answer.

Everything that touches the machine goes through ``_which``, ``_run``, ``host_os``,
``Path.home``, ``os.environ`` or the locations just below, so unit tests can replace
each one and never see the real KiCad.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.parsers.expat import ExpatError

import click

# Oldest versions pcbkit supports.
MIN_KICAD = (10, 0)
MIN_JAVA = 17

FREEROUTING_JAR_NAME = "freerouting-1.9.0.jar"

# Where the tools live on a stock install. Module-level so tests can point them at a
# temporary tree.
MAC_APP = Path("/Applications/KiCad/KiCad.app")
LINUX_SHARE = Path("/usr/share/kicad")
USR_BIN = Path("/usr/bin")
HOMEBREW_PREFIXES = (Path("/opt/homebrew"), Path("/usr/local"))  # Apple silicon, Intel
# Homebrew's JDKs are keg-only: installed, but not on PATH.
HOMEBREW_JAVA_KEGS = ("openjdk@21", "openjdk@17", "openjdk")

# Printed by the probe below so it can be found among any noise pcbnew writes.
_PROBE_TAG = "pcbkit-probe"
_PCBNEW_PROBE = (
    "import sys, pcbnew; "
    f"print('{_PROBE_TAG}', '%d.%d.%d' % sys.version_info[:3], "
    "pcbnew.GetBuildVersion())"
)


@dataclass(frozen=True)
class Tool:
    """A tool that was found: where it is and the version it reports ("" if unknown)."""

    path: str
    version: str = ""


@dataclass(frozen=True)
class KicadPython:
    """KiCad's own Python interpreter, proven to import pcbnew."""

    path: str
    python_version: str
    pcbnew_version: str


@dataclass(frozen=True)
class Run:
    """The outcome of a command: exit code, output, and why it could not run at all."""

    returncode: int
    output: str = ""
    error: str = ""


# --- the machine ------------------------------------------------------------------


def host_os() -> str:
    """Return "macos", "linux", or the raw ``sys.platform`` of any other system."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _which(name: str) -> str | None:
    """Return the path of ``name`` on PATH, or None."""
    return shutil.which(name)


def _run(args: Sequence[str], timeout: float = 20.0) -> Run:
    """Run a command with no stdin and a time limit; report a failure, don't raise it.

    Standard error is folded into the output because ``java -version`` writes there.
    """
    try:
        done = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return Run(-1, error=f"timed out after {timeout:g} s")
    except OSError as err:
        return Run(-1, error=err.strerror or str(err))
    return Run(done.returncode, done.stdout)


def _existing_files(paths: Sequence[str | None]) -> list[str]:
    """Return the distinct paths that name an existing file, in order."""
    found: list[str] = []
    for path in paths:
        if path and os.path.isfile(path) and path not in found:
            found.append(path)
    return found


# --- versions ---------------------------------------------------------------------


def version_tuple(text: str) -> tuple[int, ...]:
    """Return the first dotted version in ``text`` as ints, or () if there is none."""
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not found:
        return ()
    return tuple(int(part) for part in found.groups() if part is not None)


def at_least(version: str, minimum: tuple[int, ...]) -> bool:
    """Return True if ``version`` is at least ``minimum``; an unreadable one passes."""
    parsed = version_tuple(version)
    return not parsed or parsed[: len(minimum)] >= minimum


def java_major(version: str) -> int | None:
    """Return the major number of a Java version: "17.0.2", "21-ea" or "1.8.0_292"."""
    found = re.match(r"(\d+)(?:\.(\d+))?", version)
    if not found:
        return None
    major = int(found.group(1))
    if major == 1 and found.group(2) is not None:  # Java 8 and older call it 1.x
        return int(found.group(2))
    return major


# --- KiCad's libraries ------------------------------------------------------------


def find_share() -> Path | None:
    """Return KiCad's share directory (it holds symbols/, footprints/ and 3dmodels/).

    When KICAD_SHARE is set it is the only place looked at, so a wrong override is
    reported instead of being quietly replaced by a different KiCad.
    """
    override = os.environ.get("KICAD_SHARE")
    if override:
        candidates = [Path(override).expanduser()]
    else:
        candidates = [LINUX_SHARE, MAC_APP / "Contents" / "SharedSupport"]
    for path in candidates:
        if (path / "footprints").is_dir():
            return path
    return None


def share_dir() -> Path:
    """Return KiCad's share directory; raise a ClickException if there is none."""
    found = find_share()
    if found is not None:
        return found
    override = os.environ.get("KICAD_SHARE")
    if override:
        raise click.ClickException(f"KICAD_SHARE={override} has no footprints/ folder")
    tried = ", ".join(str(p) for p in (LINUX_SHARE, MAC_APP / "Contents/SharedSupport"))
    raise click.ClickException(f"KiCad libraries not found in {tried}: set KICAD_SHARE")


def symbols_dir() -> Path:
    """Return KiCad's stock symbol libraries."""
    return share_dir() / "symbols"


def footprints_dir() -> Path:
    """Return KiCad's stock footprint libraries."""
    return share_dir() / "footprints"


def models_dir() -> Path:
    """Return KiCad's stock 3D models."""
    return share_dir() / "3dmodels"


# --- KiCad itself -----------------------------------------------------------------


def find_kicad_app() -> Tool | None:
    """Return KiCad.app and its version on macOS; None elsewhere or if it is absent."""
    if host_os() != "macos" or not (MAC_APP / "Contents").is_dir():
        return None
    version = ""
    try:
        with open(MAC_APP / "Contents" / "Info.plist", "rb") as handle:
            info = plistlib.load(handle)
        if isinstance(info, dict):
            version = str(info.get("CFBundleShortVersionString", ""))
    except (OSError, ValueError, ExpatError):  # no or unreadable plist: version unknown
        pass
    return Tool(str(MAC_APP), version)


def find_kicad_cli() -> Tool | None:
    """Return a kicad-cli of at least MIN_KICAD; failing that, the first one found.

    The search order is PATH, then inside KiCad.app, then /usr/bin. A too-old one is
    still returned when nothing better exists, so ``pcbkit doctor`` can report it.
    """
    first: Tool | None = None
    for path in _existing_files(
        [
            _which("kicad-cli"),
            str(MAC_APP / "Contents" / "MacOS" / "kicad-cli"),
            str(USR_BIN / "kicad-cli"),
        ]
    ):
        match = re.search(r"\d+\.\d+(?:\.\d+)?", _run([path, "--version"]).output)
        tool = Tool(path, match.group(0) if match else "")
        if at_least(tool.version, MIN_KICAD):
            return tool
        first = first or tool
    return first


def _kicad_python_candidates() -> list[str]:
    """Return the interpreters that might be KiCad's Python, best first."""
    if host_os() == "macos":
        bundled = MAC_APP / "Contents/Frameworks/Python.framework/Versions/Current"
        return _existing_files([str(bundled / "bin" / "python3")])
    return _existing_files([_which("python3"), str(USR_BIN / "python3")])


def probe_kicad_python() -> tuple[KicadPython | None, list[str]]:
    """Return KiCad's Python, and why each candidate that did not qualify failed.

    A candidate qualifies when it can ``import pcbnew``. It is run in a child process
    in isolated mode (``-I``): pcbkit itself usually runs in another interpreter, and a
    stray pcbnew.py in the current folder or on PYTHONPATH must not count.
    """
    reasons: list[str] = []
    for path in _kicad_python_candidates():
        run = _run([path, "-I", "-c", _PCBNEW_PROBE], timeout=60.0)
        lines = [line.strip() for line in run.output.splitlines() if line.strip()]
        for line in lines:
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0] == _PROBE_TAG:
                return KicadPython(path, parts[1], parts[2].strip("()")), reasons
        reasons.append(f"{path}: {run.error or (lines[-1] if lines else 'no output')}")
    return None, reasons


def find_kicad_python() -> KicadPython | None:
    """Return KiCad's Python (the one that imports pcbnew), or None."""
    return probe_kicad_python()[0]


# --- pcbnew in this interpreter ---------------------------------------------------


def pcbnew_importable() -> bool:
    """Return True if ``import pcbnew`` works in the Python that is running pcbkit."""
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        return False
    return True


def require_pcbnew() -> None:
    """Raise a ClickException pointing at `pcbkit setup` unless pcbnew imports here.

    Every command that needs pcbnew (build, route, promote, finalize, check, mutants,
    compare, shots) calls this first, so a missing pcbnew is a message, not a traceback.
    """
    if not pcbnew_importable():
        raise click.ClickException(
            "pcbnew isn't importable here. In the board project, run: pcbkit setup"
        )


# --- Java and Freerouting ---------------------------------------------------------


def _java_candidates() -> list[str]:
    """Return the java executables that exist, best first.

    JAVA_HOME, then PATH, then Homebrew's keg-only JDKs, which `brew install
    openjdk@21` leaves off PATH.
    """
    paths: list[str | None] = []
    home = os.environ.get("JAVA_HOME")
    if home:
        paths.append(str(Path(home) / "bin" / "java"))
    paths.append(_which("java"))
    for prefix in HOMEBREW_PREFIXES:
        for keg in HOMEBREW_JAVA_KEGS:
            paths.append(str(prefix / "opt" / keg / "bin" / "java"))
    return _existing_files(paths)


def find_java() -> Tool | None:
    """Return a Java of at least MIN_JAVA; failing that, the first Java found.

    Returning a too-old Java lets ``pcbkit doctor`` say what it found. Callers that run
    Freerouting must use the returned path (a keg-only Java is not on PATH) and check
    its version with ``java_major``.
    """
    first: Tool | None = None
    for path in _java_candidates():
        match = re.search(r'version "([^"]+)"', _run([path, "-version"]).output)
        if not match:
            continue
        tool = Tool(path, match.group(1))
        major = java_major(tool.version)
        if major is not None and major >= MIN_JAVA:
            return tool
        first = first or tool
    return first


def find_freerouting_jar() -> Tool | None:
    """Return the Freerouting jar: FREEROUTING_JAR, else pcbkit's or its own folder.

    When FREEROUTING_JAR is set it is the only place looked at.
    """
    override = os.environ.get("FREEROUTING_JAR")
    if override:
        paths = [Path(override).expanduser()]
    else:
        share = Path.home() / ".local" / "share"
        paths = [
            share / "pcbkit" / FREEROUTING_JAR_NAME,
            share / "freerouting" / FREEROUTING_JAR_NAME,
        ]
    for path in paths:
        if path.is_file():
            match = re.search(r"freerouting-(\d+\.\d+\.\d+)", path.name)
            return Tool(str(path), match.group(1) if match else "")
    return None


def is_jar(path: str) -> bool:
    """Return True if ``path`` is a readable zip archive (a truncated jar is not)."""
    return zipfile.is_zipfile(path)


# --- other tools ------------------------------------------------------------------


def _find_tool(name: str, args: Sequence[str], pattern: str) -> Tool | None:
    """Return ``name`` from PATH with the first group of ``pattern`` as its version."""
    path = _which(name)
    if not path:
        return None
    match = re.search(pattern, _run([path, *args]).output)
    return Tool(path, match.group(1) if match else "")


def find_ngspice() -> Tool | None:
    """Return ngspice from PATH."""
    return _find_tool("ngspice", ["--version"], r"ngspice-(\d+)")


def find_rsvg_convert() -> Tool | None:
    """Return rsvg-convert (librsvg) from PATH."""
    return _find_tool("rsvg-convert", ["--version"], r"version (\S+)")


def find_uv() -> Tool | None:
    """Return uv from PATH."""
    return _find_tool("uv", ["--version"], r"uv (\S+)")
