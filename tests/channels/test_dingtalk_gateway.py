import asyncio
import contextlib
import io
import logging
import types
import unittest
from unittest.mock import patch

from channels.im_api.dingtalk.dingtalk.gateway import DingTalkGateway


class _RawMessage:
    def __init__(self, data, headers):
        self.data = data
        self.headers = headers


class TestDingTalkGateway(unittest.IsolatedAsyncioTestCase):
    async def test_start_uses_compat_logger_for_malformed_sdk_exception_calls(self):
        class Credential:
            def __init__(self, client_id, client_secret):
                self.client_id = client_id
                self.client_secret = client_secret

        class DingTalkStreamClient:
            def __init__(self, credential, logger=None):
                self.credential = credential
                self.logger = logger or logging.getLogger("tests.dingtalk.sdk")
                self.logger.handlers = []
                self.logger.addHandler(logging.StreamHandler())
                self.logger.setLevel(logging.ERROR)
                self.logger.propagate = False

            def register_callback_handler(self, _topic, _handler):
                return None

            def start_forever(self):
                self.logger.exception("unknown exception", OSError(65, "No route to host"))

            def stop(self):
                return None

        ds = types.SimpleNamespace(
            Credential=Credential,
            DingTalkStreamClient=DingTalkStreamClient,
        )

        async def event_handler(_payload, _message_id, _ack):
            return None

        stderr = io.StringIO()
        with patch.dict("sys.modules", {"dingtalk_stream": ds}), contextlib.redirect_stderr(stderr):
            gateway = DingTalkGateway("id", "secret")
            await gateway.start(event_handler)

        self.assertNotIn("TypeError: not all arguments converted during string formatting", stderr.getvalue())

    async def test_register_callback_handler_compat(self):
        class Credential:
            def __init__(self, client_id, client_secret):
                self.client_id = client_id
                self.client_secret = client_secret

        class AckMessage:
            STATUS_OK = 200

        class ChatbotMessage:
            TOPIC = "chatbot"

        class CallbackHandler:
            async def raw_process(self, callback):
                return await self.process(callback)

        class DingTalkStreamClient:
            def __init__(self, credential):
                self.credential = credential
                self.topic = None
                self.handler = None
                self.ack_calls = []

            def register_callback_handler(self, topic, handler):
                self.topic = topic
                self.handler = handler

            def socketCallBackResponse(self, message_id, data):
                self.ack_calls.append((message_id, data))

            def start_forever(self):
                return None

            def stop(self):
                return None

        ds = types.SimpleNamespace(
            Credential=Credential,
            DingTalkStreamClient=DingTalkStreamClient,
            AckMessage=AckMessage,
            ChatbotMessage=ChatbotMessage,
            CallbackHandler=CallbackHandler,
        )

        events = []

        async def event_handler(payload, message_id, ack):
            events.append((payload, message_id))
            if ack:
                ack(True)

        with patch.dict("sys.modules", {"dingtalk_stream": ds}):
            gateway = DingTalkGateway("id", "secret")
            await gateway.start(event_handler)
            client = gateway._client
            self.assertEqual(client.topic, "chatbot")

            status, _ = await client.handler.process(
                _RawMessage('{"text":{"content":"hi"}}', {"messageId": "m1"})
            )
            self.assertEqual(status, 200)

            self.assertEqual(events[0][0]["text"]["content"], "hi")
            self.assertEqual(events[0][1], "m1")
            self.assertEqual(client.ack_calls, [])

    async def test_register_callback_listener_manual_ack(self):
        class Credential:
            def __init__(self, client_id, client_secret):
                self.client_id = client_id
                self.client_secret = client_secret

        class DingTalkStreamClient:
            def __init__(self, credential):
                self.credential = credential
                self.topic = None
                self.callback = None
                self.ack_calls = []

            def registerCallbackListener(self, topic, callback):
                self.topic = topic
                self.callback = callback

            def socketCallBackResponse(self, message_id, data):
                self.ack_calls.append((message_id, data))

            def start(self):
                return None

            def disconnect(self):
                return None

        ds = types.SimpleNamespace(
            Credential=Credential,
            DingTalkStreamClient=DingTalkStreamClient,
            TOPIC_ROBOT="robot",
        )

        async def event_handler(_payload, _message_id, ack):
            if ack:
                ack(True)

        with patch.dict("sys.modules", {"dingtalk_stream": ds}):
            gateway = DingTalkGateway("id", "secret")
            await gateway.start(event_handler)
            client = gateway._client
            self.assertEqual(client.topic, "robot")

            client.callback(_RawMessage('{"text":{"content":"hi"}}', {"messageId": "m2"}))
            await asyncio.sleep(0.01)
            self.assertEqual(client.ack_calls, [("m2", {"success": True})])

    async def test_register_callback_handler_without_callback_base_returns_ack_object(self):
        class Credential:
            def __init__(self, client_id, client_secret):
                self.client_id = client_id
                self.client_secret = client_secret

        class DingTalkStreamClient:
            def __init__(self, credential):
                self.credential = credential
                self.handler = None

            def register_callback_handler(self, _topic, handler):
                self.handler = handler

            def start_forever(self):
                return None

            def stop(self):
                return None

        ds = types.SimpleNamespace(
            Credential=Credential,
            DingTalkStreamClient=DingTalkStreamClient,
        )

        async def event_handler(_payload, _message_id, _ack):
            return None

        with patch.dict("sys.modules", {"dingtalk_stream": ds}):
            gateway = DingTalkGateway("id", "secret")
            await gateway.start(event_handler)
            client = gateway._client

            ack_obj = await client.handler.process(
                _RawMessage('{"text":{"content":"hi"}}', {"messageId": "m3"})
            )
            self.assertTrue(hasattr(ack_obj, "to_dict"))
            self.assertEqual(ack_obj.to_dict().get("code"), 200)


if __name__ == "__main__":
    unittest.main()
