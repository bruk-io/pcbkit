"""Run pcbkit in a child Python and read what it writes to the real standard error.

Output written by C++ code (pcbnew's wx noise) goes to file descriptor 2, which no
in-process capture of ``sys.stderr`` sees, so a test that is about it runs the code in
a child and reads the child's pipe, like a terminal would.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

# Runs `pcbkit <args>` in this interpreter, as the console script would.
CLI = "import sys; from pcbkit.cli import cli; sys.exit(cli())"


def run_python(
    code: str, *args: str, cwd: Path | None = None, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh Python, and capture its standard output and error."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


def pcbkit(
    *args: str, cwd: Path, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    """Run the pcbkit command line with ``args`` in ``cwd``."""
    return run_python(CLI, *args, cwd=cwd, timeout=timeout)
