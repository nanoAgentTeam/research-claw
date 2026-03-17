"""Telegram HTTP/API helpers."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from telegram import Bot

logger = logging.getLogger("telegram.api")


def markdown_to_telegram_html(text: str) -> str:
    """Convert markdown-ish text to Telegram-safe HTML."""
    if not text:
        return ""

    code_blocks: list[str] = []

    def save_code_block(match: re.Match[str]) -> str:
        code_blocks.append(match.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    inline_codes: list[str] = []

    def save_inline_code(match: re.Match[str]) -> str:
        inline_codes.append(match.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    for index, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{index}\x00", f"<code>{escaped}</code>")

    for index, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{index}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


class TelegramBotAPI:
    """Thin async wrapper around `python-telegram-bot` Bot APIs."""

    def __init__(self, token: str, bot: Bot | None = None, media_dir: str | Path | None = None):
        if not token:
            raise ValueError("Telegram token is required")

        self.token = token
        self.bot = bot
        self.media_dir = Path(media_dir) if media_dir else Path.home() / ".open_research_claw" / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    async def _resolve_bot(self) -> Bot:
        if self.bot is None:
            self.bot = Bot(token=self.token)
        return self.bot

    @staticmethod
    def _normalize_chat_id(chat_id: str | int) -> str | int:
        if isinstance(chat_id, int):
            return chat_id
        text = str(chat_id).strip()
        if text.startswith("@"):
            return text
        try:
            return int(text)
        except ValueError:
            return text

    @staticmethod
    def _normalize_reply_to(reply_to_message_id: str | int | None) -> int | None:
        if reply_to_message_id is None:
            return None
        if isinstance(reply_to_message_id, int):
            return reply_to_message_id
        text = str(reply_to_message_id).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    _TG_MAX_LENGTH = 4096

    @staticmethod
    def _split_text(text: str, limit: int) -> list:
        """Split text into chunks respecting newline boundaries when possible."""
        if len(text) <= limit:
            return [text]
        chunks = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            # Try to split at last newline within limit
            cut = text.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")
        return chunks

    async def send_message(
        self,
        chat_id: str | int,
        content: str,
        reply_to_message_id: str | int | None = None,
    ) -> dict[str, Any]:
        if not content:
            return {"ok": False, "error": "empty content"}

        bot = await self._resolve_bot()
        resolved_chat_id = self._normalize_chat_id(chat_id)
        resolved_reply = self._normalize_reply_to(reply_to_message_id)

        html_content = markdown_to_telegram_html(content)

        # Split into chunks if too long
        if len(html_content) > self._TG_MAX_LENGTH:
            # HTML length unpredictable after conversion, split on plain text then convert per chunk
            chunks = self._split_text(content, self._TG_MAX_LENGTH - 200)
        else:
            chunks = None  # send as single HTML message

        sent = None
        if chunks is None:
            # Single message, try HTML first
            try:
                sent = await bot.send_message(
                    chat_id=resolved_chat_id,
                    text=html_content,
                    parse_mode="HTML",
                    reply_to_message_id=resolved_reply,
                )
            except Exception as exc:
                logger.warning("Telegram HTML send failed, fallback to plain text: %s", exc)
                sent = await bot.send_message(
                    chat_id=resolved_chat_id,
                    text=content,
                    reply_to_message_id=resolved_reply,
                )
        else:
            # Multi-chunk: send each part, only first replies to original
            for i, chunk in enumerate(chunks):
                reply_id = resolved_reply if i == 0 else None
                chunk_html = markdown_to_telegram_html(chunk)
                try:
                    sent = await bot.send_message(
                        chat_id=resolved_chat_id,
                        text=chunk_html,
                        parse_mode="HTML",
                        reply_to_message_id=reply_id,
                    )
                except Exception:
                    sent = await bot.send_message(
                        chat_id=resolved_chat_id,
                        text=chunk,
                        reply_to_message_id=reply_id,
                    )

        return {
            "ok": True,
            "message_id": getattr(sent, "message_id", None),
            "chat_id": str(getattr(getattr(sent, "chat", None), "id", chat_id)),
        }

    async def send_photo(
        self,
        chat_id: str | int,
        photo: str,
        caption: str = "",
        reply_to_message_id: str | int | None = None,
    ) -> dict[str, Any]:
        bot = await self._resolve_bot()
        resolved_chat_id = self._normalize_chat_id(chat_id)
        resolved_reply = self._normalize_reply_to(reply_to_message_id)

        if photo.startswith(("http://", "https://")):
            sent = await bot.send_photo(
                chat_id=resolved_chat_id,
                photo=photo,
                caption=caption,
                reply_to_message_id=resolved_reply,
            )
        else:
            path = Path(photo)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"photo not found: {photo}")
            with open(path, "rb") as file:
                sent = await bot.send_photo(
                    chat_id=resolved_chat_id,
                    photo=file,
                    caption=caption,
                    reply_to_message_id=resolved_reply,
                )

        return {
            "ok": True,
            "message_id": getattr(sent, "message_id", None),
            "chat_id": str(getattr(getattr(sent, "chat", None), "id", chat_id)),
        }

    async def send_document(
        self,
        chat_id: str | int,
        document: str,
        caption: str = "",
        filename: str | None = None,
        reply_to_message_id: str | int | None = None,
    ) -> dict[str, Any]:
        """Send a document (PDF, etc.) to a chat.

        Args:
            chat_id: Target chat.
            document: URL or local file path.
            caption: Optional caption text.
            filename: Override the file name shown in Telegram.
            reply_to_message_id: Optional message to reply to.
        """
        bot = await self._resolve_bot()
        resolved_chat_id = self._normalize_chat_id(chat_id)
        resolved_reply = self._normalize_reply_to(reply_to_message_id)

        if document.startswith(("http://", "https://")):
            sent = await bot.send_document(
                chat_id=resolved_chat_id,
                document=document,
                caption=caption,
                filename=filename,
                reply_to_message_id=resolved_reply,
            )
        else:
            path = Path(document)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"document not found: {document}")
            effective_filename = filename or path.name
            with open(path, "rb") as file:
                sent = await bot.send_document(
                    chat_id=resolved_chat_id,
                    document=file,
                    caption=caption,
                    filename=effective_filename,
                    reply_to_message_id=resolved_reply,
                )

        return {
            "ok": True,
            "message_id": getattr(sent, "message_id", None),
            "chat_id": str(getattr(getattr(sent, "chat", None), "id", chat_id)),
        }

    async def send_video(
        self,
        chat_id: str | int,
        video: str,
        caption: str = "",
        reply_to_message_id: str | int | None = None,
    ) -> dict[str, Any]:
        """Send a video to a chat."""
        bot = await self._resolve_bot()
        resolved_chat_id = self._normalize_chat_id(chat_id)
        resolved_reply = self._normalize_reply_to(reply_to_message_id)

        if video.startswith(("http://", "https://")):
            sent = await bot.send_video(
                chat_id=resolved_chat_id,
                video=video,
                caption=caption,
                reply_to_message_id=resolved_reply,
            )
        else:
            path = Path(video)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"video not found: {video}")
            with open(path, "rb") as file:
                sent = await bot.send_video(
                    chat_id=resolved_chat_id,
                    video=file,
                    caption=caption,
                    reply_to_message_id=resolved_reply,
                )

        return {
            "ok": True,
            "message_id": getattr(sent, "message_id", None),
            "chat_id": str(getattr(getattr(sent, "chat", None), "id", chat_id)),
        }

    async def download_file(self, file_id: str, media_type: str = "file", mime_type: str | None = None) -> str:
        bot = await self._resolve_bot()
        tg_file = await bot.get_file(file_id)

        ext = self._resolve_extension(media_type, mime_type)
        file_path = self.media_dir / f"{file_id[:16]}{ext}"
        await tg_file.download_to_drive(str(file_path))
        return str(file_path)

    @staticmethod
    def _resolve_extension(media_type: str, mime_type: str | None) -> str:
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "audio/ogg": ".ogg",
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "video/mp4": ".mp4",
                "application/pdf": ".pdf",
            }
            if mime_type in ext_map:
                return ext_map[mime_type]

        type_map = {
            "image": ".jpg",
            "voice": ".ogg",
            "audio": ".mp3",
            "video": ".mp4",
            "file": "",
        }
        return type_map.get(media_type, "")
