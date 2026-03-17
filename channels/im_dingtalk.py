"""DingTalk channel implementation using the local im_api package."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Optional

from loguru import logger

from bus.events import OutboundMessage
from bus.queue import MessageBus
from channels.base import BaseChannel
from config.schema import Config


class ImDingTalkChannel(BaseChannel):
    """DingTalk channel adapter following the same style as `ImQQChannel`."""

    name = "im_dingtalk"

    def __init__(self, config: Config, bus: MessageBus):
        super().__init__(config, bus)
        self.config = config
        self._bot = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bot_thread: Optional[threading.Thread] = None
        self._message_buffers: dict[str, str] = {}
        self._chat_context: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        dt_cfg = self.config.channels.dingtalk
        client_id = dt_cfg.client_id or os.getenv("DINGTALK_CLIENT_ID", "")
        client_secret = dt_cfg.client_secret or os.getenv("DINGTALK_CLIENT_SECRET", "")
        robot_code = dt_cfg.robot_code or os.getenv("DINGTALK_ROBOT_CODE", "")

        if not client_id or not client_secret:
            logger.error("DingTalk client_id or client_secret not configured")
            return

        logger.info(f"Starting IM DingTalk channel (client_id={client_id})")
        self._running = True

        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("No running event loop found")

        try:
            from channels.im_api.dingtalk.dingtalk.bot import DingTalkBot

            self._bot = DingTalkBot(
                client_id=client_id,
                client_secret=client_secret,
                robot_code=robot_code,
            )
            self._bot.on_message()(self._on_message_callback)

            def run_bot():
                try:
                    self._bot.run()
                except Exception as exc:
                    logger.error(f"DingTalk bot error: {exc}")

            self._bot_thread = threading.Thread(target=run_bot, daemon=True)
            self._bot_thread.start()
            logger.info("IM DingTalk channel started")
        except ImportError as exc:
            logger.error(f"Failed to import DingTalk module: {exc}")
            self._running = False
        except Exception as exc:
            logger.error(f"Failed to start DingTalk channel: {exc}")
            self._running = False

    async def stop(self) -> None:
        self._running = False
        if self._bot:
            try:
                await self._bot.stop()
            except Exception as exc:
                logger.warning(f"DingTalk stop warning: {exc}")
        logger.info("IM DingTalk channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        if not self._bot:
            logger.warning("DingTalk bot is not initialized")
            return

        chat_id = msg.chat_id

        try:
            if msg.is_chunk:
                self._message_buffers[chat_id] = self._message_buffers.get(chat_id, "") + msg.content
                return

            final_content = msg.content or ""
            if chat_id in self._message_buffers:
                buffered = self._message_buffers.pop(chat_id)
                if buffered:
                    final_content = buffered

            metadata = msg.metadata or {}
            cached_ctx = self._chat_context.get(chat_id, {})

            api = self._bot.api

            # Send media files
            for media_path in (msg.media or []):
                try:
                    lower = media_path.lower()
                    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    from pathlib import Path
                    ext = Path(media_path).suffix.lower()
                    media_type = "image" if ext in image_exts else "file"
                    await api.send_proactive_media(chat_id, media_path, media_type=media_type)
                    logger.info(f"DingTalk media sent to {chat_id}: {media_path}")
                except Exception as exc:
                    logger.error(f"Error sending DingTalk media {media_path}: {exc}")

            if not final_content.strip():
                return

            session_webhook = metadata.get("session_webhook") or cached_ctx.get("session_webhook")
            at_user_id = metadata.get("at_user_id") or cached_ctx.get("sender_id")

            if session_webhook:
                await api.send_by_session(
                    session_webhook,
                    final_content,
                    at_user_id=None if bool(metadata.get("is_private", cached_ctx.get("is_private", True))) else at_user_id,
                )
            else:
                await api.send_proactive_text(chat_id, final_content)

            logger.info(f"DingTalk sent to {chat_id}: {final_content[:100]}...")
        except Exception as exc:
            logger.error(f"Error sending DingTalk message: {exc}")

    @staticmethod
    async def _download_dingtalk_media(api, download_code: str, media_type: str) -> str:
        """Download a DingTalk media file via Robot API and return the local path."""
        from pathlib import Path
        import httpx

        token = await api.get_access_token()
        url = f"https://oapi.dingtalk.com/robot/messageFile/download?access_token={token}"
        body = {"downloadCode": download_code, "robotCode": api.robot_code}

        media_dir = Path.home() / ".open_research_claw" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        ext = {
            "image": ".jpg",
            "audio": ".ogg",
            "video": ".mp4",
            "file": "",
        }.get(media_type, "")

        save_path = media_dir / f"dt_{download_code[:16]}{ext}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)

        logger.debug(f"DingTalk media downloaded to {save_path}")
        return str(save_path)

    async def _on_message_callback(self, ctx) -> None:
        try:
            chat_id = str(ctx.user_id if ctx.is_private else (ctx.group_id or ctx.user_id))
            self._chat_context[chat_id] = {
                "msg_id": ctx.msg_id,
                "is_private": ctx.is_private,
                "session_webhook": ctx.session_webhook,
                "sender_id": ctx.user_id,
                "conversation_type": ctx.conversation_type,
            }

            metadata = {
                "msg_id": ctx.msg_id,
                "is_private": ctx.is_private,
                "session_webhook": ctx.session_webhook,
                "conversation_type": ctx.conversation_type,
            }

            # Prepend reply/quote context if present
            quote_content = getattr(ctx, "quote_content", "")
            content = ctx.content
            if quote_content:
                content = f'[Replying to: "{quote_content[:200]}"]\n{content}'

            # Download media file if present
            media_paths = []
            if getattr(ctx, "media_code", None) and getattr(ctx, "media_type", None):
                try:
                    path = await self._download_dingtalk_media(
                        self._bot.api, ctx.media_code, ctx.media_type
                    )
                    if path:
                        media_paths.append(path)
                        # Replace placeholder with actual path
                        content = f"[{ctx.media_type}: {path}]"
                except Exception as exc:
                    logger.error(f"DingTalk media download failed: {exc}")

            if self._main_loop and self._main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._handle_message(
                        sender_id=str(ctx.user_id or "unknown"),
                        chat_id=chat_id,
                        content=content,
                        media=media_paths,
                        metadata=metadata,
                    ),
                    self._main_loop,
                )
            else:
                logger.warning("Main event loop not available, DingTalk message dropped")
        except Exception as exc:
            logger.error(f"Error in DingTalk callback: {exc}")
