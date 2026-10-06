"""Generate a KiCad schematic from a Design, check it with ERC and export the netlist.

The schematic is drawn in label-per-pin style: each part is a symbol, and each of its
connected pins carries a net label at its tip, so nets are joined by name and no wire
is ever routed. The parts are packed block by block onto one sheet. The PCB is later
built from the netlist KiCad exports from this schematic, so schematic and board
cannot drift apart.

``generate`` makes the schematic text and touches nothing outside the arguments it is
given; ``build_schematic`` is what ``pcbkit sch`` runs: project libraries, schematic,
ERC, netlist.

The placement and label arithmetic, and the seeds the UUIDs are derived from, are the
ones the schematic generator has always used: the same design gives the same file, so
the schematic's links to a board built from it keep working.
"""

from __future__ import annotations

import copy
import datetime
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from pcbkit.design import Design, DesignError, Part, load_design
from pcbkit.kicad import env
from pcbkit.kicad.sexp import QStr, dump, find, findall, parse, q, walk
from pcbkit.libs import ProjectLibs, write_project_libs
from pcbkit.project import BoardConfig, Project

Node = list  # an S-expression list, as in pcbkit.kicad.sexp
Point = tuple[float, float]
BlockRect = tuple[str, float, float, float, float]  # name, x0, y0, x1, y1

# Width in mm of a block that BLOCK_WIDTHS does not size, and the sheet it is packed on.
DEFAULT_BLOCK_W = 130
PAGE_W = 594  # A2 is 594 x 420 mm; only the width limits the packing
PAPER = "A2"
MARGIN = 12

# UUIDs are uuid5 of "<seed>#<n>" in this namespace, so regenerating a schematic keeps
# every item's UUID, and with it the links a board has to the schematic's symbols.
NAMESPACE = uuid.UUID("5b0f3c2e-8d1a-4c77-9a53-5b1d0c0ffee5")

# Seeds of the three kinds of item that carry no reference. The first generator derived
# them from the line numbers of its own source ("auto:210" and so on); the strings are
# kept so that UUIDs do not change.
SEED_BLOCK_BOX = "auto:210"
SEED_BLOCK_TITLE = "auto:212"
SEED_NOTE = "auto:218"

KICAD_CLI_TIMEOUT = 600.0  # seconds; ERC of a large sheet takes a few


# --- symbol libraries -------------------------------------------------------------


class SymbolLibs:
    """KiCad symbol libraries on disk: KiCad's stock ones and the project's own.

    A library file is parsed the first time a symbol from it is asked for, and kept for
    the life of this object, so one schematic reads each file once.
    """

    def __init__(self, stock: Path, project: Mapping[str, Path]) -> None:
        self.stock = Path(stock)
        self.project = dict(project)
        self._cache: dict[str, Node] = {}

    def path(self, name: str) -> Path:
        """Return the file of library ``name``: the project's own, else a stock one."""
        return self.project.get(name) or self.stock / f"{name}.kicad_sym"

    def load(self, name: str) -> Node:
        """Return library ``name`` parsed, reading its file on first use."""
        if name not in self._cache:
            path = self.path(name)
            try:
                tree = parse(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                known = ", ".join(sorted(self.project)) or "none"
                raise click.ClickException(
                    f"symbol library {name!r} not found: no file {path} "
                    f"(the project's own libraries: {known})"
                ) from None
            except ValueError as err:  # the S-expression parser's complaint
                raise click.ClickException(
                    f"symbol library {path} cannot be read: {err}"
                ) from None
            # the parser gives [] for text with an unclosed bracket, so check the head
            if not tree or tree[0] != "kicad_symbol_lib":
                raise click.ClickException(
                    f"symbol library {path} is not a complete KiCad symbol library"
                )
            self._cache[name] = tree
        return self._cache[name]

    def raw_symbol(self, lib: str, name: str) -> Node:
        """Return symbol ``name`` of library ``lib`` exactly as the library has it."""
        for s in findall(self.load(lib), "symbol"):
            if s[1] == name:
                return s
        raise click.ClickException(f"symbol {lib}:{name} not found in {self.path(lib)}")

    def flat_symbol(self, lib: str, name: str) -> Node:
        """Return the symbol with any `extends` resolved.

        KiCad schematics need flat symbols.
        """
        s = copy.deepcopy(self.raw_symbol(lib, name))
        ext = find(s, "extends")
        if not ext:
            return s
        parent = self.flat_symbol(lib, ext[1])
        out: list[Any] = [s[0], QStr(name)]
        child_props = findall(s, "property")
        child_keys = {p[1] for p in child_props}
        for e in parent[2:]:
            if isinstance(e, list) and e and e[0] == "property":
                if e[1] in child_keys:
                    continue
                out.append(e)
            elif isinstance(e, list) and e and e[0] == "symbol":
                sub = copy.deepcopy(e)
                sub[1] = QStr(str(e[1]).replace(parent[1], name, 1))
                out.append(("SUB", sub))
            else:
                out.append(e)
        # insert child's properties before sub-symbols
        subs = [x[1] for x in out if isinstance(x, tuple)]
        base = [
            x
            for x in out
            if not isinstance(x, tuple)
            and not (isinstance(x, list) and x and x[0] == "embedded_fonts")
        ]
        base += child_props + subs + [["embedded_fonts", "no"]]
        return base


def sym_pins(s: Node) -> dict[str, dict[str, Any]]:
    """Return a symbol's pins by number: position, angle, name and electrical type."""
    pins = {}
    for n in walk(s):
        if n and n[0] == "pin" and len(n) > 2 and not isinstance(n[1], list):
            at = find(n, "at")
            num = find(n, "number")[1]
            pins[str(num)] = {
                "x": float(at[1]),
                "y": float(at[2]),
                "ang": int(float(at[3])),
                "name": str(find(n, "name")[1]),
                "type": n[1],
            }
    return pins


def sym_body_bbox(s: Node) -> tuple[float, float, float, float]:
    """Return a symbol's bounding box (x0, y0, x1, y1) in library coordinates (y up)."""
    xs, ys = [], []
    for n in walk(s):
        if not n or not isinstance(n[0], str):
            continue
        if n[0] in ("start", "end", "xy", "mid", "center") and len(n) >= 3:
            try:
                xs.append(float(n[1]))
                ys.append(float(n[2]))
            except ValueError:
                pass
    for p in sym_pins(s).values():
        xs.append(p["x"])
        ys.append(p["y"])
    if not xs:
        return (-2.54, -2.54, 2.54, 2.54)
    return (min(xs), min(ys), max(xs), max(ys))  # lib coords (y up)


def label_len(text: str) -> float:
    """Return the width in mm that a net label takes on the sheet."""
    return len(text) * 1.1 + 1.5


def snap(v: float, g: float = 2.54) -> float:
    """Round ``v`` to the nearest multiple of the grid ``g``."""
    return round(v / g) * g


def font(size: float = 1.27, bold: bool = False) -> Node:
    """Return a font node of the given size, optionally bold."""
    f: Node = ["font", ["size", size, size]]
    if bold:
        f.append(["bold", "yes"])
    return f


def uid_factory() -> Callable[[str], QStr]:
    """Return a function that makes UUIDs from seeds, counting each seed's uses.

    The same seed gives a new UUID each time (``seed#0``, ``seed#1``, ...), and a fresh
    factory starts every count again, so two schematics never share a count.
    """
    seen: dict[str, int] = {}

    def uid(seed: str) -> QStr:
        """Return the next deterministic UUID for ``seed``."""
        n = seen.get(seed, 0)
        seen[seed] = n + 1
        return q(str(uuid.uuid5(NAMESPACE, f"{seed}#{n}")))

    return uid


# --- laying the sheet out ---------------------------------------------------------


def _lib_id(p: Part) -> tuple[str, str]:
    """Return the library and symbol name of part ``p``, from its ``Lib:Name``."""
    lib, colon, name = p["sym"].partition(":")
    if not colon or not lib or not name or ":" in name:
        raise DesignError(
            f"part {p['ref']}: symbol {p['sym']!r} should be written Library:Name"
        )
    return lib, name


def measure_parts(
    parts: tuple[Part, ...], libs: SymbolLibs
) -> tuple[dict[str, Node], dict[str, dict[str, Any]]]:
    """Return each used symbol (flat, named ``Lib:Name``) and each part's extents.

    A part's extent is the room it needs on the sheet: its symbol, the net labels at
    its pin tips and the reference and value text, as (x0, y0, x1, y1) with y down.
    """
    libsyms: dict[str, Node] = {}
    info: dict[str, dict[str, Any]] = {}
    for p in parts:
        lib, name = _lib_id(p)
        if p["sym"] not in libsyms:
            fs = libs.flat_symbol(lib, name)
            fs[1] = QStr(p["sym"])
            libsyms[p["sym"]] = fs
        s = libsyms[p["sym"]]
        pins = sym_pins(s)
        for pn in p["pins"]:
            if pn not in pins:
                raise DesignError(
                    f"{p['ref']}: pin {pn} not in symbol {p['sym']} ({list(pins)})"
                )
        bx0, by0, bx1, by1 = sym_body_bbox(s)
        # extents in schematic coords relative to origin (y down)
        ex0, ex1, ey0, ey1 = bx0, bx1, -by1, -by0
        for pn, pin in pins.items():
            net = p["pins"].get(pn)
            if not net:
                continue
            L = label_len(net)
            tx, ty = pin["x"], -pin["y"]
            if pin["ang"] == 0:
                ex0 = min(ex0, tx - L)
            elif pin["ang"] == 180:
                ex1 = max(ex1, tx + L)
            elif pin["ang"] == 90:  # pin at bottom, label goes down
                ey1 = max(ey1, ty + L)
            elif pin["ang"] == 270:  # pin at top, label goes up
                ey0 = min(ey0, ty - L)
        # room for reference/value text on the right / below
        tw = max(len(p["ref"]), len(p["value"])) * 1.1 + 2
        ex1 = max(ex1, bx1 + tw + 1.5) if (bx1 - bx0) < 6 else ex1
        ey0 -= 3.0
        ey1 += 3.0
        info[p["ref"]] = {
            "pins": pins,
            "ext": (ex0, ey0, ex1, ey1),
            "body": (bx0, by0, bx1, by1),
        }
    return libsyms, info


def pack_blocks(
    design: Design, info: dict[str, dict[str, Any]]
) -> tuple[dict[str, Point], list[BlockRect]]:
    """Return where each part goes and the rectangle of each block.

    Blocks are laid out left to right in shelves down the page; the parts inside a
    block are packed the same way within the block's width.
    """
    blocks: dict[str, list[Part]] = {}
    for p in design.parts:
        blocks.setdefault(p["block"], []).append(p)
    placed: dict[str, Point] = {}
    block_rects: list[BlockRect] = []
    page_w = PAGE_W
    margin = MARGIN
    cur_x, cur_y, shelf_h = margin, margin + 4, 0
    for bname in design.block_order:
        items = blocks[bname]
        W = design.block_widths.get(bname, DEFAULT_BLOCK_W)
        # shelf pack items inside block
        x, y, row_h = 0.0, 8.0, 0.0
        local = {}
        for p in items:
            ex0, ey0, ex1, ey1 = info[p["ref"]]["ext"]
            w, h = ex1 - ex0 + 2.54, ey1 - ey0 + 2.54
            if x + w > W and x > 0:
                x = 0.0
                y += row_h
                row_h = 0.0
            ox, oy = snap(x - ex0), snap(y - ey0)
            local[p["ref"]] = (ox, oy)
            x += w
            row_h = max(row_h, h)
        bw = W
        bh = y + row_h + 2
        if cur_x + bw > page_w - margin:
            cur_x = margin
            cur_y += shelf_h + 8
            shelf_h = 0
        for ref, (ox, oy) in local.items():
            placed[ref] = (snap(cur_x + 2 + ox), snap(cur_y + oy))
        block_rects.append((bname, cur_x, cur_y, cur_x + bw, cur_y + bh))
        cur_x += bw + 8
        shelf_h = max(shelf_h, bh)
    return placed, block_rects


def sheet_items(
    design: Design,
    block_rects: list[BlockRect],
    uid: Callable[[str], QStr],
) -> list[Node]:
    """Return the dashed block rectangles with their headings, and the notes below."""
    items: list[Node] = []
    for bname, x0, y0, x1, y1 in block_rects:
        bname = design.block_titles.get(bname, bname)
        items.append(
            [
                "rectangle",
                ["start", x0, y0],
                ["end", x1, y1],
                ["stroke", ["width", 0.2], ["type", "dash"]],
                ["fill", ["type", "none"]],
                ["uuid", uid(SEED_BLOCK_BOX)],
            ]
        )
        items.append(
            [
                "text",
                q(bname),
                ["exclude_from_sim", "no"],
                ["at", x0 + 2, y0 + 5, 0],
                ["effects", font(2.5, True), ["justify", "left", "bottom"]],
                ["uuid", uid(SEED_BLOCK_TITLE)],
            ]
        )
    # notes block below everything
    ny = max(r[4] for r in block_rects) + 10
    for i, line in enumerate(design.notes):
        items.append(
            [
                "text",
                q(line),
                ["exclude_from_sim", "no"],
                ["at", MARGIN + 2, ny + i * 5, 0],
                [
                    "effects",
                    font(2.0 if i == 0 else 1.6, i == 0),
                    ["justify", "left", "bottom"],
                ],
                ["uuid", uid(SEED_NOTE)],
            ]
        )
    return items


def _prop(
    k: str,
    v: str,
    at: list[float],
    hide: bool = False,
    j: Node | None = None,
) -> Node:
    """Return a symbol property node: its name, text, position and flags."""
    eff: Node = ["effects", font()]
    if j:
        eff.append(j)
    if hide:
        eff.append(["hide", "yes"])
    return ["property", q(k), q(v), ["at"] + at, eff]


def symbol_instance(
    p: Part,
    at: tuple[float, float],
    inf: dict[str, Any],
    stem: str,
    root_uuid: str,
    uid: Callable[[str], QStr],
) -> tuple[Node, list[Node]]:
    """Return a part's symbol node and the labels or no-connects at its pin tips."""
    X, Y = at
    bx0, by0, bx1, by1 = inf["body"]
    small = (bx1 - bx0) < 6
    if small:
        ref_at = [X + bx1 + 1.5, Y - 1.27, 0]
        val_at = [X + bx1 + 1.5, Y + 1.27, 0]
        just = ["justify", "left"]
    else:
        ref_at = [X, Y - by1 - 1.5, 0]
        val_at = [X, Y - by0 + 2.0, 0]
        just = None

    hidden = p["ref"].startswith("#")
    props = [
        _prop("Reference", p["ref"], ref_at, j=just, hide=hidden),
        _prop("Value", p["value"], val_at, j=just, hide=hidden),
        _prop("Footprint", p["fp"], [X, Y, 0], True),
        _prop("Datasheet", "~", [X, Y, 0], True),
        _prop("Description", p["desc"], [X, Y, 0], True),
    ]
    if p["mpn"]:
        props.append(_prop("MPN", p["mpn"], [X, Y, 0], True))
    if p["mfr"]:
        props.append(_prop("Manufacturer", p["mfr"], [X, Y, 0], True))
    in_bom = "yes" if p["bom"] else "no"
    inst = [
        "symbol",
        ["lib_id", q(p["sym"])],
        ["at", X, Y, 0],
        ["unit", 1],
        ["exclude_from_sim", "no"],
        ["in_bom", in_bom],
        ["on_board", "yes"],
        ["dnp", "yes" if p["dnp"] else "no"],
        ["uuid", uid("sym:" + p["ref"])],
    ] + props
    for pn in inf["pins"]:
        inst.append(["pin", q(pn), ["uuid", uid(f"pin:{p['ref']}:{pn}")]])
    inst.append(
        [
            "instances",
            [
                "project",
                q(stem),
                ["path", q("/" + root_uuid), ["reference", q(p["ref"])], ["unit", 1]],
            ],
        ]
    )
    # labels / no-connects at pin tips
    marks: list[Node] = []
    for pn, pin in inf["pins"].items():
        tx, ty = X + pin["x"], Y - pin["y"]
        net = p["pins"].get(pn)
        if net is None:
            if p["ref"].startswith("#") or p["sym"].startswith("Mechanical"):
                continue
            marks.append(
                [
                    "no_connect",
                    ["at", tx, ty],
                    ["uuid", uid(f"nc:{p['ref']}:{pn}")],
                ]
            )
            continue
        ang = {0: 180, 180: 0, 90: 270, 270: 90}[pin["ang"]]
        j = ["justify", "right" if ang in (180, 270) else "left", "bottom"]
        marks.append(
            [
                "label",
                q(net),
                ["at", tx, ty, ang],
                ["fields_autoplaced", "yes"],
                ["effects", font(), j],
                ["uuid", uid(f"lbl:{p['ref']}:{pn}")],
            ]
        )
    return inst, marks


def generate(
    design: Design,
    board: BoardConfig,
    stock_symbols: Path,
    project_symbols: Mapping[str, Path] | None = None,
    date: datetime.date | None = None,
) -> str:
    """Return the text of the schematic for ``design``.

    ``stock_symbols`` is KiCad's symbol library folder and ``project_symbols`` maps the
    project's own library nicknames to their ``.kicad_sym`` files. The title block
    carries ``date``, today by default; give one to make the text reproducible.
    """
    libs = SymbolLibs(stock_symbols, project_symbols or {})
    uid = uid_factory()
    root_uuid = str(uuid.uuid5(NAMESPACE, "root"))
    libsyms, info = measure_parts(design.parts, libs)
    placed, block_rects = pack_blocks(design, info)
    items = sheet_items(design, block_rects, uid)
    sym_insts = []
    for p in design.parts:
        inst, marks = symbol_instance(
            p, placed[p["ref"]], info[p["ref"]], board.stem, root_uuid, uid
        )
        sym_insts.append(inst)
        items.extend(marks)

    when = datetime.date.today() if date is None else date
    title_block: Node = [
        "title_block",
        ["title", q(board.title)],
        ["date", q(when.isoformat())],
        ["rev", q(board.rev)],
    ]
    if design.company:
        title_block.append(["company", q(design.company)])
    if design.comment:
        title_block.append(["comment", 1, q(design.comment)])
    sch = [
        "kicad_sch",
        ["version", 20250114],
        ["generator", q("eeschema")],
        ["generator_version", q("9.0")],
        ["uuid", q(root_uuid)],
        ["paper", q(PAPER)],
        title_block,
        ["lib_symbols"] + list(libsyms.values()),
    ]
    sch = (
        sch
        + items
        + sym_insts
        + [
            ["sheet_instances", ["path", q("/"), ["page", q("1")]]],
            ["embedded_fonts", "no"],
        ]
    )
    return dump(sch) + "\n"


def write_schematic(
    design: Design,
    board: BoardConfig,
    out_dir: Path,
    stock_symbols: Path,
    project_symbols: Mapping[str, Path] | None = None,
    date: datetime.date | None = None,
) -> Path:
    """Write ``<out_dir>/<stem>.kicad_sch`` and return its path."""
    text = generate(design, board, stock_symbols, project_symbols, date)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{board.stem}.kicad_sch"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


# --- ERC and the netlist ----------------------------------------------------------

# The line that ends the findings in kicad-cli's text report, for example
# "** ERC messages: 2  Errors 2  Warnings 0".
_ERC_SUMMARY = re.compile(r"ERC messages:\s*(\d+)\s+Errors\s+(\d+)\s+Warnings\s+(\d+)")
_ERC_HEAD = re.compile(r"^\[(\w+)\]: (.*)$")


@dataclass(frozen=True)
class Violation:
    """One ERC finding: its rule, severity, message and where it is on the sheet."""

    code: str
    severity: str
    message: str
    where: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErcReport:
    """The outcome of an ERC run: the counts, and every finding in the report."""

    errors: int
    warnings: int
    violations: tuple[Violation, ...] = ()


def parse_erc_report(text: str) -> ErcReport:
    """Read the counts and findings from the text report kicad-cli writes."""
    summary = _ERC_SUMMARY.search(text)
    if summary is None:
        raise click.ClickException("the ERC report has no 'ERC messages' summary line")
    raw: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = _ERC_HEAD.match(line)
        if match:
            raw.append(
                {
                    "code": match.group(1),
                    "message": match.group(2),
                    "severity": "",
                    "where": [],
                }
            )
        elif raw and stripped.startswith(";"):
            raw[-1]["severity"] = stripped.lstrip("; ").strip()
        elif raw and stripped.startswith("@("):
            raw[-1]["where"].append(stripped)
    found = tuple(
        Violation(r["code"], r["severity"], r["message"], tuple(r["where"]))
        for r in raw
    )
    return ErcReport(int(summary.group(2)), int(summary.group(3)), found)


def run_erc(kicad_cli: str, schematic: Path, report: Path) -> ErcReport:
    """Run kicad-cli's ERC on ``schematic``, save the text report and parse it."""
    report.unlink(missing_ok=True)
    args = [kicad_cli, "sch", "erc", "-o", str(report), str(schematic)]
    run = env._run(args, timeout=KICAD_CLI_TIMEOUT)
    if run.returncode != 0 or not report.is_file():
        reason = run.error or run.output.strip() or f"exit code {run.returncode}"
        raise click.ClickException(f"kicad-cli could not run ERC: {reason}")
    return parse_erc_report(report.read_text(encoding="utf-8"))


def export_netlist(kicad_cli: str, schematic: Path, netlist: Path) -> None:
    """Export the schematic's netlist (KiCad S-expression format) to ``netlist``."""
    netlist.unlink(missing_ok=True)
    args = [kicad_cli, "sch", "export", "netlist", "--format", "kicadsexpr"]
    run = env._run(
        [*args, "-o", str(netlist), str(schematic)], timeout=KICAD_CLI_TIMEOUT
    )
    if run.returncode != 0 or not netlist.is_file():
        reason = run.error or run.output.strip() or f"exit code {run.returncode}"
        raise click.ClickException(f"kicad-cli could not export the netlist: {reason}")


@dataclass(frozen=True)
class SchematicResult:
    """What ``build_schematic`` made: the files, the part count and the ERC outcome."""

    schematic: Path
    netlist: Path
    erc_report: Path
    parts: int
    libs: ProjectLibs
    erc: ErcReport


def build_schematic(
    proj: Project, date: datetime.date | None = None
) -> SchematicResult:
    """Make the project's libraries and schematic, run ERC and export the netlist.

    Everything lands in the project's kicad/ folder. A failed ERC does not stop the
    netlist being written; the caller decides what the counts mean.
    """
    tool = env.find_kicad_cli()
    if tool is None:
        raise click.ClickException("kicad-cli not found: run `pcbkit doctor`")
    stock = env.symbols_dir()
    board = proj.config.board
    design = load_design(proj.root / "design.py")
    libs = write_project_libs(proj)
    schematic = write_schematic(
        design, board, proj.kicad_dir, stock, libs.symbol_libs, date
    )
    report = proj.kicad_dir / "erc.rpt"
    erc = run_erc(tool.path, schematic, report)
    netlist = proj.kicad_dir / f"{board.stem}.net"
    export_netlist(tool.path, schematic, netlist)
    return SchematicResult(schematic, netlist, report, len(design.parts), libs, erc)


def _shown(path: Path, root: Path) -> str:
    """Return ``path`` relative to the project root when it is inside it."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _plural(count: int, noun: str) -> str:
    """Return "1 error" or "2 errors"."""
    return f"{count} {noun}" + ("" if count == 1 else "s")


def format_result(result: SchematicResult, root: Path, limit: int = 20) -> str:
    """Return the text `pcbkit sch` prints: what was written and what ERC found.

    At most ``limit`` findings are listed; the ERC report has the rest.
    """
    lines = []
    libs = result.libs
    if libs.symbol_file or libs.footprint_dir:
        parts = [
            _plural(len(libs.symbols), "symbol"),
            _plural(len(libs.footprints), "footprint"),
        ]
        lines.append(f"libraries  {libs.nickname}: {', '.join(parts)}")
    lines.append(
        f"schematic  {_shown(result.schematic, root)} ({_plural(result.parts, 'part')})"
    )
    erc = result.erc
    lines.append(
        f"ERC        {_plural(erc.errors, 'error')}, {_plural(erc.warnings, 'warning')}"
        f" ({_shown(result.erc_report, root)})"
    )
    for found in erc.violations[:limit]:
        lines.append(f"  {found.severity} [{found.code}] {found.message}")
        lines.extend(f"    {where}" for where in found.where)
    if len(erc.violations) > limit:
        more = len(erc.violations) - limit
        lines.append(f"  ... and {more} more in {_shown(result.erc_report, root)}")
    lines.append(f"netlist    {_shown(result.netlist, root)}")
    return "\n".join(lines)
