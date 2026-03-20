import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from providers.base import LLMResponse
from providers.litellm_provider import LiteLLMProvider
from providers.openai_provider import OpenAIProvider


class TestOpenAIProviderMaxTokens(unittest.IsolatedAsyncioTestCase):
    async def test_default_max_tokens_is_capped_for_non_reasoning_models(self):
        provider = OpenAIProvider(api_key="x", api_base="https://example.com", default_model="gpt-4o-mini")

        create_mock = AsyncMock(return_value=SimpleNamespace())
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
        )

        with patch.object(provider, "get_client", return_value=fake_client), patch.object(
            provider, "_parse_response", return_value=LLMResponse(content="ok")
        ):
            await provider.chat(messages=[{"role": "user", "content": "hi"}], retries=1)

        kwargs = create_mock.await_args.kwargs
        self.assertEqual(kwargs.get("max_tokens"), 8192)

    async def test_explicit_max_tokens_above_cap_is_clamped(self):
        provider = OpenAIProvider(api_key="x", api_base="https://example.com", default_model="gpt-4o-mini")

        create_mock = AsyncMock(return_value=SimpleNamespace())
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
        )

        with patch.object(provider, "get_client", return_value=fake_client), patch.object(
            provider, "_parse_response", return_value=LLMResponse(content="ok")
        ):
            await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=20000,
                retries=1,
            )

        kwargs = create_mock.await_args.kwargs
        self.assertEqual(kwargs.get("max_tokens"), 8192)


class TestLiteLLMProviderRouting(unittest.IsolatedAsyncioTestCase):
    async def test_custom_endpoint_prefers_protocol_over_model_name(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = LiteLLMProvider(
                api_key="k",
                api_base="https://proxy.example/v1",
                default_model="gemini-3.1-pro-high",
                provider_type="anthropic",
            )

            completion_mock = AsyncMock(return_value=SimpleNamespace())
            with patch("providers.litellm_provider.acompletion", new=completion_mock), patch.object(
                provider, "_parse_response", return_value=LLMResponse(content="ok")
            ):
                await provider.chat(messages=[{"role": "user", "content": "hi"}])

            kwargs = completion_mock.await_args.kwargs
            self.assertEqual(kwargs.get("model"), "anthropic/gemini-3.1-pro-high")
            self.assertEqual(kwargs.get("api_base"), "https://proxy.example/v1")
            self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), "k")
            self.assertIsNone(os.environ.get("GEMINI_API_KEY"))

    async def test_native_routing_prefers_protocol_over_model_name(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = LiteLLMProvider(
                api_key="k",
                api_base=None,
                default_model="gemini-3.1-pro-high",
                provider_type="anthropic",
            )

            completion_mock = AsyncMock(return_value=SimpleNamespace())
            with patch("providers.litellm_provider.acompletion", new=completion_mock), patch.object(
                provider, "_parse_response", return_value=LLMResponse(content="ok")
            ):
                await provider.chat(messages=[{"role": "user", "content": "hi"}])

            kwargs = completion_mock.await_args.kwargs
            self.assertEqual(kwargs.get("model"), "anthropic/gemini-3.1-pro-high")
            self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), "k")
            self.assertIsNone(os.environ.get("GEMINI_API_KEY"))


if __name__ == "__main__":
    unittest.main()
