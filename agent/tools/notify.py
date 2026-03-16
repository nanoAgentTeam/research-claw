"""Push notification tool for project automation flows."""

from __future__ import annotations

import json
import asyncio
import smtplib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from email.message import EmailMessage

from loguru import logger

from bus.events import OutboundMessage
from core.automation.push_targets import build_apprise_url, send_apprise_notification
from core.automation.store_fs import FSAutomationStore
from core.tools.base import BaseTool
from config.i18n import t


class NotifyPushTool(BaseTool):
    """Send push notifications to subscribed channel/chat targets."""

    def __init__(self, tool_context: Any):
        self.ctx = tool_context
        self.project = getattr(tool_context, "project", None)
        self.config = getattr(tool_context, "config", None)
        self.bus = getattr(tool_context, "bus", None)
        self._dedupe_file: Optional[Path] = None
        self._legacy_dedupe_file: Optional[Path] = None
        if self.project:
            self._dedupe_file = self.project.root / ".project_memory" / "push_dedupe.json"
            self._legacy_dedupe_file = self.project.root / ".project_memory" / "automation" / "push_dedupe.json"
            self._migrate_legacy_dedupe_once()

    @property
    def name(self) -> str:
        return "notify_push"

    @property
    def description(self) -> str:
        return (
            "Send a push message to project subscribers. "
            "Use this only when notification is necessary."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Push content."},
                "channels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional target channels. Empty means default configured channels.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "description": "Priority label.",
                    "default": "normal",
                },
                "dedupe_key": {
                    "type": "string",
                    "description": "Optional dedupe key to prevent repeated push.",
                    "default": "",
                },
            },
            "required": ["content"],
        }

    def _load_dedupe(self) -> dict[str, str]:
        if not self._dedupe_file or not self._dedupe_file.exists():
            return {}
        try:
            data = json.loads(self._dedupe_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_dedupe(self, data: dict[str, str]) -> None:
        if not self._dedupe_file:
            return
        self._dedupe_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._dedupe_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._dedupe_file)

    def _migrate_legacy_dedupe_once(self) -> None:
        if not self._dedupe_file or not self._legacy_dedupe_file:
            return
        if self._dedupe_file.exists() or not self._legacy_dedupe_file.exists():
            return
        try:
            raw = json.loads(self._legacy_dedupe_file.read_text(encoding="utf-8"))
            payload = raw if isinstance(raw, dict) else {}
            self._save_dedupe(payload)
        except Exception:
            pass

    def _default_channels(self) -> list[str]:
        channels: list[str] = []
        if not self.config:
            return channels
        try:
            accounts = getattr(getattr(self.config, "channel", None), "accounts", []) or []
            for acc in accounts:
                if getattr(acc, "enabled", False):
                    platform = str(getattr(acc, "platform", "")).strip()
                    if platform and platform not in channels:
                        channels.append(platform)
        except Exception:
            pass
        return channels

    def _split_title_and_body(self, content: str) -> tuple[str, str]:
        default_title = t("notify.default_title")
        text = (content or "").strip()
        if not text:
            return default_title, ""
        lines = text.splitlines()
        title = lines[0].strip()[:100] if lines else default_title
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        return title or default_title, body

    def _send_serverchan_sync(self, send_key: str, content: str) -> None:
        key = str(send_key or "").strip()
        if not key:
            raise ValueError("empty serverchan send key")
        title, body = self._split_title_and_body(content)
        params = {"title": title}
        if body:
            params["desp"] = body
        url = f"https://sctapi.ftqq.com/{key}.send?{urlencode(params)}"
        req = Request(url=url, method="GET")
        with urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw)
            code = int(payload.get("code", 1))
            if code != 0:
                raise RuntimeError(f"serverchan failed: {payload}")
        except json.JSONDecodeError:
            if "ok" not in raw.lower() and "success" not in raw.lower():
                raise RuntimeError(f"serverchan unexpected response: {raw[:200]}")

    @staticmethod
    def _send_telegram_sync(bot_token: str, chat_id: str, content: str) -> None:
        """Send a text message via Telegram Bot API directly (no bus dependency)."""
        import http.client
        payload = json.dumps({
            "chat_id": chat_id,
            "text": content,
            "parse_mode": "Markdown",
        }).encode("utf-8")
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=30)
        try:
            conn.request("POST", f"/bot{bot_token}/sendMessage", body=payload, headers={
                "Content-Type": "application/json",
            })
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="ignore")
            if resp.status != 200:
                # Retry without parse_mode if Markdown parsing failed
                if "can't parse" in body.lower() or "bad request" in body.lower():
                    payload2 = json.dumps({
                        "chat_id": chat_id,
                        "text": content,
                    }).encode("utf-8")
                    conn2 = http.client.HTTPSConnection("api.telegram.org", timeout=30)
                    try:
                        conn2.request("POST", f"/bot{bot_token}/sendMessage", body=payload2, headers={
                            "Content-Type": "application/json",
                        })
                        resp2 = conn2.getresponse()
                        body2 = resp2.read().decode("utf-8", errors="ignore")
                        if resp2.status != 200:
                            raise RuntimeError(f"Telegram API returned {resp2.status}: {body2[:300]}")
                    finally:
                        conn2.close()
                else:
                    raise RuntimeError(f"Telegram API returned {resp.status}: {body[:300]}")
        finally:
            conn.close()

    def _get_telegram_bot_token(self) -> str:
        """Get Telegram bot token from config channel accounts."""
        if not self.config:
            return ""
        try:
            accounts = getattr(getattr(self.config, "channel", None), "accounts", []) or []
            for acc in accounts:
                if str(getattr(acc, "platform", "")).strip() == "telegram" and getattr(acc, "enabled", False):
                    creds = getattr(acc, "credentials", {}) or {}
                    token = str(creds.get("token") or "").strip()
                    if token:
                        return token
        except Exception:
            pass
        return ""

    def _send_email_sync(self, recipient: str, content: str) -> None:
        import os

        to_addr = str(recipient or "").strip()
        if not to_addr:
            raise ValueError("empty email recipient")

        host = os.getenv("CONTEXT_BOT_SMTP_HOST", "").strip()
        port = int(os.getenv("CONTEXT_BOT_SMTP_PORT", "587").strip() or "587")
        user = os.getenv("CONTEXT_BOT_SMTP_USER", "").strip()
        password = os.getenv("CONTEXT_BOT_SMTP_PASS", "").strip()
        sender = os.getenv("CONTEXT_BOT_SMTP_FROM", user).strip()
        use_tls = os.getenv("CONTEXT_BOT_SMTP_TLS", "1").strip() not in {"0", "false", "False"}

        if not host or not sender:
            raise RuntimeError("smtp config missing: CONTEXT_BOT_SMTP_HOST / CONTEXT_BOT_SMTP_FROM")

        title, body = self._split_title_and_body(content)
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = sender
        msg["To"] = to_addr
        msg.set_content(body or content or title)

        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)

    async def execute(
        self,
        content: str,
        channels: Optional[list[str]] = None,
        priority: str = "normal",
        dedupe_key: str = "",
        **kwargs,
    ) -> str:
        if not self.project:
            return t("notify.no_project")
        if not self.bus:
            return t("notify.no_bus")
        text = (content or "").strip()
        if not text:
            return t("notify.empty_content")

        # Auto-prepend project name so recipients know which project the push is from
        project_name = getattr(getattr(self.project, "config", None), "name", None) or getattr(self.project, "id", "")
        if project_name and not text.startswith(f"[{project_name}]"):
            text = f"[{project_name}] {text}"

        if dedupe_key:
            dedupe = self._load_dedupe()
            if dedupe_key in dedupe:
                return f"Skipped duplicated push (dedupe_key={dedupe_key})."

        store = FSAutomationStore(self.project)
        subs = store.get_subscriptions()

        # Resolve linked subscriptions into subs and apprise targets
        _telegram_channels = {"telegram", "im_telegram"}
        apprise_targets: list[tuple[str, str]] = []  # (channel, apprise_url)
        # Telegram credentials from linked subscriptions (bot_token → set of chat_ids)
        _tg_direct: dict[str, set[str]] = {}
        try:
            global_items = list(getattr(getattr(self.config, "push_subscriptions", None), "items", []) or [])
            linked_ids = set(store.get_linked_subscription_ids())
            for item in global_items:
                if not linked_ids:
                    break
                sid = str(getattr(item, "id", "") or "").strip()
                if not sid or sid not in linked_ids:
                    continue
                if not bool(getattr(item, "enabled", True)):
                    continue
                channel = str(getattr(item, "channel", "") or "").strip()
                if not channel:
                    continue
                params = getattr(item, "params", {}) or {}
                chat_id = str(getattr(item, "chat_id", "") or "").strip()
                if not chat_id:
                    chat_id = str(params.get("chat_id") or "").strip()

                if channel in _telegram_channels:
                    # Telegram: collect bot_token + chat_id for direct API delivery
                    bot_token = str(params.get("bot_token") or params.get("token") or "").strip()
                    if bot_token and chat_id:
                        _tg_direct.setdefault(bot_token, set()).add(chat_id)
                    continue

                if chat_id:
                    # Non-Telegram IM: merge into project subs
                    bucket = set(subs.get(channel, []))
                    bucket.add(chat_id)
                    subs[channel] = sorted(bucket)
                else:
                    # No chat_id: try apprise URL for direct delivery
                    apprise_url = str(getattr(item, "apprise_url", "") or "").strip()
                    url = build_apprise_url(channel, chat_id=chat_id, params=params, apprise_url=apprise_url)
                    if url:
                        apprise_targets.append((channel, url))
        except Exception as e:
            logger.warning(f"notify_push: failed to resolve linked subscriptions: {e}")

        # Also collect Telegram bot token from config for direct project subs
        _tg_config_token = self._get_telegram_bot_token()

        resolved_channels = [str(ch).strip() for ch in (channels or []) if str(ch).strip()]
        if not resolved_channels:
            defaults = self._default_channels()
            resolved_channels = list(dict.fromkeys(defaults + list(subs.keys())))

        has_targets = bool(resolved_channels) or bool(apprise_targets) or bool(_tg_direct)
        if not has_targets:
            return t("notify.no_channels")

        sent = 0

        # 1) Deliver linked Telegram subscriptions via direct Bot API
        for bot_token, chat_ids in _tg_direct.items():
            for cid in chat_ids:
                try:
                    await asyncio.to_thread(self._send_telegram_sync, bot_token, cid, text)
                    sent += 1
                except Exception as e:
                    logger.warning(f"notify_push telegram direct failed for {cid}: {e}")

        # 2) Deliver apprise-only targets (no chat_id, only apprise URL)
        for channel_name, target_url in apprise_targets:
            try:
                title, body = self._split_title_and_body(text)
                ok, err = await asyncio.to_thread(send_apprise_notification, target_url, title, body or text)
                if not ok:
                    raise RuntimeError(err)
                sent += 1
            except Exception as e:
                logger.warning(f"notify_push apprise failed for {channel_name}: {e}")

        # 3) Deliver project subscriptions (direct subs from subscriptions.json)
        _tg_direct_chat_ids = set()
        for ids in _tg_direct.values():
            _tg_direct_chat_ids.update(ids)

        no_subs_channels = []
        for ch in resolved_channels:
            chat_ids = subs.get(ch, [])
            if not chat_ids:
                no_subs_channels.append(ch)
                continue
            for chat_id in chat_ids:
                # Skip Telegram chat_ids already sent via direct API
                if ch in _telegram_channels and chat_id in _tg_direct_chat_ids:
                    continue
                try:
                    if ch == "serverchan":
                        await asyncio.to_thread(self._send_serverchan_sync, chat_id, text)
                    elif ch == "email":
                        await asyncio.to_thread(self._send_email_sync, chat_id, text)
                    elif ch in _telegram_channels and _tg_config_token:
                        # Direct project Telegram subs: use direct API
                        await asyncio.to_thread(self._send_telegram_sync, _tg_config_token, chat_id, text)
                    elif self.bus:
                        msg = OutboundMessage(
                            channel=ch,
                            chat_id=chat_id,
                            content=text,
                            metadata={"priority": priority, "source": "notify_push"},
                        )
                        await self.bus.publish_outbound(msg)
                    else:
                        raise RuntimeError(f"no delivery method for {ch}:{chat_id}")
                    sent += 1
                except Exception as e:
                    logger.warning(f"notify_push failed for {ch}:{chat_id}: {e}")

        if dedupe_key and sent > 0:
            dedupe = self._load_dedupe()
            dedupe[dedupe_key] = datetime.now().isoformat()
            self._save_dedupe(dedupe)

        if sent == 0:
            return t("notify.no_subscribers", channels=no_subs_channels)
        return t("notify.sent", count=sent)
