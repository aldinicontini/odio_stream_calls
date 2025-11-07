import asyncio
import websockets
import json
import ssl
import traceback
from dotenv import load_dotenv
import os

load_dotenv()

# Obtener variables
WSS_ODIO_URL = os.getenv('WSS_ODIO_URL')
SSL_CERT_PATH = os.getenv('SSL_CERT_PATH') or None
PING_INTERVAL = os.getenv('PING_INTERVAL') or None

#loging
from app_debuger import init_debugger
LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS')
logging = init_debugger(LOG_FILE_CONNECTIONS)
# end loging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("websockets")
logger.setLevel(logging.DEBUG)

# Config opcional: si tienes certificado custom o self-signed
def get_ssl_context(cert_path=None):
    if cert_path:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.load_verify_locations(cert_path)
        return ssl_context
    return ssl._create_unverified_context()

async def ws_connection(url=WSS_ODIO_URL, cert_path=SSL_CERT_PATH):
    """
    Intenta conectarse al WebSocket y retorna el objeto de conexión (stream)
    o None si falla. Loguea errores y handshake.
    """
    ssl_context = get_ssl_context(cert_path)
    try:
        logging.info(f"Intentando conectar a {url} ...")
        ws = await websockets.connect(url, ssl=ssl_context, max_size=None)
        logging.info(f"Conexión establecida correctamente.")
        return ws
    except Exception as e:
        logging.error(f"❌ Falló la conexión WebSocket: {e}")
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
            await asyncio.sleep(float(interval))  # fuerza conversión a número
            pong_waiter = await ws.ping()
            await pong_waiter
            logging.info("[DEBUG] Ping enviado y pong recibido")
    except asyncio.CancelledError:
        logging.info("Keepalive cancelled")
    except Exception as e:
        logging.exception(f"Keepalive interrumpido: {e}")

async def listen_messages(ws):
    try:
        async for message in ws:
            logging.info(f"[WS] Mensaje recibido: {message}")
            # Si el mensaje está en formato JSON:
            try:
                data = json.loads(message)
                logging.debug(f"[WS JSON] {data}")
            except json.JSONDecodeError:
                logging.debug("[WS] Mensaje no es JSON, contenido crudo recibido")
    except websockets.ConnectionClosed as e:
        logging.warning(f"Conexión cerrada por el servidor: {e.code} {e.reason}")
    except Exception as e:
        logging.error(f"Error leyendo mensajes del WebSocket: {e}")
