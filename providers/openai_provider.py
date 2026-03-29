"""OpenAI provider implementation."""

import re
import json
from typing import Any, Optional
from loguru import logger
from openai import AsyncOpenAI
from providers.base import LLMProvider, LLMResponse, ToolCallRequest


class OpenAIProvider(LLMProvider):
    """
    LLM provider using official OpenAI client.
    Compatible with OpenAI, Qwen (DashScope), DeepSeek, and other OpenAI-compatible APIs.
    """
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        default_model: str = "gpt-3.5-turbo"
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self._client: Optional[AsyncOpenAI] = None
        self._api_key = api_key
        # 使用归一化后的 api_base，补上 /v1（OpenAI 协议需要）
        self._api_base = f"{self.api_base}/v1" if self.api_base else None

    def get_client(self) -> AsyncOpenAI:
        """Lazily initialize the AsyncOpenAI client."""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._api_base,
                timeout=300.0 # Increased from 60s for large context windows
            )
        return self._client

    def _extract_xml_tool_calls(self, content: str) -> tuple[str, list[ToolCallRequest]]:
        """
        Extract XML-formatted tool calls from content.
        Returns (cleaned_content, list_of_tool_calls).
        
        Format handled:
        <tool_call>
        <function=name>
        <parameter=key>value</parameter>
        </function>
        </tool_call>
        """
        tool_calls = []
        
        # Regex to find all tool_call blocks
        # Using dotall to match across newlines
        tool_call_pattern = re.compile(r"<tool_call>\s*<function=(?P<name>\w+)>(?P<body>.*?)</function>\s*</tool_call>", re.DOTALL)
        
        # Find all matches first
        matches = list(tool_call_pattern.finditer(content))
        if not matches:
             return content, []

        for match in matches:
            name = match.group("name")
            body = match.group("body")
            
            args = {}
            # Parse parameters inside the body
            param_pattern = re.compile(r"<parameter=(?P<key>\w+)>(?P<value>.*?)</parameter>", re.DOTALL)
            for param_match in param_pattern.finditer(body):
                key = param_match.group("key")
                value = param_match.group("value").strip()
                # Try to infer type (int/float/bool) if possible, otherwise keep as string
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                elif value.isdigit():
                    value = int(value)
                else:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
                args[key] = value
            
            # Create a unique ID (random or based on name)
            import uuid
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            
            tool_calls.append(ToolCallRequest(
                id=call_id,
                name=name,
                arguments=args
            ))

        # We do NOT remove the XML from content for now, to ensure the model 'remembers' it called the tool.
        # If we remove it, we must replace it with something or structure the message history carefully.
        # For simplicity in this "fallback" mode, we treat the XML as the "content" of the assistant's thought process.
        # The tool calls are appended as a separate signal to the runtime.
        
        # However, to avoid "double vision" where the user sees XML and then the runtime sees ToolCall,
        # usually runtimes hide the ToolCall.
        # Since we already streamed the XML to the user (via Feishu), they saw it.
        # Returning cleaned_content=None tells the loop "The content is just these tool calls" (if we stripped everything).
        
        # Strategy: 
        # 1. Keep the original content as the "thought process" for history.
        # 2. Return the extracted tool calls for execution.
        
        return content, tool_calls

    # Reasoning models that should NOT receive a max_tokens parameter.
    # These models auto-adjust output length; passing max_tokens causes
    # context_length_exceeded errors on the StepFun API.
    _REASONING_MODELS: set[str] = {
        "step-3.5-flash", "step-3",
    }

    async def _close_stream(self, stream: Any) -> None:
        """Best-effort close for streaming responses to avoid hanging sockets."""
        close = getattr(stream, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception as e:
                logger.debug(f"Ignoring stream close failure: {e}")

    def _is_reasoning_model(self, model: str) -> bool:
        """Check if the model is a reasoning model that rejects max_tokens."""
        model_lower = (model or "").lower()
        return any(rm in model_lower for rm in self._REASONING_MODELS)

    @staticmethod
    def _extract_error_text(error: Any) -> str:
        """Extract the most useful error text from OpenAI-compatible exceptions."""
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            payload = body.get("error")
            if isinstance(payload, dict):
                message = payload.get("message")
                code = payload.get("code") or payload.get("type")
                if message and code:
                    return f"{code}: {message}"
                if message:
                    return str(message)
        return str(error) or f"Unknown error (type: {type(error).__name__})"

    @staticmethod
    def _is_input_inspection_error(error: Any) -> bool:
        """Detect terminal upstream prompt-filter/input-inspection failures."""
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            payload = body.get("error")
            if isinstance(payload, dict):
                code = str(payload.get("code") or payload.get("type") or "").lower()
                if code == "data_inspection_failed":
                    return True
                message = str(payload.get("message") or "").lower()
                if "inappropriate content" in message or "content inspection" in message:
                    return True
        return False

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        on_token: Optional[Any] = None,
        retries: int = 3,  # Added retry count
    ) -> LLMResponse:
        """
        Send a chat completion request with retry logic.
        """
        model = model or self.default_model

        # Sanitize messages: remove orphaned tool results / dangling tool_calls
        # that can arise from context resets or compression.
        messages = self._sanitize_messages(messages)

        # Reasoning models (e.g. step-3.5-flash) auto-adjust output length;
        # passing max_tokens causes context_length_exceeded on StepFun API.
        is_reasoning = self._is_reasoning_model(model)

        # ... (setup kwargs)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if not is_reasoning and max_tokens is not None:
            # Only pass max_tokens when explicitly set.
            # When None, let the model use its own default output limit.
            kwargs["max_tokens"] = max(1, int(max_tokens))
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
            
        logger.debug(f"Sending request to LLM with {len(messages)} messages and {len(tools) if tools else 0} tools.")
        # logger.debug(f"Request kwargs: {json.dumps({k: v for k, v in kwargs.items() if k != 'api_key'}, indent=2)}")
            
        if on_token:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            
        import asyncio
        import openai
        
        last_exception = None
        for attempt in range(retries):
            try:
                # ... (rest of the logic)
                if on_token:
                    # Streaming mode
                    logger.debug(f"Initiating streaming chat with model: {model}...")
                    stream = await self.get_client().chat.completions.create(**kwargs)
                    logger.debug("Stream started, waiting for tokens...")
                    
                    accumulated_content = []
                    tool_calls_data = [] # List of dicts to accumulate tool chunks
                    finish_reason = "stop"
                    stream_usage = {}
                    stream_chunk_timeout = max(
                        0.01, float(getattr(self, "stream_chunk_timeout", 120.0))
                    )

                    while True:
                        try:
                            chunk = await asyncio.wait_for(
                                stream.__anext__(),
                                timeout=stream_chunk_timeout,
                            )
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError as e:
                            await self._close_stream(stream)
                            raise TimeoutError(
                                f"Stream timeout after {stream_chunk_timeout}s without receiving a chunk"
                            ) from e

                        # Capture usage from final chunk (sent when stream_options.include_usage=True)
                        if hasattr(chunk, 'usage') and chunk.usage:
                            stream_usage = {
                                "prompt_tokens": chunk.usage.prompt_tokens,
                                "completion_tokens": chunk.usage.completion_tokens,
                                "total_tokens": chunk.usage.total_tokens,
                            }

                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta

                        # Handle reasoning/thinking tokens (e.g. step-3.5-flash, DeepSeek-R1)
                        reasoning_piece = getattr(delta, "reasoning_content", None)
                        if reasoning_piece and on_token:
                            on_token(reasoning_piece)

                        # Handle Content
                        if delta.content:
                            content_piece = delta.content
                            accumulated_content.append(content_piece)
                            if on_token:
                                on_token(content_piece)
                            # Internal log for debugging
                            # logger.debug(f"Received token: {content_piece}")
                                
                        # Handle Tool Calls (Accumulate chunks)
                        if delta.tool_calls:
                            if attempt == 0 and not tool_calls_data:
                                logger.debug("Received first tool call chunk")
                            for tc_chunk in delta.tool_calls:
                                if tc_chunk.index is not None:
                                    index = tc_chunk.index
                                else:
                                    # Some providers (DashScope/Qwen, DeepSeek, etc.) don't set
                                    # tc_chunk.index for parallel tool calls. Detect new tool calls
                                    # by checking for a new id or a new function name to avoid
                                    # concatenating multiple calls into the same slot.
                                    is_new_call = False
                                    if tc_chunk.id and tool_calls_data:
                                        # A new tool call id means a new tool call
                                        existing_ids = {tc["id"] for tc in tool_calls_data if tc["id"]}
                                        if tc_chunk.id not in existing_ids:
                                            is_new_call = True
                                    if not is_new_call and tc_chunk.function and tc_chunk.function.name and tool_calls_data:
                                        # A new function name appearing while the last slot already
                                        # has a name means this is a different tool call
                                        last = tool_calls_data[-1]
                                        if last["function"]["name"] and last["function"]["name"] != tc_chunk.function.name:
                                            is_new_call = True

                                    if is_new_call:
                                        index = len(tool_calls_data)
                                    elif tool_calls_data:
                                        index = len(tool_calls_data) - 1
                                    else:
                                        index = 0

                                # Extend list if needed
                                while len(tool_calls_data) <= index:
                                    tool_calls_data.append({
                                        "id": "",
                                        "function": {"name": "", "arguments": ""}
                                    })

                                # Append parts
                                if tc_chunk.id:
                                    tool_calls_data[index]["id"] += tc_chunk.id
                                if tc_chunk.function:
                                    if tc_chunk.function.name:
                                        tool_calls_data[index]["function"]["name"] += tc_chunk.function.name
                                    if tc_chunk.function.arguments:
                                        tool_calls_data[index]["function"]["arguments"] += tc_chunk.function.arguments
                            # Notify progress for tool call chunks too
                            if on_token:
                                on_token("")

                        if chunk.choices[0].finish_reason:
                            finish_reason = chunk.choices[0].finish_reason

                    await self._close_stream(stream)
                    
                    # Reconstruct final response object
                    full_content = "".join(accumulated_content) if accumulated_content else ""
                    
                    # Reconstruct ToolCallRequests from native tool calls
                    final_tool_calls = []
                    for tc_data in tool_calls_data:
                        # Skip empty tool calls
                        if not tc_data["function"]["name"]:
                            continue
                            
                        try:
                            args = json.loads(tc_data["function"]["arguments"])
                        except json.JSONDecodeError:
                            args = {"raw": tc_data["function"]["arguments"]}
                            
                        final_tool_calls.append(ToolCallRequest(
                            id=tc_data["id"],
                            name=tc_data["function"]["name"],
                            arguments=args
                        ))
                    
                    # FALLBACK: Check for XML tool calls in content if no native calls found
                    # (Some models like StepFun output XML in content instead of native tool calls)
                    if not final_tool_calls and full_content and "<tool_call>" in full_content:
                        cleaned_content, xml_calls = self._extract_xml_tool_calls(full_content)
                        if xml_calls:
                            final_tool_calls.extend(xml_calls)
                            # Update full_content logic:
                            # We keep the original XML in history so the model knows what it did.
                            # We rely on the AgentLoop to handle the "assistant message" creation.
                            # If we set full_content to None, the loop might think there's no text.
                            # If we leave it as is, the loop will save the XML text + the tool calls.
                            # This is safer for consistency.
                            finish_reason = "tool_calls" # Force finish reason

                    return LLMResponse(
                        content=full_content,
                        tool_calls=final_tool_calls,
                        finish_reason=finish_reason,
                        usage=stream_usage 
                    )

                else:
                    # Non-streaming mode (Standard)
                    response = await self.get_client().chat.completions.create(**kwargs)
                    llm_resp = self._parse_response(response)
                    
                    # Apply fallback parsing here too
                    if not llm_resp.tool_calls and llm_resp.content and "<tool_call>" in llm_resp.content:
                         cleaned_content, xml_calls = self._extract_xml_tool_calls(llm_resp.content)
                         if xml_calls:
                             llm_resp.tool_calls = xml_calls
                             # llm_resp.content = cleaned_content if cleaned_content else None # Keep content
                             llm_resp.finish_reason = "tool_calls"
                    
                    return llm_resp
                            
            except (TimeoutError, openai.RateLimitError, openai.InternalServerError, openai.APITimeoutError, openai.APIConnectionError) as e:
                last_exception = e
                # httpx.ReadTimeout and ConnectTimeout often bubble up here or under APIConnectionError
                # We normalize the check
                e_str = str(e).lower()
                is_transient = any(phrase in e_str for phrase in ["timeout", "429", "500", "502", "503", "connection reset", "readtimeout"])
                
                if attempt < retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Retriable LLM error ({type(e).__name__}): {e}. Attempt {attempt+1}/{retries}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    error_msg = str(e) or f"Unknown retriable error (type: {type(e).__name__})"
                    return LLMResponse(
                        content=f"Error calling LLM after {retries} attempts: {error_msg}",
                        finish_reason="error",
                    )
            except Exception as e:
                last_exception = e
                import traceback as _tb
                logger.error(f"LLM exception traceback:\n{_tb.format_exc()}")
                # Fallback check for transient phrases in generic exceptions
                e_str = self._extract_error_text(e).lower()
                if any(phrase in e_str for phrase in ["timeout", "429", "500", "502", "503", "connection reset"]):
                    if attempt < retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Detected transient LLM error in fallback ({type(e).__name__}): {e}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                error_msg = self._extract_error_text(e)
                if self._is_input_inspection_error(e):
                    error_msg = (
                        "Terminal LLM error (data_inspection_failed): "
                        f"Upstream content inspection rejected the request. {error_msg}"
                    )
                return LLMResponse(
                    content=f"Error calling LLM: {error_msg}",
                    finish_reason="error",
                )
        
        error_msg = str(last_exception) or "Unknown error"
        return LLMResponse(
            content=f"Error calling LLM after {retries} attempts: {error_msg}",
            finish_reason="error",
        )
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse OpenAI response into our standard format."""
        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string
                import json
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {"raw": tc.function.arguments}
                
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))
        
        usage = {}
        if response.usage:
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
