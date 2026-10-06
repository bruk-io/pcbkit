"""Unit tests for the parts of pcbkit.kicad.board that need no pcbnew at all.

Geometry, ``rect``, ``remove`` and the stackup rewrite. None of these tests asks for
the ``fake_pcbnew`` fixture: they pass whether or not pcbnew can be imported.
"""

from __future__ import annotations

import gc
import random
import re
import sys
import weakref
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from pcbkit.kicad import board as kb


class P(NamedTuple):
    """A point: anything with .x and .y will do, as a pcbnew VECTOR2I has."""

    x: float
    y: float


class Seg:
    """A straight track: a start, an end and a width, as pcbnew reports them."""

    def __init__(self, a: P, b: P, width: float) -> None:
        """Make the track."""
        self.a, self.b, self.width = a, b, width

    def GetStart(self) -> P:
        """Return the start point."""
        return self.a

    def GetEnd(self) -> P:
        """Return the end point."""
        return self.b

    def GetWidth(self) -> float:
        """Return the track width."""
        return self.width


def test_the_module_imports_and_its_pure_helpers_work_without_pcbnew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make ``import pcbnew`` fail (None in sys.modules), then use the module."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    assert kb.rect(0, 0, 2, 1) == [(0, 0), (2, 0), (2, 1), (0, 1)]
    assert kb.segment_distance(P(0, 0), P(4, 0), P(2, 3)) == 3
    with pytest.raises(ImportError):
        kb.mm(1.0)


def test_rect_lists_the_corners_clockwise_from_the_top_left() -> None:
    assert kb.rect(1, 2, 5, 9) == [(1, 2), (5, 2), (5, 9), (1, 9)]


# --- geometry: the replacement for HitTest ---------------------------------------


def test_a_point_beside_a_segment_is_its_perpendicular_distance_away() -> None:
    assert kb.segment_distance(P(0, 0), P(10, 0), P(5, 3)) == 3
    assert kb.segment_distance(P(0, 0), P(10, 10), P(10, 0)) == pytest.approx(
        7.0711, abs=1e-4
    )


def test_past_an_end_the_distance_is_to_that_end() -> None:
    """A track has round ends: beyond one, the nearest copper is its end point."""
    assert kb.segment_distance(P(0, 0), P(10, 0), P(13, 4)) == 5
    assert kb.segment_distance(P(0, 0), P(10, 0), P(-3, -4)) == 5


def test_a_zero_length_segment_is_a_point() -> None:
    assert kb.segment_distance(P(2, 2), P(2, 2), P(5, 6)) == 5


def test_a_point_on_the_segment_is_at_distance_zero() -> None:
    assert kb.segment_distance(P(0, 0), P(10, 0), P(7, 0)) == 0
    assert kb.segment_distance(P(0, 0), P(10, 0), P(0, 0)) == 0
    assert kb.segment_distance(P(0, 0), P(10, 0), P(10, 0)) == 0


def test_point_in_track_is_inside_by_half_the_width_and_the_edge_counts() -> None:
    track = Seg(P(0, 0), P(100_000, 0), 250_000)  # 0.25 mm wide, in nm
    assert kb.point_in_track(track, P(50_000, 125_000))  # exactly on the edge
    assert kb.point_in_track(track, P(50_000, 124_999))
    assert not kb.point_in_track(track, P(50_000, 125_001))
    assert kb.point_in_track(track, P(-125_000, 0))  # on the round end
    assert not kb.point_in_track(track, P(-125_001, 0))
    assert kb.point_in_track(track, P(50_000, -125_000))  # either side


def test_accuracy_widens_the_track_the_way_hittest_does() -> None:
    """HitTest(p, accuracy) reaches ``accuracy`` beyond the copper's edge."""
    track = Seg(P(0, 0), P(100_000, 0), 250_000)
    assert not kb.point_in_track(track, P(50_000, 200_000))
    assert kb.point_in_track(track, P(50_000, 200_000), accuracy=75_000)
    assert not kb.point_in_track(track, P(50_000, 200_001), accuracy=75_000)
    # post-route's "attached" test asks for a full width: HitTest(p, width // 2).
    assert kb.point_in_track(track, P(50_000, 250_000), accuracy=track.width // 2)
    assert not kb.point_in_track(track, P(50_000, 250_001), accuracy=track.width // 2)


def old_distance(a: P, b: P, p: P) -> float:
    """Return the distance as the post-route pass computes it, inline, today."""
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    u = (
        0.0
        if L2 == 0
        else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
    )
    return ((p.x - a.x - u * dx) ** 2 + (p.y - a.y - u * dy) ** 2) ** 0.5


def test_the_distance_is_the_exact_arithmetic_of_the_inline_code_it_replaces() -> None:
    """Same operations in the same order, so a swap cannot flip a decision."""
    rng = random.Random(2026)
    for _ in range(3000):
        a = P(rng.randint(-5_000_000, 5_000_000), rng.randint(-5_000_000, 5_000_000))
        b = (
            a
            if rng.random() < 0.05
            else P(
                rng.randint(-5_000_000, 5_000_000), rng.randint(-5_000_000, 5_000_000)
            )
        )
        p = P(rng.randint(-5_000_000, 5_000_000), rng.randint(-5_000_000, 5_000_000))
        assert kb.segment_distance(a, b, p) == old_distance(a, b, p)
        width = rng.randint(100_000, 3_000_000)
        old_inside = old_distance(a, b, p) <= width / 2
        assert kb.point_in_track(Seg(a, b, width), p) == old_inside


# --- remove: keep removed items alive -------------------------------------------


class Thing:
    """An object a weak reference can point at, standing for a removed track."""


class ForgetfulBoard:
    """A board whose Remove keeps no reference: only the caller can keep the item."""

    def __init__(self) -> None:
        """Start with nothing taken off."""
        self.taken = 0

    def Remove(self, item: Any) -> None:
        """Take the item off, and forget it."""
        self.taken += 1


def test_without_the_helper_a_removed_item_is_freed() -> None:
    """The control: this is what happens to the item unless something holds it."""
    board, item = ForgetfulBoard(), Thing()
    seen = weakref.ref(item)
    board.Remove(item)
    del item
    gc.collect()
    assert seen() is None


def test_remove_takes_the_item_off_and_keeps_it_referenced() -> None:
    board, item = ForgetfulBoard(), Thing()
    seen = weakref.ref(item)
    kb.remove(board, item)
    del item
    gc.collect()
    assert board.taken == 1
    assert seen() is not None


# --- set_copper: the stackup rewrite --------------------------------------------

# A stackup written by hand on one line per layer, and as KiCad 10 saves it.
ONE_LINE = (
    "\t(stackup\n"
    '\t\t(layer "F.SilkS" (type "Top Silk Screen"))\n'
    '\t\t(layer "F.Paste" (type "Top Solder Paste"))\n'
    '\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "F.Cu" (type "copper") (thickness 0.035))\n'
    '\t\t(layer "dielectric 1" (type "core") (thickness 1.44) (material "FR4")'
    " (epsilon_r 4.5) (loss_tangent 0.02))\n"
    '\t\t(layer "B.Cu" (type "copper") (thickness 0.035))\n'
    '\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))\n'
    '\t\t(layer "B.Paste" (type "Bottom Solder Paste"))\n'
    '\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))\n'
    '\t\t(copper_finish "HAL lead-free")\n'
    "\t\t(dielectric_constraints no)\n"
    "\t)\n"
)

MULTI_LINE = """\t\t(stackup
\t\t\t(layer "F.SilkS"
\t\t\t\t(type "Top Silk Screen")
\t\t\t)
\t\t\t(layer "F.Paste"
\t\t\t\t(type "Top Solder Paste")
\t\t\t)
\t\t\t(layer "F.Mask"
\t\t\t\t(type "Top Solder Mask")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "F.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 1"
\t\t\t\t(type "core")
\t\t\t\t(thickness 1.44)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "B.Mask"
\t\t\t\t(type "Bottom Solder Mask")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "B.Paste"
\t\t\t\t(type "Bottom Solder Paste")
\t\t\t)
\t\t\t(layer "B.SilkS"
\t\t\t\t(type "Bottom Silk Screen")
\t\t\t)
\t\t\t(copper_finish "HAL lead-free")
\t\t\t(dielectric_constraints no)
\t\t)
"""

# Things in a board that mention a copper layer without being its stackup entry.
OTHER_COPPER = """\t(footprint "Resistor_SMD:R_0603_1608Metric"
\t\t(layer "F.Cu")
\t\t(pad "1" smd roundrect
\t\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t\t)
\t)
\t(segment
\t\t(start 55 55)
\t\t(end 60 55)
\t\t(width 0.25)
\t\t(layer "B.Cu")
\t\t(net "/NET_A")
\t)
"""


def board_text(stackup: str) -> str:
    """Return a small board file around a stackup."""
    return (
        f"(kicad_pcb\n\t(version 20260101)\n\t(setup\n{stackup}\t)\n{OTHER_COPPER})\n"
    )


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    """Return the (old, new) pairs of lines that differ; the line counts must match."""
    old, new = before.split("\n"), after.split("\n")
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new) if a != b]


@pytest.mark.parametrize(
    "stackup", [ONE_LINE, MULTI_LINE], ids=["one line", "multi-line"]
)
def test_both_copper_layers_change_and_nothing_else_does(stackup: str) -> None:
    text = board_text(stackup)
    out = kb.set_copper_text(text, 0.07)
    diff = changed_lines(text, out)
    assert len(diff) == 2
    for old, new in diff:
        assert "(thickness 0.035)" in old
        assert new == old.replace("0.035", "0.07")
    assert out.count("(thickness 0.07)") == 2
    # the mask and the dielectric keep theirs, and so do the layers named elsewhere
    assert out.count("(thickness 0.01)") == 2
    assert "(thickness 1.44)" in out
    assert out.replace("0.07", "0.035") == text
    assert out.count('(layer "F.Cu")') == 1
    assert out.count('(layer "B.Cu")') == 1
    assert '(layers "F.Cu" "F.Paste" "F.Mask")' in out


def test_the_thickness_is_written_the_way_percent_g_writes_it() -> None:
    text = board_text(MULTI_LINE)
    assert "(thickness 0.07)" in kb.set_copper_text(text, 0.07)
    assert "(thickness 0.035)" in kb.set_copper_text(text, 0.035)
    assert "(thickness 0.0175)" in kb.set_copper_text(text, 0.0175)
    assert "(thickness 1)" in kb.set_copper_text(text, 1)


def test_a_rewrite_that_changes_nothing_returns_the_text_byte_for_byte() -> None:
    text = board_text(MULTI_LINE)
    assert kb.set_copper_text(text, 0.035) == text


@pytest.mark.parametrize("count", [0, 1, 3])
def test_anything_but_two_copper_layers_is_an_error(count: int) -> None:
    layer = '\t\t(layer "F.Cu"\n\t\t\t(type "copper")\n\t\t\t(thickness 0.035)\n\t\t)\n'
    text = "(kicad_pcb\n\t(stackup\n" + layer * count + "\t)\n)\n"
    message = f"expected two copper layers in the stackup, found {count}"
    with pytest.raises(ValueError, match=message):
        kb.set_copper_text(text, 0.07)


def test_inner_copper_layers_are_left_alone() -> None:
    """Only the two outer layers are pcbkit's to set (it builds two-layer boards)."""
    inner = '(layer "In1.Cu" (type "copper") (thickness 0.035))'
    stackup = ONE_LINE.replace(
        '\t\t(layer "dielectric 1"', f'\t\t{inner}\n\t\t(layer "dielectric 1"'
    )
    out = kb.set_copper_text(board_text(stackup), 0.07)
    assert inner in out
    assert out.count("(thickness 0.07)") == 2


def test_set_copper_rewrites_the_file_in_place(tmp_path: Path) -> None:
    path = tmp_path / "b.kicad_pcb"
    text = board_text(MULTI_LINE)
    path.write_text(text, encoding="utf-8")
    kb.set_copper(str(path), 0.07)
    assert path.read_text(encoding="utf-8") == kb.set_copper_text(text, 0.07)


def test_set_copper_keeps_windows_line_endings_and_other_bytes(tmp_path: Path) -> None:
    path = tmp_path / "b.kicad_pcb"
    raw = board_text(MULTI_LINE).replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(raw)
    kb.set_copper(str(path), 0.07)
    assert path.read_bytes() == re.sub(
        rb"\(thickness 0\.035\)", b"(thickness 0.07)", raw
    )


def test_set_copper_names_the_file_when_it_finds_no_stackup(tmp_path: Path) -> None:
    path = tmp_path / "plain.kicad_pcb"
    path.write_text("(kicad_pcb\n\t(version 20260101)\n)\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(
        ValueError, match=r"plain\.kicad_pcb: expected two copper layers"
    ):
        kb.set_copper(str(path), 0.07)
    assert path.read_bytes() == before
