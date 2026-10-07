"""Built-in checks: the ones every board can switch on with ``[checks] groups``.

Each module is one group (see ``pcbkit.check.plugin.BUILTIN_GROUPS``) and reads its
numbers from the project's ``specs.py``. docs/project-interface.md lists, group by
group, what each one reads.
"""

from __future__ import annotations
