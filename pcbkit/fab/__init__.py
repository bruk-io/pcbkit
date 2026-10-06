"""Export a finished board to the files a fab house and an assembler need.

``export_fab(project)`` writes ``out/fab`` and ``out/docs``; ``pcbkit finalize`` calls
it. The modules are ``bom``, ``centroid``, ``gerbers``, ``docs`` and ``pcbway`` (the
file names and the order-form values behind ``pcbkit quote``). Importing this package
needs neither pcbnew nor openpyxl: they are imported when an export runs.
"""

from __future__ import annotations

from pcbkit.fab.export import FabResult, export_fab

__all__ = ["FabResult", "export_fab"]
