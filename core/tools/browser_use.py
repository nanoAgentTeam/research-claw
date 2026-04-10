# DEPRECATED: Use agent.tools.browser.BrowserUseTool instead.
# This file is kept for backward compatibility and will be removed in a future release.
import asyncio
from typing import Any, Dict, Optional

from browser_use import BrowserProfile
from browser_use.agent.service import Agent
from browser_use.agent.views import AgentHistoryList
from browser_use.llm.base import BaseChatModel
from browser_use.llm.openai.chat import ChatOpenAI

try:
    import nest_asyncio  # type: ignore[import]
except ImportError:  # pragma: no cover
    nest_asyncio = None

from core.infra.config import Config
from core.tools.base import BaseTool
from core.utils.logger import Logger

DEFAULT_MAX_STEPS = 30


class BrowserUseTool(BaseTool):
    """
    A bridge to the browser-use agent for executing multi-step browsing tasks.
    """

    def __init__(self, provider_key: Optional[str] = 'qwen-vl', llm: BaseChatModel | None = None):
        self.provider_key = provider_key or Config.DEFAULT_PROVIDER
        self.llm_config = Config.get_provider_config(self.provider_key)
        self.llm = llm or self._create_llm(self.llm_config)

    def _create_llm(self, config: Dict[str, Any]) -> BaseChatModel | None:
        if not config:
            Logger.error(f"LLM config missing for provider '{self.provider_key}'.")
            return None

        model = config.get("model")
        api_key = config.get("api_key")
        if not api_key:
            Logger.warning(f"API key missing for provider '{self.provider_key}'. Please configure it in settings.")
            return None

        llm_kwargs = {}
        for key in ("base_url", "organization", "project", "timeout", "max_retries"):
            value = config.get(key)
            if value is not None:
                llm_kwargs[key] = value

        return ChatOpenAI(model=model, api_key=api_key, **llm_kwargs)

    @property
    def name(self) -> str:
        return "browser_use"

    @property
    def description(self) -> str:
        return (
            "使用浏览器执行自动化任务（基于 browser-use 库）。适用于需要真实浏览器交互的复杂任务，"
            "如点击、滚动、处理动态内容、多页跳转和结构化数据提取。"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Describe the browsing task the agent should perform."
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum agent steps before giving up.",
                    "minimum": 1,
                    "default": DEFAULT_MAX_STEPS
                }
            },
            "required": ["task"]
        }

    def get_status_message(self, **kwargs) -> str:
        task = kwargs.get("task", "")
        return f"\n\n🌐 正在启动浏览器执行任务: {task[:50]}...\n"

    async def execute_async(self, task: str, max_steps: int = DEFAULT_MAX_STEPS) -> str:
        """
        Run the browser-use Agent asynchronously and return the final extracted text.
        """
        if self.llm is None:
            return f"LLM for provider '{self.provider_key}' is not configured. Check settings."

        max_steps = max(max_steps, 1)

        # 创建禁用扩展的配置
        profile = BrowserProfile(enable_default_extensions=False)

        try:
            agent = Agent(task=task, llm=self.llm, browser_profile=profile)
            history = await agent.run(max_steps=max_steps)

            final_output = history.final_result()
            if final_output:
                return str(final_output)

            errors = [error for error in history.errors() if error]
            if errors:
                return f"Task completed, but browser-use reported errors: {'; '.join(errors)}"

            return self._summarize_history(history)

        except Exception as exc:  # pragma: no cover - external dependency
            Logger.error(f"BrowserUseTool failed for provider '{self.provider_key}': {exc}")
            return f"Error executing browser task: {exc}"

    def execute(self, task: str, max_steps: Optional[int] = None) -> str:
        """
        Synchronous wrapper around execute_async for compatibility with sync tool flows.
        """
        coroutine = self.execute_async(task, max_steps if max_steps is not None else DEFAULT_MAX_STEPS)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        if nest_asyncio is None:
            error_message = (
                "BrowserUseTool requires nest_asyncio to run from an existing async loop. "
                "Install nest-asyncio or call execute_async directly."
            )
            Logger.error(error_message)
            return error_message

        nest_asyncio.apply()
        return loop.run_until_complete(coroutine)

    def _summarize_history(self, history: AgentHistoryList) -> str:
        if not history.history:
            return "Task finished but produced no history."

        last_step = history.history[-1]
        last_result = last_step.result[-1] if last_step.result else None
        parts = []

        if last_result:
            for label, value in (
                ("Extracted content", last_result.extracted_content),
                ("Long-term memory", last_result.long_term_memory),
                ("Error", last_result.error),
            ):
                if value:
                    parts.append(f"{label}: {value}")

        if parts:
            return " | ".join(parts)

        if last_step.model_output:
            seen_actions: list[str] = []
            for action in last_step.model_output.action:
                action_data = action.model_dump(exclude_none=True, mode="json")
                if action_data:
                    action_name = next(iter(action_data.keys()))
                    seen_actions.append(action_name)
            if seen_actions:
                return f"Last actions executed: {', '.join(seen_actions)}."

        return "Task completed but browser-use did not produce textual output."
