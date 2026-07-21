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

    fields_to_remove = [
        "customerPhoneNumber",
        "lead_id",
        "caller_code",
        "recording_name",
        "uniqueid2",
        "event_date"
    ]
    for field in fields_to_remove:
        custom_parameters.pop(field, None)


    start_event = {
        "event": "Start",
        "streamSid": CALL_ID,
        "start": {
            "customParameters": custom_parameters
        }
    }
    encoded = json.dumps(start_event)
    logging.info(f"Start Event send: {encoded}")

    try:
        await ws.send(encoded)

        try:
            return await wait_fot_ack(ws)
        except asyncio.TimeoutError:
            logging.warning("Timeout waiting for response to connected_event")
            return None
    except Exception as e:
        logging.error(f"Error while trying to send start event: {encoded} {e}")

async def send_media_event(ws, CALL_ID, DIRECTION, sequence, time_elapsed, chunk):
    # for every ejecution of the function, we will have two variables that will be incremented: 
    # - sequenceNumber: its an incremental variable for the hole process, it includes both directions (inbound and outbound) and starts at 1 and increments by 1 for each chunk sent, regardless of the direction.
    # 
    # Chunk:
    # it's an incremental variable for each DIRECTION (inbound and outbound) that starts at 1 and increments by 1 for each chunk sent.
    # 
    # For example, if we have the following sequence of chunks sent:
    # Outbound - chunk 1, chunk 2, chunk 3, ...
    # Inbound - chunk 1, chunk 2, chunk 3, ...
    #
    # We will have the following values for sequenceNumber and chunk: 
    #   Sequence Number: 1, Chunk: 1 (Outbound)
    #   Sequence Number: 2, Chunk: 1 (Inbound)
    #   Sequence Number: 3, Chunk: 2 (Outbound)
    #   Sequence Number: 4, Chunk: 2 (Inbound)


    media_event = {
        "event": "Media",
        "streamSid": CALL_ID,
        "sequenceNumber": sequence,
        "media": {
            "track": DIRECTION,
            "chunk": sequence, 
            "payload": base64.b64encode(chunk).decode("utf-8"), 
            "timestamp": time_elapsed,
        }
    }
    encoded = json.dumps(media_event)

    try:
        await ws.send(encoded)
        # logging.debug(f"Media Event send: {encoded}")
    except Exception as e:
        logging.error(f"Error while trying to send media event: {e}")

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
        logging.error(f"Error while trying to send stop event: {e}")


async def wait_fot_ack(ws):
    """
    Envía pings periódicos para mantener viva la conexión.
    """
    try:
        ack = await asyncio.wait_for(ws.recv(), timeout=5)
        logging.info(f"ACK received: {ack}")

        return json.loads(ack)
    except asyncio.TimeoutError:
        logging.error(f"ACK not received within timeout")
        return None