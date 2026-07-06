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

    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Load config
    config = load_config(args.config)
    host = args.host or config.server.host
    port = args.port or config.server.port

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
