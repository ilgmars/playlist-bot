"""Entry point for running the bot + web server."""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def main():
    """Run either the Discord bot, the web server, or both."""
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        logging.info("Usage: python -m playlistbot (set ROLE=web|bot|all)")
        return
    role = os.environ.get("ROLE", "web")

    if role == "bot":
        token = os.environ.get("DISCORD_TOKEN")
        if not token:
            logging.error("DISCORD_TOKEN environment variable is required for bot mode")
            sys.exit(1)
        from .bot import run_bot

        run_bot(token)
    elif role == "web":
        import uvicorn

        host = os.environ.get("WEB_HOST", "0.0.0.0")  # nosec B104 - containers must accept external traffic
        port = int(os.environ.get("WEB_PORT", "8000"))
        uvicorn.run("playlistbot.web:app", host=host, port=port, log_level="info")
    elif role == "all":
        import multiprocessing

        import uvicorn

        def run_web():
            uvicorn.run("playlistbot.web:app", host="0.0.0.0", port=8000, log_level="info")  # nosec B104 - container bind

        token = os.environ.get("DISCORD_TOKEN")
        if not token:
            logging.error("DISCORD_TOKEN required when ROLE=all")
            sys.exit(1)

        from .bot import run_bot

        p = multiprocessing.Process(target=run_web)
        p.start()
        run_bot(token)
    else:
        logging.error("Unknown ROLE: %s. Use 'bot', 'web', or 'all'.", role)
        sys.exit(1)


if __name__ == "__main__":
    main()
