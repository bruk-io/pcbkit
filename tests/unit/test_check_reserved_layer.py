"""Unit test for the layer name that ``reserved.intruders`` accepts."""

from __future__ import annotations

import pytest

from pcbkit.check import reserved


@pytest.mark.parametrize("layer", ["Bottom", "F.Cu", "back", "", "top "])
def test_a_misspelt_layer_is_an_error_not_a_region_with_nothing_in_it(
    layer: str,
) -> None:
    """Refuse a layer that is not "top" or "bottom" before reading any copper.

    A typo here used to match no copper at all, so the region passed unchecked.
    """
    with pytest.raises(ValueError, match="layer must be 'top' or 'bottom'"):
        reserved.intruders(None, layer, (0, 0, 1, 1), {"/GND"})
