"""
control_server.py — Servidor WebSocket de control (puerto 9020).

Recibe mensajes JSON desde los navegadores de los agentes.
Comparte el diccionario `sessions` con audio_socket_server.py dentro
del mismo event loop de asyncio — sin IPC, sin Redis, sin procesos separados.

Protocolo de mensajes (cliente → servidor):
--------------------------------------------
  ANSWER  — El agente contestó una llamada.
  HANGUP  — El agente colgó manualmente.
  PING    — Keepalive del navegador.

Protocolo de respuestas (servidor → cliente):
---------------------------------------------
  ACK     — Operación aceptada.
  PONG    — Respuesta al PING.
  ERROR   — Error con código y descripción.

Autenticación:
--------------
  Todos los mensajes deben incluir el campo "token" con el valor
  configurado en CONTROL_AUTH_TOKEN. Si el token es vacío (no configurado),
  la autenticación queda deshabilitada (útil para desarrollo local).
"""

import asyncio
import json
import logging
import os
import ssl

import websockets
from http import HTTPStatus
from dotenv import load_dotenv
from datetime import datetime

from utils.app_debugger import init_debugger
from utils.socket_utils import ensure_single_instance
from stream_gateway.session import sessions, SocketSink

load_dotenv()

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

CONTROL_HOST        = os.getenv("CONTROL_HOST", "0.0.0.0")
CONTROL_PORT        = int(os.getenv("CONTROL_PORT", "9020"))
CONTROL_AUTH_TOKEN  = os.getenv("CONTROL_AUTH_TOKEN", "")
CONTROL_RECV_TIMEOUT = float(os.getenv("CONTROL_RECV_TIMEOUT", "300"))  # segundos

SSL_CERT_PATH       = os.getenv("SSL_CERT_PATH", "").strip()
SSL_KEY_PATH        = os.getenv("SSL_KEY_PATH", "").strip()

LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logger = init_debugger(LOG_FILE)


# ---------------------------------------------------------------------------
# Helpers de respuesta
# ---------------------------------------------------------------------------

async def _send(ws, data: dict) -> None:
    """Envía un mensaje JSON. Ignora errores de conexión ya cerrada."""
    try:
        await ws.send(json.dumps(data))
    except Exception as exc:
        logger.warning(f"[CONTROL] Failed to send response: {exc}")


async def _ack(ws, callid: str) -> None:
    await _send(ws, {"type": "ACK", "callid": callid})


async def _error(ws, code: str, message: str) -> None:
    await _send(ws, {"type": "ERROR", "code": code, "message": message})


# ---------------------------------------------------------------------------
# Callback de task completado — captura errores silenciosos
# ---------------------------------------------------------------------------

def _on_stream_done(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception(f"[CONTROL] Streaming task raised an exception: {exc}")


# ---------------------------------------------------------------------------
# Handlers de mensajes
# ---------------------------------------------------------------------------

async def _validate_customer_information(ws, customer_information: dict) -> bool:
    """Valida los campos requeridos y vacíos de customer_information."""
    required_fields = [
        "coeName",
        "agentName",
        "agentId",
        "customerName",
        "uuid"
    ]

    missing_fields = [f for f in required_fields if f not in customer_information]
    if missing_fields:
        await _error(
            ws,
            "INVALID_FIELDS",
            f"customer_information is missing required fields: {', '.join(missing_fields)}"
        )
        return False

    # Los únicos campos que pueden estar vacíos
    allowed_empty = {"customerName", "uuid"}

    empty_fields = [
        f for f in required_fields
        if f not in allowed_empty
        and (customer_information[f] is None or str(customer_information[f]).strip() == "")
    ]

    if empty_fields:
        await _error(
            ws,
            "INVALID_FIELDS",
            f"The following customer_information fields cannot be empty: {', '.join(empty_fields)}"
        )
        return False

    return True


async def _handle_answer(ws, msg: dict, peer: tuple) -> None:
    """
    El agente contestó la llamada.

    Valida los campos requeridos, localiza la sesión, cambia los sinks
    de NullSink → SocketSink, y lanza run_both_live() como asyncio.Task.
    """
    from stream_gateway.stream_socket import run_both_live

    callid               = msg.get("callid")
    agent                = msg.get("agent")
    customer_information = msg.get("customer_information")

    # Validación de campos obligatorios
    if not callid or not agent or not customer_information:
        logger.warning(f"[CONTROL] ANSWER missing required fields from {peer}: {msg}")
        await _error(ws, "MISSING_FIELDS", "callid, agent, and customer_information are required")
        return

    if not isinstance(customer_information, dict):
        await _error(ws, "INVALID_FIELDS", "customer_information must be a JSON object")
        return

    if not await _validate_customer_information(ws, customer_information):
        return

    # Buscar sesión activa
    session = sessions.get(callid)
    if session is None:
        logger.warning(f"[CONTROL] ANSWER for unknown callid={callid} from {peer}")
        await _error(ws, "NOT_FOUND", f"Session '{callid}' not found or already closed")
        return

    if session.state == "answered":
        logger.warning(f"[CONTROL] ANSWER for already-answered callid={callid}")
        await _error(ws, "ALREADY_ANSWERED", f"Session '{callid}' is already answered")
        return

    if session.state == "finished":
        logger.warning(f"[CONTROL] ANSWER for finished callid={callid}")
        await _error(ws, "HANGUP", f"Session '{callid}' already finished")
        return

    logger.info(f"[CONTROL] ANSWER callid={callid} agent={agent} from {peer}")

    ## default values and current datetime
    customer_information["callTime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if "custom" in callid.lower():
        customer_information["callType"] = "inbound"
    else:
        customer_information["callType"] = "outbound"

    # Activar streaming mediante CallSession (NullSink → SocketSink)
    session.activate_streaming(agent, customer_information)

    # Lanzar streaming como task independiente — no bloquea el control server
    task = asyncio.create_task(run_both_live(session))
    task.add_done_callback(_on_stream_done)
    session.stream_task = task

    await _ack(ws, callid)
    logger.info(f"[CONTROL] Live streaming started for callid={callid}")


async def _handle_hangup(ws, msg: dict, peer: tuple) -> None:
    """Cuelga/pausa el streaming de una llamada desde el control server."""
    callid = msg.get("callid")
    if not callid:
        await _error(ws, "MISSING_FIELDS", "callid is required")
        return

    session = sessions.get(callid)
    if session is None:
        await _error(ws, "NOT_FOUND", f"Session '{callid}' not found")
        return

    logger.info(f"[CONTROL] Manual HANGUP callid={callid} from {peer}")
    # Redirigir audio a NullSink, detener consumidores de audio y mantener la conexión TCP
    session.deactivate_streaming()
    await _ack(ws, callid)


# ---------------------------------------------------------------------------
# Handler principal de conexión de agente
# ---------------------------------------------------------------------------

async def handle_agent(websocket) -> None:
    """
    Atiende una conexión WebSocket de un agente.
    """
    remote_addr = websocket.remote_address
    peer = websocket.request.headers.get(
        "X-Real-IP",
        remote_addr[0] if remote_addr else "unknown"
    )
    logger.info(f"[CONTROL] Agent connected from {peer}")

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=CONTROL_RECV_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[CONTROL] No message from {peer} in {CONTROL_RECV_TIMEOUT}s — closing"
                )
                break
            except websockets.exceptions.ConnectionClosed:
                logger.info(f"[CONTROL] Agent {peer} disconnected")
                break

            # Parse JSON
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[CONTROL] Invalid JSON from {peer}: {raw[:200]!r}")
                await _error(websocket, "INVALID_JSON", "Message is not valid JSON")
                continue

            if not isinstance(msg, dict):
                await _error(websocket, "INVALID_FORMAT", "Message must be a JSON object")
                continue

            # Autenticación por token
            if CONTROL_AUTH_TOKEN:
                if msg.get("token") != CONTROL_AUTH_TOKEN:
                    logger.warning(f"[CONTROL] Unauthorized access from {peer} original msg: {msg}")
                    await _error(websocket, "UNAUTHORIZED", "Invalid or missing token")
                    continue

            msg_type = str(msg.get("type", "")).upper()

            if msg_type == "ANSWER":
                await _handle_answer(websocket, msg, peer)

            elif msg_type == "HANGUP":
                await _handle_hangup(websocket, msg, peer)

            elif msg_type == "PING":
                await _send(websocket, {"type": "PONG"})

            else:
                logger.warning(f"[CONTROL] Unknown message type '{msg_type}' from {peer}")
                await _error(websocket, "UNKNOWN_TYPE", f"Unknown message type: '{msg_type}'")

    except Exception as exc:
        logger.exception(f"[CONTROL] Unexpected error from {peer}: {exc}")

    finally:
        logger.info(f"[CONTROL] Connection closed: {peer}")

async def process_request(connection, request):
    headers = request.headers

    if headers.get("Upgrade", "").lower() != "websocket":
        logger.debug(
            f"HTTP request from {connection.remote_address} ignored "
            f"(Upgrade={headers.get('Upgrade')}, Connection={headers.get('Connection')})"
        )

        return (
            HTTPStatus.BAD_REQUEST,
            [],
            b"Bad request.\n"
        )

    return None

# ---------------------------------------------------------------------------
# Start del servidor — expuesto para main.py
# ---------------------------------------------------------------------------

async def start_control_server() -> None:
    """Inicia el servidor WebSocket de control. Asegura antes la instancia única del puerto."""
    await ensure_single_instance(CONTROL_HOST, CONTROL_PORT, is_ws=True, logger=logger)
    
    ssl_context = None
    protocol_scheme = "ws"

    if SSL_CERT_PATH and SSL_KEY_PATH:
        if os.path.exists(SSL_CERT_PATH) and os.path.exists(SSL_KEY_PATH):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(certfile=SSL_CERT_PATH, keyfile=SSL_KEY_PATH)
            protocol_scheme = "wss"
            logger.info(f"[CONTROL] SSL certificate loaded successfully from {SSL_CERT_PATH}")
        else:
            logger.error(
                f"[CONTROL] SSL enabled but cert/key file not found. "
                f"cert='{SSL_CERT_PATH}', key='{SSL_KEY_PATH}'"
            )

    async with websockets.serve(handle_agent, CONTROL_HOST, CONTROL_PORT, ssl=ssl_context, process_request=process_request):
        logger.info(f"[CONTROL] WebSocket control server listening on {protocol_scheme}://{CONTROL_HOST}:{CONTROL_PORT}")
        print(f"[CONTROL] WebSocket listening on {protocol_scheme}://{CONTROL_HOST}:{CONTROL_PORT}")
        await asyncio.Future()  # correr indefinidamente


# ---------------------------------------------------------------------------
# Entry point standalone
# ---------------------------------------------------------------------------

async def main() -> None:
    await start_control_server()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("[CONTROL] Server stopped by user.")
