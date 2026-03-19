"""Global chat contact registry for IM push subscriptions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class ChatContactRegistry:
    """Lightweight global contact registry stored at workspace/.chat_contacts.json.

    Records IM contacts (channel + chat_id) seen across all projects so that
    push subscriptions can be auto-populated without manual chat_id entry.
    """

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._file = workspace / ".chat_contacts.json"
        # In-memory cache: keys already persisted on disk
        self._known_keys: set[str] = set()
        self._load_known_keys()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(channel: str, chat_id: str) -> str:
        return f"{channel}:{chat_id}"

    def _load_known_keys(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._known_keys = set(data.keys())
        except Exception:
            pass

    def _read(self) -> Dict[str, Any]:
        if not self._file.exists():
            return {}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to read chat contacts: {e}")
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_contact(
        self,
        channel: str,
        chat_id: str,
        sender_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Record or update a contact. Returns True if this is a *new* contact."""
        channel = str(channel).strip()
        chat_id = str(chat_id).strip()
        if not channel or not chat_id:
            return False

        key = self._make_key(channel, chat_id)
        now = datetime.now().isoformat()

        # If file was deleted externally, reset the in-memory cache
        if self._known_keys and not self._file.exists():
            self._known_keys.clear()

        is_new = key not in self._known_keys
        if not is_new:
            return False

        data = self._read()
        entry = data.get(key, {})
        if not entry:
            entry = {
                "channel": channel,
                "chat_id": chat_id,
                "sender_id": str(sender_id).strip(),
                "first_seen": now,
            }
        entry["last_seen"] = now
        if metadata:
            entry.setdefault("metadata", {}).update(metadata)

        data[key] = entry
        self._write(data)
        self._known_keys.add(key)
        logger.info(f"New chat contact recorded: {key}")
        return True

    def get_contacts(self) -> Dict[str, Any]:
        """Return all known contacts."""
        return self._read()

    def delete_contact(self, channel: str, chat_id: str) -> bool:
        """Remove a contact. Returns True if the contact existed and was deleted."""
        channel = str(channel).strip()
        chat_id = str(chat_id).strip()
        if not channel or not chat_id:
            return False
        key = self._make_key(channel, chat_id)
        data = self._read()
        if key not in data:
            return False
        del data[key]
        self._write(data)
        self._known_keys.discard(key)
        logger.info(f"Chat contact deleted: {key}")
        return True

    def get_contacts_by_channel(self, channel: str) -> List[Dict[str, Any]]:
        """Return contacts filtered by channel name."""
        channel = str(channel).strip()
        return [
            v for v in self._read().values()
            if isinstance(v, dict) and v.get("channel") == channel
        ]
