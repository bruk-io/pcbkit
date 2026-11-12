"""The exact I2C bus scan: the addresses that answer are the ones expected.

At bring-up an I2C scan shows which addresses answer on a bus. ``I2C_EXPECTED`` is
``{bus: [7-bit address]}``, the set the spec says must answer, for buses that are
keys of ``I2C_BUSES``. The addresses found are those of the on-board devices the
project's ``circuits.i2c_devices`` reports on the bus's SDA net, and the bus's
off-board devices from ``I2C_BUSES``. A device that answers an all-call or general-call
address must be reported at that address too: the check counts what it is told.

A device list that is not rows of ``(sda net, name, address)`` with a whole address
from 0 to 0x7F, two devices at one address, an address listed twice in ``I2C_EXPECTED``
and an SDA net that is not in the design all fail by name.

The functions here are pure (they take the devices as plain tuples) and import no
pytest, so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pcbkit.check.tables import DictOf, Int, ListOf, Shape, Text

TABLES: dict[str, Shape] = {
    "I2C_EXPECTED": DictOf(Text("bus"), ListOf(Int("7-bit address", lo=0, hi=0x7F))),
}


def hexes(addresses: Iterable[int]) -> list[str]:
    """Return the addresses, lowest first, as ``0x3c`` text."""
    return [f"0x{a:02x}" for a in sorted(addresses)]


def address_problem(address: Any) -> str | None:
    """Return what is wrong with an address, or None for a whole number 0 to 0x7F."""
    if isinstance(address, bool) or not isinstance(address, int):
        problem = f"address {address!r} is not a whole number"
        if isinstance(address, str):
            try:
                int(address.strip(), 0)
            except ValueError:
                return problem
            # text that reads as a number: say to write it without the quotes
            return f"{problem}: write {address.strip()}, not {address!r}"
        return problem
    if not 0 <= address <= 0x7F:
        text = f"0x{address:02x}" if address >= 0 else str(address)
        return f"address {text} is not a 7-bit address"
    return None


def device_rows(rows: Any, source: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    """Return ``(clean rows, problems)`` for what ``source`` gave as the device list.

    Each row must be ``(sda net, name, address)`` with a whole address from 0 to 0x7F.
    When any problem is found the clean list is empty, so nothing is compared with a
    half-read list. ``source`` is ``circuits.i2c_devices``, for the messages.
    """
    expected = "expected (sda net, name, address)"
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Iterable):
        kind = "None" if rows is None else type(rows).__name__
        return [], [
            f"{source} returned {kind}, expected a list of (sda net, name, address)"
        ]
    clean: list[tuple[str, str, int]] = []
    problems: list[str] = []
    for index, row in enumerate(rows):
        if isinstance(row, (str, bytes)) or not isinstance(row, (tuple, list)):
            problems.append(f"{source} row {index} is {row!r}, {expected}")
        elif len(row) != 3:
            problems.append(f"{source} row {index} is {tuple(row)!r}, {expected}")
        else:
            net, name, address = row
            problem = address_problem(address)
            if problem:
                problems.append(f"{source} row {index} ({name}): {problem}")
            else:
                clean.append((net, name, address))
    return ([] if problems else clean), problems


def clashes(found: Mapping[int, list[str]]) -> dict[int, list[str]]:
    """Return the addresses at which more than one device answers, lowest first."""
    return {a: found[a] for a in sorted(found) if len(found[a]) > 1}


def clash_problems(found: Mapping[int, list[str]]) -> list[str]:
    """Return one line for each address that two devices or more answer at."""
    return [
        f"address 0x{a:02x} answers for two devices: {', '.join(names)}"
        if len(names) == 2
        else f"address 0x{a:02x} answers for {len(names)} devices: {', '.join(names)}"
        for a, names in clashes(found).items()
    ]


def expected_duplicates(expected: Iterable[int]) -> list[int]:
    """Return the addresses the expected list names more than once, lowest first."""
    seen: set[int] = set()
    repeated: set[int] = set()
    for a in expected:
        (repeated if a in seen else seen).add(a)
    return sorted(repeated)


def devices_record(found: Mapping[int, list[str]]) -> dict[str, list[str]]:
    """Return ``{"0x3c": [device names]}`` for what was found, lowest address first."""
    return {f"0x{a:02x}": list(found[a]) for a in sorted(found)}


def sda_problem(sda: str, nets: Mapping[str, object]) -> str | None:
    """Return a line when the bus's SDA net is not a net of the design, else None."""
    if sda in nets:
        return None
    if sda.startswith("/") and sda.lstrip("/") in nets:
        return (
            f"SDA net {sda} is not in the design (net names carry no leading "
            f"slash: write {sda.lstrip('/')})"
        )
    return f"SDA net {sda} is not in the design"


def found_on_bus(
    on_board: Iterable[tuple[str, str, int]],
    sda: str,
    off_board: Iterable[tuple[str, int]],
) -> dict[int, list[str]]:
    """Return ``{address: [device names]}`` for what answers on the bus.

    ``on_board`` is ``circuits.i2c_devices``'s ``(sda net, name, address)`` rows, of
    which those on net ``sda`` are the bus's; ``off_board`` is ``(name, address)`` for
    the devices outside the board.
    """
    found: dict[int, list[str]] = {}
    for net, name, address in on_board:
        if net == sda:
            found.setdefault(address, []).append(name)
    for name, address in off_board:
        found.setdefault(address, []).append(name)
    return found


@dataclass
class BusReport:
    """What comparing one bus's scan with its expected set found.

    ``expected`` and ``found`` are the addresses, ``missing`` the expected ones that
    do not answer, ``extra`` the ones that answer unexpectedly, each lowest first.
    ``names`` says which devices answer at each found address.
    """

    expected: list[int]
    found: list[int]
    missing: list[int]
    extra: list[int]
    names: dict[int, list[str]]

    def problem(self) -> str | None:
        """Return one line naming what is missing and what is extra, or None."""
        parts = []
        if self.missing:
            parts.append("missing " + ", ".join(hexes(self.missing)))
        if self.extra:
            shown = [
                f"0x{a:02x} ({', '.join(self.names[a])})" for a in sorted(self.extra)
            ]
            parts.append("extra " + ", ".join(shown))
        if not parts:
            return None
        return (
            f"expected {', '.join(hexes(self.expected)) or 'no address'}; "
            + "; ".join(parts)
        )


def compare(expected: Iterable[int], found: dict[int, list[str]]) -> BusReport:
    """Compare the expected addresses with those found (as sets)."""
    want = sorted(set(expected))
    got = sorted(found)
    return BusReport(
        expected=want,
        found=got,
        missing=[a for a in want if a not in found],
        extra=[a for a in got if a not in want],
        names=found,
    )
