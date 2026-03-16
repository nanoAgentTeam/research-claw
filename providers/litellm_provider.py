"""LiteLLM provider implementation for multi-provider support."""

import os
from typing import Any, Optional

import litellm
from litellm import acompletion

from providers.base import LLMProvider, LLMResponse, ToolCallRequest


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, and many other providers through
    a unified interface.
    """
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        default_model: str = "anthropic/claude-3-5-sonnet-20240620"
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        
        # Detect OpenRouter by api_key prefix or explicit api_base
        self.is_openrouter = (
            (api_key and api_key.startswith("sk-or-")) or
            (api_base and "openrouter" in api_base)
        )
        
        # Track if using custom endpoint (vLLM, etc.)
        self.is_vllm = bool(api_base) and not self.is_openrouter
        
        # Configure LiteLLM based on provider
        if api_key:
            if self.is_openrouter:
                # OpenRouter mode - set key
                os.environ["OPENROUTER_API_KEY"] = api_key
            elif self.is_vllm:
                # vLLM/custom endpoint - uses OpenAI-compatible API
                os.environ["OPENAI_API_KEY"] = api_key
            elif "anthropic" in default_model:
                os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
            elif "gemini" in default_model.lower():
                os.environ.setdefault("GEMINI_API_KEY", api_key)
            elif "zhipu" in default_model or "glm" in default_model or "zai" in default_model:
                os.environ.setdefault("ZHIPUAI_API_KEY", api_key)
            elif "groq" in default_model:
                os.environ.setdefault("GROQ_API_KEY", api_key)
            
            # Default fallback for OpenAI compatible APIs (including Qwen)
            else:
                os.environ["OPENAI_API_KEY"] = api_key
                if api_base:
                    os.environ["OPENAI_API_BASE"] = api_base
        
        if api_base:
            litellm.api_base = api_base
        
        # Disable LiteLLM logging noise
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
        """
        Send a chat completion request via LiteLLM.
        """
        model = model or self.default_model

        # Sanitize messages: remove orphaned tool results / dangling tool_calls
        # that can arise from context resets or compression.
        messages = self._sanitize_messages(messages)

        # For OpenRouter, prefix model name if not already prefixed
        if self.is_openrouter and not model.startswith("openrouter/"):
            model = f"openrouter/{model}"

        # For Zhipu/Z.ai, ensure prefix is present
        # Handle cases like "glm-4.7-flash" -> "zhipu/glm-4.7-flash"
        if ("glm" in model.lower() or "zhipu" in model.lower()) and not (
            model.startswith("zhipu/") or
            model.startswith("zai/") or
            model.startswith("openrouter/")
        ):
            model = f"zhipu/{model}"

        # For vLLM, use hosted_vllm/ prefix per LiteLLM docs
        # Convert openai/ prefix to hosted_vllm/ if user specified it
        if self.is_vllm:
            model = f"hosted_vllm/{model}"

        # Prepare kwargs first
        all_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            all_kwargs["max_tokens"] = max_tokens
        all_kwargs.update(kwargs)

        # For Qwen via DashScope compatible API, treat as OpenAI compatible
        # but let litellm handle it as openai/model_name
        if "qwen" in model.lower() and not model.startswith("openai/"):
             model = f"openai/{model}"
             all_kwargs["model"] = model
             all_kwargs["api_base"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
             all_kwargs["api_key"] = self.api_key
             # Important: Remove tool_choice for Qwen as it can cause issues
             # We handle tool_choice logic below, so we can set a flag here to skip it later if needed
             # or just rely on litellm's handling. For now, let's keep standard behavior.

        # For Gemini, ensure gemini/ prefix if not already present
        if "gemini" in model.lower() and not model.startswith("gemini/"):
            model = f"gemini/{model}"
            all_kwargs["model"] = model

        # Pass api_base directly for custom endpoints (vLLM, etc.)
        if self.api_base and "api_base" not in all_kwargs:
            all_kwargs["api_base"] = self.api_base

        if tools:
            all_kwargs["tools"] = tools
            # Only set tool_choice if not explicitly disabled for Qwen above (if we were to add that logic)
            # Some Qwen versions struggle with explicit "auto"
            all_kwargs["tool_choice"] = "auto"

        try:
            response = await acompletion(**all_kwargs)
            return self._parse_response(response)
        except Exception as e:
            # Return error as content for graceful handling
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
                # Parse arguments from JSON string if needed
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
