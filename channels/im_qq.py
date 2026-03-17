"""QQ channel implementation using im_api."""

import asyncio
import threading
from typing import Any, Optional

from loguru import logger

from bus.events import InboundMessage, OutboundMessage
from bus.queue import MessageBus
from channels.base import BaseChannel
from config.schema import Config


class ImQQChannel(BaseChannel):
    """
    QQ channel using the standalone im_api package.

    This channel wraps the im_api/qq implementation to provide
    QQ Bot integration while using the project's message bus.
    """

    name = "im_qq"

    def __init__(self, config: Config, bus: MessageBus):
        super().__init__(config, bus)
        self.config = config
        self._bot = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bot_thread: Optional[threading.Thread] = None
        # Buffer for accumulating streaming chunks
        self._message_buffers: dict[str, str] = {}  # chat_id -> accumulated content
        # Cache latest inbound context per chat to recover reply metadata when bus metadata is lost
        self._chat_context: dict[str, dict[str, Any]] = {}  # chat_id -> {is_private, msg_id, event_type}

    async def start(self) -> None:
        """Start the QQ channel using im_api."""
        # QQ uses APP_ID and APP_SECRET from config.channels.qq
        app_id = self.config.channels.qq.app_id
        app_secret = self.config.channels.qq.app_secret

        if not app_id or not app_secret:
            # Try from environment
            import os
            app_id = os.getenv("QQ_APP_ID") or app_id
            app_secret = os.getenv("QQ_APP_SECRET") or app_secret

        if not app_id or not app_secret:
            logger.error("QQ app_id or app_secret not configured")
            return

        logger.info(f"Starting IM QQ channel (App ID: {app_id})...")
        self._running = True

        # Store the main event loop for callbacks
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("No running event loop found")

        # Import and initialize the bot from im_api
        try:
            from channels.im_api.qq.qq.bot import QQBot
            from channels.im_api.qq.qq.context import Context

            self._bot = QQBot(
                app_id=app_id,
                app_secret=app_secret
            )

            # Set up the message callback for the bus
            self._bot.on_message()(self._on_message_callback)

            # Start the bot in a separate thread
            def run_bot():
                try:
                    self._bot.run()
                except Exception as e:
                    logger.error(f"QQ bot error: {e}")

            self._bot_thread = threading.Thread(target=run_bot, daemon=True)
            self._bot_thread.start()

            logger.info("IM QQ channel started successfully")

        except ImportError as e:
            logger.error(f"Failed to import im_api: {e}")
            self._running = False
        except Exception as e:
            logger.error(f"Failed to start IM QQ channel: {e}")
            self._running = False

    async def stop(self) -> None:
        """Stop the QQ channel."""
        self._running = False
        logger.info("IM QQ channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through QQ.

        This method receives messages from the message bus and sends them
        to QQ using the im_api sender.

        For QQ, we accumulate streaming chunks and send the complete message
        at the end to avoid "2-3 characters per message" issue.
        """
        if not self._bot:
            logger.warning("QQ bot not initialized")
            return

        chat_id = msg.chat_id

        try:
            # Handle streaming chunks - accumulate them
            if msg.is_chunk:
                if chat_id not in self._message_buffers:
                    self._message_buffers[chat_id] = ""
                self._message_buffers[chat_id] += msg.content
                logger.debug(f"QQ chunk buffered for {chat_id}: {msg.content[:50]}...")
                # Don't send yet - wait for the complete message
                return

            # Final message (not a chunk) - combine with any buffered content
            final_content = msg.content or ""
            if chat_id in self._message_buffers:
                # If we have buffered chunks, use the buffer (it already contains the full message)
                if self._message_buffers[chat_id]:
                    final_content = self._message_buffers[chat_id]
                del self._message_buffers[chat_id]

            # Don't send empty messages
            if not final_content.strip():
                logger.debug(f"QQ skipping empty message for {chat_id}")
                return

            # Send the complete message to QQ
            metadata = msg.metadata or {}
            cached_ctx = self._chat_context.get(chat_id, {})

            message_id = metadata.get("msg_id") or cached_ctx.get("msg_id")
            from channels.im_api.qq.qq.api import QQBotAPI

            api = QQBotAPI(
                app_id=self._bot.app_id,
                client_secret=self._bot.client_secret
            )

            if "is_private" in metadata:
                is_private = bool(metadata.get("is_private"))
            else:
                is_private = bool(cached_ctx.get("is_private", True))
                logger.debug(
                    f"QQ using cached chat context for {chat_id}: "
                    f"is_private={is_private}, event_type={cached_ctx.get('event_type')}"
                )

            # Send media files
            for media_path in (msg.media or []):
                try:
                    await self._send_media_file(api, chat_id, media_path, message_id, is_group=not is_private)
                except Exception as exc:
                    logger.error(f"Error sending QQ media {media_path}: {exc}")

            if not final_content.strip():
                return

            logger.info(f"QQ about to send: chat_id={chat_id}, is_private={is_private}, content_length={len(final_content)}, content_preview={final_content[:50]}")

            if is_private:
                await api.send_c2c_message(chat_id, final_content, message_id)
            else:
                await api.send_group_message(chat_id, final_content, message_id)

            logger.info(f"QQ sent to {chat_id} ({'private' if is_private else 'group'}): {final_content[:100]}...")

        except Exception as e:
            logger.error(f"Error sending QQ message: {e}")

    @staticmethod
    async def _send_media_file(
        api: "QQBotAPI",
        chat_id: str,
        media_path: str,
        msg_id: str,
        is_group: bool = False,
    ) -> None:
        """Dispatch a media file to the appropriate QQ send method."""
        from pathlib import Path

        ext = Path(media_path).suffix.lower()
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        video_exts = {".mp4", ".mov", ".avi", ".mkv"}

        if ext in image_exts:
            await api.send_image(chat_id, media_path, msg_id, is_group=is_group)
        elif ext in video_exts:
            await api.send_video(chat_id, media_path, msg_id, is_group=is_group)
        else:
            await api.send_file(chat_id, media_path, msg_id, is_group=is_group)

        logger.info(f"QQ media sent to {chat_id}: {media_path}")

    @staticmethod
    async def _download_qq_attachment(url: str, content_type: str = "") -> str:
        """Download a QQ attachment URL to the local media directory."""
        from pathlib import Path
        import hashlib
        import httpx

        media_dir = Path.home() / ".open_research_claw" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        # Determine extension from content_type or URL
        ext = ""
        if "image" in content_type:
            ext = ".jpg"
        elif "video" in content_type:
            ext = ".mp4"
        elif "audio" in content_type:
            ext = ".ogg"
        elif "." in url.split("?")[0].split("/")[-1]:
            ext = "." + url.split("?")[0].split("/")[-1].rsplit(".", 1)[-1]

        name = hashlib.md5(url.encode()).hexdigest()[:16]
        save_path = media_dir / f"qq_{name}{ext}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)

        logger.debug(f"QQ attachment downloaded to {save_path}")
        return str(save_path)

    async def _on_message_callback(self, ctx) -> None:
        """
        Internal callback for im_api messages.

        This is called by the im_api bot when a message is received.
        It runs in the bot's thread, so we need to schedule the work
        on the main event loop.
        """
        try:
            chat_id = str(ctx.group_id) if ctx.group_id else str(ctx.user_id)
            self._chat_context[chat_id] = {
                "msg_id": ctx.msg_id,
                "is_private": ctx.is_private,
                "event_type": ctx.event_type,
            }

            # Download attachments if present
            media_paths = []
            content_parts = [ctx.content] if ctx.content else []
            for att in (ctx.attachments or []):
                url = att.get("url") if isinstance(att, dict) else str(att)
                ctype = att.get("content_type", "") if isinstance(att, dict) else ""
                if url:
                    try:
                        path = await self._download_qq_attachment(url, ctype)
                        if path:
                            media_paths.append(path)
                            media_label = "image" if "image" in ctype else "file"
                            content_parts.append(f"[{media_label}: {path}]")
                    except Exception as exc:
                        logger.error(f"QQ attachment download failed: {exc}")
                        content_parts.append("[attachment: download failed]")

            content = "\n".join(content_parts) if content_parts else "[empty message]"

            # Schedule on main event loop if available
            if self._main_loop and self._main_loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_message(
                            sender_id=str(ctx.user_id) if ctx.user_id else "unknown",
                            chat_id=chat_id,
                            content=content,
                            media=media_paths,
                            metadata={
                                "msg_id": ctx.msg_id,
                                "is_private": ctx.is_private,
                                "event_type": ctx.event_type,
                            }
                        ),
                        self._main_loop
                    )
                except RuntimeError as e:
                    logger.error(f"Failed to schedule message on main loop: {e}")
            else:
                logger.warning("Main event loop not available, message dropped")

        except Exception as e:
            logger.error(f"Error in message callback: {e}")
