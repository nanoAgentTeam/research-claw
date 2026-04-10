"""
LLM 中台类型定义模块

定义 LLM 中台的核心数据类型和配置结构。

主要类型：
    - SystemPromptConfig: 系统提示词配置
    - AgentSession: AI Agent 会话状态

设计理念：
    - 使用 dataclass 简化数据结构定义
    - 提供不可变的默认值（通过 field(default_factory)）
    - 支持动态扩展（extra_sections, metadata）

依赖关系：
    - 被依赖: core.llm.engine, core.llm.middleware
    - 依赖: 标准库 dataclasses, typing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union, Callable

from core.llm.prompt_builder import PromptBuilder


@dataclass
class SystemPromptConfig:
    """
    系统提示词配置类

    内部委托给 PromptBuilder，提供 key-based section 管理。
    保留 base_prompt / extra_sections 字段以兼容构造方式。

    典型用法：
        >>> config = SystemPromptConfig(base_prompt="You are helpful.")
        >>> config.set("mw:budget", "WARNING: budget exceeded")
        >>> config.build()
    """

    base_prompt: str = "You are a helpful assistant."
    extra_sections: List[str] = field(default_factory=list)

    def __post_init__(self):
        self._pb = PromptBuilder()
        self._pb.set("base", self.base_prompt)
        for i, section in enumerate(self.extra_sections):
            self._pb.set(f"_extra_{i}", section)

    # --- PromptBuilder delegation ---

    def set(self, key: str, content: str) -> "SystemPromptConfig":
        self._pb.set(key, content)
        if key == "base":
            object.__setattr__(self, "base_prompt", content)
        return self

    def remove(self, key: str) -> "SystemPromptConfig":
        self._pb.remove(key)
        return self

    def get(self, key: str) -> str | None:
        return self._pb.get(key)

    def has(self, key: str) -> bool:
        return self._pb.has(key)

    def keys(self) -> list[str]:
        return self._pb.keys()

    def build(self) -> str:
        return self._pb.build()


@dataclass
class AgentSession:
    """
    AI Agent 会话状态类

    封装一次 Agent 执行的完整状态，包括对话历史、工具列表、配置和元数据。

    会话是 Agent 执行的基本单位，贯穿整个 ReAct 循环（思考-行动-观察）。
    中间件可以读取和修改会话状态，从而影响 Agent 的行为。

    属性：
        history: 对话历史消息列表
                 格式: [{"role": "user", "content": "..."},
                        {"role": "assistant", "content": "...", "tool_calls": [...]},
                        {"role": "tool", "content": "...", "tool_call_id": "..."}]
        depth: 递归深度（用于子代理委派嵌套调用）
               顶层为 1，每次委派子代理增加 1
        system_config: 系统提示词配置
        tools: 当前会话可用的工具列表
        metadata: 自定义元数据字典（用于中间件传递信息）
                  例如: {"iteration_count": 5, "user_id": "123"}

    设计特点：
        - 可变状态：history 和 metadata 可以在执行过程中修改
        - 工具隔离：每个会话独立的工具列表（子代理约束）
        - 元数据扩展：支持任意自定义数据

    生命周期：
        1. 创建会话：初始化 history 和 tools
        2. 中间件处理：读取/修改会话状态
        3. LLM 调用：根据 history 和 system_config 生成响应
        4. 工具执行：根据 tools 执行函数调用
        5. 更新历史：追加 assistant 和 tool 消息
        6. 循环继续：直到 LLM 返回最终答案

    典型用法：
        >>> from core.tools.base import BaseTool
        >>> session = AgentSession(
        ...     history=[{"role": "user", "content": "What's 2+2?"}],
        ...     depth=1,
        ...     system_config=SystemPromptConfig(),
        ...     tools=[],
        ...     metadata={"user_id": "user_123"}
        ... )
        >>> session.history.append({
        ...     "role": "assistant",
        ...     "content": "The answer is 4."
        ... })

    与中间件的交互：
        - ExecutionBudgetManager: 统计 history 中的 assistant 消息数量
    """

    history: List[Dict[str, Any]]
    depth: int
    system_config: SystemPromptConfig
    tools: List['BaseTool']
    metadata: Dict[str, Any] = field(default_factory=dict)

