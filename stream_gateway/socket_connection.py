import asyncio
import websockets
import json
import ssl
import traceback
from dotenv import load_dotenv
import os

from utils.app_debugger import init_debugger

load_dotenv()

# Obtener variables
WSS_ODIO_URL = os.getenv('WSS_ODIO_URL')
SSL_CERT_PATH = os.getenv('SSL_CERT_PATH') or None
PING_INTERVAL = os.getenv('PING_INTERVAL') or None

LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS', 'connections.log')
logging = init_debugger(LOG_FILE_CONNECTIONS)


def get_ssl_context(cert_path=None):
    if cert_path:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.load_verify_locations(cert_path)
        return ssl_context
    return ssl._create_unverified_context()


async def ws_connection(url=WSS_ODIO_URL, cert_path=SSL_CERT_PATH, timeout=10.0):
    ssl_context = get_ssl_context(cert_path)
    try:
        ws = await asyncio.wait_for(
            websockets.connect(url, ssl=ssl_context, max_size=None, ping_interval=None, ping_timeout=None),
            timeout=timeout
        )
        return ws
    except Exception as e:
        logging.error(f"WebSocket connection failed to {url}: {e}")
        return None


async def ws_send_test_message(ws, message="ping"):
    """
    Envía un mensaje simple al servidor y espera respuesta.
    """
    if ws is None:
        logging.error("No hay conexión activa.")
        return

    try:
        logging.info(f"→ Enviando mensaje de prueba: {message}")
        await ws.send(json.dumps({"event": "Test", "payload": message}))
        reply = await asyncio.wait_for(ws.recv(), timeout=5)
        logging.info(f"← Respuesta recibida: {reply}")
    except asyncio.TimeoutError:
        logging.error("⏱ Sin respuesta del servidor (timeout).")
    except Exception:
        logging.exception("Error while try to send test message")
        traceback.print_exc()


async def ws_keepalive(ws, interval=PING_INTERVAL):
    try:
        while True:
            logging.debug(f"Durmiendo {interval} segundos antes del ping")
            await asyncio.sleep(float(interval))
            pong_waiter = await ws.ping()
            await pong_waiter
            logging.info("[DEBUG] Ping enviado y pong recibido")
    except asyncio.CancelledError:
        logging.info("Keepalive cancelled")
    except Exception as e:
        logging.error(f"Keepalive interrumpido: {e}")
