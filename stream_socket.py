import asyncio
import argparse
import time
import wave
import os
from datetime import datetime

from socket_connection import ws_connection, ws_keepalive
from odio_socket import send_connected_event, send_start_event, send_stop_event, send_media_event
from custom_information import get_customer_information
from dotenv import load_dotenv

load_dotenv()

# Obtener variables
WSS_ODIO_URL = os.getenv('WSS_ODIO_URL')
SSL_CERT_PATH = os.getenv('SSL_CERT_PATH') or None
FRAME_DURATION = float(os.getenv('FRAME_DURATION'))

# Logging
from app_debuger import init_debugger
LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS')
logging = init_debugger(LOG_FILE_CONNECTIONS)
# End logging

CHUNK_SIZE = int(os.getenv('CHUNK_SIZE'))  # 20ms de audio PCM 16-bit a 8kHz
INACTIVITY_TIMEOUT = int(os.getenv('INACTIVITY_TIMEOUT'))  # segundos sin nuevos bytes
MONITORING_TIMEOUT = int(os.getenv('MONITORING_TIMEOUT', 15))  # segundos de inactividad durante la llamada

# ----------------------------
# Configuración de audio
# ----------------------------
SAMPLE_RATE = int(os.getenv('SAMPLE_RATE'))   # Hz
SAMPLE_WIDTH = int(os.getenv('SAMPLE_WIDTH')) # bytes (16-bit PCM)
CHANNELS = int(os.getenv('CHANNELS'))         # mono
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
    logging.info(f"Full monitor path: {full_audio_path}")
    return full_audio_path


def init_wave_file(filename):
    """Inicializa un WAV file para escritura."""
    wf = wave.open(filename, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(SAMPLE_WIDTH)
    wf.setframerate(SAMPLE_RATE)
    return wf


async def stream_audio(ws, audio_file, direction, CALL_ID, sequence_counter, sequence_lock, test=False):
    """Stream a single audio direction (inbound or outbound) over a shared WebSocket ]
    """
    chunk_number = 0 
    time_elapsed = 0.0

    #Sample Rate: 8000 Hz
    #Chunk Size: 1024 samples
    #Chunk Duration: 128 ms
    #Encoding: audio/x-mulaw
    #Raw bytes per chunk: 1024 bytes
    #const interval = (chunkSize / audioBuffer.sampleRate) * 1000;

    if not test:
        try:
            last_chunk_time = time.time()
            has_started = False
            with open(audio_file, "rb", buffering=0) as audio_pipe:
                audio_pipe.seek(0, os.SEEK_END)
                logging.info(f"{CALL_ID} - [{direction}] Iniciando lectura en vivo de {audio_file}")

                while True:
                    chunk = audio_pipe.read(CHUNK_SIZE)
                    if not chunk:
                        timeout_limit = MONITORING_TIMEOUT if has_started else INACTIVITY_TIMEOUT
                        if time.time() - last_chunk_time > timeout_limit:
                            logging.info(f"{CALL_ID} - [{direction}] Archivo inactivo por {timeout_limit}s, se asume fin de grabación.")
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
                logging.info(f"{CALL_ID} - [{direction}] Iniciando lectura de {audio_file} (test mode)")

                while True:
                    chunk = audio_pipe.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    
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
    CALL_ID = audio_file  # base name without -in/-out suffix

    # Resolve customer info using the inbound filename as reference
    audio_in_name = f"{audio_file}-in.wav"
    audio_out_name = f"{audio_file}-out.wav"

    customer_information = get_customer_information(audio_in_name)
    logging.info(f"{CALL_ID} - running both funcion: infomation {audio_in_name} - {customer_information}")

    if not customer_information:
        logging.error(f"{CALL_ID} - Customer information not found for: {audio_in_name}")
        return

    audio_in_path = getRecordingPath(customer_information, audio_in_name)
    audio_out_path = getRecordingPath(customer_information, audio_out_name)

    if not audio_in_path or not audio_out_path:
        logging.error(f"{CALL_ID} - Could not resolve audio file paths.")
        return

    # Single shared WebSocket connection
    ws = await ws_connection()
    if not ws or ws.state == 3:
        logging.error(f"{CALL_ID} - {customer_information.get('customerName', 'Unknown')} Cannot connect to WebSocket.")
        return

    # Single connected event
    connect = await send_connected_event(ws)
    if not connect["success"]:
        logging.error(f"{CALL_ID} - Failed to send connected event.")
        return

    # Single start event
    start = await send_start_event(ws, CALL_ID, customer_information)
    if not start["success"]:
        logging.error(f"{CALL_ID} - Failed to send start event.")
        return

    sequence_counter = [0]          # lista mutable: sequence_counter[0] es el valor actual
    sequence_lock = asyncio.Lock()  # garantiza acceso exclusivo al incremento

    if "custom" in CALL_ID.lower():
        
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

        # Single shared WebSocket connection (Duplicated for inbound)
        ws = await ws_connection()
        if not ws or ws.state == 3:
            logging.error(f"{CALL_ID} - {customer_information_inbound.get('customerName', 'Unknown')} Cannot connect to WebSocket.")
            return

        # Single connected event
        connect = await send_connected_event(ws)
        if not connect["success"]:
            logging.error(f"{CALL_ID} - Failed to send connected event.")
            return

        # Single start event
        start = await send_start_event(ws, CALL_ID, customer_information_inbound)
        if not start["success"]:
            logging.error(f"{CALL_ID} - Failed to send start event.")
            return

        sequence_counter = [0]          # lista mutable: sequence_counter[0] es el valor actual
        sequence_lock = asyncio.Lock()  # garantiza acceso exclusivo al incremento

        await asyncio.gather(
            stream_audio(ws, audio_in_path,  "inbound",  CALL_ID, sequence_counter, sequence_lock, test_flag),
            stream_audio(ws, audio_out_path, "outbound", CALL_ID, sequence_counter, sequence_lock, test_flag),
        )

        # Single stop event after both streams complete
        await send_stop_event(ws, CALL_ID)
        await ws.close()
        logging.info(f"{CALL_ID} - Conexión WebSocket cerrada correctamente.")
    else:
        dir_in, dir_out = "outbound", "inbound"

    # Stream both directions concurrently over the same socket
    await asyncio.gather(
        stream_audio(ws, audio_in_path,  dir_in,  CALL_ID, sequence_counter, sequence_lock, test_flag),
        stream_audio(ws, audio_out_path, dir_out, CALL_ID, sequence_counter, sequence_lock, test_flag),
        # stream_audio(ws, audio_in_path,  "inbound",  CALL_ID, sequence_counter, sequence_lock, test_flag),
        # stream_audio(ws, audio_out_path, "outbound", CALL_ID, sequence_counter, sequence_lock, test_flag),
    )

    # Single stop event after both streams complete
    await send_stop_event(ws, CALL_ID)
    await ws.close()
    logging.info(f"{CALL_ID} - Conexión WebSocket cerrada correctamente.")


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