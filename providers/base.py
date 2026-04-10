"""Base LLM provider interface."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: Optional[str]
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    Implementations should handle the specifics of each provider's API
    while maintaining a consistent interface.
    """

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None):
        self.api_key = api_key
        self.api_base = self._normalize_api_base(api_base)

    @staticmethod
    def _normalize_api_base(api_base: Optional[str]) -> Optional[str]:
        """归一化 api_base：去掉尾部斜杠和 /v1，各 provider 按协议自行拼接。"""
        if not api_base:
            return api_base
        api_base = api_base.rstrip("/")
        if api_base.endswith("/v1"):
            api_base = api_base[:-3]
        return api_base

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        pass

    @staticmethod
    def _sanitize_messages(messages: list) -> list:
        """Remove orphaned tool result messages whose tool_call_id
        has no matching tool_use in any preceding assistant message,
        and strip dangling tool_calls entries from assistant messages
        that have no corresponding tool result."""
        # Collect all valid tool_call_ids from assistant messages
        valid_call_ids = set()
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    valid_call_ids.add(tc.get("id"))

        # Collect all tool_call_ids that actually have a tool result
        answered_ids = set()
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id"):
                answered_ids.add(m["tool_call_id"])

        cleaned = []
        for m in messages:
            if m.get("role") == "tool":
                # Drop orphaned tool results (no matching assistant tool_call)
                if m.get("tool_call_id") not in valid_call_ids:
                    _logger.debug(f"[Sanitize] Dropping orphaned tool result: {m.get('tool_call_id')}")
                    continue
            elif m.get("role") == "assistant" and m.get("tool_calls"):
                # Strip tool_calls whose results are missing
                kept = [tc for tc in m["tool_calls"] if tc.get("id") in answered_ids]
                if len(kept) != len(m["tool_calls"]):
                    _logger.debug(f"[Sanitize] Trimmed {len(m['tool_calls']) - len(kept)} dangling tool_calls from assistant message")
                    m = dict(m)  # shallow copy to avoid mutating original
                    if kept:
                        m["tool_calls"] = kept
                    else:
                        # No tool_calls left — remove the key entirely
                        m = {k: v for k, v in m.items() if k != "tool_calls"}
            cleaned.append(m)
        return cleaned

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass
