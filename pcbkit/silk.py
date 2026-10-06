"""The silkscreen pass: labels, reference designators, the assembly drawing, the title.

``apply_silk`` runs after the board is routed and filled (it is the last thing
``pcbkit finalize`` does to the copper board). It loads ``kicad/<stem>.kicad_pcb``, or
the board it is given, adds the text and saves the board in place:

1. the project's fixed labels (``LABELS``), each at its spot or the nearest one that
   is clear of pads, the parts' own silkscreen and the labels before it;
2. a function label beside each connector in ``CONN_LABELS``;
3. whatever the project's ``extra(board, api)`` draws, if it has that hook;
4. a reference designator for every part, on the silkscreen where there is room around
   its courtyard, otherwise on the fab layer (the assembly drawing);
5. the assembly drawing: a fab-layer reference that overlaps another fab text, or sits
   on a through-hole pin, is moved to a clear place or shrunk;
6. the values, which never print; and the board's title block.

What the project supplies, and the names it uses, are in docs/project-interface.md.
Everything in this module is the engine: nothing in it is about one board.

Placement is greedy and in that order, so what comes first gets the best spot and what
comes later keeps clear of it. Every text the pass adds is also an obstacle for the
texts after it. The geometry is in layout millimetres (``pcbkit.kicad.board``).

Running the pass twice on one board would draw every label on top of itself, so it
refuses a board that carries its text already: see ``SilkError``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import click

from pcbkit import project
from pcbkit.kicad import board as kb
from pcbkit.place import REF_TEXT_MM, REF_THICKNESS_MM, is_number, layout_size
from pcbkit.project import Project, ProjectError

REF_SIZE = REF_TEXT_MM
LBL = 0.8  # the size of a label that does not say

# Where a fixed label may move to when its own spot is taken: (dx, dy) in mm, tried in
# this order. For a label turned 90 degrees the two are swapped.
LABEL_OFFSETS = (
    (0, 0),
    (0, -0.7),
    (0, 0.7),
    (0, -1.4),
    (0, 1.4),
    (-1.5, 0),
    (1.5, 0),
)

# The room kept clear of the board edge for text, and of a pad or a part's silkscreen.
EDGE_MARGIN = 0.6
PAD_MARGIN = 0.2
GRAPHIC_MARGIN = 0.05
TEXT_MARGIN = 0.1

# A text on a layout-millimetre (text, layer id, x, y) key, rounded to a micrometre.
TextKey = tuple[str, int, float, float]


class SilkError(click.ClickException):
    """The silk pass cannot run on this board: it has had the pass already, or more."""


class Boxes:
    """A set of rectangles (x0, y0, x1, y1), and whether another one touches any."""

    def __init__(self) -> None:
        """Start with no rectangles."""
        self.b: list[tuple[float, float, float, float]] = []

    def add(self, x0: float, y0: float, x1: float, y1: float, pad: float = 0.0) -> None:
        """Add the rectangle, grown by ``pad`` on every side."""
        self.b.append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))

    def hit(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Return True if the rectangle overlaps any in the set (touching is not)."""
        for a in self.b:
            if x0 < a[2] and x1 > a[0] and y0 < a[3] and y1 > a[1]:
                return True
        return False


# --- geometry ---------------------------------------------------------------------


def bbox_of(item: Any) -> tuple[float, float, float, float]:
    """Return a board item's bounding box (x0, y0, x1, y1) in layout millimetres."""
    import pcbnew

    bb = item.GetBoundingBox()
    x0 = pcbnew.ToMM(bb.GetX()) - kb.OX
    y0 = pcbnew.ToMM(bb.GetY()) - kb.OY
    return x0, y0, x0 + pcbnew.ToMM(bb.GetWidth()), y0 + pcbnew.ToMM(bb.GetHeight())


def iloc(item: Any) -> tuple[float, float]:
    """Return a board item's position (x, y) in layout millimetres."""
    import pcbnew

    return (pcbnew.ToMM(item.GetX()) - kb.OX, pcbnew.ToMM(item.GetY()) - kb.OY)


def text_box(
    x: float, y: float, text: str, size: float, vertical: bool = False
) -> tuple[float, float, float, float]:
    """Return the box (x0, y0, x1, y1) a text centred on (x, y) is taken to fill.

    It is an estimate from the text's length: 0.9 of the size per character and a
    little to spare. ``vertical`` is for text turned a quarter turn.
    """
    w = len(text) * size * 0.9 + 0.3
    h = size + 0.25
    if vertical:
        w, h = h, w
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def add_text(
    board: Any,
    text: str,
    x: float,
    y: float,
    size: float = LBL,
    rot: float = 0,
    layer: int | None = None,
    bold: bool = False,
    obstacles: Boxes | None = None,
) -> Any:
    """Add a text centred on (x, y) mm, F.SilkS by default, and return it.

    The stroke is 0.15 of ``size``, 0.2 if ``bold``, and never under 0.15 mm (the least
    the fab house prints). With ``obstacles`` given, the text's box is added to it.
    This is not ``pcbkit.kicad.board.add_text``, which has no minimum stroke.
    """
    import pcbnew

    layer = pcbnew.F_SilkS if layer is None else layer
    t = pcbnew.PCB_TEXT(board)
    t.SetText(text)
    t.SetPosition(kb.pt(x, y))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(kb.mm(size), kb.mm(size)))
    t.SetTextThickness(kb.mm(max(0.15, size * (0.2 if bold else 0.15))))
    t.SetTextAngleDegrees(rot)
    board.Add(t)
    if obstacles is not None:
        vertical = rot in (90, -90, 270)
        obstacles.add(*text_box(x, y, text, size, vertical), pad=TEXT_MARGIN)
    return t


def board_texts(board: Any) -> list[TextKey]:
    """Return (text, layer id, x, y) of every text that belongs to the board itself.

    Footprint texts are not included. The position is in layout mm, to a micrometre.
    """
    found = []
    for item in board.GetDrawings():
        if item.GetClass() == "PCB_TEXT":
            x, y = kb.to_local(item.GetPosition())
            found.append(
                (item.GetText(), int(item.GetLayer()), round(x, 3), round(y, 3))
            )
    return found


def new_texts(before: Iterable[TextKey], after: Iterable[TextKey]) -> list[TextKey]:
    """Return the texts in ``after`` that ``before`` lacks (a multiset difference)."""
    return list((Counter(after) - Counter(before)).elements())


def stacked(before: Iterable[TextKey], after: Iterable[TextKey]) -> list[TextKey]:
    """Return the texts added since ``before`` that sit exactly on one already there.

    "Exactly" is the same text on the same layer at the same place (to a micrometre):
    what a second run of the pass would draw over the first.
    """
    seen = set(before)
    return [key for key in new_texts(before, after) if key in seen]


# --- the project's data -----------------------------------------------------------


@dataclass(frozen=True)
class SilkData:
    """The names of silk.py, checked, with the defaults filled in."""

    labels: list[tuple[str, float, float, float, float]]
    conn_labels: dict[str, str]
    hide_ref: frozenset[str]
    keep_ref: frozenset[str]
    extra: Any  # extra(board, api), or None
    company: str
    comments: list[str]
    date: str


def _refs(module: ModuleType, name: str) -> frozenset[str]:
    """Return silk.py's ``name`` as a set of references; empty if it has none."""
    value = getattr(module, name, ())
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ProjectError(
            f"silk.py: {name} should be a set of references, got {value!r}"
        )
    refs = list(value)
    if not all(isinstance(ref, str) for ref in refs):
        raise ProjectError(f"silk.py: {name} should hold only references (strings)")
    return frozenset(refs)


def read_silk(module: ModuleType | None) -> SilkData:
    """Check the names of the project's silk.py and return them.

    Every name is optional, and a project without a silk.py has none of them.
    """
    if module is None:
        return SilkData([], {}, frozenset(), frozenset(), None, "", [], "")
    labels = []
    for index, entry in enumerate(getattr(module, "LABELS", [])):
        if (
            not isinstance(entry, (tuple, list))
            or len(entry) != 5
            or not isinstance(entry[0], str)
            or not all(is_number(v) for v in entry[1:])
        ):
            raise ProjectError(
                f"silk.py: LABELS[{index}] should be (text, x, y, size, rot), "
                f"got {entry!r}"
            )
        labels.append((entry[0], entry[1], entry[2], entry[3], entry[4]))
    conn = getattr(module, "CONN_LABELS", {})
    if not isinstance(conn, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in conn.items()
    ):
        raise ProjectError("silk.py: CONN_LABELS should be a dict of reference to text")
    hide, keep = _refs(module, "HIDE_REF"), _refs(module, "KEEP_REF")
    if hide & keep:
        raise ProjectError(
            "silk.py: in both HIDE_REF and KEEP_REF: " + ", ".join(sorted(hide & keep))
        )
    extra = getattr(module, "extra", None)
    if extra is not None and not callable(extra):
        raise ProjectError("silk.py: extra should be a function (board, api)")
    company = getattr(module, "COMPANY", "")
    date = getattr(module, "DATE", "")
    comments = getattr(module, "COMMENTS", [])
    if not isinstance(company, str) or not isinstance(date, str):
        raise ProjectError("silk.py: COMPANY and DATE should be strings")
    if (
        not isinstance(comments, (list, tuple))
        or len(comments) > 9
        or not all(isinstance(c, str) for c in comments)
    ):
        raise ProjectError(
            "silk.py: COMMENTS should be a list of up to 9 strings (KiCad's limit)"
        )
    return SilkData(
        labels, dict(conn), hide, keep, extra, company, list(comments), date
    )


# --- the pass ---------------------------------------------------------------------


class Silk:
    """What one silkscreen pass shares between its steps, and hands to ``extra``.

    ``board`` is the pcbnew board, ``footprints`` its parts by reference and
    ``obstacles`` the boxes the pass keeps clear of: pads, the parts' own silkscreen,
    the board edge, and every text added so far. ``width`` and ``height`` are the
    board's, from layout.py. Each method is what the engine itself uses.
    """

    def __init__(self, board: Any, width: float, height: float) -> None:
        """Start a pass over ``board``: index its parts, mark what text must avoid."""
        import pcbnew

        self.board = board
        self.width = width
        self.height = height
        self.footprints = {f.GetReference(): f for f in board.GetFootprints()}
        self.obstacles = Boxes()
        self.warnings: list[str] = []
        self.moved: list[str] = []
        obs = self.obstacles
        # board edge margin
        obs.add(-5, -5, width + 5, EDGE_MARGIN)
        obs.add(-5, height - EDGE_MARGIN, width + 5, height + 5)
        obs.add(-5, -5, EDGE_MARGIN, height + 5)
        obs.add(width - EDGE_MARGIN, -5, width + 5, height + 5)
        for fp in board.GetFootprints():
            for p in fp.Pads():
                obs.add(*bbox_of(p), pad=PAD_MARGIN)
            for g in fp.GraphicalItems():
                if g.GetLayer() == pcbnew.F_SilkS and g.GetClass() != "PCB_TEXT":
                    obs.add(*bbox_of(g), pad=GRAPHIC_MARGIN)

    def text(
        self,
        text: str,
        x: float,
        y: float,
        size: float = LBL,
        rot: float = 0,
        layer: int | None = None,
        bold: bool = False,
    ) -> Any:
        """Add a text centred on (x, y) and mark its box, so later texts keep clear."""
        return add_text(self.board, text, x, y, size, rot, layer, bold, self.obstacles)

    def free(
        self, x: float, y: float, text: str, size: float, vertical: bool = False
    ) -> bool:
        """Return True if ``text`` centred on (x, y) would touch nothing."""
        return not self.obstacles.hit(*text_box(x, y, text, size, vertical))

    def place(
        self,
        text: str,
        candidates: Iterable[tuple[float, ...]],
        size: float = LBL,
        bold: bool = False,
    ) -> bool:
        """Add ``text`` at the first of the candidate spots that is free.

        Each candidate is (x, y) or (x, y, rot). Return False, adding nothing, if none
        is free.
        """
        for spot in candidates:
            x, y = spot[0], spot[1]
            rot = spot[2] if len(spot) > 2 else 0
            if self.free(x, y, text, size, rot == 90):
                self.text(text, x, y, size, rot, bold=bold)
                return True
        return False

    def warn(self, message: str) -> None:
        """Record something the user should look at in the result."""
        self.warnings.append(message)


def _fixed_labels(
    silk: Silk, labels: list[tuple[str, float, float, float, float]]
) -> None:
    """Place LABELS: each at its own spot, else the nearest free LABEL_OFFSETS one."""
    for text, x, y, size, rot in labels:
        placed = False
        for dx, dy in LABEL_OFFSETS:
            if rot == 90:
                dx, dy = dy, dx
            if silk.free(x + dx, y + dy, text, size, rot == 90):
                silk.text(text, x + dx, y + dy, size, rot, bold=size >= 1.0)
                placed = True
                break
        if not placed:
            silk.warn(f"label collides: {text}")
            silk.text(text, x, y, size, rot, bold=size >= 1.0)


def _courtyard(fp: Any) -> tuple[float, float, float, float]:
    """Return the box of a footprint's courtyard in layout millimetres."""
    import pcbnew

    bb = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
    bx0 = pcbnew.ToMM(bb.GetX()) - kb.OX
    by0 = pcbnew.ToMM(bb.GetY()) - kb.OY
    return bx0, by0, bx0 + pcbnew.ToMM(bb.GetWidth()), by0 + pcbnew.ToMM(bb.GetHeight())


def _connector_labels(silk: Silk, labels: Mapping[str, str]) -> None:
    """Place CONN_LABELS: each beside its part, on the first side with room."""
    for ref, txt in labels.items():
        bx0, by0, bx1, by1 = _courtyard(silk.footprints[ref])
        cx, cyy = (bx0 + bx1) / 2, (by0 + by1) / 2
        tw = len(txt) * LBL * 0.9
        cands = [
            (cx, by1 + 0.6, 0),
            (cx, by0 - 0.6, 0),
            (bx1 + 0.7, cyy, 90),
            (bx0 - 0.7, cyy, 90),
            (bx0 + tw / 2, by1 + 0.6, 0),
            (bx1 - tw / 2, by1 + 0.6, 0),
            (bx0 + tw / 2, by0 - 0.6, 0),
            (bx1 - tw / 2, by0 - 0.6, 0),
            (cx, by1 + 1.4, 0),
            (cx, by0 - 1.4, 0),
            (bx1 + tw / 2 + 0.5, cyy, 0),
            (bx0 - tw / 2 - 0.5, cyy, 0),
        ]
        if not silk.place(txt, cands, LBL):
            silk.warn(f"no room for label {ref} {txt}")


def _references(silk: Silk, hide: frozenset[str], keep: frozenset[str]) -> None:
    """Place each reference designator: try spots around the courtyard, else go to fab.

    Parts with the longest references go first. One in ``hide`` goes straight to the
    fab layer; one in ``keep`` is left as its footprint has it.
    """
    import pcbnew

    obs = silk.obstacles
    fps = sorted(silk.board.GetFootprints(), key=lambda f: -len(f.GetReference()))
    for fp in fps:
        ref = fp.GetReference()
        rt = fp.Reference()
        rt.SetTextSize(pcbnew.VECTOR2I(kb.mm(REF_SIZE), kb.mm(REF_SIZE)))
        rt.SetTextThickness(kb.mm(REF_THICKNESS_MM))
        if ref in hide:
            rt.SetLayer(pcbnew.F_Fab)
            continue
        if ref in keep:
            continue
        if fp.GetCourtyard(pcbnew.F_CrtYd).OutlineCount():
            bx0, by0, bx1, by1 = _courtyard(fp)
        else:
            bx0, by0, bx1, by1 = bbox_of(fp)
        cx, cyy = (bx0 + bx1) / 2, (by0 + by1) / 2
        tw = len(ref) * REF_SIZE * 0.9
        cands = [
            (cx, by0 - 0.55, 0),
            (cx, by1 + 0.55, 0),
            (bx0 - tw / 2 - 0.3, cyy, 0),
            (bx1 + tw / 2 + 0.3, cyy, 0),
            (bx0 - 0.55, cyy, 90),
            (bx1 + 0.55, cyy, 90),
            (cx, cyy, 0),
        ]
        done = False
        for x, y, rot in cands:
            b = text_box(x, y, ref, REF_SIZE, rot == 90)
            if not obs.hit(*b):
                rt.SetLayer(pcbnew.F_SilkS)
                rt.SetPosition(kb.pt(x, y))
                rt.SetTextAngleDegrees(rot)
                rt.SetKeepUpright(True)
                obs.add(*b, pad=TEXT_MARGIN)
                done = True
                break
        if not done:
            rt.SetLayer(pcbnew.F_Fab)
            # the footprint may already print its ref on F.Fab (${REFERENCE}): don't
            # print it twice
            if any(
                it.GetClass() in ("PCB_TEXT", "FP_TEXT")
                and it.GetLayer() == pcbnew.F_Fab
                and it.GetText() == "${REFERENCE}"
                for it in fp.GraphicalItems()
            ):
                rt.SetVisible(False)
            silk.moved.append(ref)


def _assembly_drawing(silk: Silk, keep: frozenset[str]) -> None:
    """Move a fab-layer reference that collides with another fab text or a drilled hole.

    A fab reference that overlaps another fab text, or sits on a through-hole pad (a
    connector's pins), is moved: first to a clear spot inside its own outline, then
    just outside it, preferring spots off other parts; it is shrunk if nothing fits.
    Parts in ``keep`` are left alone, but their texts still count as in the way.
    """
    import pcbnew

    board = silk.board

    def fab_texts() -> list[tuple[Any, Any]]:
        out = []
        for f in board.GetFootprints():
            items = [f.Reference()] + [
                g for g in f.GraphicalItems() if g.GetClass() in ("PCB_TEXT", "FP_TEXT")
            ]
            for t in items:
                if t.GetLayer() == pcbnew.F_Fab and t.IsVisible():
                    out.append((f, t))
        return out

    def box(t: Any) -> tuple[int, int, int, int]:
        bb = t.GetBoundingBox()
        return (bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom())

    def hits(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    # the drawing shows through-hole pins as filled drill dots
    tht = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                c, d = p.GetPosition(), p.GetDrillSize()
                tht.append(
                    (c.x - d.x // 2, c.y - d.y // 2, c.x + d.x // 2, c.y + d.y // 2)
                )
    crt = {}
    for f in board.GetFootprints():
        c = f.GetCourtyard(pcbnew.F_CrtYd)
        if c.OutlineCount():
            bb = c.BBox()
            crt[f.GetReference()] = (
                bb.GetX(),
                bb.GetY(),
                bb.GetRight(),
                bb.GetBottom(),
            )
    edge = (
        kb.mm(kb.OX + 0.3),
        kb.mm(kb.OY + 0.3),
        kb.mm(kb.OX + silk.width - 0.3),
        kb.mm(kb.OY + silk.height - 0.3),
    )

    def key(f: Any, t: Any) -> tuple[str, str, str]:
        # fields and footprint texts don't carry distinct uuids through SWIG;
        # identify by content
        return (f.GetReference(), t.GetClass(), t.GetText())

    def clear(f: Any, t: Any, strict: bool) -> bool:
        b = box(t)
        if not (
            b[0] >= edge[0] and b[1] >= edge[1] and b[2] <= edge[2] and b[3] <= edge[3]
        ):
            return False
        if any(hits(b, o) for o in tht):
            return False
        if any(hits(b, box(o)) for g, o in fab_texts() if key(g, o) != key(f, t)):
            return False
        return not strict or not any(
            hits(b, c) for r, c in crt.items() if r != f.GetReference()
        )

    def is_ref(f: Any, t: Any) -> bool:
        return t.GetText() in ("${REFERENCE}", f.GetReference())

    # one designator per part: where the footprint prints ${REFERENCE} on F.Fab and the
    # ref field also landed there, hide the field (a footprint text's hidden flag isn't
    # saved; a field's is)
    for f in board.GetFootprints():
        refs = [
            t
            for g, t in fab_texts()
            if g.GetReference() == f.GetReference() and is_ref(f, t)
        ]
        if len(refs) > 1 and any(t.GetClass() != "PCB_FIELD" for t in refs):
            for t in refs:
                if t.GetClass() == "PCB_FIELD":
                    t.SetVisible(False)
    for f, t in fab_texts():
        if f.GetReference() in keep or not is_ref(f, t) or clear(f, t, False):
            continue
        x0, y0, x1, y1 = crt.get(f.GetReference(), box(t))
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        g = kb.mm(0.15)
        placed = False
        for shrink in (1.0, 0.7):
            if shrink != 1.0:
                sz = int(t.GetTextHeight() * shrink)
                t.SetTextSize(pcbnew.VECTOR2I(sz, sz))
                t.SetTextThickness(max(int(sz * 0.15), kb.mm(0.08)))
            for ang in (
                t.GetTextAngleDegrees() % 180,
                (t.GetTextAngleDegrees() + 90) % 180,
            ):
                t.SetTextAngleDegrees(ang)
                t.SetPosition(pcbnew.VECTOR2I(cx, cy))
                b = box(t)
                hw, hh = (b[2] - b[0]) // 2, (b[3] - b[1]) // 2
                inside = [
                    (cx, cy),
                    (cx, y0 + hh + g),
                    (cx, y1 - hh - g),
                    (x0 + hw + g, cy),
                    (x1 - hw - g, cy),
                ]
                outside = [
                    (cx, y0 - hh - g),
                    (cx, y1 + hh + g),
                    (x0 - hw - g, cy),
                    (x1 + hw + g, cy),
                ]
                for strict, spots in (
                    (True, inside),
                    (True, outside),
                    (False, inside),
                    (False, outside),
                ):
                    for x, y in spots:
                        t.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                        if clear(f, t, strict):
                            placed = True
                            break
                    if placed:
                        break
                if placed:
                    break
            if placed:
                break
        if not placed:
            silk.warn(f"assembly drawing: no clear spot for {f.GetReference()}")


def _title_block(board: Any, config: Any, data: SilkData) -> None:
    """Fill the title block: the [board] title and revision, and silk.py's extras."""
    block = board.GetTitleBlock()
    block.SetTitle(config.title)
    block.SetRevision(config.rev)
    block.SetDate(data.date)
    block.SetCompany(data.company)
    for index, text in enumerate(data.comments):
        block.SetComment(index, text)


@dataclass(frozen=True)
class SilkResult:
    """What ``apply_silk`` did to the board.

    ``texts`` is the number of texts it added to the board itself (labels, and whatever
    the project's ``extra`` drew; reference designators are footprint text and are not
    counted). ``moved_to_fab`` lists, sorted, the references that had no room on the
    silkscreen and went to the fab layer (the assembly drawing). ``warnings`` are things
    that did not fit: look at each.
    """

    pcb: Path
    texts: int
    moved_to_fab: list[str]
    warnings: list[str]


def apply_silk(proj: Project, pcb: Path | None = None) -> SilkResult:
    """Add the silkscreen and the fab-layer text to a board, saving it in place.

    The board is ``kicad/<stem>.kicad_pcb`` unless ``pcb`` names another. The project's
    ``layout.py`` gives the board size, and its ``silk.py`` (optional) says what to
    draw; see the module docstring. Needs pcbnew.

    Raise SilkError, and leave the file as it is, if the board already carries the
    texts this pass would add: they would be drawn twice, on top of each other.
    """
    import pcbnew

    path = (
        Path(pcb)
        if pcb is not None
        else proj.kicad_dir / (proj.config.board.stem + ".kicad_pcb")
    )
    if not path.is_file():
        raise click.ClickException(f"{path} not found: run `pcbkit build` first")
    layout = project.import_project_module(proj.root, "layout")
    width, height = layout_size(layout)
    data = read_silk(project.import_optional_project_module(proj.root, "silk"))
    board = pcbnew.LoadBoard(str(path))
    before = board_texts(board)
    silk = Silk(board, width, height)
    absent = sorted(set(data.conn_labels) - set(silk.footprints))
    if absent:
        raise ProjectError(
            f"silk.py: CONN_LABELS names {', '.join(absent)}, which is not on the board"
        )
    for name, refs in (("HIDE_REF", data.hide_ref), ("KEEP_REF", data.keep_ref)):
        absent = sorted(refs - set(silk.footprints))
        if absent:
            silk.warn(f"silk.py: {name} names {', '.join(absent)}, not on the board")

    _fixed_labels(silk, data.labels)
    _connector_labels(silk, data.conn_labels)
    if data.extra is not None:
        data.extra(board, silk)
    _references(silk, data.hide_ref, data.keep_ref)
    _assembly_drawing(silk, data.keep_ref)
    # values never on silk
    for fp in board.GetFootprints():
        fp.Value().SetLayer(pcbnew.F_Fab)
        fp.Value().SetVisible(False)
    _title_block(board, proj.config.board, data)

    after = board_texts(board)
    twice = stacked(before, after)
    if twice:
        text, _, x, y = twice[0]
        raise SilkError(
            f"{path.name} already carries silkscreen text from an earlier run: "
            f"{len(twice)} of the {len(new_texts(before, after))} texts this run adds "
            f"are already on the board at the same place (the first is {text!r} at "
            f"{x:g}, {y:g}). Running the pass twice draws every label on top of "
            "itself. Run it on a board that has not had it: the routed board, before "
            "silk. Nothing was written."
        )
    pcbnew.SaveBoard(str(path), board)
    return SilkResult(
        path, len(new_texts(before, after)), sorted(silk.moved), silk.warnings
    )


def format_result(result: SilkResult, root: Path) -> str:
    """Return the text a command prints for a finished silk pass."""
    try:
        shown = str(result.pcb.relative_to(root))
    except ValueError:
        shown = str(result.pcb)
    lines = [f"silk       {shown}: {result.texts} text(s) added"]
    if result.moved_to_fab:
        lines.append(
            "  refs moved to fab (see assembly drawing): "
            + " ".join(result.moved_to_fab)
        )
    lines.extend(f"  warning: {warning}" for warning in result.warnings)
    return "\n".join(lines)
