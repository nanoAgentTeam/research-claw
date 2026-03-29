"""Best-effort DingTalk stream gateway wrapper.

This module uses optional `dingtalk-stream` package when available.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, Awaitable, Callable


def _to_dict_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return {"raw": raw}
    data = getattr(raw, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            return json.loads(data)
        except Exception:
            return {"raw": data}
    return {"raw": str(raw)}


class DingTalkGateway:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = None
        self._started = False

    async def start(
        self,
        event_handler: Callable[[dict, str | None, Callable[[bool], None] | None], Awaitable[None]],
    ) -> None:
        try:
            import dingtalk_stream as ds  # type: ignore
        except ImportError as exc:
            raise RuntimeError("dingtalk-stream is required for inbound stream mode") from exc

        credential_cls = getattr(ds, "Credential", None)
        client_cls = getattr(ds, "DingTalkStreamClient", None)
        topic_robot = getattr(ds, "TOPIC_ROBOT", None)

        if not credential_cls or not client_cls:
            raise RuntimeError("Unsupported dingtalk-stream SDK API")

        credential = credential_cls(self.client_id, self.client_secret)
        sdk_logger = _CompatSDKLogger(logging.getLogger("dingtalk.gateway.sdk"))

        try:
            client_sig = inspect.signature(client_cls)
            supports_logger = "logger" in client_sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in client_sig.parameters.values()
            )
        except (TypeError, ValueError):
            supports_logger = False

        client = client_cls(credential, logger=sdk_logger) if supports_logger else client_cls(credential)
        if getattr(client, "logger", None) is not sdk_logger:
            try:
                client.logger = sdk_logger
            except Exception:
                pass
        self._client = client

        ack_message_cls = getattr(ds, "AckMessage", None)
        ack_status_ok = getattr(ack_message_cls, "STATUS_OK", None)
        callback_base_cls = getattr(ds, "CallbackHandler", None)
        if callback_base_cls is None:
            handlers_mod = getattr(ds, "handlers", None)
            callback_base_cls = getattr(handlers_mod, "CallbackHandler", None) if handlers_mod else None
        if callback_base_cls is None:
            callback_base_cls = object
        base_has_raw_process = callable(getattr(callback_base_cls, "raw_process", None))
        topic_chatbot = None
        chatbot_message_cls = getattr(ds, "ChatbotMessage", None)
        if chatbot_message_cls is not None:
            topic_chatbot = getattr(chatbot_message_cls, "TOPIC", None)
        if topic_chatbot is None:
            chatbot_module = getattr(ds, "chatbot", None)
            chatbot_message_cls = getattr(chatbot_module, "ChatbotMessage", None) if chatbot_module else None
            topic_chatbot = getattr(chatbot_message_cls, "TOPIC", None) if chatbot_message_cls else None
        topic = topic_robot or topic_chatbot or "chatbot"

        use_manual_ack = True

        class _AckCompat:
            def __init__(self, status: int, message: str = "OK", data: dict | None = None):
                self.status = status
                self.message = message
                self.data = data

            def to_dict(self) -> dict:
                payload: dict[str, Any] = {
                    "code": self.status,
                    "message": self.message,
                }
                if self.data is not None:
                    payload["data"] = self.data
                return payload

        def _build_ack_object() -> Any:
            status_code = int(ack_status_ok) if isinstance(ack_status_ok, int) else 200
            if ack_message_cls is None:
                return _AckCompat(status_code, "OK")
            for args in (
                (status_code, "OK"),
                (status_code,),
                (),
            ):
                try:
                    obj = ack_message_cls(*args)
                    if hasattr(obj, "to_dict"):
                        return obj
                except Exception:
                    continue
            return _AckCompat(status_code, "OK")

        async def _handle(raw: Any):
            payload = _to_dict_payload(raw)
            message_id = None
            headers = getattr(raw, "headers", None)
            if isinstance(headers, dict):
                message_id = headers.get("messageId") or headers.get("message_id")

            def ack(_: bool = True) -> None:
                if not use_manual_ack:
                    return
                try:
                    ack_fn = getattr(client, "socketCallBackResponse", None)
                    if ack_fn and message_id:
                        ack_fn(message_id, {"success": True})
                except Exception:
                    return

            await event_handler(payload, message_id, ack)

        def _callback(raw: Any):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_handle(raw))
            except RuntimeError:
                asyncio.run(_handle(raw))

        register_fn = getattr(client, "register_callback_handler", None) or getattr(
            client,
            "registerCallbackListener",
            None,
        )
        if not register_fn:
            raise RuntimeError("Unsupported dingtalk-stream register API")

        register_name = getattr(register_fn, "__name__", "")
        if register_name == "register_callback_handler":
            use_manual_ack = False

            class _CompatCallbackHandler(callback_base_cls):
                def pre_start(self, *args: Any, **kwargs: Any) -> None:
                    super_pre_start = getattr(super(), "pre_start", None)
                    if callable(super_pre_start):
                        super_pre_start(*args, **kwargs)
                    return

                def post_stop(self, *args: Any, **kwargs: Any) -> None:
                    super_post_stop = getattr(super(), "post_stop", None)
                    if callable(super_post_stop):
                        super_post_stop(*args, **kwargs)
                    return

                async def process(self, callback: Any):
                    await _handle(callback)
                    if base_has_raw_process:
                        return (ack_status_ok if ack_status_ok is not None else 200, "OK")
                    return _build_ack_object()

            register_fn(topic, _CompatCallbackHandler())
        else:
            try:
                positional_count = len(
                    [
                        p
                        for p in inspect.signature(register_fn).parameters.values()
                        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    ]
                )
            except Exception:
                positional_count = 2

            if positional_count >= 2:
                register_fn(topic, _callback)
            else:
                register_fn(_callback)

        start_fn = getattr(client, "start_forever", None) or getattr(client, "start", None)
        if not start_fn:
            raise RuntimeError("Unsupported dingtalk-stream start API")

        self._started = True
        await asyncio.to_thread(start_fn)

    async def stop(self) -> None:
        if not self._client or not self._started:
            return

        stop_fn = getattr(self._client, "stop", None) or getattr(self._client, "disconnect", None)
        if stop_fn:
            await asyncio.to_thread(stop_fn)
        self._started = False


class _CompatSDKLogger:
    """Normalize malformed SDK logging calls such as logger.exception("msg", exc)."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)

    def exception(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        if args and isinstance(msg, str) and "%" not in msg and len(args) == 1 and isinstance(args[0], BaseException):
            exc = args[0]
            self._logger.error("%s: %s", msg, exc, exc_info=exc)
            return
        self._logger.exception(msg, *args, **kwargs)
