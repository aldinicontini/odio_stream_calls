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

    def write(self, payload: bytes) -> None:  # noqa: D401
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
            pass  # frame descartado por backpressure — la cola está llena


# ---------------------------------------------------------------------------
# CallSession
# ---------------------------------------------------------------------------

class CallSession:
    """
    Estado completo de una llamada activa.

    Atributos
    ---------
    call_uuid : str
        UUID único asignado por Asterisk.
    state : str
        "ringing" → "answered" → "hangup"
    rx_sink / tx_sink : NullSink | SocketSink
        Destino actual del audio. Se cambia dinámicamente en ANSWER.
        RX = audio entrante al canal (READ)  → track "outbound" en odio.
        TX = audio saliente del canal (WRITE) → track "inbound" en odio.
    rx_queue / tx_queue : asyncio.Queue
        Buffers de audio en memoria. Se usan solo cuando state == "answered".
    sequence_counter / sequence_lock
        Contador de secuencia compartido entre ambas direcciones,
        con la misma convención que stream_socket.py.
    answered_event : asyncio.Event
        Se dispara cuando el agente contesta. No se usa actualmente
        para bloquear (run_both_live se lanza desde control_server),
        pero queda disponible para futuras extensiones.
    customer_information : dict
        Información del cliente, recibida en el mensaje ANSWER.
    agent_id : str
        ID del agente que contestó la llamada.
    hangup_who / hangup_cause
        Metadata del hangup recibida desde el parser.
    stream_task : asyncio.Task | None
        Task de run_both_live(). Se cancela si llega HANGUP antes de que
        el streaming termine por sí solo.
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

    def signal_hangup(self) -> None:
        """
        Señala el fin de la llamada hacia los consumidores activos de las queues.

        Pone un centinela None en ambas queues para que stream_audio_live()
        pueda salir limpiamente. Si el task de streaming sigue activo,
        también lo cancela.
        """
        self.state = "hangup"

        # Centinela: None indica fin de stream
        for queue in (self.rx_queue, self.tx_queue):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass  # si la cola está llena, la cancelación del task se encarga

        if self.stream_task and not self.stream_task.done():
            self.stream_task.cancel()


# ---------------------------------------------------------------------------
# Registro global de sesiones
# ---------------------------------------------------------------------------

# Compartido por audio_socket_server.py y control_server.py.
# Ambos corren en el mismo event loop → no se necesita ningún lock adicional
# para el acceso al dict (asyncio es single-threaded por diseño).
sessions: dict[str, CallSession] = {}
