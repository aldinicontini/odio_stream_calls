import os
import json
import wave
import struct
import asyncio
import logging


HOST = "0.0.0.0"
PORT = 9019

DEBUG = bool(int(os.getenv("DEBUG", "0")))
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "8000"))
SAMPLE_WIDTH = int(os.getenv("SAMPLE_WIDTH", "2"))

LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
OUTPUT_DIR = "./recordings"

os.makedirs(OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# Tipos de paquete de app_streamsocket (framing estilo AudioSocket:
# 1 byte tipo + 2 bytes longitud big-endian + payload)
PKT_TYPE_HANGUP = 0x00
PKT_TYPE_UUID = 0x01
PKT_TYPE_AUDIO_RX = 0x10  # audio entrante al canal (READ)
PKT_TYPE_AUDIO_TX = 0x11  # audio saliente del canal (WRITE)


async def read_packet(reader):
    header = await reader.readexactly(3)
    ptype = header[0]
    length = struct.unpack("!H", header[1:3])[0]
    payload = await reader.readexactly(length) if length else b""
    return ptype, payload


async def handle_client(reader, writer):
    peer = writer.get_extra_info("peername")
    call_uuid = None
    wav_rx = None
    wav_tx = None
    bytes_rx = 0
    bytes_tx = 0
    hangup_who = None      # "TX" | "RX" | None (desconocido)
    hangup_cause = None

    logging.info(f"New StreamSocket connection from {peer}")

    def open_wavs(uuid_str):
        rx_path = os.path.join(OUTPUT_DIR, f"{uuid_str}_rx.wav")
        tx_path = os.path.join(OUTPUT_DIR, f"{uuid_str}_tx.wav")

        w_rx = wave.open(rx_path, "wb")
        w_rx.setnchannels(1)
        w_rx.setsampwidth(SAMPLE_WIDTH)
        w_rx.setframerate(SAMPLE_RATE)

        w_tx = wave.open(tx_path, "wb")
        w_tx.setnchannels(1)
        w_tx.setsampwidth(SAMPLE_WIDTH)
        w_tx.setframerate(SAMPLE_RATE)

        return w_rx, w_tx

    try:
        while True:
            ptype, payload = await read_packet(reader)

            if ptype == PKT_TYPE_UUID:
                call_uuid = payload.decode(errors="replace")
                wav_rx, wav_tx = open_wavs(call_uuid)
                logging.info(f"Call UUID: {call_uuid}")

            elif ptype == PKT_TYPE_AUDIO_RX:
                if wav_rx is not None:
                    wav_rx.writeframes(payload)
                    bytes_rx += len(payload)

            elif ptype == PKT_TYPE_AUDIO_TX:
                if wav_tx is not None:
                    wav_tx.writeframes(payload)
                    bytes_tx += len(payload)

            elif ptype == PKT_TYPE_HANGUP:
                who_label = "desconocido"
                if len(payload) >= 2:
                    who, cause = payload[0], payload[1]
                    who_label = {0: "desconocido", 1: "TX", 2: "RX"}.get(who, "desconocido")
                    hangup_who = who_label
                    hangup_cause = cause
                logging.info(f"Hangup received for {call_uuid} - colgo: {who_label} - cause={hangup_cause}")
                break

            else:
                logging.warning(f"Tipo de paquete desconocido: {ptype}")

    except asyncio.IncompleteReadError:
        logging.info(f"Connection closed by peer {peer}")

    except Exception as e:
        logging.exception(f"Error handling connection: {e}")

    finally:
        if wav_rx is not None:
            wav_rx.close()
        if wav_tx is not None:
            wav_tx.close()

        writer.close()
        await writer.wait_closed()

        if call_uuid:
            meta = {
                "uuid": call_uuid,
                "bytes_rx": bytes_rx,
                "bytes_tx": bytes_tx,
                "hangup_who": hangup_who,       # "TX", "RX" o None
                "hangup_cause": hangup_cause,   # codigo Q.850 o None
            }
            meta_path = os.path.join(OUTPUT_DIR, f"{call_uuid}.json")
            try:
                with open(meta_path, "w") as f:
                    json.dump(meta, f)
            except Exception:
                logging.exception(f"No se pudo escribir metadata para {call_uuid}")

        logging.info(
            f"Finished call {call_uuid} - rx={bytes_rx}B tx={bytes_tx}B - hangup_who={hangup_who} cause={hangup_cause}"
        )


async def main():
    server = await asyncio.start_server(handle_client, HOST, PORT)
    addr = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    logging.info(f"StreamSocket server listening on {addr}")
    print(f"StreamSocket listening on {addr}")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())