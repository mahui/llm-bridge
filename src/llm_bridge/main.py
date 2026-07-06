"""LLM-Bridge entry point."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from llm_bridge.config import load_config
from llm_bridge.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-Bridge - AI Proxy Gateway")
    parser.add_argument("--host", default=None, help="Bind host (default: from config)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: from config)")
    parser.add_argument("--config", default=None, help="Path to config YAML file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Load config first so logging.level applies; --debug overrides it
    config = load_config(args.config)
    log_level = logging.DEBUG if args.debug else config.logging.level.upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Write CLI overrides back so downstream consumers (startup log,
    # CORS origin list) see the effective host/port.
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    host = config.server.host
    port = config.server.port

    # Create app
    app = create_app(config)

    # Run
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="debug" if args.debug else "info",
    )


if __name__ == "__main__":
    main()
