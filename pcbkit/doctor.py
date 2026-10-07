"""``pcbkit doctor``: check this machine for everything pcbkit drives.

``diagnose`` runs the discovery in pcbkit.kicad.env and turns each result into a
``Check``. ``format_report`` and ``exit_code`` are pure functions over a list of
checks, so the report text and the exit status are testable without discovering
anything.

Required (exit status 1 if one is missing): KiCad 10.0 or newer, kicad-cli, KiCad's
libraries, KiCad's Python importing pcbnew, Java 17 or newer, the Freerouting jar,
rsvg-convert (finalize draws the assembly drawing with it, shots every PNG) and uv.

Optional: ngspice, which only checks that run SPICE need, and pcbnew in the Python that
is running pcbkit, which only the tier 2 commands need (`pcbkit setup` makes a project
environment where it works).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

import click

from pcbkit.kicad import env

SETUP = "run `pcbkit setup` in a board project"
KICAD_DOWNLOAD = "install KiCad 10 from https://www.kicad.org/download/"

# Fix hints by item, then by OS ("macos", "linux"); "other" covers any OS not listed.
FIXES: dict[str, dict[str, str]] = {
    "kicad": {"macos": "brew install --cask kicad", "other": KICAD_DOWNLOAD},
    "kicad-cli": {
        "macos": "brew install --cask kicad (kicad-cli is inside KiCad.app)",
        "other": KICAD_DOWNLOAD,
    },
    "libraries": {
        "other": "reinstall KiCad, or set KICAD_SHARE to the folder that holds "
        "footprints/, symbols/ and 3dmodels/"
    },
    "python": {
        "macos": "brew install --cask kicad (its bundled Python provides pcbnew)",
        "other": f"{KICAD_DOWNLOAD}; a system python3 must be able to import pcbnew",
    },
    "java": {
        "macos": "brew install openjdk@21 (pcbkit finds the keg-only install itself)",
        "linux": "sudo apt install openjdk-21-jre",
        "other": "install a JDK or JRE, version 17 or newer",
    },
    "freerouting": {"other": f"{SETUP}, or set FREEROUTING_JAR to the jar"},
    "rsvg-convert": {
        "macos": "brew install librsvg",
        "linux": "sudo apt install librsvg2-bin",
        "other": "install librsvg (it provides rsvg-convert)",
    },
    "uv": {
        "macos": "brew install uv",
        "other": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    },
    "ngspice": {
        "macos": "brew install ngspice",
        "linux": "sudo apt install ngspice",
        "other": "install ngspice",
    },
    "here": {"other": f"{SETUP}, then run pcbkit from that project's .venv"},
}


@dataclass(frozen=True)
class Check:
    """One line of the doctor report: an item, whether it is fine, and what to do."""

    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def _fix(item: str) -> str:
    """Return the fix hint for ``item`` on this OS."""
    options = FIXES[item]
    return options.get(env.host_os(), options["other"])


def _found(version: str, path: str) -> str:
    """Return "version  path", saying so when the version is unknown."""
    return f"{version or 'version unknown'}  {path}"


def _kicad_floor() -> str:
    """Return the oldest KiCad pcbkit supports, as text."""
    return ".".join(str(part) for part in env.MIN_KICAD)


def _check_kicad(name: str, tool: env.Tool | None, missing: str, item: str) -> Check:
    """Check a KiCad piece that has a version: present, and at least MIN_KICAD."""
    if tool is None:
        return Check(name, False, missing, _fix(item))
    if not env.at_least(tool.version, env.MIN_KICAD):
        detail = f"found {tool.version} at {tool.path}; pcbkit needs {_kicad_floor()}"
        return Check(name, False, detail, _fix(item))
    return Check(name, True, _found(tool.version, tool.path))


def _check_libraries() -> Check:
    """Check KiCad's symbol, footprint and 3D model libraries."""
    share = env.find_share()
    if share is not None:
        return Check("KiCad libraries", True, str(share))
    override = os.environ.get("KICAD_SHARE")
    if override:
        detail = f"KICAD_SHARE={override} has no footprints/ folder"
    else:
        detail = "no footprints/ folder in the usual places"
    return Check("KiCad libraries", False, detail, _fix("libraries"))


def _check_kicad_python() -> Check:
    """Check that KiCad's own Python can import pcbnew, at a supported version."""
    found, reasons = env.probe_kicad_python()
    name = "KiCad Python"
    if found is None:
        detail = "no interpreter that imports pcbnew: " + (
            "; ".join(reasons) if reasons else "none found"
        )
        return Check(name, False, detail, _fix("python"))
    if not env.at_least(found.pcbnew_version, env.MIN_KICAD):
        detail = (
            f"pcbnew {found.pcbnew_version} at {found.path}; "
            f"pcbkit needs {_kicad_floor()}"
        )
        return Check(name, False, detail, _fix("python"))
    detail = (
        f"Python {found.python_version}, pcbnew {found.pcbnew_version}  {found.path}"
    )
    return Check(name, True, detail)


def _check_java() -> Check:
    """Check for a Java that runs Freerouting."""
    java = env.find_java()
    if java is None:
        detail = f"not found; Freerouting needs Java {env.MIN_JAVA} or newer"
        return Check("Java", False, detail, _fix("java"))
    major = env.java_major(java.version)
    if major is None or major < env.MIN_JAVA:
        detail = f"found {java.version} at {java.path}; need {env.MIN_JAVA} or newer"
        return Check("Java", False, detail, _fix("java"))
    return Check("Java", True, _found(java.version, java.path))


def _check_freerouting() -> Check:
    """Check for the Freerouting jar, and that it is a whole zip file."""
    jar = env.find_freerouting_jar()
    if jar is None:
        override = os.environ.get("FREEROUTING_JAR")
        if override:
            detail = f"FREEROUTING_JAR={override} does not exist"
        else:
            detail = (
                f"{env.FREEROUTING_JAR_NAME} is not in ~/.local/share/pcbkit "
                "or ~/.local/share/freerouting"
            )
        return Check("Freerouting", False, detail, _fix("freerouting"))
    if not env.is_jar(jar.path):
        detail = f"{jar.path} is not a valid jar (a truncated download?)"
        return Check("Freerouting", False, detail, _fix("freerouting"))
    return Check("Freerouting", True, _found(jar.version, jar.path))


def _check_tool(
    name: str, tool: env.Tool | None, item: str, why: str, required: bool = True
) -> Check:
    """Check a tool that only needs to be present."""
    if tool is None:
        return Check(name, False, f"not found; {why}", _fix(item), required)
    return Check(name, True, _found(tool.version, tool.path), required=required)


def _check_pcbnew_here() -> Check:
    """Report whether tier 2 commands can run in the Python that is running pcbkit."""
    name = "pcbnew here"
    if env.pcbnew_importable():
        detail = "importable: build, route, finalize and the other tier 2 commands run"
        return Check(name, True, detail, required=False)
    detail = "not importable here, so tier 2 commands (build, route, ...) cannot run"
    return Check(name, False, detail, _fix("here"), required=False)


def diagnose() -> list[Check]:
    """Probe this machine and return one Check per item, in report order."""
    checks: list[Check] = []
    if env.host_os() == "macos":
        checks.append(
            _check_kicad(
                "KiCad", env.find_kicad_app(), f"not found at {env.MAC_APP}", "kicad"
            )
        )
    checks.append(
        _check_kicad(
            "kicad-cli",
            env.find_kicad_cli(),
            "not found on PATH or in KiCad",
            "kicad-cli",
        )
    )
    checks.append(_check_libraries())
    checks.append(_check_kicad_python())
    checks.append(_check_java())
    checks.append(_check_freerouting())
    checks.append(
        _check_tool(
            "rsvg-convert",
            env.find_rsvg_convert(),
            "rsvg-convert",
            "finalize and shots draw PNGs with it",
        )
    )
    checks.append(_check_tool("uv", env.find_uv(), "uv", "pcbkit projects use it"))
    checks.append(
        _check_tool(
            "ngspice",
            env.find_ngspice(),
            "ngspice",
            "only checks that run SPICE need it",
            required=False,
        )
    )
    checks.append(_check_pcbnew_here())
    return checks


def _summary(checks: Sequence[Check]) -> str:
    """Return the closing line: what is missing, required and optional."""
    missing = [c.name for c in checks if not c.ok and c.required]
    optional = [c.name for c in checks if not c.ok and not c.required]
    if missing:
        plural = "" if len(missing) == 1 else "s"
        text = f"{len(missing)} required item{plural} missing: {', '.join(missing)}."
    else:
        text = "All required items are present."
    if optional:
        text += f" Optional, not found: {', '.join(optional)}."
    return text


def format_report(checks: Sequence[Check], *, color: bool = False) -> str:
    """Return the report: one line per check, then a summary line.

    Each line is the status (OK or MISSING), the item, what was found or what is wrong,
    and, for anything not OK, the fix. ``color`` styles the status for a terminal.
    """
    labels = [c.name if c.required else f"{c.name} (optional)" for c in checks]
    width = max((len(label) for label in labels), default=0)
    lines = []
    for check, label in zip(checks, labels):
        status = f"{'OK' if check.ok else 'MISSING':<8}"
        if color:
            hue = "green" if check.ok else ("red" if check.required else "yellow")
            status = click.style(status, fg=hue)
        line = f"{status} {label:<{width}}  {check.detail}"
        if not check.ok and check.fix:
            line += f"  (fix: {check.fix})"
        lines.append(line)
    lines.append(_summary(checks))
    return "\n".join(lines)


def exit_code(checks: Sequence[Check]) -> int:
    """Return 1 if a required item is missing, else 0."""
    return 1 if any(c.required and not c.ok for c in checks) else 0
