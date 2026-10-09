import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import stream_gateway.recording as recording
from stream_gateway.session import (
    CallSession, SocketSink, sessions, cleanup_stale_sessions, SESSION_MAX_AGE_SECONDS,
)


class TestSessionLifecycle(unittest.TestCase):
    def setUp(self):
        sessions.clear()

    def tearDown(self):
        sessions.clear()

    def test_session_retention_on_hangup(self):
        call_uuid = "test-call-12345"
        session = CallSession(call_uuid)
        sessions[call_uuid] = session

        # Simular fin de llamada / disconnect en AudioSocket
        session.signal_hangup()

        # Verificar que el estado cambió a finished pero la sesión SIGUE en sessions
        self.assertEqual(session.state, "finished")
        self.assertIsNotNone(session.finished_at)
        self.assertIn(call_uuid, sessions)
        self.assertIs(sessions.get(call_uuid), session)

    def test_cleanup_stale_sessions(self):
        call_uuid_fresh = "fresh-call-1"
        call_uuid_stale = "stale-call-2"

        sess_fresh = CallSession(call_uuid_fresh)
        sess_stale = CallSession(call_uuid_stale)

        # Ambas terminaron; la stale hace 2 horas (7200s)
        sess_fresh.signal_hangup()
        sess_stale.signal_hangup()
        sess_stale.finished_at = time.time() - 7200

        sessions[call_uuid_fresh] = sess_fresh
        sessions[call_uuid_stale] = sess_stale

        # Ejecutar limpieza con TTL de 3600 segundos (1 hora)
        cleaned = cleanup_stale_sessions(max_age_seconds=3600)

        self.assertEqual(cleaned, 1)
        self.assertIn(call_uuid_fresh, sessions)
        self.assertNotIn(call_uuid_stale, sessions)

    def test_cleanup_keeps_long_live_call(self):
        # Una llamada en curso de más de 1h no debe purgarse por el TTL
        session = CallSession("long-call")
        session.created_at = time.time() - 7200
        sessions["long-call"] = session

        self.assertEqual(cleanup_stale_sessions(max_age_seconds=3600), 0)
        self.assertIn("long-call", sessions)

        session.created_at = time.time() - SESSION_MAX_AGE_SECONDS - 1
        self.assertEqual(cleanup_stale_sessions(max_age_seconds=3600), 1)

    def test_cleanup_deletes_unsent_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [os.path.join(tmp, f"call-01-{k}.wav") for k in ("rx", "tx", "all")]
            for p in paths:
                open(p, "wb").close()

            session = CallSession("unsent-call")
            session.recording_files = list(paths)
            session.signal_hangup()
            session.finished_at = time.time() - 7200
            sessions["unsent-call"] = session

            cleanup_stale_sessions(max_age_seconds=3600)

            self.assertEqual([p for p in paths if os.path.exists(p)], [])

    def test_hangup_releases_queued_audio(self):
        async def run_test():
            session = CallSession("queue-call")
            session.rx_sink = SocketSink(session.rx_queue)
            for _ in range(session.rx_queue.maxsize):
                session.rx_sink.write(b"\x00" * 320)
            self.assertTrue(session.rx_queue.full())

            session.deactivate_streaming()

            # Solo queda el centinela: los frames sin consumir se liberaron
            self.assertEqual(session.rx_queue.qsize(), 1)
            self.assertIsNone(session.rx_queue.get_nowait())

        asyncio.run(run_test())


class TestDiscardRecording(unittest.TestCase):
    def tearDown(self):
        sessions.clear()

    def test_discard_deletes_files_and_session(self):
        from unittest.mock import AsyncMock
        from ws_agent_gateway.control_server import _handle_discard_recording

        async def run_test():
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "voicemail-01-all.wav")
                open(path, "wb").close()
                session = CallSession("voicemail")
                session.recording_files = [path]
                session.signal_hangup()
                sessions["voicemail"] = session

                ws = AsyncMock()
                await _handle_discard_recording(ws, {"callid": "voicemail"}, ("127.0.0.1", 1))

                self.assertFalse(os.path.exists(path))
                self.assertNotIn("voicemail", sessions)
                self.assertIn('"type": "ACK"', ws.send.call_args[0][0])

        asyncio.run(run_test())


class TestRecordingSweep(unittest.TestCase):
    def test_sweep_removes_only_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            day_dir = os.path.join(tmp, "2026", "10", "02")
            empty_old_dir = os.path.join(tmp, "2026", "10", "01")
            os.makedirs(day_dir)
            os.makedirs(empty_old_dir)
            old_file = os.path.join(day_dir, "old-all.wav")
            new_file = os.path.join(tmp, "new-all.wav")
            open(old_file, "wb").close()
            open(new_file, "wb").close()
            past = time.time() - 7200
            for path in (old_file, empty_old_dir):
                os.utime(path, (past, past))

            with patch.object(recording, "RECORDINGS_BASE_DIR", tmp):
                removed = recording._sweep_old_recordings_sync(3600)

            self.assertEqual(removed, 1)
            self.assertFalse(os.path.exists(old_file))
            self.assertTrue(os.path.exists(new_file))
            self.assertFalse(os.path.exists(empty_old_dir))
            self.assertTrue(os.path.exists(day_dir))


if __name__ == "__main__":
    unittest.main()
