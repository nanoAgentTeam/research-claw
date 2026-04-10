"""
LLM 中间件模块

提供中间件模式用于 LLM 调用的拦截和增强。

主要组件：
    - StrategyMiddleware: 中间件抽象基类
    - ExecutionBudgetManager: 执行预算管理
    - HistorySummaryMiddleware: 历史轮次裁剪
    - StepCompressionMiddleware: ReAct 步骤压缩

设计模式：
    - 中间件模式（Middleware Pattern）
    - 责任链模式（Chain of Responsibility）
    - 策略模式（Strategy Pattern）

工作原理：
    1. 中间件形成管道（Pipeline）
    2. 每个中间件可以在 LLM 调用前后执行逻辑
    3. 中间件可以修改 AgentSession 状态
    4. 中间件可以决定是否继续调用链

典型应用：
    - 预算控制：限制总迭代次数和 token 消耗
    - 历史裁剪：丢弃过旧的对话轮次
    - 步骤压缩：压缩 ReAct 步骤以节省上下文
    - 日志记录：记录每次 LLM 调用的详情
    - 错误处理：捕获和处理 LLM 调用异常

依赖关系：
    - 依赖: core.llm.types.AgentSession, core.utils.logger.Logger
    - 被依赖: core.llm.engine.AgentEngine
"""

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List
from core.llm.types import AgentSession
from core.utils.logger import Logger
from core.prompts import render as render_prompt


class StrategyMiddleware(ABC):
    """
    LLM 中间件抽象基类

    定义中间件的标准接口，所有具体中间件必须继承此类。

    中间件模式：
        中间件位于 LLM 调用链中，可以在调用前后执行逻辑：
        - 调用前：检查和修改 session 状态
        - 调用后：处理和转换响应结果

    设计理念：
        - 单一职责：每个中间件专注一个任务
        - 可组合性：多个中间件可以串联
        - 透明性：对 LLM 调用逻辑透明

    实现要求：
        子类必须实现 __call__ 方法，该方法：
        1. 接收 session 和 next_call
        2. 可以检查/修改 session
        3. 调用 next_call(session) 继续链
        4. 可以处理 next_call 的返回值
        5. 返回最终结果

    典型实现模式：
        ```python
        class MyMiddleware(StrategyMiddleware):
            def __call__(self, session, next_call):
                # 前置处理
                print("Before LLM call")
                session.metadata["start_time"] = time.time()

                # 调用下一个中间件或 LLM
                result = next_call(session)

                # 后置处理
                print("After LLM call")
                session.metadata["end_time"] = time.time()

                return result
        ```
    """

    @abstractmethod
    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        """
        中间件调用接口

        Args:
            session: 当前 Agent 会话状态
            next_call: 下一个中间件或 LLM 调用函数
                      签名: (AgentSession) -> Any
                      返回: LLM 响应（可能是 generator）

        Returns:
            Any: LLM 响应或处理后的结果

        实现建议：
            - 总是调用 next_call(session) 继续链
            - 修改 session 时要谨慎，避免破坏状态
            - 如果需要中断链，可以抛出异常
            - 返回值类型应该与 next_call 保持一致
        """
        pass

class ExecutionBudgetManager(StrategyMiddleware):
    """
    执行预算管理中间件

    限制总迭代次数和工具调用次数，控制成本和时间。

    预算管理目标：
        - Token 成本控制：减少不必要的 LLM 调用
        - 响应时间控制：避免用户等待过久
        - 资源保护：防止恶意或错误的无限循环
        - 服务质量：保证系统稳定性

    工作原理：
        1. 统计 session.history 中 assistant 消息数量
        2. 与 max_iterations 比较
        3. 超过预算时注入强制终止指令

    干预措施：
        - 通过 system_config.set() 注入 CRITICAL 警告（key-based，跨迭代幂等）
        - 明确要求 AI 立即给出最终答案
        - 禁止继续调用工具
        - 不会抛出异常（AI 理论上可以忽略）

    参数：
        max_iterations: 最大迭代次数（默认 50）
                       一次迭代 = 一次 assistant 响应

    与 AgentEngine 的关系：
        - AgentEngine 的 run 有自己的 max_iterations 参数
        - 那个参数是硬限制（for 循环）
        - 这个中间件是软限制（通过提示词）
        - 两者配合使用效果最佳

    最佳实践：
        - 根据任务复杂度调整 max_iterations
        - 简单问答：3-5 次
        - 中等任务：10-15 次
        - 复杂任务：20-30 次
        - 超过 50 次通常表示设计问题

    示例：
        >>> budget = ExecutionBudgetManager(max_iterations=50)
        >>> # 第 50 次迭代时，注入指令：
        >>> # "CRITICAL: You have exceeded your execution budget.
        >>> #  You MUST provide your final best answer NOW
        >>> #  and stop calling tools."
    """

    def __init__(self, max_iterations: int = 50):
        """
        初始化执行预算管理器

        Args:
            max_iterations: 最大允许的迭代次数，默认 50
                           建议根据任务复杂度调整
        """
        self.max_iterations = max_iterations

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # In this architecture, AgentEngine's run handles the loop.
        # However, middleware can still monitor the history length.

        turns = sum(1 for msg in session.history if msg.get("role") == "assistant")

        if turns >= self.max_iterations:
            Logger.error(f"Execution budget exceeded: {turns} turns.")
            # We can't easily 'stop' the loop from here without raising an exception
            # or modifying the session in a way that the engine stops.
            # For now, we inject a mandatory termination instruction.
            _BUDGET_FALLBACK = (
                "CRITICAL: You have exceeded your execution budget. "
                "You MUST provide your final best answer NOW and stop calling tools."
            )
            session.system_config.set(
                "mw:budget_warning",
                render_prompt("mw_budget_warning.txt", _BUDGET_FALLBACK)
            )

        return next_call(session)


# ---------------------------------------------------------------------------
# Shared helpers & constants
# ---------------------------------------------------------------------------

MAX_TOOL_OUTPUT_LENGTH = 100000
"""Maximum tool output length (chars) before truncation."""

TOOL_TRUNCATION_EXEMPT = {"write_file", "str_replace", "patch_file", "insert_content", "arxiv_search", "semantic_scholar_search"}
"""Tool names exempt from output truncation (write-type tools)."""

REQUEST_OVERHEAD_TOKENS = 2048
"""Fixed safety overhead for request wrappers/metadata not captured by message serialization."""


def _estimate_text_tokens(text: str) -> int:
    """Estimate token count for plain text."""
    if not text:
        return 0
    non_ascii = len(re.sub(r'[\x00-\x7F]+', '', text))
    ascii_count = len(text) - non_ascii
    return (ascii_count // 3) + (non_ascii * 2)


def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate token count for a message list.

    ASCII: ~1 token per 3 chars.  Non-ASCII (CJK etc.): ~2 tokens per char.
    """
    total = 0
    for m in messages:
        for field in ("content", "tool_calls"):
            text = str(m.get(field, "") or "")
            total += _estimate_text_tokens(text)
    return total


def _estimate_request_tokens(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]] | None = None,
    extra_text: str = "",
) -> int:
    """Estimate request tokens including messages + tool schemas + extra system text."""
    total = _estimate_tokens(messages)
    if tools:
        try:
            total += _estimate_text_tokens(json.dumps(tools, ensure_ascii=False))
        except Exception:
            total += _estimate_text_tokens(str(tools))
    if extra_text:
        total += _estimate_text_tokens(extra_text)
    return total + REQUEST_OVERHEAD_TOKENS


def infer_context_limit(model: str) -> int:
    """Infer model context window from model name."""
    model_lower = model.lower() if model else ""
    if "gpt-4" in model_lower:
        return 128_000
    if "gpt-5" in model_lower:
        return 256_000
    if "step" in model_lower:
        return 262_144
    if "claude" in model_lower:
        if "claude-3" in model_lower:
            return 200_000
        return 256_000
    if "gemini" in model_lower:
        return 1_000_000
    return 256_000


# ---------------------------------------------------------------------------
# HistorySummaryMiddleware — trim old conversation rounds
# ---------------------------------------------------------------------------

class HistorySummaryMiddleware(StrategyMiddleware):
    """Trim old history rounds to keep the conversation focused.

    Operates on the *history* region of ``session.history`` (everything before
    ``history_boundary``).  When the number of user/assistant round-trips
    exceeds ``max_rounds``, the oldest rounds are dropped so that only the
    most recent ``keep_rounds`` remain.

    This middleware does NOT generate summaries — that is the responsibility
    of the application layer (e.g. ``AgentLoop.summarize()`` writing to
    ``active_context.md`` which is injected via the system prompt).
    """

    def __init__(self, max_rounds: int = 30, keep_rounds: int = 10):
        self.max_rounds = max_rounds
        self.keep_rounds = keep_rounds

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        boundary = session.metadata.get("history_boundary", 0)
        if boundary <= 0:
            return next_call(session)

        history_region = session.history[:boundary]

        # Count rounds (each user message = 1 round)
        round_count = sum(1 for m in history_region if m.get("role") == "user")
        if round_count <= self.max_rounds:
            return next_call(session)

        # Keep the last `keep_rounds` rounds
        rounds_to_drop = round_count - self.keep_rounds
        new_start = 0
        dropped = 0
        for i, m in enumerate(history_region):
            if m.get("role") == "user":
                dropped += 1
                if dropped > rounds_to_drop:
                    new_start = i
                    break

        trimmed_history = history_region[new_start:]
        session.history = trimmed_history + session.history[boundary:]
        session.metadata["history_boundary"] = len(trimmed_history)

        Logger.info(
            f"HistorySummary: trimmed {rounds_to_drop} old rounds, "
            f"kept {self.keep_rounds} (history {boundary} → {len(trimmed_history)} msgs)"
        )
        return next_call(session)


# ---------------------------------------------------------------------------
# StepCompressionMiddleware — compress ReAct steps in current turn
# ---------------------------------------------------------------------------

_STEP_SUMMARY_PROMPT = (
    "Summarize the following tool-call steps concisely. "
    "For each step, state what tool was called, with what intent, and the key outcome. "
    "Output a numbered list, one line per step. Be brief.\n\n{steps_text}"
)


class StepCompressionMiddleware(StrategyMiddleware):
    """Compression for ReAct steps within the current turn.

    Operates on the *steps* region of ``session.history`` (everything from
    ``history_boundary`` onward).

    Level 1 (moderate, 70% threshold):
        Keep first 2 steps + last 2 steps, replace middle with an LLM-generated
        summary.  Requires ``session.metadata["llm_provider"]`` and
        ``session.metadata["llm_model"]`` to be set by the caller.
        Falls back to rule-based summary if LLM is unavailable.

    Note: L2 emergency compression (discard middle entirely) is triggered
    externally by engine/loop error handlers via ``_split_steps()``.
    """

    def __init__(
        self,
        model_context_limit: int = 256_000,
        compression_threshold: float = 0.7,
    ):
        self.model_context_limit = model_context_limit
        self.compression_threshold = compression_threshold

    # -- public entry (sync wrapper) --

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        boundary = session.metadata.get("history_boundary", 0)
        steps = session.history[boundary:]
        if len(steps) <= 6:
            return next_call(session)

        total_tokens = _estimate_request_tokens(
            session.history,
            tools=session.metadata.get("llm_tools"),
            extra_text=session.system_config.build(),
        )
        threshold = int(self.model_context_limit * self.compression_threshold)

        if total_tokens > threshold:
            Logger.warning(
                f"StepCompression: tokens {total_tokens} > 70% threshold {threshold}, "
                f"triggering Level 1 compression"
            )
            steps = self._compress_with_summary(steps, session)
            session.history = session.history[:boundary] + steps

        return next_call(session)

    # -- async entry (called by engine when available) --

    async def async_call(self, session: AgentSession) -> None:
        """Async variant that can call LLM for step summary."""
        boundary = session.metadata.get("history_boundary", 0)
        steps = session.history[boundary:]
        if len(steps) <= 6:
            return

        total_tokens = _estimate_request_tokens(
            session.history,
            tools=session.metadata.get("llm_tools"),
            extra_text=session.system_config.build(),
        )
        threshold = int(self.model_context_limit * self.compression_threshold)

        if total_tokens > threshold:
            Logger.warning(
                f"StepCompression: tokens {total_tokens} > 70% threshold {threshold}, "
                f"triggering Level 1 compression (async)"
            )
            steps = await self._async_compress_with_summary(steps, session)
            session.history = session.history[:boundary] + steps

    # -- Level 1: keep first 2 + last 2, summarize middle --

    def _compress_with_summary(self, steps: list, session: AgentSession) -> list:
        """Sync fallback: keep first 2 + last 2, replace middle with rule-based summary."""
        head, middle, tail = self._split_steps(steps)
        if not middle:
            return steps
        summary_text = self._rule_based_summary(middle)
        summary_msg = {"role": "system", "content": f"[Compressed steps summary]\n{summary_text}"}
        return head + [summary_msg] + tail

    async def _async_compress_with_summary(self, steps: list, session: AgentSession) -> list:
        """Async: try LLM summary, fall back to rule-based."""
        head, middle, tail = self._split_steps(steps)
        if not middle:
            return steps

        llm_provider = session.metadata.get("llm_provider")
        llm_model = session.metadata.get("llm_model")

        if llm_provider and llm_model:
            try:
                summary_text = await self._llm_summary(
                    middle,
                    llm_provider,
                    llm_model,
                    on_token=session.metadata.get("stream_on_token"),
                )
            except Exception as e:
                Logger.warning(f"StepCompression LLM summary failed, using rule-based: {e}")
                summary_text = self._rule_based_summary(middle)
        else:
            summary_text = self._rule_based_summary(middle)

        summary_msg = {"role": "system", "content": f"[Compressed steps summary]\n{summary_text}"}
        return head + [summary_msg] + tail

    # -- internals --

    @staticmethod
    def _split_steps(steps: list) -> tuple:
        """Split steps into head(2), middle, tail(2)."""
        if len(steps) <= 4:
            return steps, [], []
        # A "step" is an assistant+tool pair, but messages are interleaved.
        # Keep first 2 and last 2 messages as atomic units.
        # Find boundaries: first 2 assistant messages, last 2 assistant messages.
        head_end = 0
        assistant_count = 0
        for i, m in enumerate(steps):
            if m.get("role") == "assistant":
                assistant_count += 1
                if assistant_count >= 2:
                    # Include trailing tool messages
                    head_end = i + 1
                    while head_end < len(steps) and steps[head_end].get("role") == "tool":
                        head_end += 1
                    break
        if assistant_count < 2:
            return steps, [], []

        tail_start = len(steps)
        assistant_count = 0
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].get("role") == "assistant":
                assistant_count += 1
                if assistant_count >= 2:
                    tail_start = i
                    break
        if tail_start <= head_end:
            return steps, [], []

        return steps[:head_end], steps[head_end:tail_start], steps[tail_start:]

    @staticmethod
    def _rule_based_summary(middle: list) -> str:
        """Generate a concise rule-based summary of middle steps."""
        lines = []
        step_num = 0
        for m in middle:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    step_num += 1
                    fn = tc.get("function", {})
                    name = fn.get("name", "unknown")
                    args_str = fn.get("arguments", "{}")
                    try:
                        args = json.loads(args_str)
                        # Show key argument (first string value)
                        key_arg = next((str(v)[:80] for v in args.values() if isinstance(v, str)), "")
                    except Exception:
                        key_arg = args_str[:80]
                    lines.append(f"{step_num}. {name}({key_arg})")
            elif m.get("role") == "tool":
                content = str(m.get("content", ""))
                if lines:
                    lines[-1] += f" → {len(content)} chars"
        return "\n".join(lines) if lines else "(no steps)"

    @staticmethod
    async def _llm_summary(
        middle: list,
        provider: Any,
        model: str,
        on_token: Any | None = None,
    ) -> str:
        """Use LLM provider to summarize middle steps."""
        steps_text = ""
        step_num = 0
        for m in middle:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    step_num += 1
                    fn = tc.get("function", {})
                    name = fn.get("name", "unknown")
                    args_str = fn.get("arguments", "{}")[:200]
                    steps_text += f"Step {step_num}: Called {name}({args_str})\n"
            elif m.get("role") == "tool":
                content = str(m.get("content", ""))[:500]
                steps_text += f"  Result: {content}\n"

        prompt = _STEP_SUMMARY_PROMPT.format(steps_text=steps_text)

        response = await provider.chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            on_token=on_token,
        )
        return response.content or "(summary unavailable)"
