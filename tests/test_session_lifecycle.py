import asyncio
import time
import unittest
from stream_gateway.session import CallSession, sessions, cleanup_stale_sessions


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
        self.assertIn(call_uuid, sessions)
        self.assertIs(sessions.get(call_uuid), session)

    def test_cleanup_stale_sessions(self):
        call_uuid_fresh = "fresh-call-1"
        call_uuid_stale = "stale-call-2"

        sess_fresh = CallSession(call_uuid_fresh)
        sess_stale = CallSession(call_uuid_stale)

        # Forzar created_at del stale a hace 2 horas (7200s)
        sess_stale.created_at = time.time() - 7200

        sessions[call_uuid_fresh] = sess_fresh
        sessions[call_uuid_stale] = sess_stale

        # Ejecutar limpieza con TTL de 3600 segundos (1 hora)
        cleaned = cleanup_stale_sessions(max_age_seconds=3600)

        self.assertEqual(cleaned, 1)
        self.assertIn(call_uuid_fresh, sessions)
        self.assertNotIn(call_uuid_stale, sessions)


if __name__ == "__main__":
    unittest.main()
