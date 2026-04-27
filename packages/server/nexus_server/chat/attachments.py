"""Removed — never had a caller (Phase C placeholder).

Use the package facade ``from nexus_server.chat import attachments``
(an alias for :mod:`nexus_server.attachment_distiller`) or import
the SDK pipeline directly: :mod:`nexus_core.distiller`.
"""

raise ImportError(
    "nexus_server.chat.attachments was a Phase C placeholder with "
    "no callers — removed during dead-code cleanup. Use "
    "``from nexus_server.chat import attachments`` (re-exported in "
    "the package __init__.py) or "
    "``from nexus_core.distiller import distill, extract_text``."
)
