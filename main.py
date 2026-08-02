"""
main.py — Entry point unificado del servidor de audio event-driven.

Levanta ambos servidores en el mismo event loop de asyncio:
  - Puerto AUDIO_PORT  (default 9019) → recepción de audio desde Asterisk
  - Puerto CONTROL_PORT (default 9020) → control WebSocket para agentes

Comparten el diccionario `sessions` de session.py sin necesidad de IPC,
Redis ni procesos independientes.

Uso:
    python main.py
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from app_debuger import init_debugger
from audio_socket_server import start_audio_server
from control_server import start_control_server

LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logger = init_debugger(LOG_FILE)


async def main() -> None:
    logger.info("=" * 60)
    logger.info("Starting event-driven audio server (main.py)")
    logger.info("=" * 60)

    await asyncio.gather(
        start_audio_server(),
        start_control_server(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user (KeyboardInterrupt).")
    except Exception as exc:
        logger.exception(f"Server crashed: {exc}")
        raise
