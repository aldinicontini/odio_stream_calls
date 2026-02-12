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

WSS_ODIO_URL = os.getenv('WSS_ODIO_URL')
SSL_CERT_PATH = os.getenv('SSL_CERT_PATH') or None
CHUNK_SIZE = int(os.getenv('CHUNK_SIZE'))  # 20ms de audio PCM 16-bit a 8kHz
INACTIVITY_TIMEOUT = int(os.getenv('INACTIVITY_TIMEOUT')) # segundos sin nuevos bytes
                                 
last_chunk_time = time.time()

# ----------------------------
# Configuración de audio
# ----------------------------
SAMPLE_RATE = int(os.getenv('SAMPLE_RATE')) # Hz
SAMPLE_WIDTH = int(os.getenv('SAMPLE_WIDTH')) # bytes (16-bit PCM)
CHANNELS = int(os.getenv('CHANNELS')) # mono
TEST_OUTPUT_FILE = os.getenv('TEST_OUTPUT_FILE')

async def main(audio_file, direction, test = False):
    CALL_ID = audio_file.replace("-in.wav", "").replace("-out.wav", "")
    customer_information = get_customer_information(audio_file)

    if not customer_information:
        logging.error(f"{CALL_ID} - Customer information not found for customer: {audio_file}")
        return

    audio_file = getRecordingPath(customer_information, audio_file)
    
    try_number = 1
    while not os.path.exists(audio_file):
        if try_number > 20:
            logging.warning(f"{CALL_ID} - Timeout alcanzado ({TIMEOUT}s), archivo no encontrado: {audio_file}")
        break

        logging.info(f"{CALL_ID} - Waiting for audio file: {audio_file} ... try {try_number}")
        try_number += 1
        await asyncio.sleep(1)

    filename = os.path.basename(audio_file)
    

    # Conectar al WebSocket
    ws = await ws_connection()
    # keepalive_task = asyncio.create_task(ws_keepalive(ws))

    if not ws or ws.state == 3:
        logging.error("Cannot connect to WebSocket.")
        return
    
    connect = await send_connected_event(ws)
    if connect["success"] != True:
        return

    # Enviar evento Start
    start = await send_start_event(ws, CALL_ID, customer_information)
    if start["success"] != True:
        return

    sequence = 0
    time_elapsed = 0.0

    if not test:
        try:
            last_chunk_time = time.time()
            # wf = init_wave_file(TEST_OUTPUT_FILE)
            with open(audio_file, "rb", buffering=0) as audio_pipe:
                audio_pipe.seek(0, os.SEEK_END)
                logging.info(f"{CALL_ID} - Iniciando lectura en vivo de {audio_file}")

                while True:
                    chunk = audio_pipe.read(CHUNK_SIZE)
                    if not chunk:
                        # logging.info("Archivo inactivo.")
                        if time.time() - last_chunk_time > INACTIVITY_TIMEOUT:
                            logging.info(f"{CALL_ID} -Archivo inactivo por 5 segundos, se asume fin de grabación.")
                            break
                        await asyncio.sleep(FRAME_DURATION)
                        continue

                    await send_media_event(ws, CALL_ID, direction, sequence, round(time_elapsed, 3), chunk)
                    # Escribe localmente para validar
                    # wf.writeframes(chunk)
                    sequence += 1

                    time_elapsed += FRAME_DURATION
                    last_chunk_time = time.time()

                    # Ritmo real aproximado de envío
                    await asyncio.sleep(FRAME_DURATION)
        except Exception as e:
            logging.error(f"{CALL_ID} - Error en transmisión: {e}")
    else:
        try:
            # wf = init_wave_file(TEST_OUTPUT_FILE)
            with open(audio_file, "rb", buffering=0) as audio_pipe:
                logging.info("Iniciando lectura en vivo de %s", audio_file)

                while True:
                    chunk = audio_pipe.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    await send_media_event(ws, CALL_ID, direction, sequence, round(time_elapsed, 3), chunk)

                    # Escribe localmente para validar
                    # wf.writeframes(chunk)
                    sequence += 1

                    time_elapsed += FRAME_DURATION

                    # Ritmo real aproximado de envío
                    await asyncio.sleep(FRAME_DURATION)
        except Exception as e:
            logging.error(f"Error en transmisión: {e}")


    # Enviar evento Stop al terminar
    await send_stop_event(ws, CALL_ID)
    
    # keepalive_task.cancel()
    await ws.close()
    logging.info("Conexión WebSocket cerrada correctamente.")

def getRecordingPath(customer_information, audio_file):
    event_date = customer_information.get("event_date")
    if not event_date:
        logging.error(f"Error getting the date")
        return

    try:
        dt = datetime.strptime(event_date, "%Y-%m-%d")
        monitor_dir = f"/var/spool/asterisk/monitor/{dt.year}/{dt.month:02d}/{dt.day:02d}"
    except ValueError:
        logging.error(f"invalid date formatt {event_date}")
        return
    
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

async def run_both(audio_file, test_flag):
    # Crea las rutas completas
    audio_in = f"{audio_file}-in.wav"
    audio_out = f"{audio_file}-out.wav"

    # print(f"{audio_in}, {audio_out}")
    await asyncio.gather(
        main(audio_in, "inbound", test_flag),
        main(audio_out, "outbound", test_flag)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file", help="Ruta base del archivo de audio (sin -in/out)")
    parser.add_argument("--test", action="store_true", help="Modo live")

    args = parser.parse_args()

    try:
        asyncio.run(run_both(args.audio_file, args.test))
    except KeyboardInterrupt:
        print("[INFO] Finalizado por usuario")
    except Exception as e:
        print(f"[ERROR] Interrumpido: {e}")
