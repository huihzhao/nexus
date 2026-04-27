"""Views domain — read-only HTTP endpoints over twin state + legacy anchors.

Phase C navigation aid. Code currently lives at
:mod:`nexus_server.agent_state`. This package re-exports the public
surface so new code can use the domain-grouped path:

    from nexus_server.views import router       # /agent/{state,timeline,memories,messages}
    from nexus_server.views import sync_router  # /sync/anchors (legacy read)

Phase D will move the code under here for real and split per-endpoint
files (``state.py``, ``timeline.py``, ``messages.py``, ``memories.py``)
once we add Planning (``plans.py``).
"""

from nexus_server.agent_state import *  # noqa: F401, F403
from nexus_server.agent_state import router  # noqa: F401
from nexus_server.agent_state import sync_router  # noqa: F401
