"""Removed — never had a caller (Phase C placeholder).

Use ``from nexus_server.chain import router`` (re-exported in the
package __init__.py) or ``from nexus_server.chain_proxy import …``.
"""

raise ImportError(
    "nexus_server.chain.routes was a Phase C placeholder with no "
    "callers — removed during dead-code cleanup. "
    "Use ``from nexus_server.chain_proxy import …`` instead."
)
