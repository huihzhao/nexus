"""Chain domain — BSC + ERC-8004 identity + legacy anchor reads.

Phase C navigation aid. Code currently lives at the legacy top-level
modules (``nexus_server.chain_proxy``, ``nexus_server.sync_anchor``).
This package re-exports the public surface so new code can use the
domain-grouped path:

    from nexus_server.chain import router            # was chain_proxy.router
    from nexus_server.chain import legacy_anchors    # was sync_anchor

The chain identity bootstrap (``bootstrap_chain_identity``) currently
lives inside :mod:`nexus_server.twin_manager`. Phase D will extract it
to ``nexus_server.chain.bootstrap``; until then it's reachable as
``nexus_server.twin_manager.bootstrap_chain_identity``.
"""

from nexus_server.chain_proxy import *  # noqa: F401, F403
from nexus_server.chain_proxy import router  # noqa: F401
from nexus_server import sync_anchor as legacy_anchors  # noqa: F401
