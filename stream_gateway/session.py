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

    def deactivate_streaming(self) -> None:
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

    def signal_hangup(self) -> None:
        """
        Señala el fin definitivo de la llamada (ejecutado en el bloque finally
        cuando la conexión TCP física de AudioSocket se desconecta).
        """
        self.deactivate_streaming()
        self.state = "finished"


# ---------------------------------------------------------------------------
# Registro global de sesiones
# ---------------------------------------------------------------------------

sessions: dict[str, CallSession] = {}
