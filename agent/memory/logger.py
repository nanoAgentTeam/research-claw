"""Session memory logger."""

import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING
from loguru import logger
from bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from config.registry import ConfigRegistry

class HistoryLogger:
    """
    Logs chat history to local JSONL files (Session Memory).

    Paths are configurable via ConfigRegistry (vfs.json memory_paths).
    Falls back to hardcoded defaults for backward compatibility.
    """

    def __init__(self, workspace: Path, registry: Optional["ConfigRegistry"] = None):
        self.workspace = workspace

        # Read paths from config or use defaults
        if registry:
            history_rel = registry.get_memory_path("history_dir", "memory/history")
            trajectory_rel = registry.get_memory_path("trajectory_dir", "memory/trajectories")
        else:
            history_rel = "memory/history"
            trajectory_rel = "memory/trajectories"

        self.history_dir = workspace / history_rel
        self.trajectory_dir = workspace / trajectory_rel
        self._lock = asyncio.Lock()

    async def log_trajectory(self, trajectory_data: dict[str, Any]):
        """
        Logs a full interaction trajectory (turn) to a standalone JSON file.
        Also appends a lightweight summary line to token_usage.jsonl for fast querying.
        """
        if not self.trajectory_dir.exists():
            self.trajectory_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        chat_id = trajectory_data.get("chat_id", "unknown")
        filename = f"turn_{timestamp}_{chat_id}.json"

        log_file = self.trajectory_dir / filename

        try:
            # We use standard synchronous write within the lock context for safety,
            # as these are small to medium sized JSONs.
            async with self._lock:
                with open(log_file, "w", encoding="utf-8") as f:
                    json.dump(trajectory_data, f, ensure_ascii=False, indent=2)

                # Append token usage index line (lightweight, for fast aggregation)
                token_usage = trajectory_data.get("token_usage")
                if token_usage:
                    index_line = json.dumps({
                        "timestamp": trajectory_data.get("timestamp", ""),
                        "session_id": trajectory_data.get("session_id", ""),
                        "chat_id": chat_id,
                        "role": trajectory_data.get("role", ""),
                        "mode": trajectory_data.get("mode", ""),
                        "prompt_tokens": token_usage.get("prompt_tokens", 0),
                        "completion_tokens": token_usage.get("completion_tokens", 0),
                        "total_tokens": token_usage.get("total_tokens", 0),
                        "duration_ms": trajectory_data.get("duration_ms", 0),
                        "inbound": (str(trajectory_data.get("inbound", "") or ""))[:80],
                    }, ensure_ascii=False)
                    index_file = self.trajectory_dir / "token_usage.jsonl"
                    with open(index_file, "a", encoding="utf-8") as f:
                        f.write(index_line + "\n")

            logger.debug(f"Saved trajectory to {log_file}")
        except Exception as e:
            logger.error(f"Failed to log trajectory: {e}")

    async def log_inbound(self, msg: InboundMessage):
        """Log an inbound user message."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "inbound",
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "content": msg.content,
            "media": msg.media if msg.media else [],
            "metadata": msg.metadata
        }
        await self._append_to_log(entry)

    async def log_outbound(self, msg: OutboundMessage):
        """Log an outbound agent message."""
        # Skip chunks to avoid spamming the log, only log full messages or finalized chunks
        if msg.is_chunk and not msg.metadata.get("final", False):
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "outbound",
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "content": msg.content,
            "media": msg.media if msg.media else [],
            "metadata": msg.metadata
        }
        await self._append_to_log(entry)

    async def get_recent_history(self, chat_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Get recent history for a specific chat_id.
        Returns OpenAI-compatible message list: [{"role": "user", "content": "..."}, ...]

        [G-M9] Reads across month boundaries when current month has insufficient entries.
        """
        from datetime import timedelta

        now = datetime.now()
        month_str = now.strftime("%Y_%m")
        log_file = self.history_dir / f"chat_{month_str}.jsonl"

        # Collect candidate log files: current month + previous month
        log_files = []
        if log_file.exists():
            log_files.append(log_file)
        # Previous month fallback
        prev_month = (now.replace(day=1) - timedelta(days=1))
        prev_month_str = prev_month.strftime("%Y_%m")
        prev_log_file = self.history_dir / f"chat_{prev_month_str}.jsonl"
        if prev_log_file.exists():
            log_files.append(prev_log_file)

        if not log_files:
            return []

        messages = []

        try:
            # [G-M9] Read from multiple log files (current month first, then previous)
            chat_entries = []
            for lf in log_files:
                if len(chat_entries) >= limit:
                    break
                try:
                    lines = lf.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue

                for line in reversed(lines):
                    if not line.strip(): continue
                    try:
                        entry = json.loads(line)
                        if entry.get("chat_id") == chat_id:
                            if entry.get("type") == "system" and entry.get("content") == "[SESSION_RESET]":
                                 break
                            chat_entries.append(entry)
                            if len(chat_entries) >= limit:
                                break
                    except json.JSONDecodeError:
                        continue

            for entry in reversed(chat_entries):
                role = "user" if entry["type"] == "inbound" else "assistant"
                content = entry.get("content", "")
                if content:
                    # Supplement media paths not already present in content text
                    media = entry.get("media") or []
                    if media:
                        missing = [p for p in media if p not in content]
                        if missing:
                            tags = " ".join(f"[attachment: {p}]" for p in missing)
                            content = f"{content}\n{tags}"
                    messages.append({"role": role, "content": content})

        except Exception as e:
            logger.warning(f"Error reading history: {e}")

        return messages

    async def count_messages(self, chat_id: str) -> int:
        """
        Count the number of messages for a specific chat_id in the current log.
        """
        month_str = datetime.now().strftime("%Y_%m")
        log_file = self.history_dir / f"chat_{month_str}.jsonl"

        if not log_file.exists():
            return 0

        count = 0
        try:
            # We just need to count relevant lines.
            # Reading lines is simple enough for now.
            lines = log_file.read_text(encoding="utf-8").splitlines()

            for line in lines:
                if not line.strip(): continue
                try:
                    # Quick string check optimization before JSON parse
                    if f'"{chat_id}"' not in line:
                        continue

                    entry = json.loads(line)
                    if entry.get("chat_id") == chat_id:
                        # Reset logic: if we hit a reset, previous counts might be irrelevant if we were strictly context window counting.
                        # But count_messages is usually used for total activity or since last reset.
                        # If get_recent_history stops at RESET, count_messages should probably too if it's for context length check.
                        # However, iterating forward: we count all.
                        # Iterating backward (like get_recent_history) would be better to find the reset point.
                        # Let's count *all* for now as a simple metric, or *since last reset*.

                        # Let's count all in current file for simplicity.
                        if entry.get("type") == "system" and entry.get("content") == "[SESSION_RESET]":
                             count = 0 # Reset count
                             continue

                        count += 1
                except:
                    continue
        except Exception:
            return 0

        return count

    async def log_reset(self, channel: str, chat_id: str):
        """Log a session reset marker."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "system",
            "channel": channel,
            "chat_id": chat_id,
            "content": "[SESSION_RESET]",
            "metadata": {}
        }
        await self._append_to_log(entry)

    async def _append_to_log(self, entry: dict[str, Any]):
        """Append entry to the current month's log file."""
        # Create directory if needed
        if not self.history_dir.exists():
            self.history_dir.mkdir(parents=True, exist_ok=True)

        # File name: chat_2023_10.jsonl
        month_str = datetime.now().strftime("%Y_%m")
        log_file = self.history_dir / f"chat_{month_str}.jsonl"

        json_line = json.dumps(entry, ensure_ascii=False)

        async with self._lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json_line + "\n")
