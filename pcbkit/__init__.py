"""pcbkit: design, route, check and export KiCad boards from Python."""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("pcbkit")
except metadata.PackageNotFoundError:  # a source tree that has not been installed
    __version__ = "0+unknown"
