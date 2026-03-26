"""LiteLLM provider implementation for Anthropic protocol support."""

import os
from typing import Any, Optional

import litellm
from litellm import acompletion

from providers.base import LLMProvider, LLMResponse, ToolCallRequest


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for Anthropic protocol.

    Used when the user selects Anthropic protocol in WebUI.
    Handles api_key/api_base passthrough and model prefix routing.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        default_model: str = "claude-3-5-sonnet-20240620",
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model

        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key

        # 基类已归一化去掉 /v1，litellm 走 Anthropic 协议会自动拼 /v1/messages
        if self.api_base:
            litellm.api_base = self.api_base

        litellm.suppress_debug_info = True

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> LLMResponse:
        """Send a chat completion request via LiteLLM using Anthropic protocol."""
        model = model or self.default_model

        messages = self._sanitize_messages(messages)

        # Ensure anthropic/ prefix for litellm routing
        if not model.startswith("anthropic/"):
            model = f"anthropic/{model}"

        all_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            all_kwargs["max_tokens"] = max_tokens
        all_kwargs.update(kwargs)

        if self.api_base:
            all_kwargs["api_base"] = self.api_base

        if tools:
            all_kwargs["tools"] = tools
            all_kwargs["tool_choice"] = "auto"

        try:
            response = await acompletion(**all_kwargs)
            return self._parse_response(response)
        except Exception as e:
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    import json
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}

                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
