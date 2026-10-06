"""A fake machine for unit tests: tools that "exist", answer commands, and nothing else.

pcbkit.kicad.env asks the machine four things: where a file is, what is on PATH, what
a command prints, and which OS this is. ``FakeMachine`` answers all four from a
temporary folder, so a unit test passes or fails the same on a Mac with KiCad and on a
bare CI box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pcbkit.kicad.env import Run


@dataclass
class FakeMachine:
    """Stand-ins for /Applications, /usr, the Homebrew prefixes and $HOME.

    ``exe`` creates an executable file and records what it prints; give ``on_path`` to
    make it findable by that name. A command that was not set up fails the test, so a
    unit test can never reach a real tool by accident.
    """

    root: Path
    os_name: str = "linux"
    answers: dict[str, Run] = field(default_factory=dict)
    path_tools: dict[str, str] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    @property
    def mac_app(self) -> Path:
        """Return the stand-in for /Applications/KiCad/KiCad.app."""
        return self.root / "Applications" / "KiCad" / "KiCad.app"

    @property
    def linux_share(self) -> Path:
        """Return the stand-in for /usr/share/kicad."""
        return self.root / "usr" / "share" / "kicad"

    @property
    def usr_bin(self) -> Path:
        """Return the stand-in for /usr/bin."""
        return self.root / "usr" / "bin"

    @property
    def brew_arm(self) -> Path:
        """Return the stand-in for /opt/homebrew."""
        return self.root / "opt" / "homebrew"

    @property
    def brew_intel(self) -> Path:
        """Return the stand-in for /usr/local."""
        return self.root / "usr" / "local"

    @property
    def home(self) -> Path:
        """Return the stand-in for $HOME."""
        return self.root / "home"

    def exe(
        self,
        path: Path,
        output: str = "",
        returncode: int = 0,
        error: str = "",
        on_path: str = "",
    ) -> Path:
        """Create an executable at ``path`` that answers any arguments the same way."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        self.answers[str(path)] = Run(returncode, output, error)
        if on_path:
            self.path_tools[on_path] = str(path)
        return path

    def run(self, args: list[str], timeout: float = 20.0) -> Run:
        """Answer a command the way ``pcbkit.kicad.env._run`` would."""
        self.calls.append(list(args))
        if args[0] not in self.answers:
            raise AssertionError(f"unit test ran a command it did not set up: {args}")
        return self.answers[args[0]]
