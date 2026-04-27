"""Twins domain — per-user DigitalTwin lifecycle + read views.

Phase C navigation aid. Code currently lives at the legacy top-level
modules (``nexus_server.twin_manager``, ``nexus_server.twin_event_log``).
This package re-exports the public surface so new code can use the
domain-grouped path:

    from nexus_server.twins import manager            # twin lifecycle
    from nexus_server.twins import event_views        # read-only EventLog access
    from nexus_server.twins import (
        get_twin, close_user, bootstrap_chain_identity,
        install_chain_activity_handler,
    )

Phase D will move the modules under here for real (and split
``twin_manager`` into ``manager.py`` + ``chain_log.py`` + the
``bootstrap_chain_identity`` helper which conceptually belongs to
``chain/``).
"""

from nexus_server import twin_manager as manager  # noqa: F401
from nexus_server import twin_event_log as event_views  # noqa: F401

# Most-used helpers, re-exported at the package root for convenience.
from nexus_server.twin_manager import (  # noqa: F401
    get_twin,
    close_user,
    bootstrap_chain_identity,
    install_chain_activity_handler,
    uninstall_chain_activity_handler,
    start_reaper,
    shutdown_all,
)
