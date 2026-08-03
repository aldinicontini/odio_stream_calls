import os
import json
import struct
import asyncio

from dotenv import load_dotenv
load_dotenv()

from utils.app_debugger import init_debugger
from utils.socket_utils import ensure_single_instance
from stream_gateway.session import CallSession, SocketSink, sessions

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

HOST = os.getenv("AUDIO_HOST", "0.0.0.0")
PORT = int(os.getenv("AUDIO_PORT", "9019"))

LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logging = init_debugger(LOG_FILE)

# ---------------------------------------------------------------------------
# Tipos de paquete AudioSocket
# 1 byte tipo + 2 bytes longitud big-endian + payload
# ---------------------------------------------------------------------------
PKT_TYPE_HANGUP   = 0x00
PKT_TYPE_UUID     = 0x01
PKT_TYPE_AUDIO_RX = 0x10  # audio entrante al canal (READ)  → track "outbound" en odio
PKT_TYPE_AUDIO_TX = 0x11  # audio saliente del canal (WRITE) → track "inbound"  en odio


# ---------------------------------------------------------------------------
# Parser de framing
# ---------------------------------------------------------------------------

async def read_packet(reader):
    header = await reader.readexactly(3)
    ptype = header[0]
    length = struct.unpack("!H", header[1:3])[0]
    payload = await reader.readexactly(length) if length else b""
    return ptype, payload


# ---------------------------------------------------------------------------
# Handler de conexión
# ---------------------------------------------------------------------------

async def handle_client(reader, writer):
    peer = writer.get_extra_info("peername")
    session: CallSession | None = None
    bytes_rx = 0
    bytes_tx = 0

    logging.info(f"New StreamSocket connection from {peer}")

    try:
        while True:
            ptype, payload = await read_packet(reader)

            if ptype == PKT_TYPE_UUID:
                call_uuid = payload.decode(errors="replace")
                session = CallSession(call_uuid)
                sessions[call_uuid] = session
                logging.info(f"Call UUID: {call_uuid} — session created (NullSink active)")

            elif ptype == PKT_TYPE_AUDIO_RX:
                if session is not None:
                    session.rx_sink.write(payload)
                    bytes_rx += len(payload)

            elif ptype == PKT_TYPE_AUDIO_TX:
                if session is not None:
                    session.tx_sink.write(payload)
                    bytes_tx += len(payload)

            elif ptype == PKT_TYPE_HANGUP:
                who_label = "desconocido"
                if len(payload) >= 2:
                    who, cause = payload[0], payload[1]
                    who_label = {0: "desconocido", 1: "TX", 2: "RX"}.get(who, "desconocido")
                    if session is not None:
                        session.hangup_who = who_label
                        session.hangup_cause = cause
                        session.deactivate_streaming()
                logging.info(
                    f"Hangup received for {session.call_uuid if session else 'unknown'} "
                    f"— colgó: {who_label} — cause={payload[1] if len(payload) >= 2 else None} "
                    f"— NullSink active, connection remaining open"
                )

            else:
                logging.warning(f"Tipo de paquete desconocido: {ptype:#04x}")

    except asyncio.IncompleteReadError:
        logging.info(f"Connection closed by peer {peer}")

    except Exception as e:
        logging.exception(f"Error handling connection from {peer}: {e}")

    finally:
        if session is not None:
            session.signal_hangup()
            sessions.pop(session.call_uuid, None)
            logging.info(
                f"Finished call {session.call_uuid} "
                f"— rx={bytes_rx}B tx={bytes_tx}B "
                f"— hangup_who={session.hangup_who} cause={session.hangup_cause}"
            )

        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------------------
# Servidor TCP — expuesto para uso desde main.py
# ---------------------------------------------------------------------------

async def start_audio_server():
    """Inicia el servidor TCP de AudioSocket. Asegura antes la instancia única del puerto."""
    await ensure_single_instance(HOST, PORT, logger=logging)
    server = await asyncio.start_server(handle_client, HOST, PORT)
    addr = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    logging.info(f"StreamSocket server listening on {addr}")
    print(f"[AUDIO] StreamSocket listening on {addr}")
    async with server:
        await server.serve_forever()


async def main():
    await start_audio_server()


if __name__ == "__main__":
    asyncio.run(main())
