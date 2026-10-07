"""Design checks: the engines, the pytest plugin, the built-in checks and the packs.

* ``netlist``, ``dc``, ``spice``, ``copper``, ``coupling``, ``stitching`` and
  ``reserved`` are the engines. They know nothing about pytest, and import without
  pcbnew (the ones that read a board load it when they first need it).
* ``plugin`` is the pytest plugin (``-p pcbkit.check.plugin``): the fixtures ``nl``,
  ``board``, ``record``, ``project``, ``specs`` and ``circuits``, and the results file.
* ``builtin`` holds the checks every board can switch on with ``[checks] groups``, and
  ``packs`` the rules that belong to one part (the ESP32-S3 DevKitC).
* ``runner`` builds the one pytest command line that ``pcbkit check`` and
  ``pcbkit mutants`` both use.
"""

from __future__ import annotations
