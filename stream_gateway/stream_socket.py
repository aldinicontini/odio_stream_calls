import asyncio
import argparse
import time
import wave
import os
from datetime import datetime
from dotenv import load_dotenv

from stream_gateway.socket_connection import ws_connection, ws_keepalive
from stream_gateway.odio_socket import send_connected_event, send_start_event, send_stop_event, send_media_event
from utils.custom_information import get_customer_information
from utils.app_debugger import init_debugger

load_dotenv()

# Obtener variables
WSS_ODIO_URL = os.getenv('WSS_ODIO_URL')
SSL_CERT_PATH = os.getenv('SSL_CERT_PATH') or None
FRAME_DURATION = float(os.getenv('FRAME_DURATION', '0.02'))
WSS_ODIO_URL_INBOUND_FLOW = os.getenv('WSS_ODIO_URL_INBOUND_FLOW')

# Logging
LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS', 'connections.log')
logging = init_debugger(LOG_FILE_CONNECTIONS)

CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', '320'))  # 20ms de audio PCM 16-bit a 8kHz
INACTIVITY_TIMEOUT = int(os.getenv('INACTIVITY_TIMEOUT', '30'))
MONITORING_TIMEOUT = int(os.getenv('MONITORING_TIMEOUT', '15'))

# Configuración de audio
SAMPLE_RATE = int(os.getenv('SAMPLE_RATE', '8000'))
SAMPLE_WIDTH = int(os.getenv('SAMPLE_WIDTH', '2'))
CHANNELS = int(os.getenv('CHANNELS', '1'))
TEST_OUTPUT_FILE = os.getenv('TEST_OUTPUT_FILE')


def getRecordingPath(customer_information, audio_file):
    event_date = customer_information.get("event_date")
    if not event_date:
        logging.error("Error getting the date")
        return None

    try:
        dt = datetime.strptime(event_date, "%Y-%m-%d")
        monitor_dir = f"/var/spool/asterisk/monitor/{dt.year}/{dt.month:02d}/{dt.day:02d}"
    except ValueError:
        logging.error(f"Invalid date format: {event_date}")
        return None

    full_audio_path = os.path.join(monitor_dir, audio_file)
    return full_audio_path


def init_wave_file(filename):
    """Inicializa un WAV file para escritura."""
    wf = wave.open(filename, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(SAMPLE_WIDTH)
    wf.setframerate(SAMPLE_RATE)
    return wf


async def stream_audio(ws, audio_file, direction, CALL_ID, sequence_counter, sequence_lock, test=False):
    """Stream a single audio direction (inbound or outbound) over a shared WebSocket."""
    chunk_number = 0 
    time_elapsed = 0.0
    last_chunk_time = time.time()
    has_started = False

    if not test:
        try:
            with open(audio_file, "rb", buffering=0) as audio_pipe:
                audio_pipe.seek(0, os.SEEK_END)
                logging.info(f"{CALL_ID} - [{direction}] Starting live reading of channel")

                while True:
                    chunk = audio_pipe.read(CHUNK_SIZE)
                    if not chunk:
                        timeout_limit = MONITORING_TIMEOUT if has_started else INACTIVITY_TIMEOUT
                        if time.time() - last_chunk_time > timeout_limit:
                            logging.info(f"{CALL_ID} - [{direction}] channel dead for {timeout_limit}s, end of call.")
                            break
                        await asyncio.sleep(FRAME_DURATION)
                        continue

                    has_started = True
                    async with sequence_lock:
                        sequence = sequence_counter[0]
                        sequence_counter[0] += 1

                    chunk_number += 1
                    logging.debug(f"{CALL_ID} - [{direction}] Sequence Number: {sequence}, Chunk: {chunk_number}")

                    await send_media_event(ws, CALL_ID, direction, sequence, round(time_elapsed, 3), chunk)
                    time_elapsed += FRAME_DURATION
                    last_chunk_time = time.time()
                    await asyncio.sleep(FRAME_DURATION)

        except Exception as e:
            logging.error(f"{CALL_ID} - [{direction}] Error en transmisión: {e}")
    else:
        try:
            with open(audio_file, "rb", buffering=0) as audio_pipe:
                logging.info(f"{CALL_ID} - [{direction}] starting reading of {audio_file} (test mode)")

                while True:
                    chunk = audio_pipe.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    has_started = True
                    async with sequence_lock:
                        sequence = sequence_counter[0]
                        sequence_counter[0] += 1

                    chunk_number += 1
                    logging.debug(f"{CALL_ID} - [{direction}] Sequence Number: {sequence}, Chunk: {chunk_number}")

                    await send_media_event(ws, CALL_ID, direction, sequence, round(time_elapsed, 3), chunk)
                    time_elapsed += FRAME_DURATION
                    await asyncio.sleep(FRAME_DURATION)

        except Exception as e:
            logging.error(f"{CALL_ID} - [{direction}] Error en transmisión: {e}")


async def run_both(audio_file, test_flag):
    CALL_ID = audio_file
    if "custom" in CALL_ID.lower():
        call_direction = "inbound"
        dir_in, dir_out = "inbound", "outbound"
    else:
        call_direction = "outbound"
        dir_in, dir_out = "outbound", "inbound"

    logging.info(f"{CALL_ID} - Starting new '{call_direction}' call streaming process.")

    audio_in_name = f"{audio_file}-in.wav"
    audio_out_name = f"{audio_file}-out.wav"

    customer_information = get_customer_information(audio_in_name)
    if not customer_information:
        logging.error(f"{CALL_ID} - Customer information not found for: {audio_in_name}")
        return
    customer_information["callType"] = call_direction
    logging.info(f"{CALL_ID} - getting customer information - {customer_information}")

    audio_in_path = getRecordingPath(customer_information, audio_in_name)
    audio_out_path = getRecordingPath(customer_information, audio_out_name)

    if not audio_in_path or not audio_out_path:
        logging.error(f"{CALL_ID} - Could not resolve audio file paths.")
        return

    ws = await ws_connection()
    if not ws or ws.state == 3:
        logging.error(f"{CALL_ID} - Cannot connect to WebSocket - direction: {call_direction} Agent: {customer_information.get('agentId', '')}")
    else:
        logging.info(f"{CALL_ID} - WebSocket connection established - direction: {call_direction} Agent: {customer_information.get('agentId', '')}")
        
        connect = await send_connected_event(ws)
        if not connect or not connect.get("success"):
            logging.error(f"{CALL_ID} - Failed to send connected event.")
            return

        start = await send_start_event(ws, CALL_ID, customer_information)
        if not start or not start.get("success"):
            logging.error(f"{CALL_ID} - Failed to send start event.")
            return

        sequence_counter = [0]
        sequence_lock = asyncio.Lock()

        await asyncio.gather(
            stream_audio(ws, audio_in_path,  dir_in,  CALL_ID, sequence_counter, sequence_lock, test_flag),
            stream_audio(ws, audio_out_path, dir_out, CALL_ID, sequence_counter, sequence_lock, test_flag),
        )

        await send_stop_event(ws, CALL_ID)
        await ws.close()
        logging.info(f"{CALL_ID} - WebSocket connection closed correctly.")

    if call_direction == "inbound" and WSS_ODIO_URL_INBOUND_FLOW:
        logging.info(f"{CALL_ID} ⬇️ - Starting preparation of customer information for inbound stream.")  
        customer_information_inbound = {
            "tenantId": "75612601",
            "coeName": customer_information.get("coeName", ""),
            "agentName": customer_information.get("agentName", ""),
            "agentId": customer_information.get("agentId", ""),
            "customerPhone": customer_information.get("customerPhoneNumber", ""),
            "customerName": customer_information.get("customerName", ""),
            "callTime": customer_information.get("callTime", ""),
            "call_type": "inbound"
        }

        ws_ns = await ws_connection(WSS_ODIO_URL_INBOUND_FLOW)
        if not ws_ns or ws_ns.state == 3:
            logging.error(f"INBOUND {CALL_ID} - {customer_information_inbound.get('customerName', 'Unknown')} Cannot connect to WebSocket.")
            return
        logging.info(f"{CALL_ID} ⬇️ - WebSocket connection established for inbound stream - Agent: {customer_information_inbound.get('agentId', '')}")  

        connect_ns = await send_connected_event(ws_ns)
        if not connect_ns or not connect_ns.get("success"):
            logging.error(f"INBOUND {CALL_ID} - Failed to send connected event.")
            return

        start_ns = await send_start_event(ws_ns, CALL_ID, customer_information_inbound)
        if not start_ns or not start_ns.get("success"):
            logging.error(f"INBOUND {CALL_ID} - Failed to send start event.")
            return

        sequence_counter_ns = [0]
        sequence_lock_ns = asyncio.Lock()

        await asyncio.gather(
            stream_audio(ws_ns, audio_in_path,  "inbound",  CALL_ID, sequence_counter_ns, sequence_lock_ns, test_flag),
            stream_audio(ws_ns, audio_out_path, "outbound", CALL_ID, sequence_counter_ns, sequence_lock_ns, test_flag),
        )

        await send_stop_event(ws_ns, CALL_ID)
        await ws_ns.close()
        logging.info(f"INBOUND {CALL_ID} ⬇️ - WebSocket connection closed correctly.")


# ===========================================================================
# LIVE STREAMING — Consume audio desde una CallSession en tiempo real
# ===========================================================================

async def stream_audio_live(ws, queue: asyncio.Queue, direction: str, CALL_ID: str,
                            sequence_counter: list, sequence_lock: asyncio.Lock) -> None:
    chunk_number = 0
    time_elapsed = 0.0
    has_started = False

    logging.info(f"{CALL_ID} - [{direction}] Starting live stream from queue")

    try:
        while True:
            timeout = MONITORING_TIMEOUT if has_started else INACTIVITY_TIMEOUT

            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logging.info(
                    f"{CALL_ID} - [{direction}] Queue timeout after {timeout}s, ending live stream."
                )
                break

            if chunk is None:
                logging.info(f"{CALL_ID} - [{direction}] Hangup sentinel received, ending live stream.")
                break

            has_started = True

            async with sequence_lock:
                sequence = sequence_counter[0]
                sequence_counter[0] += 1

            chunk_number += 1
            logging.debug(f"{CALL_ID} - [{direction}] Sequence: {sequence}, Chunk: {chunk_number}")

            await send_media_event(ws, CALL_ID, direction, sequence, round(time_elapsed, 3), chunk)
            time_elapsed += FRAME_DURATION

    except asyncio.CancelledError:
        logging.info(f"{CALL_ID} - [{direction}] Live stream cancelled.")
    except Exception as e:
        logging.error(f"{CALL_ID} - [{direction}] Error in live stream: {e}")


async def run_both_live(session) -> None:
    CALL_ID = session.call_uuid
    customer_information = dict(session.customer_information)

    if "custom" in CALL_ID.lower():
        call_direction = "inbound"
        dir_rx, dir_tx = "inbound", "outbound"
    else:
        call_direction = "outbound"
        dir_rx, dir_tx = "outbound", "inbound"

    customer_information["callType"] = call_direction
    logging.info(
        f"{CALL_ID} - [LIVE] Starting '{call_direction}' live stream. "
        f"Agent: {session.agent_id}"
    )

    ws = await ws_connection()
    if not ws or ws.state == 3:
        logging.error(
            f"{CALL_ID} - [LIVE] Cannot connect to WebSocket. Agent: {session.agent_id}"
        )
        return

    logging.info(
        f"{CALL_ID} - [LIVE] WebSocket connection established. Agent: {session.agent_id}"
    )

    connect = await send_connected_event(ws)
    if not connect or not connect.get("success"):
        logging.error(f"{CALL_ID} - [LIVE] Failed to send connected event.")
        await ws.close()
        return

    start = await send_start_event(ws, CALL_ID, customer_information)
    if not start or not start.get("success"):
        logging.error(f"{CALL_ID} - [LIVE] Failed to send start event.")
        await ws.close()
        return

    await asyncio.gather(
        stream_audio_live(
            ws, session.rx_queue, dir_rx, CALL_ID,
            session.sequence_counter, session.sequence_lock
        ),
        stream_audio_live(
            ws, session.tx_queue, dir_tx, CALL_ID,
            session.sequence_counter, session.sequence_lock
        ),
    )

    await send_stop_event(ws, CALL_ID)
    await ws.close()
    logging.info(f"{CALL_ID} - [LIVE] WebSocket connection closed correctly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file", help="Ruta base del archivo de audio (sin -in/out)")
    parser.add_argument("--test", action="store_true", help="Modo test (lee el archivo completo sin esperar)")

    args = parser.parse_args()

    try:
        asyncio.run(run_both(args.audio_file, args.test))
    except KeyboardInterrupt:
        print("[INFO] Finalizado por usuario")
    except Exception as e:
        print(f"[ERROR] Interrumpido: {e}")
