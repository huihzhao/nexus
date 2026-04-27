"""Removed — never had a caller (Phase C placeholder).

Use ``from nexus_server.twins import manager`` (the package
__init__.py aliases :mod:`nexus_server.twin_manager` under that
name) or ``from nexus_server.twin_manager import …`` directly.

The promised Phase D split (separate ``manager.py`` lifecycle file,
``chain_log.py`` for ``_ChainActivityLogHandler``, lifting
``bootstrap_chain_identity`` to ``chain/bootstrap.py``) was not
done — those would be a future phase. The canonical implementation
remains in ``nexus_server.twin_manager``.
"""

raise ImportError(
    "nexus_server.twins.manager was a Phase C placeholder with no "
    "callers — removed during dead-code cleanup. "
    "Use ``from nexus_server.twin_manager import …`` (or the "
    "``from nexus_server.twins import manager`` package alias)."
)
