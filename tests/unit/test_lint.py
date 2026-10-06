"""Lint guards: rules ruff cannot express, enforced by reading pcbkit's own source.

HitTest is banned. It is a pcbnew method that asks a track (or a zone, a pad) whether a
point lies on it. During the KiCad 10 port, calling PCB_TRACK.HitTest in the post-route
loops was blamed for corrupting pcbnew's SWIG track list. A plain loop of many thousand
calls did not reproduce that on KiCad 10.0.6, so the ban rests on the earlier finding
rather than on a reproduction; what it costs is nothing, because
``pcbkit.kicad.board.point_in_track`` gives the same answer from pure geometry (a test
in the integration tier compares the two on a real board).

The scan is a walk of each module's syntax tree, so a docstring or comment may talk
about HitTest, and ``HitTestFilledArea`` (a different method, on zones) is not caught.
Files that are not Python, such as a template that generates project code, are scanned
for the call as text.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "pcbkit"

REASON = (
    "HitTest is banned in pcbkit: calling it inside loops corrupted pcbnew's SWIG "
    "track list on KiCad 10. Use pcbkit.kicad.board.point_in_track(track, point, "
    "accuracy), which answers the same question with pure geometry."
)

TEXT_CALL = re.compile(r"\bHitTest\s*\(")


def hit_test_uses(root: Path) -> list[str]:
    """Return ``path:line`` for each place under ``root`` that uses HitTest."""
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        where = path.relative_to(root.parent)
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".py":
            for node in ast.walk(ast.parse(text, filename=str(path))):
                named = (
                    (isinstance(node, ast.Attribute) and node.attr == "HitTest")
                    or (isinstance(node, ast.Name) and node.id == "HitTest")
                    or (isinstance(node, ast.Constant) and node.value == "HitTest")
                )
                if named:
                    found.append(f"{where}:{node.lineno}")  # type: ignore[attr-defined]
        else:
            for number, line in enumerate(text.splitlines(), start=1):
                if TEXT_CALL.search(line):
                    found.append(f"{where}:{number}")
    return found


def test_pcbkit_never_uses_hittest() -> None:
    found = hit_test_uses(PACKAGE)
    assert not found, f"{REASON}\nFound at: {', '.join(found)}"


def test_the_scan_really_covers_the_package() -> None:
    """Guard the guard: a wrong path would make the test above pass for nothing."""
    python_files = list(PACKAGE.rglob("*.py"))
    assert PACKAGE / "kicad" / "board.py" in python_files
    assert len(python_files) >= 8


def test_the_guard_catches_a_planted_call(tmp_path: Path) -> None:
    """Plant the mistake and watch the guard go red, three ways round."""
    pkg = tmp_path / "pkg"
    (pkg / "templates").mkdir(parents=True)
    (pkg / "route.py").write_text(
        "def near(o, p):\n    return o.HitTest(p, 5)\n", encoding="utf-8"
    )
    (pkg / "dynamic.py").write_text(
        'def near(o, p):\n    return getattr(o, "HitTest")(p, 5)\n', encoding="utf-8"
    )
    (pkg / "templates" / "hook.py.tmpl").write_text(
        "ok = 1\nhit = o.HitTest (p, 2)\n", encoding="utf-8"
    )
    assert hit_test_uses(pkg) == [
        "pkg/dynamic.py:2",
        "pkg/route.py:2",
        "pkg/templates/hook.py.tmpl:2",
    ]


def test_the_guard_leaves_alone_what_is_not_a_call_to_hittest(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "fine.py").write_text(
        '"""Do not call HitTest(point, 0) here: use point_in_track."""\n'
        "# o.HitTest(p, 5) was removed\n"
        "def inside(z, layer, p):\n"
        "    note = 'PCB_TRACK.HitTest is banned'\n"
        "    return z.HitTestFilledArea(layer, p) or z.HitTestForCorner(p, 0)\n",
        encoding="utf-8",
    )
    assert hit_test_uses(pkg) == []
