"""``pcbkit setup``: make a board project ready to build, route and check.

pcbnew only imports under KiCad's own Python, which neither ``uvx`` nor ``uv run``
ever picks, so a board project gets its own ``.venv`` built on that interpreter. In
order, for the project at ``root``:

1. find uv and KiCad's Python (``pcbkit.kicad.env``), and the project's pyproject.toml;
2. make ``.venv`` with ``uv venv --python <KiCad's> --system-site-packages``, unless it
   exists already and imports pcbnew (then it is kept: ``uv venv`` refuses to replace
   a venv, and a second ``pcbkit setup`` must do no harm);
3. ``uv sync --python <KiCad's>`` installs the project's dependencies (pcbkit and
   pytest) into it. The interpreter is named again so that a ``.python-version`` file
   in this folder or above it cannot make uv rebuild the venv on another Python;
4. prove ``.venv/bin/python`` imports pcbnew, which is the point of all of it;
5. make sure the Freerouting 1.9.0 jar is there: one ``env.find_freerouting_jar`` finds,
   else a download to ``~/.local/share/pcbkit``, checked for size and for being a zip;
6. look for Java 17 or newer, and say if it is missing (the router needs it; setup
   itself does not).

Every way this can fail is a ``SetupError`` that says what failed and what to do about
it. The two things that touch the machine are ``env._run`` (a captured probe) and, here,
``_call`` (a command whose output goes to the terminal) and ``_open`` (the download),
so unit tests replace them.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.request import urlopen

import click

from pcbkit import doctor
from pcbkit.kicad import env

FREEROUTING_VERSION = "1.9.0"
FREEROUTING_URL = (
    "https://github.com/freerouting/freerouting/releases/download/"
    f"v{FREEROUTING_VERSION}/{env.FREEROUTING_JAR_NAME}"
)
# The size of the release's jar, to the byte: a download that comes to any other size
# was cut short, or is not that file.
FREEROUTING_BYTES = 5044336
DOWNLOAD_SECONDS = 60
CHUNK = 1 << 16

VENV = ".venv"

# What the project's interpreter is asked to run, and the word its answer starts with,
# so that it can be found among any noise pcbnew writes on the way in.
PROBE_TAG = "pcbkit-setup"
PROBE = f"import pcbnew; print('{PROBE_TAG}', pcbnew.GetBuildVersion())"


class SetupError(click.ClickException):
    """Say what failed in `pcbkit setup`, and what to do about it."""


@dataclass(frozen=True)
class SetupResult:
    """What ``setup_project`` left behind.

    ``java`` is None when no Java of version 17 or newer was found (setup still
    succeeds: only routing needs it). ``downloaded`` says whether the jar was fetched
    now rather than found.
    """

    root: Path
    kicad_python: env.KicadPython
    venv_python: Path
    jar: Path
    downloaded: bool
    java: env.Tool | None


# --- the machine ---------------------------------------------------------------------


def _call(args: Sequence[str], cwd: Path) -> int:
    """Run a command with its output on the terminal; return its exit code.

    No input is given, so uv can never stop to ask. VIRTUAL_ENV is dropped: uv would
    only warn that it differs from the project's .venv and say it is ignoring it.
    """
    clean = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    try:
        done = subprocess.run(
            list(args), cwd=cwd, env=clean, stdin=subprocess.DEVNULL, check=False
        )
    except OSError as err:
        raise SetupError(f"could not run {args[0]}: {err.strerror or err}") from None
    return done.returncode


def _open(url: str) -> BinaryIO:
    """Open ``url`` for reading, following redirects."""
    return urlopen(url, timeout=DOWNLOAD_SECONDS)


def jar_home() -> Path:
    """Return the folder pcbkit keeps its Freerouting jar in: ~/.local/share/pcbkit."""
    return Path.home() / ".local" / "share" / "pcbkit"


# --- the pieces ----------------------------------------------------------------------


def _fix(item: str) -> str:
    """Return doctor's fix hint for ``item`` on this OS."""
    options = doctor.FIXES[item]
    return options.get(env.host_os(), options["other"])


def find_uv() -> env.Tool:
    """Return uv; raise SetupError, saying how to install it, if it is missing."""
    uv = env.find_uv()
    if uv is None:
        raise SetupError(
            f"uv is not installed, and setup needs it. Install it: {_fix('uv')}"
        )
    return uv


def find_kicad_python() -> env.KicadPython:
    """Return KiCad's Python, proven to import pcbnew; raise SetupError if not found."""
    found, reasons = env.probe_kicad_python()
    if found is None:
        tried = "; ".join(reasons) if reasons else "no interpreter to try"
        raise SetupError(
            f"no Python that can import pcbnew was found ({tried}). pcbkit builds the "
            f"project's .venv on KiCad's own Python. Fix: {_fix('python')}. "
            "`pcbkit doctor` shows what is found."
        )
    if not env.at_least(found.pcbnew_version, env.MIN_KICAD):
        floor = ".".join(str(part) for part in env.MIN_KICAD)
        raise SetupError(
            f"pcbnew {found.pcbnew_version} at {found.path} is older than the {floor} "
            f"pcbkit needs. Fix: {_fix('python')}"
        )
    return found


def venv_imports_pcbnew(python: Path) -> tuple[bool, str]:
    """Return whether ``python`` imports pcbnew, and what it said if it did not.

    The probe is run isolated (``-I``), as KiCad's own Python is, so that a stray
    pcbnew.py in the current folder or on PYTHONPATH does not count.
    """
    if not python.is_file():
        return False, f"{python} does not exist"
    run = env._run([str(python), "-I", "-c", PROBE], timeout=60.0)
    tagged = [line for line in run.output.splitlines() if line.startswith(PROBE_TAG)]
    if run.returncode == 0 and tagged:
        return True, tagged[-1][len(PROBE_TAG) :].strip().strip("()")
    lines = [line.strip() for line in run.output.splitlines() if line.strip()]
    return False, run.error or (lines[-1] if lines else "no output")


def make_venv(
    root: Path, python: env.KicadPython, uv: env.Tool, say: Callable[[str], None]
) -> Path:
    """Make ``root/.venv`` on KiCad's Python unless it is there and imports pcbnew.

    Return the venv's interpreter. A folder called .venv that is not a virtual
    environment is never replaced.
    """
    venv = root / VENV
    interpreter = venv / "bin" / "python"
    if venv.exists():
        ok, detail = venv_imports_pcbnew(interpreter)
        if ok:
            say(f"{VENV}  kept: it imports pcbnew {detail}")
            return interpreter
        if not (venv / "pyvenv.cfg").is_file():
            raise SetupError(
                f"{venv} exists and is not a virtual environment: move it away, "
                "then run `pcbkit setup` again"
            )
        say(f"{VENV}  does not import pcbnew ({detail}): making it again")
    else:
        say(f"{VENV}  making it on KiCad's Python (uv venv --system-site-packages)")
    command = [uv.path, "venv", "--python", python.path, "--system-site-packages"]
    if venv.exists():
        command.append("--clear")
    code = _call([*command, VENV], root)
    if code:
        raise SetupError(
            f"`uv venv` failed (exit {code}); its message is above. Fix what it says, "
            "then run `pcbkit setup` again."
        )
    return interpreter


def sync_venv(
    root: Path, python: env.KicadPython, uv: env.Tool, say: Callable[[str], None]
) -> None:
    """Install the project's dependencies into its .venv with ``uv sync``."""
    say(f"{VENV}  installing the dependencies in pyproject.toml (uv sync)")
    code = _call([uv.path, "sync", "--python", python.path], root)
    if code:
        raise SetupError(
            f"`uv sync` failed (exit {code}); its message is above. A dependency that "
            "cannot be found or built is the usual cause: check the network, and the "
            "dependencies and [tool.uv.sources] in pyproject.toml, then run "
            "`pcbkit setup` again."
        )


def fetch_jar(url: str, dest: Path, size: int | None = None) -> None:
    """Download ``url`` to ``dest``, which exists afterwards only if it is right.

    The bytes go to ``dest`` plus ``.part``. They are accepted only if there are exactly
    ``size`` of them (FREEROUTING_BYTES unless given) and they form a zip file, which a
    jar is; the file is then moved into place. A short, long or damaged download, or an
    error on the way, raises a SetupError that says where to put the file by hand, and
    leaves nothing behind.
    """
    size = FREEROUTING_BYTES if size is None else size
    part = dest.with_name(dest.name + ".part")
    hand = (
        f"Download {url} yourself and save it as {dest}, then run `pcbkit setup` again."
    )
    got = 0
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with closing(_open(url)) as response, open(part, "wb") as out:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                got += len(chunk)
                if got > size:  # not the file that was asked for: stop reading it
                    break
                out.write(chunk)
        if got != size:
            reason = "it was cut short" if got < size else "it was longer than that"
            raise SetupError(
                f"the Freerouting download was {got} bytes and should be {size} "
                f"({reason}). {hand}"
            )
        if not env.is_jar(str(part)):
            raise SetupError(
                f"the Freerouting download is not a jar (a zip file). {hand}"
            )
        os.replace(part, dest)
    except OSError as err:
        why = getattr(err, "reason", None) or err.strerror or err
        raise SetupError(
            f"could not download Freerouting from {url}: {why}. {hand}"
        ) from None
    finally:
        with suppress(OSError):  # the folder may never have been made
            part.unlink(missing_ok=True)


def ensure_freerouting(say: Callable[[str], None]) -> tuple[Path, bool]:
    """Return the Freerouting jar and whether it was downloaded just now.

    A jar ``env.find_freerouting_jar`` finds is used as it is, if it is a whole zip.
    FREEROUTING_JAR, when set, is the only place looked at and is never replaced by a
    download somewhere else. A damaged jar in pcbkit's own folder is fetched again; a
    damaged one anywhere else is reported, because it is not ours to overwrite.
    """
    dest = jar_home() / env.FREEROUTING_JAR_NAME
    found = env.find_freerouting_jar()
    if found is not None:
        if env.is_jar(found.path):
            say(f"Freerouting  {found.path}")
            return Path(found.path), False
        if Path(found.path) != dest:
            raise SetupError(
                f"{found.path} is not a valid jar (a truncated download?). Delete it, "
                "or point FREEROUTING_JAR at a good one, then run `pcbkit setup` again."
            )
        say(f"Freerouting  {dest} is damaged: downloading it again")
    elif os.environ.get("FREEROUTING_JAR"):
        raise SetupError(
            f"FREEROUTING_JAR={os.environ['FREEROUTING_JAR']} does not exist. Unset it "
            "and pcbkit will download Freerouting for you, or point it at the jar."
        )
    megabytes = FREEROUTING_BYTES / 1e6
    say(
        f"Freerouting  downloading {FREEROUTING_VERSION} ({megabytes:.1f} MB) to {dest}"
    )
    fetch_jar(FREEROUTING_URL, dest)
    return dest, True


def setup_project(root: Path, say: Callable[[str], None] = click.echo) -> SetupResult:
    """Set up the board project at ``root``; see the module docstring for the steps."""
    uv = find_uv()
    say(f"uv  {uv.version}  {uv.path}")
    python = find_kicad_python()
    say(
        f"KiCad Python  {python.python_version}, pcbnew {python.pcbnew_version}  "
        f"{python.path}"
    )
    if not (root / "pyproject.toml").is_file():
        raise SetupError(
            f"{root / 'pyproject.toml'} not found: the project's .venv is made from "
            "it. `pcbkit new` writes one; any that lists pcbkit and pytest as "
            "dependencies will do."
        )
    interpreter = make_venv(root, python, uv, say)
    sync_venv(root, python, uv, say)
    ok, detail = venv_imports_pcbnew(interpreter)
    if not ok:
        raise SetupError(
            f"pcbnew does not import in {root / VENV} ({detail}). `uv sync` may have "
            "rebuilt it on another Python. Fix: delete .venv, make sure no "
            ".python-version file or UV_PYTHON variable names a different Python, "
            "and run `pcbkit setup` again."
        )
    say(f"{VENV}  imports pcbnew {detail}")
    jar, downloaded = ensure_freerouting(say)
    java = env.find_java()
    if java is not None and (env.java_major(java.version) or 0) >= env.MIN_JAVA:
        say(f"Java  {java.version}  {java.path}")
    else:
        java = None
        say(
            f"Java  not found, and Freerouting needs {env.MIN_JAVA} or newer. "
            f"Fix: {_fix('java')}"
        )
    return SetupResult(root, python, interpreter, jar, downloaded, java)


def format_next(result: SetupResult, cwd: Path | None = None) -> str:
    """Return the commands to run next, from the folder the user is in."""
    here = Path.cwd() if cwd is None else Path(cwd)
    try:
        folder = os.path.relpath(result.root, here)
    except ValueError:  # another drive: only the full path will do
        folder = str(result.root)
    where = "" if folder == "." else f"cd {folder} && "
    lines = ["", "Ready. Next:"]
    lines += [
        f"  {where}.venv/bin/pcbkit {command}"
        for command in ("doctor", "build", "route", "promote", "finalize", "check")
    ]
    return "\n".join(lines)
