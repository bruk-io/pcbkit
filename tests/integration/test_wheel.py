"""Integration: the wheel carries the board template, byte for byte.

`pcbkit new` copies `pcbkit/templates/board`, which only exists on the user's machine if
the wheel holds it. Hatchling leaves out what the repository's .gitignore matches and
what its own settings exclude, so this builds the wheel with the real uv and looks
inside. It needs uv and the build backend (hatchling, from PyPI or a warm uv cache).
"""

from __future__ import annotations

import subprocess
import zipfile

import pytest

from pcbkit import scaffold
from pcbkit.kicad import env
from tests.scaffold_files import REPO

TEMPLATE = scaffold.TEMPLATE_ROOT / scaffold.TEMPLATES["blinky"]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> zipfile.ZipFile:
    """Build the wheel of this checkout, and return it opened."""
    uv = env.find_uv()
    if uv is None:
        pytest.skip("needs uv to build the wheel: brew install uv")
    out = tmp_path_factory.mktemp("dist")
    done = subprocess.run(
        [uv.path, "build", "--wheel", "--out-dir", str(out), str(REPO)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    (path,) = out.glob("pcbkit-*.whl")
    return zipfile.ZipFile(path)


def test_the_wheel_holds_every_file_of_the_template_unchanged(
    wheel: zipfile.ZipFile,
) -> None:
    """Find each template file in the wheel, dotfiles and golden/ included."""
    inside = {n: wheel.read(n) for n in wheel.namelist() if "/templates/" in n}
    expected = {
        f"pcbkit/templates/{scaffold.TEMPLATES['blinky']}/{name}": data
        for name, data in scaffold.template_files(TEMPLATE).items()
    }
    assert inside == expected
    assert "pcbkit/templates/board/.gitignore" in inside
    assert "pcbkit/templates/board/golden/prerouted.kicad_pcb" in inside


def test_the_wheel_holds_the_modules_that_new_and_setup_run(
    wheel: zipfile.ZipFile,
) -> None:
    """Carry scaffold.py and bootstrap.py, and nothing of the examples folder."""
    names = set(wheel.namelist())
    assert {"pcbkit/scaffold.py", "pcbkit/bootstrap.py"} <= names
    assert not [n for n in names if n.startswith("examples/")]
