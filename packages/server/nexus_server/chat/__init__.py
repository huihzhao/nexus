"""Chat domain — POST /api/v1/llm/chat + attachments + file upload.

Phase C navigation aid. Code currently lives at the legacy top-level
modules (``nexus_server.llm_gateway``, ``nexus_server.attachment_distiller``,
``nexus_server.files``). This package re-exports the public surface so
new code can use the cleaner domain-grouped path:

    from nexus_server.chat import router        # was llm_gateway.router
    from nexus_server.chat import attachments    # was attachment_distiller
    from nexus_server.chat import files          # was nexus_server.files

Phase D will move the code under here for real (along with the
``nexus_server`` → ``nexus_server`` rename). Until then this file is a
thin facade — see :mod:`nexus_server.llm_gateway` for the actual
implementation.
"""

from nexus_server.llm_gateway import *  # noqa: F401, F403
from nexus_server.llm_gateway import router  # noqa: F401
from nexus_server import files  # noqa: F401
from nexus_server import attachment_distiller as attachments  # noqa: F401
