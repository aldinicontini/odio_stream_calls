import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import utils.user_flters as user_filters
from utils.user_flters import _is_agent_allowed
from stream_gateway.session import CallSession, sessions
from ws_agent_gateway.control_server import _handle_answer


class TestAgentFilter(unittest.TestCase):
    def setUp(self):
        sessions.clear()

    def tearDown(self):
        sessions.clear()

    def test_is_agent_allowed_with_whitelist(self):
        with patch.object(user_filters, 'AGENT_ALLOWED', {'joshua.molino', 'aldo.medina'}):
            self.assertTrue(_is_agent_allowed("aldo.medina"))
            self.assertTrue(_is_agent_allowed("Joshua.Molino "))
            self.assertFalse(_is_agent_allowed("unallowed.agent"))
            self.assertFalse(_is_agent_allowed(""))
            self.assertFalse(_is_agent_allowed(None))

    def test_is_agent_allowed_without_whitelist(self):
        with patch.object(user_filters, 'AGENT_ALLOWED', set()):
            self.assertTrue(_is_agent_allowed("any.agent"))
            self.assertTrue(_is_agent_allowed("another.agent"))

    def test_handle_answer_recording_active_even_if_not_allowed(self):
        async def run_test():
            callid = "test-call-filter-1"
            session = CallSession(callid)
            sessions[callid] = session

            ws = AsyncMock()

            msg = {
                "type": "ANSWER",
                "callid": callid,
                "agent": "disallowed.agent",
                "customer_information": {
                    "coeName": "TestCOE",
                    "agentName": "Disallowed Agent",
                    "agentId": "disallowed.agent",
                    "customerName": "John Doe",
                    "uuid": "12345"
                }
            }

            with patch.object(user_filters, 'AGENT_ALLOWED', {'allowed.agent'}), \
                 patch('stream_gateway.recording.merge_stereo', new_callable=AsyncMock):

                await _handle_answer(ws, msg, ("127.0.0.1", 12345))

                # Session should be marked as answered and recording sinks created
                self.assertEqual(session.state, "answered")
                self.assertIsNotNone(session.rx_recorder)
                self.assertIsNotNone(session.tx_recorder)
                # Stream task should NOT be created for disallowed agent
                self.assertIsNone(session.stream_task)
                # ACK should still be sent to the control WS client
                ws.send.assert_called_once()
                sent_msg = ws.send.call_args[0][0]
                self.assertIn('"type": "ACK"', sent_msg)
                self.assertIn(callid, sent_msg)

        asyncio.run(run_test())

    def test_handle_answer_launches_stream_for_allowed_agent(self):
        async def run_test():
            callid = "test-call-filter-2"
            session = CallSession(callid)
            sessions[callid] = session

            ws = AsyncMock()

            msg = {
                "type": "ANSWER",
                "callid": callid,
                "agent": "allowed.agent",
                "customer_information": {
                    "coeName": "TestCOE",
                    "agentName": "Allowed Agent",
                    "agentId": "allowed.agent",
                    "customerName": "Jane Doe",
                    "uuid": "67890"
                }
            }

            with patch.object(user_filters, 'AGENT_ALLOWED', {'allowed.agent'}), \
                 patch('stream_gateway.stream_socket.run_both_live', new_callable=AsyncMock) as mock_run_both_live, \
                 patch('stream_gateway.recording.merge_stereo', new_callable=AsyncMock):

                await _handle_answer(ws, msg, ("127.0.0.1", 12345))

                self.assertEqual(session.state, "answered")
                self.assertIsNotNone(session.stream_task)

                # Clean up task
                session.stream_task.cancel()
                try:
                    await session.stream_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
