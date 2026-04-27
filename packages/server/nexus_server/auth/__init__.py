"""Auth domain — passkey registration / login + JWT verification.

Public surface (what other server modules / tests import):

    from nexus_server.auth import (
        router,                # FastAPI router for /api/v1/auth/*
        get_current_user,      # dependency for authenticated routes
        create_jwt, verify_jwt,
    )

The HTML/JS payload that browsers hit during passkey ceremonies
(``/passkey``) lives at :mod:`nexus_server.auth.passkey_page`.
"""

from .routes import *  # noqa: F401, F403  — re-export for back-compat
from .routes import (
    router,
    get_current_user,
    create_jwt_token,
    verify_jwt_token,
)

# Sub-router for the passkey HTML page (mounted separately by main.py).
from . import passkey_page  # noqa: F401
