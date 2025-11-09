import asyncio
import base64
import time
import os
import json

#loging
from app_debuger import init_debugger
LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS')
logging = init_debugger(LOG_FILE_CONNECTIONS)
# end loging

async def send_connected_event(ws):
    connected_event = {
        "event": "connected",
        "protocol": "Call",
        "version": "1.0.0"
    }
    encoded = json.dumps(connected_event)

    try:
        await ws.send(encoded)
        logging.info(f"connected_event send: {encoded}")

        try:
            return await wait_fot_ack(ws)
        except asyncio.TimeoutError:
            logging.warning("Timeout waiting for response to connected_event")
            return None
    except Exception as e:
        logging.error(f"Error while trying to send connected_event: {encoded} {e}")

async def send_start_event(ws, CALL_ID, custom_parameters):
    start_event = {
        "event": "Start",
        "streamSid": CALL_ID,
        "start": {
            "customParameters": custom_parameters
        }
    }
    encoded = json.dumps(start_event)

    try:
        await ws.send(encoded)
        logging.info(f"Start Event send: {encoded}")

        try:
            return await wait_fot_ack(ws)
        except asyncio.TimeoutError:
            logging.warning("Timeout waiting for response to connected_event")
            return None
    except Exception as e:
        logging.error(f"Error while trying to send start event: {encoded} {e}")

async def send_media_event(ws, CALL_ID, DIRECTION, sequence, time_elapsed, chunk):
    media_event = {
        "event": "Media",
        "streamSid": CALL_ID,
        "sequenceNumber": sequence,
        "media": {
            "track": DIRECTION,
            "payload": base64.b64encode(chunk).decode("utf-8"), 
            "timestamp": time_elapsed,
        }
    }
    encoded = json.dumps(media_event)

    try:
        await ws.send(encoded)
        logging.info(f"Media Event send: {encoded}")
    except Exception as e:
        logging.error(f"Error while trying to send media event: {encoded}")

async def send_stop_event(ws, CALL_ID):
    stop_event = {
        "event": "Stop",
        "streamSid": CALL_ID
    }
    encoded = json.dumps(stop_event)

    try:
        await ws.send(encoded)
        logging.info(f"Stop Event send: {encoded}")
    except Exception as e:
        logging.error(f"Error while trying to send stop event: {encoded}")


async def wait_fot_ack(ws):
    """
    Envía pings periódicos para mantener viva la conexión.
    """
    try:
        ack = await asyncio.wait_for(ws.recv(), timeout=5)
        logging.info(f"ACK recibido: {ack}")

        return json.loads(ack)
    except asyncio.TimeoutError:
        logging.error(f"No se recibió ACK")
        return None