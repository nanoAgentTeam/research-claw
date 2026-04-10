"""
LLM Agent 引擎

本模块实现了基于 LLM 的 AI Agent 执行引擎，支持流式输出和工具调用。

核心类：
    - AgentEngine: AI Agent 引擎，执行 ReAct 循环（推理-行动-观察）

核心功能：
    - 流式对话：实时生成响应内容
    - 工具调用：自动执行 Function Calling
    - 中间件管道：历史裁剪、步骤压缩、预算管理

设计理念：
    - 流式优先：所有响应均为 Generator
    - 无状态引擎：每次调用创建新会话
    - 中间件可组合：支持自定义中间件链

依赖关系：
    - 被依赖: core.analysis.worker
    - 依赖: core.llm.types, core.llm.providers, core.llm.middleware
"""

import json
from typing import List, Dict, Any, Optional, Generator
from core.llm.types import AgentSession, SystemPromptConfig
from core.llm.providers import LLMFactory
from core.prompts import render as render_prompt
from core.llm.middleware import (
    StrategyMiddleware,
    ExecutionBudgetManager,
    HistorySummaryMiddleware, StepCompressionMiddleware, infer_context_limit,
    MAX_TOOL_OUTPUT_LENGTH, TOOL_TRUNCATION_EXEMPT,
)
from core.tools.base import BaseTool
from core.utils.logger import Logger
from core.utils.langfuse_manager import observe
from core.llm.events import AgentEvent


class AgentEngine:
    """
    AI Agent 引擎

    执行基于 LLM 的智能体循环，支持工具调用、流式输出和中间件管道。

    引擎采用 ReAct（Reasoning-Acting-Observing）模式：
    1. **推理 (Reasoning)**: LLM 分析问题，决定下一步行动
    2. **行动 (Acting)**: 调用工具执行具体操作
    3. **观察 (Observing)**: 获取结果，继续推理

    属性：
        provider: LLMProvider 实例（统一的 LLM 调用接口）
        model: 模型名称（如 gpt-4, deepseek-chat）
        strategies: 中间件列表，按顺序执行
        depth: 当前递归深度

    中间件：
        - HistorySummaryMiddleware: 裁剪过旧的对话轮次
        - StepCompressionMiddleware: 压缩 ReAct 步骤
        - ExecutionBudgetManager: 限制执行预算

    典型用法：
        >>> engine = AgentEngine()
        >>> config = SystemPromptConfig(base_prompt="You are helpful.")
        >>> tools = [SearchTool(), CalculatorTool()]
        >>> for event in engine.run(
        ...     messages=[{"role": "user", "content": "Search for Python"}],
        ...     system_config=config,
        ...     tools=tools
        ... ):
        ...     if event.type == "token":
        ...         print(event.data["delta"], end="")
        ...     elif event.type == "message":
        ...         print(f"\\nMessage: {event.data}")
    """

    def __init__(self, strategies: List[StrategyMiddleware] = None, provider_key: str = None, depth: int = 0, parallel_tools: bool = True, max_parallel_workers: int = 5, provider=None, api_key: str = None, base_url: str = None, model: str = None):
        """
        初始化 Agent 引擎

        Args:
            strategies: 自定义中间件列表，None 则使用默认中间件
            provider_key: 供应商标识符，用于从配置中获取 LLM 配置。如果为 None，使用默认供应商。
            depth: 当前递归深度
            parallel_tools: 是否并行执行工具调用（默认 True）。
            max_parallel_workers: 最大并行工作线程数（默认 5）。
            provider: LLMProvider 实例（优先使用）
            api_key: 可选 API Key 覆盖（向后兼容，provider 为 None 时使用）
            base_url: 可选 Base URL 覆盖（向后兼容，provider 为 None 时使用）
            model: 可选模型名称覆盖

        默认中间件：
            - HistorySummaryMiddleware: 裁剪过旧的对话轮次
            - StepCompressionMiddleware: 步骤压缩（70% 阈值触发 L1）
            - ExecutionBudgetManager: 最多 50 次 LLM 调用（软限制）
        """
        if provider:
            self.provider = provider
        else:
            # 向后兼容：从参数创建 OpenAIProvider
            from providers.openai_provider import OpenAIProvider
            _key = api_key or LLMFactory.get_api_key(provider_key)
            _base = base_url or LLMFactory.get_base_url(provider_key)
            _model = model or LLMFactory.get_model_name(provider_key)
            self.provider = OpenAIProvider(api_key=_key, api_base=_base, default_model=_model)
        self.model = model or self.provider.get_default_model()
        compression_threshold = 0.65 if "step" in (self.model or "").lower() else 0.7
        self.strategies = strategies if strategies is not None else [
            HistorySummaryMiddleware(),
            StepCompressionMiddleware(
                model_context_limit=infer_context_limit(self.model),
                compression_threshold=compression_threshold,
            ),
            ExecutionBudgetManager()
        ]
        self.depth = depth
        self.parallel_tools = parallel_tools
        self.max_parallel_workers = max_parallel_workers

    async def _diagnose_deadlock(self, history: list[dict], on_token: Any | None = None) -> str:
        """Use LLM to diagnose and break a tool-call deadlock."""
        recent_actions = []
        for m in history[-10:]:
            tool_calls = m.get("tool_calls")
            if tool_calls:
                recent_actions.append(
                    f"Agent tried tools: {[tc['function']['name'] for tc in tool_calls]}"
                )
            elif m.get("role") == "tool":
                tool_out = str(m.get("content", ""))[:200]
                recent_actions.append(f"Tool Output: {tool_out}")

        _DIAGNOSIS_FALLBACK = (
            "You are a Meta-Cognitive System Diagnostician. An AI agent is stuck in a repetitive loop.\n\n"
            "RECENT ACTION HISTORY:\n{recent_actions}\n\n"
            "TASK:\n"
            "1. Identify EXACTLY why the agent is looping.\n"
            "2. Provide a CRITICAL INTERVENTION with a specific alternative strategy.\n"
            "3. Be brief, authoritative, and direct."
        )
        prompt = render_prompt(
            "loop_meta_diagnosis.txt", _DIAGNOSIS_FALLBACK,
            role_name="Worker", recent_actions="\n".join(recent_actions),
        )
        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                on_token=on_token,
            )
            return response.content or "System intervention: You are in a loop. Stop repeating. Try a different approach."
        except Exception as e:
            Logger.error(f"Meta-diagnosis failed: {e}")
            return "System intervention: You are in a loop. Stop repeating. Try a different approach."

    @observe(as_type="span")
    async def run(self, messages: List[Dict[str, Any]], system_config: SystemPromptConfig, tools: List[BaseTool], max_iterations: int = 10, on_step_log: callable = None, return_full_history: bool = True) -> Generator[AgentEvent, None, None]:
        """
        执行 Agent 结构化流式对话 (Async)
        """
        import asyncio
        import inspect

        self.depth += 1
        search_citations = []

        tools = list(tools)

        session = AgentSession(history=list(messages), depth=self.depth, system_config=system_config, tools=tools)
        session.metadata["history_boundary"] = len(messages)
        session.metadata["llm_provider"] = self.provider
        session.metadata["llm_model"] = self.model
        llm_tools = [t.to_openai_schema() for t in session.tools] if session.tools else None
        session.metadata["llm_tools"] = llm_tools

        # Loop detection state
        action_history: list[dict] = []
        consecutive_deadlocks = 0
        _MAX_ACTION_HISTORY = 10
        _MAX_REPEATS = 7

        try:
            for iteration in range(max_iterations):
                session.metadata["iteration_count"] = iteration + 1

                # Prepare iteration streaming callback early so middleware can reuse it.
                _token_buffer = []

                def _on_token(token):
                    _token_buffer.append(token)

                session.metadata["stream_on_token"] = _on_token

                # --- Middleware Pre-processing ---
                if self.strategies:
                    dummy_next = lambda s: None
                    for strategy in self.strategies:
                        try:
                            if hasattr(strategy, 'async_call'):
                                await strategy.async_call(session)
                            else:
                                strategy(session, dummy_next)
                        except Exception as e:
                            Logger.error(f"Middleware {strategy.__class__.__name__} failed: {e}")

                # --- LLM 调用部分 ---
                model = self.model

                # Inject iteration info and progressive urgency warnings
                # Milestone warnings (50%/60%/70%/80%/90%) trigger once each;
                # last-5 warnings trigger every iteration.
                cur = iteration + 1
                remaining = max_iterations - cur

                urgency = None
                hint = None

                if remaining < 5:
                    # Last 5 iterations — always warn
                    urgency = "CRITICAL"
                    hint = "仅剩 %d 步，立即完成当前工作并保存所有文件。" % remaining
                else:
                    # Check milestone thresholds — each fires only once
                    _milestones = [
                        (0.9, "URGENT", "已用 90% 步数，开始收尾，确保产出完整可用。"),
                        (0.8, "WARNING", "已用 80% 步数，请尽快完成核心内容。"),
                        (0.7, "NOTICE", "已用 70% 步数，注意控制进度。"),
                        (0.6, "NOTICE", "已用 60% 步数，请评估剩余工作量。"),
                        (0.5, "INFO", "已用 50% 步数。"),
                    ]
                    for threshold, level, msg in _milestones:
                        milestone_iter = int(max_iterations * threshold)
                        if cur == milestone_iter:
                            urgency = level
                            hint = msg
                            break

                iter_info = None
                if urgency and urgency in ("CRITICAL", "URGENT"):
                    _ITER_URGENT_FALLBACK = (
                        "\n[SYSTEM STATS]: Iteration {iteration}/{max_iterations}. "
                        "{urgency}: {hint}"
                    )
                    iter_info = render_prompt("engine_iteration_urgent.txt", _ITER_URGENT_FALLBACK,
                                             iteration=cur, max_iterations=max_iterations,
                                             urgency=urgency, hint=hint)
                elif urgency:
                    iter_info = (
                        "\n[SYSTEM STATS]: Iteration %d/%d. %s: %s"
                        % (cur, max_iterations, urgency, hint)
                    )

                msgs = [{"role": "system", "content": session.system_config.build()}] + session.history
                if iter_info:
                    msgs.append({"role": "system", "content": iter_info})

                # --- LLM Call via LLMProvider ---

                try:
                    response = await self.provider.chat(
                        messages=msgs,
                        model=model,
                        tools=llm_tools,
                        on_token=_on_token,
                    )
                except Exception as e:
                    error_str = str(e).lower()
                    if "context_length_exceeded" in error_str or "too long" in error_str:
                        # L2 emergency compression
                        Logger.warning("Context length exceeded, triggering L2 emergency compression...")
                        boundary = session.metadata.get("history_boundary", 0)
                        steps = session.history[boundary:]
                        head, _middle, tail = StepCompressionMiddleware._split_steps(steps)
                        session.history = session.history[:boundary] + head + tail
                        Logger.info(f"L2 recovery: {len(steps)} → {len(head) + len(tail)} step msgs")
                        continue  # retry this iteration
                    Logger.error(f"LLM API Error: {e}")
                    yield AgentEvent(type="error", data=f"LLM API Error: {e}")
                    return

                # Check for error response from provider
                if response.finish_reason == "error":
                    error_content = response.content or ""
                    error_lower = error_content.lower()
                    if "context_length_exceeded" in error_lower or "too long" in error_lower:
                        # L2 emergency compression (same as except branch)
                        Logger.warning("Context length exceeded (via finish_reason=error), triggering L2 emergency compression...")
                        boundary = session.metadata.get("history_boundary", 0)
                        steps = session.history[boundary:]
                        head, _middle, tail = StepCompressionMiddleware._split_steps(steps)
                        session.history = session.history[:boundary] + head + tail
                        Logger.info(f"L2 recovery: {len(steps)} → {len(head) + len(tail)} step msgs")
                        continue  # retry this iteration
                    yield AgentEvent(type="error", data=f"LLM Error: {error_content}")
                    return

                # Yield buffered tokens
                for delta in _token_buffer:
                    yield AgentEvent(type="token", data={"delta": delta})

                full_content = response.content or ""

                # 转换 tool_calls 格式（LLMResponse → raw dict，兼容下游 tool execution）
                tool_calls = [
                    {"id": tc.id, "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments
                    }}
                    for tc in response.tool_calls
                ]

                if not tool_calls:
                    if search_citations:
                        citation_text = "\\n\\n**参考来源：**\\n"
                        for idx, item in enumerate(search_citations, 1):
                            title = item.get("title", "No Title")
                            href = item.get("href", "#")
                            citation_text += f"{idx}. [{title}]({href})\\n"

                        full_content += citation_text
                        yield AgentEvent(type="token", data={"delta": citation_text})

                    session.history.append({"role": "assistant", "content": full_content})
                    yield AgentEvent(type="message", data=session.history[-1])

                    if on_step_log: on_step_log("assistant_response", content=full_content)
                    break

                session.history.append({
                    "role": "assistant",
                    "content": full_content or None,
                    "tool_calls": [{
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"]
                    } for tc in tool_calls]
                })
                yield AgentEvent(type="message", data=session.history[-1])

                if on_step_log: on_step_log("tool_call_request", tool_calls=tool_calls, assistant_content=full_content)

                # Yield tool call events
                yield AgentEvent(type="tool_call", data={"tool_calls": tool_calls})

                # [NEW] Event queue for real-time tool progress
                event_queue = asyncio.Queue()

                # 准备工具执行任务
                async def execute_single_tool(tc):
                    """执行单个工具调用，返回 (tc, result, fn_name, args)"""
                    fn_name = tc["function"]["name"]
                    args_str = tc["function"]["arguments"]
                    try:
                        args = json.loads(args_str)
                    except:
                        args = {}

                    tool = next((t for t in tools if t.name == fn_name), None)
                    result = None
                    if tool:
                        try:
                            # 详细的异步检测逻辑
                            is_async = inspect.iscoroutinefunction(tool.execute)
                            if not is_async and hasattr(tool.execute, '__call__'): # Handle callable objects
                                is_async = inspect.iscoroutinefunction(tool.execute.__call__)

                            # [FIX] Inject on_token for real-time progress rendering
                            tool_args = args.copy()
                            sig = inspect.signature(tool.execute)
                            if 'on_token' in sig.parameters:
                                def tool_on_token(token: str):
                                    # Put token into queue to be yielded by main loop
                                    event_queue.put_nowait(AgentEvent(type="token", data={"delta": token}))
                                tool_args["on_token"] = tool_on_token

                            if is_async:
                                result = await tool.execute(**tool_args)
                            else:
                                # 同步执行
                                result = await asyncio.to_thread(tool.execute, **tool_args)

                                # Double check
                                if inspect.isawaitable(result):
                                    result = await result
                        except Exception as e:
                            result = f"Error executing {fn_name}: {e}"
                            Logger.error(f"Tool execution failed: {e}")
                    else:
                        result = f"Error: Tool {fn_name} not found."

                    # --- Output Truncation ---
                    result_str = str(result)
                    if len(result_str) > MAX_TOOL_OUTPUT_LENGTH and fn_name not in TOOL_TRUNCATION_EXEMPT:
                        truncated_len = len(result_str)
                        approx_lines = result_str[:MAX_TOOL_OUTPUT_LENGTH].count("\n")
                        result = (
                            f"{result_str[:MAX_TOOL_OUTPUT_LENGTH]}\n\n"
                            f"... [OUTPUT TRUNCATED] (Original length: {truncated_len} chars, ~{approx_lines} lines shown). "
                            f"To read the rest, call read_file with start_line={approx_lines + 1}. "
                            f"You can also use grep to search for specific content."
                        )

                    return (tc, result, fn_name, args)

                # 执行工具调用（并行，带循环检测）
                execution_tasks = []
                for tc in tool_calls:
                    fingerprint = {"name": tc["function"]["name"], "args": tc["function"]["arguments"]}
                    repeat_count = sum(1 for h in action_history if h == fingerprint)
                    action_history.append(fingerprint)
                    if len(action_history) > _MAX_ACTION_HISTORY:
                        action_history.pop(0)

                    if repeat_count >= _MAX_REPEATS:
                        consecutive_deadlocks += 1
                        if consecutive_deadlocks >= 3:
                            async def meta_wrapper(tc=tc):
                                Logger.error(f"[CRITICAL DEADLOCK] Initiating Meta-Diagnosis...")
                                diagnosis = await self._diagnose_deadlock(session.history, on_token=_on_token)
                                return (tc, f"[CRITICAL DEADLOCK] Meta-Diagnosis: {diagnosis}",
                                        tc["function"]["name"], {})
                            execution_tasks.append(meta_wrapper())
                        else:
                            warning = (
                                f"[LOOP DETECTION]: '{tc['function']['name']}' called with identical "
                                f"arguments {repeat_count + 1} times. STOP repeating. Try a different approach."
                            )
                            async def warning_result(tc=tc, msg=warning):
                                return (tc, msg, tc["function"]["name"], {})
                            execution_tasks.append(warning_result())
                    else:
                        execution_tasks.append(execute_single_tool(tc))

                async def run_parallel_tools():
                    return await asyncio.gather(*execution_tasks)

                tool_task = asyncio.create_task(run_parallel_tools())

                # While tools are running, yield progress events from the queue
                while not tool_task.done():
                    try:
                        # Wait for an event with timeout to check task status
                        event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                        yield event
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break

                # Final drain of the queue
                while not event_queue.empty():
                    yield event_queue.get_nowait()

                tool_results = await tool_task

                # 按顺序处理结果
                has_loop_warning = False
                for tc, result, fn_name, args in tool_results:
                    result_str = str(result)
                    if result_str.startswith("[LOOP DETECTION]") or result_str.startswith("[CRITICAL DEADLOCK]"):
                        has_loop_warning = True
                    # 处理 web_search 的特殊逻辑
                    if fn_name == "web_search":
                        try:
                            search_data = json.loads(result)
                            if isinstance(search_data, list):
                                for item in search_data:
                                    if isinstance(item, dict) and item.get("href") and not any(x.get("href") == item.get("href") for x in search_citations):
                                        search_citations.append(item)
                        except: pass

                    yield AgentEvent(type="tool_result", data={
                        "tool_call_id": tc["id"],
                        "name": fn_name,
                        "result": str(result)
                    })

                    session.history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": fn_name,
                        "content": str(result)
                    })
                    yield AgentEvent(type="message", data=session.history[-1])

                    if on_step_log: on_step_log("tool_result", tool_call_id=tc["id"], function_name=fn_name, arguments=args, result=str(result))

                # Reset deadlock counter if this iteration had no loop warnings
                if not has_loop_warning:
                    consecutive_deadlocks = 0
                else:
                    # On critical deadlock with meta-diagnosis, reset context
                    # Keep original messages (query) + inject diagnosis
                    for tc, result, fn_name, args in tool_results:
                        if str(result).startswith("[CRITICAL DEADLOCK]"):
                            boundary = session.metadata.get("history_boundary", 0)
                            original_msgs = session.history[:boundary]
                            diagnosis_msg = {"role": "user", "content": str(result)}
                            session.history = original_msgs + [diagnosis_msg]
                            session.metadata["history_boundary"] = len(original_msgs)
                            consecutive_deadlocks = 0
                            action_history.clear()
                            break

            # End of loop — check if we exhausted iterations (no break)
            iterations_exhausted = (session.metadata.get("iteration_count", 0) >= max_iterations)
            if iterations_exhausted:
                exhausted_msg = (
                    f"[SYSTEM] 已达到最大迭代次数 ({max_iterations})，自动停止。"
                    f"已完成的工作已保存。"
                )
                session.history.append({"role": "assistant", "content": exhausted_msg})
                yield AgentEvent(type="message", data=session.history[-1])

            final_history = session.history if return_full_history else [session.history[-1]]
            yield AgentEvent(type="finish", data={"history": final_history, "iterations_exhausted": iterations_exhausted})

        except Exception as e:
            import traceback
            error_msg = f"{type(e).__name__}: {str(e)}"
            if not str(e):
                error_msg += f"\nTraceback: {traceback.format_exc()}"

            Logger.error(f"AgentEngine execution error: {error_msg}")
            yield AgentEvent(type="error", data={"error": error_msg})
            raise e
        finally:
            self.depth -= 1
