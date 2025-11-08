import asyncio
import argparse
import time
import wave
import os

from socket_connection import ws_connection, ws_keepalive
from odio_socket import send_connected_event, send_start_event, send_stop_event, send_media_event
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
INACTIVITY_TIMEOUT = os.getenv('INACTIVITY_TIMEOUT') # segundos sin nuevos bytes
                                 
last_chunk_time = time.time()

# ----------------------------
# Configuración de audio
# ----------------------------
SAMPLE_RATE = int(os.getenv('SAMPLE_RATE')) # Hz
SAMPLE_WIDTH = int(os.getenv('SAMPLE_WIDTH')) # bytes (16-bit PCM)
CHANNELS = int(os.getenv('CHANNELS')) # mono
TEST_OUTPUT_FILE = os.getenv('TEST_OUTPUT_FILE')

CALL_ID = "ABC12345"
customer_information = {
    "clientName": "Clear Nexus",
    "clientExternalId": 50763012,
    "coeName": "New York",
    "coeExternalId": 68715092,
    "agentName": "Tim Johnson",
    "agentExternalId": 83862713,
    "customerName": "John wick",
    "customerPhoneNumber": 9876776321,
    "momentBucketId": 157
}

async def main(audio_file, direction, test = False):
    try_number = 1

    # Espera hasta que el archivo exista
    while not os.path.exists(audio_file):
        logging.info(f"Waiting for audio file: {audio_file} ... try {try_number}")
        try_number += 1
        await asyncio.sleep(1)

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
            with open(audio_file, "rb", buffering=0) as audio_pipe:
                audio_pipe.seek(0, os.SEEK_END)
                logging.info("Iniciando lectura en vivo de %s", audio_file)

                while True:
                    chunk = audio_pipe.read(CHUNK_SIZE)
                    if not chunk:
                        if time.time() - last_chunk_time > INACTIVITY_TIMEOUT:
                            logging.info("Archivo inactivo, se asume fin de grabación.")
                            break
                        await asyncio.sleep(FRAME_DURATION)
                        continue

                    await send_media_event(ws, direction, sequence, chunk)
                    sequence += 1

                    # Ritmo real aproximado de envío
                    await asyncio.sleep(FRAME_DURATION)
        except Exception as e:
            logging.error(f"Error en transmisión: {e}")
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


def init_wave_file(filename):
    """Inicializa un WAV file para escritura."""
    wf = wave.open(filename, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(SAMPLE_WIDTH)
    wf.setframerate(SAMPLE_RATE)
    return wf


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file", help="Ruta del archivo de audio")
    parser.add_argument("direction", help="Dirección de la llamada (inbound/outbound)")
    parser.add_argument("--test", action="store_true", help="Leer archivo mientras se escribe (modo live)")

    args = parser.parse_args()

    try:
        asyncio.run(main(args.audio_file, args.direction, args.test))
    except KeyboardInterrupt:
        print("[INFO] Proxy finalizado por usuario")
    except Exception as e:
        print(f"[ERROR] Proxy interrumpido: {e}")
