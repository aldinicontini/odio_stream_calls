"""
main.py — Unified entry point for the event-driven audio server.

Spawns both network listeners inside the same asyncio event loop:
  - AUDIO_PORT   (default 9019) -> High-throughput TCP audio reception from Asterisk
  - CONTROL_PORT (default 9020) -> Low-latency WebSocket control gateway for agent state

Shared Memory Model:
  Both services share the `sessions` dictionary in `stream_gateway.session`
  without requiring inter-process communication (IPC), Redis, or message brokers.

Usage:
    python main.py
"""

import asyncio
import os
from dotenv import load_dotenv

# Load environment configuration prior to importing custom app modules
load_dotenv()

from utils.app_debugger import init_debugger
from stream_gateway.audio_socket_server import start_audio_server
from ws_agent_gateway.control_server import start_control_server

# Initialize logging singleton
LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logger = init_debugger(LOG_FILE)


async def main() -> None:
    """
    Main asynchronous supervisor task.
    
    Concurrently runs both the raw TCP Audio Server and the WebSocket Agent Gateway
    in a non-blocking, single-process event loop.
    """
    logger.info("=" * 60)
    logger.info("Starting event-driven audio server (main.py)")
    logger.info("=" * 60)

    # Concurrently execute audio streaming worker and WebSocket agent control servers
    await asyncio.gather(
        start_audio_server(),
        start_control_server(),
    )


if __name__ == "__main__":
    try:
        # Bootstrap and run the asyncio event loop lifecycle
        asyncio.run(main())
    except KeyboardInterrupt:
        # Graceful shutdown handler for interactive terminals / systemd signals
        logger.info("Server stopped by user (KeyboardInterrupt).")
    except Exception as exc:
        # Log unhandled runtime crashes with complete stack trace
        logger.exception(f"Server crashed: {exc}")
        raise