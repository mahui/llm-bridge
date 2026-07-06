"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from llm_bridge import __version__
from llm_bridge.api import admin, auth, chat, models
from llm_bridge.auth.manager import AuthManager
from llm_bridge.config import BridgeConfig
from llm_bridge.gateway.middleware import (
    APIKeyMiddleware,
    ErrorHandlerMiddleware,
    LoggingMiddleware,
)
from llm_bridge.gateway.rate_limiter import RateLimiter
from llm_bridge.gateway.router import ModelRouter
from llm_bridge.providers import get_provider, register_provider
from llm_bridge.providers.antigravity import AntigravityProvider
from llm_bridge.providers.claude import ClaudeProvider
from llm_bridge.providers.codex import CodexProvider
from llm_bridge.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize providers on startup, shutdown on exit."""
    config: BridgeConfig = app.state.config

    # Initialize auth manager
    auth_manager = AuthManager()
    await auth_manager.initialize()
    app.state.auth_manager = auth_manager

    # Initialize rate limiter
    rate_limiter = RateLimiter(default_rpm=config.rate_limiting.default_rpm)
    for name, limits in config.rate_limiting.per_provider.items():
        rate_limiter.configure_provider(name, limits.rpm)
    app.state.rate_limiter = rate_limiter

    # Initialize providers
    providers_to_init = []

    if config.providers.antigravity.enabled:
        ag = AntigravityProvider(
            auth_manager=auth_manager,
            api_base=config.providers.antigravity.api_base,
            project_id=config.providers.antigravity.project_id,
        )
        providers_to_init.append(("antigravity", ag))

    if config.providers.claude.enabled:
        cl = ClaudeProvider(cli_path=config.providers.claude.cli_path)
        providers_to_init.append(("claude", cl))

    if config.providers.codex.enabled:
        cx = CodexProvider(cli_path="codex")
        providers_to_init.append(("codex", cx))

    if config.providers.gemini.enabled:
        shared_project = config.providers.antigravity.project_id
        gm = GeminiProvider(
            auth_manager=auth_manager,
            cli_path="gemini",
            api_base=config.providers.gemini.api_base,
            project_id=shared_project,
            mode="cli",  # CLI subprocess as primary strategy
        )
        providers_to_init.append(("gemini", gm))

    for name, provider in providers_to_init:
        try:
            await provider.initialize()
            register_provider(name, provider)
            logger.info("Provider %s initialized (status: %s)", name, provider.status.value)
        except Exception:
            logger.exception("Failed to initialize provider %s", name)

    # Share project_id between Antigravity and Gemini (same API)
    ag_instance = get_provider("antigravity")
    gm_instance = get_provider("gemini")
    if ag_instance and gm_instance:
        ag_pid = getattr(ag_instance, "project_id", "")
        gm_pid = getattr(gm_instance, "project_id", "")
        if ag_pid and not gm_pid:
            gm_instance.project_id = ag_pid
        elif gm_pid and not ag_pid:
            ag_instance.project_id = gm_pid

    # Initialize router
    app.state.router = ModelRouter(config)

    logger.info("LLM-Bridge v%s started on %s:%d", __version__, config.server.host, config.server.port)

    yield

    # Shutdown providers
    for name, provider in providers_to_init:
        try:
            await provider.shutdown()
        except Exception:
            logger.exception("Error shutting down provider %s", name)

    logger.info("LLM-Bridge shutdown complete")


def create_app(config: BridgeConfig) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="LLM-Bridge",
        version=__version__,
        description="AI Proxy Gateway with OpenAI-compatible API",
        lifespan=lifespan,
    )

    app.state.config = config

    # Middleware (applied in reverse order)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(APIKeyMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(chat.router)
    app.include_router(models.router)
    app.include_router(auth.router)
    app.include_router(admin.router)

    # Static files & UI
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def root():
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"name": "LLM-Bridge", "version": __version__, "docs": "/docs"}

    return app
