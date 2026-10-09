import asyncio
import time
import unittest

from websockets.exceptions import ConnectionClosedError

from stream_gateway.session import CallSession, SocketSink, AUDIO_QUEUE_MAXSIZE
from stream_gateway.stream_socket import stream_audio_live


class StalledOdio:
    """Odio que acepta la conexión pero nunca procesa lo que se le envía."""

    async def send(self, data):
        await asyncio.Event().wait()


class ClosedOdio:
    def __init__(self):
        self.sends = 0

    async def send(self, data):
        self.sends += 1
        raise ConnectionClosedError(None, None)


class TestOdioIsolation(unittest.TestCase):
    def test_stalled_odio_never_blocks_asterisk_side(self):
        async def run_test():
            session = CallSession("stalled-odio")
            session.rx_sink = SocketSink(session.rx_queue)
            consumer = asyncio.create_task(stream_audio_live(
                StalledOdio(), session.rx_queue, "inbound", "stalled-odio",
                session.sequence_counter, session.sequence_lock,
            ))
            await asyncio.sleep(0)

            # Lo que hace handle_client por cada frame de Asterisk: nunca espera
            start = time.monotonic()
            for _ in range(5000):
                session.rx_sink.write(b"\x00" * 320)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertLessEqual(session.rx_queue.qsize(), AUDIO_QUEUE_MAXSIZE)

            # Al colgar, la tarea de Odio se cancela aunque siga atorada
            session.stream_task = consumer
            session.deactivate_streaming()
            await asyncio.sleep(0)
            self.assertTrue(consumer.done())

        asyncio.run(run_test())

    def test_closed_odio_ends_stream_instead_of_looping(self):
        async def run_test():
            queue = asyncio.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)
            for _ in range(10):
                queue.put_nowait(b"\x00" * 320)
            odio = ClosedOdio()

            await asyncio.wait_for(stream_audio_live(
                odio, queue, "inbound", "closed-odio", [0], asyncio.Lock(),
            ), timeout=1)

            self.assertEqual(odio.sends, 1)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
