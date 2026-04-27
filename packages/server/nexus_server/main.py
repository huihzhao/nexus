"""FastAPI application assembly and entry point.

Creates and configures the main FastAPI application with:
  - Routers (auth, llm_gateway, chain_proxy, agent_state, files,
    user_profile, passkey_page) — note ``sync_hub`` was retired in
    Phase B when the desktop became a thin client.
  - CORS middleware
  - Exception handlers
  - Health check endpoint
  - Lifecycle management (startup/shutdown)
"""

import argparse
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

# Load .env before anything reads os.getenv.
#
# Lookup order (first to set a key wins; later files only fill blanks):
#   1. cwd .env                — operator/CI override
#   2. packages/server/.env    — server-specific (SERVER_PRIVATE_KEY, JWT, …)
#   3. packages/sdk/.env       — network-level fallback (NEXUS_TESTNET_RPC,
#                                contract addresses) so chain_proxy can find
#                                network config without duplicating it.
#
# Custodial signing key (SERVER_PRIVATE_KEY) is server-only and never read
# from sdk/.env; sdk/.env is only used here as a network/contract config
# source. SDK's NEXUS_PRIVATE_KEY may also be present — we let it through
# into os.environ because SDK code may consult it, but chain_proxy treats
# it as ignored.
def _load_dotenv():
    server_pkg = Path(__file__).parent.parent
    sdk_env = server_pkg.parent / "sdk" / ".env"
    candidates = [
        Path(".env"),
        server_pkg / ".env",
        sdk_env,
    ]
    for p in candidates:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        # Note: we keep walking — earlier files take precedence via the
        # `key not in os.environ` guard, while later files fill in any
        # leftover blanks (e.g. sdk/.env supplies NEXUS_TESTNET_RPC).

_load_dotenv()

# Configure file + console logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("nexus_server.log", mode="a"),
        logging.StreamHandler(),
    ],
)
# Suppress noisy HTTP debug logs
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from nexus_server import (
    agent_state, auth, chain_proxy, llm_gateway,
    user_profile,
)
# Phase C: passkey_page moved into the ``auth`` domain package.
from nexus_server.auth import passkey_page
# Phase B: ``sync_hub`` is gone (raises ImportError). /sync/push and
# /sync/pull retired after Round 2 made the desktop a thin client.
from nexus_server.config import get_config
from nexus_server.database import init_db

logger = logging.getLogger(__name__)
config = get_config()


# ───────────────────────────────────────────────────────────────────────────
# Response Models
# ───────────────────────────────────────────────────────────────────────────


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: str
    timestamp: str
    version: str = "0.1.0"


# ───────────────────────────────────────────────────────────────────────────
# Lifecycle
# ───────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle (startup/shutdown).

    Spins up the TwinManager idle-eviction reaper + chain-activity log
    handler on startup, drains them on shutdown so the process exits
    cleanly. Phase B removed the legacy anchor retry daemon — see
    sync_anchor.py for the tombstone explanation.
    """
    import asyncio as _asyncio
    import os as _os

    # Startup
    logger.info("Starting Rune Protocol API Server")
    config.validate()
    init_db()

    # Phase B: the anchor retry daemon was removed entirely. After S4
    # nothing in production created retryable rows (twin's ChainBackend
    # owns anchoring directly), and the daemon stayed opt-in for an
    # operator-on-demand drain. With Phase B's full sync_anchor cleanup
    # the daemon is gone — the read-only ``list_anchors_for_user`` view
    # remains for legacy history.
    daemon_task = None
    stop_event = None

    # Phase D: TwinManager idle reaper. Only spin it up when twin is
    # enabled, so the legacy LLM gateway path doesn't pay the import
    # cost of nexus.
    twin_reaper_task = None
    twin_stop_event = None
    if config.USE_TWIN and _os.environ.get("NEXUS_DISABLE_TWIN_REAPER") != "1":
        try:
            from nexus_server import twin_manager
            twin_reaper_task, twin_stop_event = twin_manager.start_reaper()
            # Bug 3: capture SDK chain activity into twin_chain_events
            # so /agent/state and /agent/timeline can surface anchor
            # successes / Greenfield failures to the desktop sidebar.
            twin_manager.install_chain_activity_handler()
        except Exception as e:
            logger.warning(
                "TwinManager reaper failed to start (twin path disabled): %s", e
            )

    try:
        yield
    finally:
        # Shutdown
        logger.info("Shutting down Rune Protocol API Server")
        if daemon_task is not None and stop_event is not None:
            stop_event.set()
            try:
                await _asyncio.wait_for(daemon_task, timeout=5.0)
            except _asyncio.TimeoutError:
                logger.warning(
                    "Anchor retry daemon did not stop in 5s; cancelling."
                )
                daemon_task.cancel()
                try:
                    await daemon_task
                except _asyncio.CancelledError:
                    pass

        if twin_stop_event is not None:
            try:
                from nexus_server import twin_manager
                twin_manager.uninstall_chain_activity_handler()
                await twin_manager.shutdown_all(twin_stop_event, twin_reaper_task)
            except Exception as e:
                logger.warning("TwinManager shutdown failed: %s", e)


# ───────────────────────────────────────────────────────────────────────────
# Exception Handlers
# ───────────────────────────────────────────────────────────────────────────


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """Handle HTTP exceptions with consistent format.

    Args:
        request: Request object
        exc: HTTPException raised

    Returns:
        JSONResponse with error details
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


async def generic_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Handle unexpected exceptions.

    Args:
        request: Request object
        exc: Exception raised

    Returns:
        JSONResponse with error details
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "status_code": 500,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ───────────────────────────────────────────────────────────────────────────
# Application Factory
# ───────────────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Create and configure FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    app = FastAPI(
        title="Rune Protocol API",
        description=(
            "Modular FastAPI server: LLM Gateway, Auth Provider, "
            "Data Sync Hub, and Chain Proxy"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    cors_origins = (
        config.CORS_ALLOW_ORIGINS.split(",")
        if config.CORS_ALLOW_ORIGINS != "*"
        else ["*"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception handlers
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Health check endpoint
    @app.get(
        "/health",
        response_model=HealthCheckResponse,
        tags=["health"],
    )
    async def health_check() -> HealthCheckResponse:
        """Health check endpoint.

        Returns:
            HealthCheckResponse with status and timestamp
        """
        return HealthCheckResponse(
            status="healthy",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Include routers with API prefixes
    app.include_router(auth.router)
    app.include_router(passkey_page.router)
    app.include_router(llm_gateway.router)
    app.include_router(chain_proxy.router)
    app.include_router(user_profile.router)
    app.include_router(agent_state.router)
    # Phase B: legacy /api/v1/sync/anchors read endpoint moved out of
    # the deleted sync_hub into agent_state.sync_router. Same path,
    # different module.
    app.include_router(agent_state.sync_router)

    return app


# ───────────────────────────────────────────────────────────────────────────
# Entry Point
# ───────────────────────────────────────────────────────────────────────────


def run_server() -> None:
    """Entry point for rune-server CLI command.

    Parses command-line arguments and starts uvicorn server.
    """
    parser = argparse.ArgumentParser(
        description="Rune Protocol API Server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.SERVER_PORT,
        help=f"Server port (default: {config.SERVER_PORT})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=config.SERVER_HOST,
        help=f"Server host (default: {config.SERVER_HOST})",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on file changes",
    )

    args = parser.parse_args()

    logger.info(
        f"Starting Rune Protocol API Server on {args.host}:{args.port}"
    )

    import uvicorn
    uvicorn.run(
        "nexus_server.main:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
        log_level=config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    import uvicorn

    app = create_app()

    parser = argparse.ArgumentParser(
        description="Rune Protocol API Server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.SERVER_PORT,
        help=f"Server port (default: {config.SERVER_PORT})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=config.SERVER_HOST,
        help=f"Server host (default: {config.SERVER_HOST})",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on file changes",
    )

    args = parser.parse_args()

    logger.info(
        f"Starting Rune Protocol API Server on {args.host}:{args.port}"
    )

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=config.LOG_LEVEL.lower(),
    )
