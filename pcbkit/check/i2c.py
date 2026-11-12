"""The exact I2C bus scan: the addresses that answer are the ones expected.

At bring-up an I2C scan shows which addresses answer on a bus. ``I2C_EXPECTED`` is
``{bus: [7-bit address]}``, the set the spec says must answer, for buses that are
keys of ``I2C_BUSES``. The addresses found are those of the on-board devices the
project's ``circuits.i2c_devices`` reports on the bus's SDA net, and the bus's
off-board devices from ``I2C_BUSES``. A device that answers an all-call or general-call
address must be reported at that address too: the check counts what it is told.

The functions here are pure (they take the devices as plain tuples) and import no
pytest, so ``pcbkit specs`` can read ``TABLES``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pcbkit.check.tables import DictOf, Int, ListOf, Shape, Text

TABLES: dict[str, Shape] = {
    "I2C_EXPECTED": DictOf(Text("bus"), ListOf(Int("7-bit address", lo=0, hi=0x7F))),
}


def hexes(addresses: Iterable[int]) -> list[str]:
    """Return the addresses, lowest first, as ``0x3c`` text."""
    return [f"0x{a:02x}" for a in sorted(addresses)]


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
