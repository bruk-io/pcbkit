"""Describe, validate and read the tables of a project's ``specs.py``.

A table is plain Python data (a dict of rows, a list of tuples). Its *shape* is a small
value built from the primitives below, so the shape is data too: ``validate`` checks a
table against it and returns every error with its path, ``describe`` renders it as the
text the docs show, and ``read`` returns the table, normalised, or raises
``TableError``.
Nothing here imports pytest, pcbnew or any other pcbkit module, so the tier 1
``pcbkit specs`` command and the check plugin both use it.

Where the shapes live:

* ``EXISTING`` holds the shapes of the tables the built-in checks read today. They
  are as permissive as the checks are: a row may be a tuple or a list, a pad an int
  or a str, and a key a check does not read is left alone (an *open* ``Record``).
  ``COUPLING`` is not here: ``pcbkit.check.coupling.from_spec`` validates it.
* ``PROPOSED`` holds the shapes of tables whose checks are not written yet. They are
  strict (a ``Record`` is closed: an unknown key is an error), so a review that writes
  rows can run ``pcbkit specs`` at once.
* An engine module under ``pcbkit/check/`` may declare ``TABLES = {name: shape}`` for
  tables that are its own. ``registry`` gathers them with ``pkgutil``, so parallel work
  shares no list. A table that moves from ``PROPOSED`` to its engine module is removed
  from here in the same change; a name in two places is an error.

Normalising: a ``Pad`` is a pad number, as the netlist writes it, so ``1`` and ``"1"``
are the same pad and ``read`` gives the string. A sequence becomes a tuple or a list as
the shape says, an ``Opt`` with a default fills in a missing key.
"""

from __future__ import annotations

import ast
import copy
import importlib
import json
import math
import numbers
import pkgutil
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

_UNSET = object()


class TableError(Exception):
    """A table in ``specs.py`` does not have the shape it should.

    ``errors`` holds one line per problem, each starting with the path of the value;
    ``str(error)`` is those lines joined, so a failing check shows all of them.
    """

    def __init__(self, errors: list[str]) -> None:
        """Keep the error lines."""
        super().__init__("\n".join(errors))
        self.errors = list(errors)


# --- paths ------------------------------------------------------------------------


def _show(value: Any) -> str:
    """Return ``repr(value)``, or a stand-in when the value's own ``repr`` raises.

    An error line must never be lost because the value it is about cannot print
    itself (a broken ``__repr__``, an int too long to print).
    """
    try:
        return repr(value)
    except Exception:  # noqa: BLE001 - any failure of the project's own object
        return f"<{type(value).__name__} object whose repr failed>"


def _key(key: Any) -> str:
    """Return a dict key as it is written in a path: ``"name"`` or ``5``."""
    if isinstance(key, str):
        return json.dumps(key, ensure_ascii=False)
    return _show(key)


def _sequence(value: Any) -> bool:
    """Return True for a list, tuple, set or frozenset (not a str, bytes or dict)."""
    return isinstance(value, (list, tuple, set, frozenset))


def _iterable(value: Any) -> bool:
    """Return True for any iterable that is not a str, bytes or mapping.

    A ``range`` or a generator counts: the checks of the tables that take these only
    iterate them (``frozenset(...)``, ``in``).
    """
    return not isinstance(value, (str, bytes, bytearray, Mapping)) and hasattr(
        value, "__iter__"
    )


def _number(value: Any) -> bool:
    """Return True for a finite real number that is not a bool.

    An int too large for a float is not finite: ``math.isfinite`` raises OverflowError
    for it.
    """
    try:
        return (
            isinstance(value, numbers.Real)
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    except OverflowError:
        return False


def _as_dict(value: Any) -> Any:
    """Return ``value`` as a dict if ``dict()`` would take it, else unchanged.

    ``dict(rows)`` takes a mapping or a list of ``(key, value)`` pairs; so does a check
    that reads its table that way.
    """
    if isinstance(value, Mapping):
        return value
    if _sequence(value) and all(
        isinstance(row, (list, tuple)) and len(row) == 2 for row in value
    ):
        try:
            return dict(value)
        except TypeError:  # an unhashable key: let the inner shape report it
            return value
    return value


# --- the shapes -------------------------------------------------------------------


class Shape:
    """The base of every shape: it describes itself and converts a value."""

    def describe(self) -> str:
        """Return the shape as text, for the docs and the error messages."""
        raise NotImplementedError

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return ``value`` normalised; add to ``errors`` for each problem found."""
        raise NotImplementedError

    def _expected(
        self, value: Any, path: str, errors: list[str], extra: str = ""
    ) -> Any:
        """Add the standard "expected X, got Y" error and return None."""
        errors.append(f"{path}: expected {self.describe()}{extra}, got {_show(value)}")
        return None


class Any_(Shape):
    """Accept any value (a keyword argument's value, a scenario's setting)."""

    def __init__(self, label: str = "value") -> None:
        """Name the value in descriptions."""
        self.label = label

    def describe(self) -> str:
        """Return the label."""
        return self.label

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the value unchanged."""
        return value


class Text(Shape):
    """A non-empty string (or an empty one when ``allow_empty``)."""

    def __init__(self, label: str = "text", allow_empty: bool = False) -> None:
        """Name the value in descriptions."""
        self.label = label
        self.allow_empty = allow_empty

    def describe(self) -> str:
        """Return the label."""
        return self.label

    def _ok(self, value: Any) -> bool:
        """Return True if ``value`` is acceptable."""
        return isinstance(value, str) and (self.allow_empty or value != "")

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the string, or add an error."""
        if self._ok(value):
            return value
        return self._expected(value, path, errors)


class Net(Text):
    """A net name, as the netlist writes it."""

    def __init__(self, label: str = "net") -> None:
        """Name the value in descriptions."""
        super().__init__(label)


class Ref(Text):
    """A reference designator: a non-empty string with no space in it."""

    def __init__(self, label: str = "ref") -> None:
        """Name the value in descriptions."""
        super().__init__(label)

    def _ok(self, value: Any) -> bool:
        """Return True for a string with no whitespace."""
        return super()._ok(value) and not any(c.isspace() for c in value)


class Pad(Shape):
    """A pad number as the netlist writes it: an int or a str, normalised to str.

    Pad numbers are not always digits ("A1", "MP", "S1"), so any string without a
    space is accepted. Pin *names* ("EN", "VIN") are not rejected here, because a name
    cannot be told from a pad label by its shape; a pad that is not in the netlist is
    for the check that reads the table to name.
    """

    def __init__(self, label: str = "pad") -> None:
        """Name the value in descriptions."""
        self.label = label

    def describe(self) -> str:
        """Return the label."""
        return self.label

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the pad as a string."""
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return str(value)
        if (
            isinstance(value, str)
            and value != ""
            and not any(c.isspace() for c in value)
        ):
            return value
        return self._expected(value, path, errors)


class Bool(Shape):
    """True or False; with ``allow_int``, 0 and 1 too (a check that tests truth)."""

    def __init__(self, allow_int: bool = False) -> None:
        """Say whether 0 and 1 are accepted, and read as False and True."""
        self.allow_int = allow_int

    def describe(self) -> str:
        """Return the two values as Python writes them."""
        return "True or False"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the bool, or add an error."""
        if isinstance(value, bool):
            return value
        if self.allow_int and isinstance(value, int) and value in (0, 1):
            return bool(value)
        return self._expected(value, path, errors)


class Int(Shape):
    """A whole number, optionally within ``lo`` and ``hi`` (both inclusive)."""

    def __init__(
        self, label: str = "n", lo: int | None = None, hi: int | None = None
    ) -> None:
        """Name the value in descriptions and bound it."""
        self.label = label
        self.lo = lo
        self.hi = hi

    def describe(self) -> str:
        """Return the label."""
        return self.label

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the int, or add an error."""
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and (self.lo is None or value >= self.lo)
            and (self.hi is None or value <= self.hi)
        ):
            return value
        extra = ""
        if self.lo is not None and self.hi is not None:
            extra = f" from {self.lo} to {self.hi}"
        elif self.lo is not None:
            extra = f" of at least {self.lo}"
        return self._expected(value, path, errors, extra)


class Number(Shape):
    """A finite number, optionally bounded.

    ``lo`` and ``hi`` bound it; ``lo_open`` makes the lower bound exclusive. A shape
    with a bound says so in ``describe`` only when ``show_range`` is set (``Fraction``).
    """

    default_label = "number"

    def __init__(
        self,
        label: str | None = None,
        lo: float | None = None,
        hi: float | None = None,
        lo_open: bool = False,
        show_range: bool = False,
    ) -> None:
        """Name the value in descriptions and bound it."""
        self.label = label if label is not None else self.default_label
        self.lo = lo
        self.hi = hi
        self.lo_open = lo_open
        self.show_range = show_range

    def describe(self) -> str:
        """Return the label, with the range when it is part of the shape."""
        if not self.show_range:
            return self.label
        left = "(" if self.lo_open else "["
        low = "-inf" if self.lo is None else f"{self.lo:g}"
        high = "inf" if self.hi is None else f"{self.hi:g}"
        return f"{self.label} in {left}{low}, {high}]"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the number, or add an error."""
        if _number(value):
            if self.lo is not None and (
                value < self.lo or (self.lo_open and value == self.lo)
            ):
                return self._expected(value, path, errors, self._bounds())
            if self.hi is not None and value > self.hi:
                return self._expected(value, path, errors, self._bounds())
            return value
        return self._expected(value, path, errors, self._bounds())

    def _bounds(self) -> str:
        """Return " (at least 0)" and the like, for an error, or an empty string."""
        if self.show_range or (self.lo is None and self.hi is None):
            return ""
        parts = []
        if self.lo is not None:
            word = "above" if self.lo_open else "at least"
            parts.append(f"{word} {self.lo:g}")
        if self.hi is not None:
            parts.append(f"at most {self.hi:g}")
        return " (" + ", ".join(parts) + ")"


class Volts(Number):
    """A voltage in volts; it may be negative."""

    default_label = "V"


class Amps(Number):
    """A current in amps."""

    default_label = "A"


class Ohms(Number):
    """A resistance in ohms, never negative."""

    default_label = "ohm"

    def __init__(self, label: str | None = None, positive: bool = False) -> None:
        """Name the value in descriptions; ``positive`` refuses zero as well."""
        super().__init__(label, lo=0, lo_open=positive)


class Mm(Number):
    """A length or position in millimetres; ``nonneg`` refuses a negative one."""

    default_label = "mm"

    def __init__(self, label: str | None = None, nonneg: bool = False) -> None:
        """Name the value in descriptions; ``nonneg`` bounds it at zero."""
        super().__init__(label, lo=0 if nonneg else None)


class Mm2(Number):
    """An area in square millimetres; ``nonneg`` refuses a negative one."""

    default_label = "mm2"

    def __init__(self, label: str | None = None, nonneg: bool = False) -> None:
        """Name the value in descriptions; ``nonneg`` bounds it at zero."""
        super().__init__(label, lo=0 if nonneg else None)


class Fraction(Number):
    """A fraction above 0 and up to ``hi`` (1 by default)."""

    default_label = "fraction"

    def __init__(self, label: str | None = None, hi: float = 1.0) -> None:
        """Name the value in descriptions and set the ceiling."""
        super().__init__(label, lo=0, hi=hi, lo_open=True, show_range=True)


class Choice(Shape):
    """One of a few literal values, such as ``"top"`` or ``"bottom"``, or 1 or 2."""

    def __init__(self, *options: Any) -> None:
        """Keep the allowed values."""
        self.options = options

    def describe(self) -> str:
        """Return the options joined with "or"."""
        return " or ".join(_key(o) for o in self.options)

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the value if it is one of the options (a bool is not a 1)."""
        for option in self.options:
            if type(option) is type(value) and option == value:
                return value
        return self._expected(value, path, errors)


class Nullable(Shape):
    """A value of the inner shape, or None."""

    def __init__(self, inner: Shape) -> None:
        """Keep the inner shape."""
        self.inner = inner

    def describe(self) -> str:
        """Return the inner description and "or None"."""
        return f"{self.inner.describe()} or None"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return None, or the inner shape's conversion."""
        if value is None:
            return None
        found: list[str] = []
        out = self.inner.convert(value, path, found)
        if not found:
            return out
        if len(found) == 1 and found[0].startswith(
            f"{path}: expected {self.inner.describe()}"
        ):
            return self._expected(value, path, errors)
        errors.extend(found)
        return None


class Callable(Shape):
    """A function (a table row that is computed from the netlist)."""

    def describe(self) -> str:
        """Return the word."""
        return "function"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the callable, or add an error."""
        if callable(value):
            return value
        return self._expected(value, path, errors)


class Window(Shape):
    """A pair ``(min, max)`` of the inner shape with min <= max."""

    def __init__(self, inner: Shape) -> None:
        """Keep the shape of each end."""
        self.inner = inner

    def describe(self) -> str:
        """Return ``(min V, max V)`` for volts."""
        unit = self.inner.describe()
        return f"(min {unit}, max {unit})"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the pair as a tuple; refuse a different length or min > max."""
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return self._expected(value, path, errors)
        found: list[str] = []
        low = self.inner.convert(value[0], f"{path}[0]", found)
        high = self.inner.convert(value[1], f"{path}[1]", found)
        errors.extend(found)
        if found:
            return None
        if low > high:
            return self._expected(value, path, errors, " with min <= max")
        return (low, high)


class Box(Shape):
    """A rectangle ``(x0, y0, x1, y1)`` in layout mm, with x0 < x1 and y0 < y1.

    ``lead`` shapes come before the four numbers in the same tuple:
    ``Box(Text("name"))`` is ``(name, x0, y0, x1, y1)``.
    """

    def __init__(self, *lead: Shape) -> None:
        """Keep the shapes that come first."""
        self.lead = lead

    def describe(self) -> str:
        """Return the tuple's description."""
        names = [s.describe() for s in self.lead] + ["x0", "y0", "x1", "y1"]
        return "(" + ", ".join(names) + ")"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the tuple; refuse another length or a box with no area."""
        n = len(self.lead)
        if not isinstance(value, (list, tuple)) or len(value) != n + 4:
            return self._expected(value, path, errors)
        found: list[str] = []
        out = [
            s.convert(v, f"{path}[{i}]", found)
            for i, (s, v) in enumerate(zip(self.lead, value))
        ]
        corner = [
            Mm(name).convert(v, f"{path}[{n + i}]", found)
            for i, (name, v) in enumerate(zip(("x0", "y0", "x1", "y1"), value[n:]))
        ]
        errors.extend(found)
        if found:
            return None
        x0, y0, x1, y1 = corner
        if not (x0 < x1 and y0 < y1):
            return self._expected(value, path, errors, " with x0 < x1 and y0 < y1")
        return tuple(out + corner)


class Opt(Shape):
    """An optional key of a ``Record``, with the default used when it is absent."""

    def __init__(self, inner: Shape, default: Any = _UNSET) -> None:
        """Keep the shape of the value and its default, if it has one."""
        self.inner = inner
        self.default = default

    @property
    def has_default(self) -> bool:
        """Return True if absent means this value."""
        return self.default is not _UNSET

    def describe(self) -> str:
        """Return the inner description and "(optional, default X)"."""
        if self.has_default:
            return f"{self.inner.describe()} (optional, default {_show(self.default)})"
        return f"{self.inner.describe()} (optional)"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Convert with the inner shape."""
        return self.inner.convert(value, path, errors)


class Tuple(Shape):
    """A fixed-length row; each place has its own shape. A list is accepted."""

    def __init__(self, *items: Shape) -> None:
        """Keep the shape of each place."""
        self.items = items

    def describe(self) -> str:
        """Return the places in parentheses."""
        return "(" + ", ".join(s.describe() for s in self.items) + ")"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the row as a tuple; a wrong length is one error for the whole row."""
        if not isinstance(value, (list, tuple)) or len(value) != len(self.items):
            return self._expected(value, path, errors)
        found: list[str] = []
        out = [
            s.convert(v, f"{path}[{i}]", found)
            for i, (s, v) in enumerate(zip(self.items, value))
        ]
        errors.extend(found)
        return tuple(out) if not found else None


class ListOf(Shape):
    """Any number of items of one shape, in a list, tuple or set. Gives a list.

    With ``iterable`` any iterable that is not a str or a mapping is taken (a
    ``range``, a generator), for a table that a check only iterates or turns into a
    ``frozenset``.
    """

    def __init__(self, item: Shape, iterable: bool = False) -> None:
        """Keep the shape of an item and what counts as a sequence."""
        self.item = item
        self.iterable = iterable

    def describe(self) -> str:
        """Return the item in brackets."""
        return f"[{self.item.describe()}]"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return a list of the converted items."""
        if not (_iterable(value) if self.iterable else _sequence(value)):
            return self._expected(value, path, errors)
        items = list(value)
        out = [
            self.item.convert(v, f"{path}[{i}]", errors) for i, v in enumerate(items)
        ]
        return out


class Pairs(Shape):
    """A dict, or a list of ``(key, value)`` pairs, as ``dict()`` takes it.

    A check that reads ``dict(specs.NAME)`` takes either; the inner shape judges the
    dict that results, and ``describe`` is the inner shape's.
    """

    def __init__(self, inner: Shape) -> None:
        """Keep the shape of the dict."""
        self.inner = inner

    def describe(self) -> str:
        """Return the inner description."""
        return self.inner.describe()

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Convert the value as a dict."""
        return self.inner.convert(_as_dict(value), path, errors)


class DictOf(Shape):
    """A dict whose keys are of one shape and values of another.

    ``required`` lists keys that must be present (``{"default": ...}``). Keys are
    normalised too, so ``{1: "x"}`` with ``Pad`` keys is read as ``{"1": "x"}``; two
    keys that normalise to the same one are an error.
    """

    def __init__(
        self, key: Shape, value: Shape, required: tuple[Any, ...] = ()
    ) -> None:
        """Keep the two shapes and the required keys."""
        self.key = key
        self.value = value
        self.required = tuple(required)

    def describe(self) -> str:
        """Return ``{key: value}``."""
        return "{" + f"{self.key.describe()}: {self.value.describe()}" + "}"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return a dict of the converted keys and values."""
        if not isinstance(value, Mapping):
            return self._expected(value, path, errors)
        out: dict[Any, Any] = {}
        for k, v in value.items():
            here = f"{path}[{_key(k)}]"
            new_key = self.key.convert(k, here, errors)
            new_value = self.value.convert(v, here, errors)
            if new_key is None and k is not None:
                continue
            if new_key in out:
                errors.append(
                    f"{here}: the key is a duplicate of another after reading"
                )
                continue
            out[new_key] = new_value
        for name in self.required:
            if name not in value:
                errors.append(f"{path}: missing key {_key(name)}")
        return out


class Record(Shape):
    """A dict with named keys, each with its own shape.

    ``required`` keys must be there; ``optional`` keys may be (give an ``Opt`` with a
    default to have it filled in). A closed record refuses any other key; an ``open``
    one ignores them, as a check that reads only some keys of a row does.
    """

    def __init__(
        self,
        required: Mapping[str, Shape] | None = None,
        optional: Mapping[str, Shape] | None = None,
        open: bool = False,
    ) -> None:
        """Keep the keys and whether other keys are allowed."""
        self.required = dict(required or {})
        self.optional = dict(optional or {})
        self.open = open

    def describe(self) -> str:
        """Return ``{"key": shape, ...}`` with the optional keys marked."""
        parts = [f"{_key(k)}: {s.describe()}" for k, s in self.required.items()]
        for k, s in self.optional.items():
            shown = s.describe() if isinstance(s, Opt) else f"{s.describe()} (optional)"
            parts.append(f"{_key(k)}: {shown}")
        if self.open:
            parts.append("...")
        return "{" + ", ".join(parts) + "}"

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return a dict with each known key converted and defaults filled in."""
        if not isinstance(value, Mapping):
            return self._expected(value, path, errors)
        out: dict[Any, Any] = {}
        for name, shape in self.required.items():
            if name not in value:
                errors.append(f"{path}: missing key {_key(name)}")
            else:
                out[name] = shape.convert(value[name], f"{path}[{_key(name)}]", errors)
        for name, shape in self.optional.items():
            if name in value:
                out[name] = shape.convert(value[name], f"{path}[{_key(name)}]", errors)
            elif isinstance(shape, Opt) and shape.has_default:
                out[name] = copy.deepcopy(shape.default)
        known = set(self.required) | set(self.optional)
        for name in value:
            if name not in known:
                if self.open:
                    out[name] = value[name]
                else:
                    allowed = ", ".join(
                        _key(k) for k in [*self.required, *self.optional]
                    )
                    errors.append(
                        f"{path}: unknown key {_key(name)} (the keys are {allowed})"
                    )
        return out


class OneOf(Shape):
    """The first of several shapes that the value fits."""

    def __init__(self, *options: Shape) -> None:
        """Keep the alternatives."""
        self.options = options

    def describe(self) -> str:
        """Return the alternatives joined with "or"."""
        return " or ".join(s.describe() for s in self.options)

    def convert(self, value: Any, path: str, errors: list[str]) -> Any:
        """Return the first alternative's conversion that raises no error.

        When none fits, the value may still be of the kind one of them describes (a
        dict with a key missing, a list with one bad item): that alternative is the
        closest, and its errors, which say what is wrong, are reported. An alternative
        is of the kind when none of its errors is "expected ..." at this very path. Of
        several, the one with fewest errors at this path itself (a key missing or not
        known here, which says the row is of the other kind), then fewest in all. When
        no alternative is of the kind, the one error lists them all.
        """
        attempts = []
        for option in self.options:
            found: list[str] = []
            out = option.convert(value, path, found)
            if not found:
                return out
            attempts.append(found)
        close = [
            found
            for found in attempts
            if not any(line.startswith(f"{path}: expected ") for line in found)
        ]
        if close:
            here = f"{path}:"
            errors.extend(
                min(
                    close,
                    key=lambda found: (
                        sum(line.startswith(here) for line in found),
                        len(found),
                    ),
                )
            )
            return None
        return self._expected(value, path, errors)


# --- validating and reading -------------------------------------------------------


def convert(name: str, value: Any, shape: Shape) -> tuple[Any, list[str]]:
    """Return ``(normalised value, errors)`` for table ``name``."""
    errors: list[str] = []
    out = shape.convert(value, f"specs.{name}", errors)
    return out, errors


def validate(name: str, value: Any, shape: Shape) -> list[str]:
    """Return every error in table ``name``, each starting with its path.

    An empty list means the table has the shape. For example::

        specs.FAULT_STATES["c"]["pins"][1]: expected (ref, pad, (min V, max V)),
        got ('U2', 5)
    """
    return convert(name, value, shape)[1]


def describe(shape: Shape) -> str:
    """Return ``shape`` as text, as the docs show it."""
    return shape.describe()


def read(specs: Any, name: str, shape: Shape | None = None) -> Any:
    """Return the project's table ``name``, validated and normalised.

    ``shape`` defaults to the registered one. A table that is not defined raises what
    ``specs.NAME`` raises (the plugin's ``MissingSpec``, which names the file); a table
    of the wrong shape raises ``TableError`` listing every problem.
    """
    if shape is None:
        try:
            shape = registry()[name]
        except KeyError:
            raise KeyError(f"no shape is registered for the table {name}") from None
    out, errors = convert(name, getattr(specs, name), shape)
    if errors:
        raise TableError(errors)
    return out


# --- the shapes of the tables that exist today ------------------------------------


def _scenario() -> Shape:
    """Return the shape of keyword arguments for ``circuits.scenario``."""
    return DictOf(Text("keyword"), Any_())


_PCBWAY_KEYS = (
    "min_annular",
    "min_drill",
    "min_plated_slot",
    "silk_min_height",
    "silk_min_stroke",
    "min_track_2oz",
    "min_space_2oz",
)

# the third field is not read by any check (the cable's capacitance comes from
# ``I2C_CABLE_C_PER_M``), so None is as good as a number
_I2C_DEVICE = Tuple(Text("name"), Int("7-bit address"), Nullable(Number("pF")))
_POSITIVE_AMPS = Amps(lo=0, lo_open=True)

EXISTING: dict[str, Shape] = {
    # fab
    "PCBWAY": Record(required={k: Mm() for k in _PCBWAY_KEYS}, open=True),
    "WIRE_PADS": DictOf(Ref(), Tuple(Pad("positive pad"), Pad("negative pad"))),
    "DECOUPLING": ListOf(
        Tuple(Ref("ic"), Pad("pin"), ListOf(Ref("capacitor")), Mm("limit mm"))
    ),
    "POWER_FOOTPRINTS": ListOf(
        Record(
            required={"ref": Ref()},
            optional={
                "tab_pad": Pad(),
                "tab_net": Nullable(Net()),
                "smaller": ListOf(Tuple(Pad("small pad"), Pad("large pad"))),
                "drill_mm": DictOf(Pad(), Mm("drill mm")),
            },
            open=True,
        )
    ),
    "I2C_BUSES": DictOf(
        Text("bus"),
        Tuple(
            Net("sda net"),
            Net("scl net"),
            Number("speed Hz"),
            ListOf(_I2C_DEVICE),
        ),
    ),
    "I2C_TR_MAX": DictOf(Number("speed Hz"), Number("s")),
    "I2C_PIN_C": Number("F"),
    "TRACE_C_PER_MM": Number("F per mm"),
    "I2C_CABLE_LEN": Number("m"),
    "I2C_CABLE_C_PER_M": Number("F per m"),
    "I2C_PULLUP_NET": Net(),
    "I2C_DEVICE_PREFIXES": OneOf(
        Text("prefixes", allow_empty=True),
        ListOf(Text("prefix", allow_empty=True)),
    ),
    "ASSEMBLY_TEXT_EXEMPT": ListOf(Ref()),
    # copper (COUPLING is read by coupling.from_spec, which validates it)
    "RESERVED_COPPER": ListOf(
        Tuple(
            Text("name"),
            Choice("top", "bottom"),
            Mm("x0"),
            Mm("y0"),
            Mm("x1"),
            Mm("y1"),
            # ``intruders`` tests ``in allowed``, so one string works (as a substring
            # test); a list is the intended form
            OneOf(Net("allowed net"), ListOf(Net("allowed net"))),
        )
    ),
    "SENSE_LOOPS": ListOf(
        Record(
            required={
                "name": Text(),
                "nets": Tuple(Net(), Net()),
                "from_ref": Ref(),
                "to_ref": Ref(),
                # compared as str with the board's pad numbers, so 1 is not "1" here
                "to_pads": ListOf(Text("pad")),
                "max_mm2": Number("mm2"),
            },
            open=True,
        )
    ),
    "COPPER_SEGMENTS": ListOf(
        Record(
            required={
                "name": Text(),
                "net": Net(),
                "terminals": ListOf(
                    OneOf(
                        Tuple(Ref(), Pad(), Number("share")),
                        Tuple(Ref(), Pad(), Number("share"), Text("role")),
                    )
                ),
            },
            optional={"heating": Bool(allow_int=True)},
            open=True,
        )
    ),
    # i_cont divides (the peak drop scales by i_peak / i_cont), so both are above 0;
    # max_rise_c is read only for a segment that heats, which the table cannot know
    "COPPER_BUDGETS": Record(
        required={
            "i_cont": _POSITIVE_AMPS,
            "i_peak": _POSITIVE_AMPS,
            "segment_drop_max_v": Volts(),
        },
        optional={"max_rise_c": Number("C")},
        open=True,
    ),
    "SUPPLY_REGULATION": Record(
        required={
            "supply": Text("segment"),
            "ground": Text("segment"),
            "label": Text(),
            "nominal_v": Volts(lo=0, lo_open=True),
            "max_fraction": Number("fraction"),
        },
        open=True,
    ),
    "GROUND_NET": Net(),
    "STITCH_BASIS": Pairs(
        Record(optional={"f_hz": Number("Hz", lo=0, lo_open=True), "er_fr4": Number()})
    ),
    # circuit
    "PINOUT": DictOf(Ref(), DictOf(Pad(), Text("function", allow_empty=True))),
    "PIN_ALIASES": Pairs(
        DictOf(
            Text("symbol pin", allow_empty=True),
            Nullable(Text("datasheet function", allow_empty=True)),
        )
    ),
    "ABSENT_REFS": Pairs(DictOf(Ref(), Text("reason"))),
    "STRESS_SCENARIOS": ListOf(_scenario()),
    "RESISTOR_POWER_W": Pairs(
        DictOf(Text("ref or default"), Number("W"), required=("default",))
    ),
    # an MPN-less capacitor looks up the key ""
    "CAP_VRATED": DictOf(Text("mpn", allow_empty=True), Volts()),
    "LED_REFS": ListOf(Text("ref")),
    "LED_SCENARIO": _scenario(),
    "LED_I_MIN": Amps(),
    "LED_I_MAX": Amps(),
    "SUPPLY_SCENARIO": _scenario(),
    "SUPPLY_PINS": ListOf(Tuple(Ref(), Pad(), Window(Volts()))),
    "SUPPLY_LIMITS": ListOf(
        OneOf(
            Tuple(
                Text("what", allow_empty=True),
                Number("value"),
                Choice("<", "<="),
                Number("limit"),
            ),
            Callable(),
        )
    ),
    "I2C_IOL": DictOf(Number("speed Hz"), _POSITIVE_AMPS),
    "I2C_VDD": Volts(),
    # esp32s3
    "DEVKIT_REF": Ref(),
    "DEVKIT_GPIO": DictOf(Pad("header pin"), Int("GPIO")),
    "ANALOG_NETS": ListOf(Net()),
    "STRAP_SCENARIO": _scenario(),
    "GPIO_SOURCE_SCENARIO": _scenario(),
    "GPIO_SCENARIOS": DictOf(Any_("id"), _scenario()),
    "OPEN_DRAIN_NETS": ListOf(Net()),
    "RAIL_3V3": Net(),
    "ESP32_VDD": Volts(),
    "ESP32_VIH": Volts(),
    "ESP32_VIL": Volts(),
    "ESP32_VOH_MIN": Volts(),
    "ESP32_PIN_ABS_MAX": Volts(),
    "ESP32_GPIO_SOURCE_MAX": Amps(),
    "ESP32_INTERNAL_PULL": Ohms(),
    "RESERVED": ListOf(Int("GPIO"), iterable=True),
    "UART0": ListOf(Int("GPIO"), iterable=True),
    "ADC1": ListOf(Int("GPIO"), iterable=True),
    "INPUT_ONLY": ListOf(Int("GPIO"), iterable=True),
    "STRAPS": Pairs(DictOf(Int("GPIO"), Tuple(Text("level"), Text("pull")))),
}


# --- the shapes of the tables whose checks are not written yet --------------------

_SIDE = Choice("top", "bottom")

PROPOSED: dict[str, Shape] = {
    "POWER_PATHS": Record(
        required={
            "nets": ListOf(Net()),
            "paths": ListOf(Tuple(Net("from net"), Net("to net"), ListOf(Ref()))),
        },
        optional={
            "parts": Opt(
                DictOf(Ref(), ListOf(Tuple(Pad("in pad"), Pad("out pad")))), default={}
            ),
            "max_ohm": Opt(Ohms(positive=True), default=10),
        },
    ),
    "MATING": DictOf(
        Text("plug"),
        Record(
            required={
                "pins": ListOf(Nullable(Net())),
                "fits": ListOf(Ref()),
                "keyed": Bool(),
            },
            optional={"rows": Opt(Choice(1, 2), default=1)},
        ),
    ),
    "NET_LIMITS": DictOf(Net(), Volts("max V")),
    "TEST_POINTS": ListOf(Tuple(Net(), Mm("max ground mm", nonneg=True))),
    "PROBE_KEEPAWAY": ListOf(Box(Text("name"))),
    "REFERENCE_PLANES": DictOf(
        Net(), OneOf(_SIDE, Tuple(_SIDE, Mm("max unreferenced mm", nonneg=True)))
    ),
    "PART_SPACING": ListOf(Tuple(Ref(), Ref(), Mm("min mm", nonneg=True))),
    "POLARITY_MARKS": ListOf(Ref()),
    "FIDUCIALS": Record(
        required={"min": Int("n", lo=0), "clearance_mm": Mm("mm", nonneg=True)}
    ),
    "BOTTOM_PARTS": ListOf(Ref()),
    "EXCLUSIONS": ListOf(
        Tuple(
            Text("id"),
            Text("text"),
            ListOf(Text("ref pattern")),
            ListOf(Net()),
        )
    ),
    "PORT_EDGES": DictOf(
        Ref(),
        Tuple(Choice("top", "bottom", "left", "right"), Mm("max mm", nonneg=True)),
    ),
    "CONTACT_RATINGS": DictOf(
        Ref(),
        Record(
            required={
                "amps": Amps("A per contact", lo=0, lo_open=True),
                "derate": Fraction(),
            }
        ),
    ),
    "HOT_LOOPS": ListOf(
        Record(
            required={
                "name": Text(),
                "refs": ListOf(Ref()),
                "pads": ListOf(Tuple(Ref(), Pad())),
                "max_mm2": Mm2("max mm2", nonneg=True),
            }
        )
    ),
    "HEIGHT_MAX_MM": Mm("mm", nonneg=True),
}


# --- the registry -----------------------------------------------------------------


def _assigned_names(statements: list[ast.stmt]) -> set[str]:
    """Return the names a block of statements binds with a value, at its own level.

    It follows ``if``, ``try`` and ``with`` blocks (a module may declare ``TABLES``
    under ``if`` or ``try``), unpacks tuple and list targets, and counts an annotated
    name only when it has a value: ``TABLES: dict[str, Shape]`` alone binds nothing.
    """
    names: set[str] = set()

    def targets(node: ast.expr) -> None:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                targets(item)
        elif isinstance(node, ast.Starred):
            targets(node.value)

    for node in statements:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                targets(target)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets(node.target)
        elif isinstance(node, ast.If):
            names |= _assigned_names(node.body) | _assigned_names(node.orelse)
        elif isinstance(node, ast.With):
            names |= _assigned_names(node.body)
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            blocks = [node.body, node.orelse, node.finalbody]
            blocks += [handler.body for handler in node.handlers]
            for block in blocks:
                names |= _assigned_names(block)
    return names


def _declares_tables(path: str | None) -> bool:
    """Return True if the source file binds ``TABLES`` at module level."""
    if not path or not path.endswith(".py"):
        return False
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return "TABLES" in _assigned_names(tree.body)


def engine_modules(package: ModuleType | None = None) -> list[ModuleType]:
    """Return the modules of ``package`` that declare ``TABLES``, imported.

    Only the package's own modules are looked at, not its sub-packages (the built-in
    check modules and the packs), and a module is imported only when its source assigns
    ``TABLES`` at module level, so a module that needs pytest or numpy is not imported
    by ``pcbkit specs`` for nothing.
    """
    if package is None:
        import pcbkit.check as package  # noqa: PLC0415
    found = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.ispkg or info.name == "tables":
            continue
        spec = info.module_finder.find_spec(info.name)
        if spec is not None and _declares_tables(spec.origin):
            found.append(importlib.import_module(f"{package.__name__}.{info.name}"))
    return found


_ENGINE_MODULES: list[ModuleType] | None = None


def clear_cache() -> None:
    """Forget the engine modules ``registry`` found, so the next call looks again."""
    global _ENGINE_MODULES
    _ENGINE_MODULES = None


def registry(package: ModuleType | None = None) -> dict[str, Shape]:
    """Return the shape of every registered table, by name.

    The sources are ``EXISTING``, ``PROPOSED`` and each engine module's ``TABLES``.
    For the real package the engine modules are found once per process (reading their
    source is the slow part); ``clear_cache`` makes the next call look again. A
    ``package`` given is looked at every time.

    A name declared in two places raises ValueError naming both.
    """
    global _ENGINE_MODULES
    merged: dict[str, tuple[str, Shape]] = {}

    def add(source: str, tables: Mapping[str, Shape]) -> None:
        for name, shape in tables.items():
            if name in merged:
                raise ValueError(
                    f"table {name} is declared in both {merged[name][0]} and {source}"
                )
            merged[name] = (source, shape)

    if package is not None:
        modules = engine_modules(package)
    else:
        if _ENGINE_MODULES is None:
            _ENGINE_MODULES = engine_modules()
        modules = _ENGINE_MODULES
    add("pcbkit.check.tables.EXISTING", EXISTING)
    add("pcbkit.check.tables.PROPOSED", PROPOSED)
    for module in modules:
        # a TABLES bound only under a condition that did not hold is not there
        add(module.__name__, getattr(module, "TABLES", {}))
    return {name: shape for name, (_, shape) in merged.items()}


def _safe_str(err: BaseException) -> str:
    """Return ``str(err)``, or the type's name when that raises."""
    try:
        return str(err)
    except Exception:  # noqa: BLE001
        return type(err).__name__


def check_module(
    module: Any, shapes: Mapping[str, Shape] | None = None
) -> tuple[int, list[str]]:
    """Validate each registered table that ``module`` defines.

    Return ``(how many tables were checked, error lines)``, in table-name order. A
    table the module does not define is skipped; a name it defines that has no shape
    is not looked at.
    """
    shapes = registry() if shapes is None else shapes
    checked = 0
    errors: list[str] = []
    for name in sorted(shapes):
        if not hasattr(module, name):
            continue
        checked += 1
        try:
            errors.extend(validate(name, getattr(module, name), shapes[name]))
        except Exception as err:  # noqa: BLE001 - one table's failure hides no other
            errors.append(
                f"specs.{name}: pcbkit could not check this table "
                f"({type(err).__name__}: {_safe_str(err)}); this is a bug in pcbkit"
            )
    return checked, errors
