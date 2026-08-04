"""
session.py — Estado compartido de llamadas activas.

Toda la información relacionada con una llamada vive dentro de una sola
CallSession. El diccionario `sessions` es el registro global compartido
por el servidor de audio (puerto 9019) y el servidor de control (puerto 9020),
que corren en el mismo event loop de asyncio.
"""

import asyncio
import os
from dotenv import load_dotenv
from stream_gateway.recording import WavFileSink, build_recording_paths, merge_stereo
from utils.app_debugger import init_debugger


LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logging = init_debugger(LOG_FILE)

load_dotenv()

AUDIO_QUEUE_MAXSIZE = int(os.getenv("AUDIO_QUEUE_MAXSIZE", "500"))


# ---------------------------------------------------------------------------
# Audio Sinks
# ---------------------------------------------------------------------------

class NullSink:
    """
    Descarta todos los frames.

    Se usa mientras la llamada no ha sido contestada por un agente.
    No escribe en disco, no acumula memoria, no crea buffers.
    Costo prácticamente cero.
    """

    def write(self, payload: bytes) -> None:
        pass


class SocketSink:
    """
    Entrega los frames a una asyncio.Queue para consumo en tiempo real.

    Se activa dinámicamente cuando un agente contesta la llamada.
    Si la cola está llena (backpressure), el frame se descarta silenciosamente
    en lugar de bloquear el event loop o consumir memoria descontrolada.
    """

    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue = queue

    def write(self, payload: bytes) -> None:
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# CallSession
# ---------------------------------------------------------------------------

class CallSession:
    """
    Estado completo de una llamada activa.
    """

    def __init__(self, call_uuid: str) -> None:
        self.call_uuid: str = call_uuid
        self.state: str = "ringing"

        # Queues de audio — activas solo en estado "answered"
        self.rx_queue: asyncio.Queue = asyncio.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)
        self.tx_queue: asyncio.Queue = asyncio.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)

        # Destinos de audio — NullSink por defecto hasta que llegue ANSWER
        self.rx_sink: NullSink | SocketSink = NullSink()
        self.tx_sink: NullSink | SocketSink = NullSink()

        # Secuencia compartida (mismo patrón que stream_socket.py)
        self.sequence_counter: list = [0]
        self.sequence_lock: asyncio.Lock = asyncio.Lock()

        # Señal de ANSWER
        self.answered_event: asyncio.Event = asyncio.Event()

        # Información de la llamada (llega en el mensaje ANSWER del agente)
        self.customer_information: dict = {}
        self.agent_id: str = ""

        # Metadata de hangup
        self.hangup_who: str | None = None
        self.hangup_cause: int | None = None

        # Task de streaming activo
        self.stream_task: asyncio.Task | None = None

        # Grabación a disco — por segmentos (uno por cada tramo Answer→Pausa)
        self.recording_segment: int = 0
        self.rx_recorder: WavFileSink | None = None
        self.tx_recorder: WavFileSink | None = None
        self.current_segment_paths: tuple | None = None  # (rx, tx, all) del segmento abiert

    def activate_streaming(self, agent_id: str, customer_information: dict) -> None:
        """
        Activa el envío de audio a SocketSink y prepara las colas para consumo.
        Permite reanudar la sesión si fue pausada anteriormente.
        """
        self.customer_information = customer_information
        self.agent_id = agent_id
        self.state = "answered"
        self.answered_event.set()

        # Re-crear colas limpias por si se pausó/reanudó anteriormente
        self.rx_queue = asyncio.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)
        self.tx_queue = asyncio.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)

        self.rx_sink = SocketSink(self.rx_queue)
        self.tx_sink = SocketSink(self.tx_queue)

        self._start_recording_segment()

    def deactivate_streaming(self) -> tuple | None:
        """
        Desactiva el streaming: redirige el audio a NullSink(), detiene los
        consumidores activos enviando un centinela None a las colas y cambia
        el estado a "paused". Mantiene la conexión TCP viva y descartando paquetes.
        """
        self.rx_sink = NullSink()
        self.tx_sink = NullSink()
        self.state = "paused"

        for queue in (self.rx_queue, self.tx_queue):
            try:
                queue.put_nowait(None)
            except (asyncio.QueueFull, AttributeError):
                pass

        if self.stream_task and not self.stream_task.done():
            self.stream_task.cancel()
            self.stream_task = None

        return self._close_recording_segment()

    def signal_hangup(self) -> tuple | None:
        """
        Señala el fin definitivo de la llamada (ejecutado en el bloque finally
        cuando la conexión TCP física de AudioSocket se desconecta).
        """
        finished_segment = self.deactivate_streaming()
        self.state = "finished"
        return finished_segment

    def _on_segment_merge_done(self, task: asyncio.Task, call_uuid: str, all_path: str) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logging.error(f"{call_uuid} - Error al fusionar {all_path}: {exc}")
        else:
            logging.info(f"{call_uuid} - Segmento fusionado correctamente: {all_path}")

    def _start_recording_segment(self) -> None:
        self.recording_segment += 1
        rx_path, tx_path, all_path = build_recording_paths(
            self.call_uuid, self.recording_segment
        )
        self.rx_recorder = WavFileSink(rx_path)
        self.tx_recorder = WavFileSink(tx_path)
        self.current_segment_paths = (rx_path, tx_path, all_path)

    def _close_recording_segment(self) -> tuple | None:
        """
        Cierra los .wav mono del segmento activo y devuelve sus rutas para
        que el llamador dispare el merge estéreo. Devuelve None si no había
        ningún segmento abierto (p. ej. llamada nunca contestada, o ya en pausa).
        """
        if self.rx_recorder is None and self.tx_recorder is None:
            return None

        if self.rx_recorder:
            self.rx_recorder.close()
            self.rx_recorder = None
        if self.tx_recorder:
            self.tx_recorder.close()
            self.tx_recorder = None
        
        paths = self.current_segment_paths
        self.current_segment_paths = None

        # Disparar el merge aquí mismo — sin importar qué servidor
        # (control o audio) fue quien cerró el segmento.
        if paths:
            rx_path, tx_path, all_path = paths
            task = asyncio.create_task(merge_stereo(rx_path, tx_path, all_path))
            task.add_done_callback(
                lambda t, uuid=self.call_uuid, ap=all_path: self._on_segment_merge_done(t, uuid, ap)
            )

        return paths


# ---------------------------------------------------------------------------
# Registro global de sesiones
# ---------------------------------------------------------------------------

sessions: dict[str, CallSession] = {}
