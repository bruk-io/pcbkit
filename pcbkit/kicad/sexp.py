"""Minimal KiCad S-expression parser and serialiser.

Atoms are kept as Python str. Quoted strings are wrapped in QStr so they
round-trip with quotes; bare atoms stay plain str.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Union

Node = list  # an S-expression list: atoms (str / QStr / numbers / bool) and child lists
Atom = Union[str, int, float, bool]


class QStr(str):
    """Mark a string that serialises with quotes."""


_tok = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text: str) -> Any:
    """Parse KiCad S-expression text into nested lists.

    Return the single top-level list, or a list of them if the text holds several.
    """
    stack: list[list[Any]] = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _tok.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"parse error at {pos}: {text[pos : pos + 40]!r}")
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            lst = stack.pop()
            stack[-1].append(lst)
        elif m.group(3) is not None:
            s = m.group(3).replace('\\"', '"').replace("\\\\", "\\")
            stack[-1].append(QStr(s))
        else:
            stack[-1].append(m.group(4))
    return stack[0][0] if len(stack[0]) == 1 else stack[0]


def q(s: str) -> QStr:
    """Return s marked to serialise with quotes."""
    return QStr(s)


def _atom(a: Atom) -> str:
    if isinstance(a, QStr):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, float):
        s = f"{a:.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(a)


def dump(x: Any, indent: int = 0) -> str:
    """Serialise nested lists back to KiCad S-expression text (tab-indented)."""
    if not isinstance(x, list):
        return _atom(x)
    if all(not isinstance(e, list) for e in x) and len(x) < 12:
        return "(" + " ".join(_atom(e) for e in x) + ")"
    pad = "\t" * (indent + 1)
    head = []
    i = 0
    while i < len(x) and not isinstance(x[i], list):
        head.append(_atom(x[i]))
        i += 1
    s = "(" + " ".join(head)
    for e in x[i:]:
        s += "\n" + pad + dump(e, indent + 1)
    s += "\n" + "\t" * indent + ")"
    return s


def find(node: list, key: str) -> list | None:
    """Return the first child list whose head is key, or None."""
    for e in node:
        if isinstance(e, list) and e and e[0] == key:
            return e
    return None


def findall(node: list, key: str) -> list[list]:
    """Return every child list whose head is key."""
    return [e for e in node if isinstance(e, list) and e and e[0] == key]


def walk(node: list) -> Iterator[list]:
    """Yield node and every list nested in it, depth first."""
    yield node
    for e in node:
        if isinstance(e, list):
            yield from walk(e)
