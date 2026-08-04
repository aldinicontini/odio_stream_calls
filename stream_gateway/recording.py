"""
recording.py — Grabación a disco de audio RX/TX y fusión a estéreo.

Cada llamada contestada genera dos archivos mono (rx/tx) escritos en tiempo
real mientras dura la llamada, y al colgar se combinan en un único WAV
estéreo (L=RX, R=TX) para revisión/QA.
"""

import os
import wave
import array
import time
import asyncio
from datetime import datetime
from utils.app_debugger import init_debugger

RECORDINGS_BASE_DIR = os.getenv("RECORDINGS_BASE_DIR", "recordings")
RECORDING_PUBLIC_BASE_URL = os.getenv("RECORDING_PUBLIC_BASE_URL", "").rstrip("/")
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "8000"))
SAMPLE_WIDTH = int(os.getenv("SAMPLE_WIDTH", "2"))
CHANNELS = int(os.getenv("CHANNELS", "1"))

LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logging = init_debugger(LOG_FILE)

BYTES_PER_SECOND = SAMPLE_RATE * SAMPLE_WIDTH


def build_public_recording_url(local_path: str) -> str | None:
    if not RECORDING_PUBLIC_BASE_URL or not local_path:
        return None
    rel_path = os.path.relpath(local_path, RECORDINGS_BASE_DIR)
    return f"{RECORDING_PUBLIC_BASE_URL}/{rel_path.replace(os.sep, '/')}"


# ---------------------------------------------------------------------------
# Sink de grabación a disco
# ---------------------------------------------------------------------------

class WavFileSink:
    """
    Escribe frames PCM a un .wav mono, manteniendo alineación temporal real.

    Si no llegan paquetes durante un intervalo (p. ej. un canal se queda en
    silencio mientras el otro ya tiene audio — tono de timbrado, hold, etc.),
    se rellena ese hueco con silencio PCM en vez de saltarlo. Esto garantiza
    que rx.wav y tx.wav avancen al mismo ritmo de reloj real y por lo tanto
    queden sincronizados al fusionarlos en estéreo.
    """

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._path = path
        self._wf = wave.open(path, "wb")
        self._wf.setnchannels(CHANNELS)
        self._wf.setsampwidth(SAMPLE_WIDTH)
        self._wf.setframerate(SAMPLE_RATE)
        self._closed = False

        # Reloj de referencia para el padding — monotonic, no datetime,
        # para no verse afectado por ajustes del reloj del sistema.
        self._start_time = time.monotonic()
        self._bytes_written = 0

    def _pad_silence_to_now(self) -> None:
        elapsed = time.monotonic() - self._start_time
        expected_bytes = int(elapsed * BYTES_PER_SECOND)
        # Alinear al tamaño de muestra para no corromper el framing PCM
        expected_bytes -= expected_bytes % SAMPLE_WIDTH

        gap = expected_bytes - self._bytes_written
        if gap > 0:
            self._wf.writeframes(b"\x00" * gap)
            self._bytes_written += gap

    def write(self, payload: bytes) -> None:
        if self._closed or not payload:
            return
        try:
            self._pad_silence_to_now()
            self._wf.writeframes(payload)
            self._bytes_written += len(payload)
        except Exception:
            pass

    def close(self) -> None:
        if not self._closed:
            try:
                # Rellenar hasta el instante del cierre, para que ambos
                # archivos (rx/tx) terminen con la misma duración exacta.
                self._pad_silence_to_now()
                self._wf.close()
            except Exception:
                pass
            self._closed = True

    @property
    def path(self) -> str:
        return self._path


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

def build_recording_paths(call_uuid: str, segment: int) -> tuple[str, str, str]:
    """Devuelve (rx_path, tx_path, all_path) para un segmento específico
    de la llamada, evitando sobrescritura en transferencias."""
    now = datetime.now()
    day_dir = os.path.join(
        RECORDINGS_BASE_DIR, f"{now.year:04d}", f"{now.month:02d}", f"{now.day:02d}"
    )
    suffix = f"-{segment:02d}"
    rx_path = os.path.join(day_dir, f"{call_uuid}{suffix}-rx.wav")
    tx_path = os.path.join(day_dir, f"{call_uuid}{suffix}-tx.wav")
    all_path = os.path.join(day_dir, f"{call_uuid}{suffix}-all.wav")
    return rx_path, tx_path, all_path


# ---------------------------------------------------------------------------
# Fusión a estéreo (bloqueante — se ejecuta en un thread aparte)
# ---------------------------------------------------------------------------

def _merge_stereo_sync(rx_path: str, tx_path: str, all_path: str) -> None:
    """Combina rx/tx mono en un wav estéreo, bloqueante (para ejecutarse en un thread aparte)."""
    logging.info(f"Starting merge of {rx_path} + {tx_path} → {all_path} in recordings thread")
    with wave.open(rx_path, "rb") as rx_wf:
        rx_raw = rx_wf.readframes(rx_wf.getnframes())
    with wave.open(tx_path, "rb") as tx_wf:
        tx_raw = tx_wf.readframes(tx_wf.getnframes())

    os.makedirs(os.path.dirname(all_path), exist_ok=True)

    if SAMPLE_WIDTH == 2:
        # Camino rápido: interleave vectorizado con array('h', ...)
        rx = array.array("h")
        rx.frombytes(rx_raw)
        tx = array.array("h")
        tx.frombytes(tx_raw)

        n = max(len(rx), len(tx))
        rx.extend([0] * (n - len(rx)))  # rellena con silencio si un canal es más corto
        tx.extend([0] * (n - len(tx)))

        stereo = array.array("h", (0,)) * (n * 2)
        stereo[0::2] = rx  # canal izquierdo = RX (cliente)
        stereo[1::2] = tx  # canal derecho  = TX (agente)
        stereo_bytes = stereo.tobytes()
    else:
        # Fallback genérico byte a byte para otros anchos de muestra
        w = SAMPLE_WIDTH
        n = max(len(rx_raw), len(tx_raw))
        rx_raw += b"\x00" * (n - len(rx_raw))
        tx_raw += b"\x00" * (n - len(tx_raw))
        buf = bytearray(n * 2)
        for i in range(0, n, w):
            buf[i * 2 : i * 2 + w] = rx_raw[i : i + w]
            buf[i * 2 + w : i * 2 + 2 * w] = tx_raw[i : i + w]
        stereo_bytes = bytes(buf)

    logging.info(f"Writing merged stereo to {all_path}, {len(stereo_bytes)} bytes")
    with wave.open(all_path, "wb") as out_wf:
        out_wf.setnchannels(2)
        out_wf.setsampwidth(SAMPLE_WIDTH)
        out_wf.setframerate(SAMPLE_RATE)
        out_wf.writeframes(stereo_bytes)


async def merge_stereo(rx_path: str, tx_path: str, all_path: str) -> None:
    """Combina rx/tx mono en un wav estéreo, sin bloquear el event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _merge_stereo_sync, rx_path, tx_path, all_path)