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
from llm_bridge.config import BridgeConfig
from llm_bridge.gateway.middleware import (
    APIKeyMiddleware,
    ErrorHandlerMiddleware,
    LoggingMiddleware,
)
from llm_bridge.gateway.router import ModelRouter
from llm_bridge.providers import register_provider
from llm_bridge.providers.claude import ClaudeProvider
from llm_bridge.providers.codex import CodexProvider
from llm_bridge.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize providers on startup, shutdown on exit."""
    config: BridgeConfig = app.state.config

    providers_to_init = []

    if config.providers.claude.enabled:
        cl = ClaudeProvider(cli_path=config.providers.claude.cli_path)
        providers_to_init.append(("claude", cl))

    if config.providers.codex.enabled:
        cx = CodexProvider(
            cli_path=config.providers.codex.cli_path,
            ignore_user_config=config.providers.codex.ignore_user_config,
        )
        providers_to_init.append(("codex", cx))

    if config.providers.gemini.enabled:
        gm = GeminiProvider(cli_path=config.providers.gemini.cli_path)
        providers_to_init.append(("gemini", gm))

    for name, provider in providers_to_init:
        try:
            await provider.initialize()
            register_provider(name, provider)
            logger.info("Provider %s initialized (status: %s)", name, provider.status.value)
        except Exception:
            logger.exception("Failed to initialize provider %s", name)

    app.state.router = ModelRouter(config)

    logger.info(
        "LLM-Bridge v%s started on %s:%d", __version__, config.server.host, config.server.port
    )

    yield

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
    # Only the built-in UI needs cross-origin access. A wildcard here would
    # let any web page the user visits drive this gateway (and burn paid
    # subscription quota) via the browser.
    ui_origins = [
        f"http://127.0.0.1:{config.server.port}",
        f"http://localhost:{config.server.port}",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ui_origins,
        allow_credentials=False,
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
