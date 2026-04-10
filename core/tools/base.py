"""
工具基类模块

本模块定义了系统中所有工具的抽象基类 BaseTool。
所有具体的工具类（如 SearchTool, WebReaderTool）都必须继承此类并实现其抽象方法。

设计理念：
    - 接口一致性：通过抽象方法强制要求工具提供名称、描述和参数定义。
    - 兼容性：提供 `to_openai_schema` 方法，直接将工具定义转换为 OpenAI Function Calling 格式。
    - 可配置性：提供 `configure` 钩子，允许在工具实例化后注入运行时上下文。
    - 状态反馈：通过 `get_status_message` 提供统一的 UI 反馈机制。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from core.utils.langfuse_manager import observe


class BaseTool(ABC):
    """
    所有可执行工具的抽象基类

    工具是 Agent 能力的扩展，代表了 Agent 可以执行的原子操作或复杂技能。
    每个工具都应该具有清晰的定义（名称、描述、参数）和可重复的执行逻辑。
    """

    def __init_subclass__(cls, **kwargs):
        """
        自动为所有子类的 execute 方法添加 Langfuse 跟踪
        """
        super().__init_subclass__(**kwargs)
        if hasattr(cls, 'execute') and callable(cls.execute):
            # Check if it's the class's own execute to avoid double decoration on inheritance
            if 'execute' in cls.__dict__:
                cls.execute = observe(as_type="span")(cls.execute)

    @property
    @abstractmethod
    def name(self) -> str:
        """工具的唯一标识名称（用于 LLM 识别）"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具的功能描述（用于 LLM 理解工具用途）"""
        pass

    @property
    @abstractmethod
    def parameters_schema(self) -> Dict[str, Any]:
        """
        工具的参数 JSON Schema 定义
        符合 JSON Schema 规范，描述工具接受哪些参数及其类型、描述。
        """
        pass

    def configure(self, context: Dict[str, Any]):
        """
        工具配置钩子
        允许在工具实例化后，执行前注入动态配置（如 API Key、路径等）。
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """
        执行工具的核心逻辑。子类应实现此方法。

        Args:
            **kwargs: LLM 根据 parameters_schema 生成的参数

        Returns:
            Any: 执行结果，通常应该是字符串或可 JSON 序列化的对象
        """
        pass

    async def validate_and_execute(self, **kwargs) -> tuple[Any, str]:
        """
        验证参数并执行工具。
        自动识别并移除不在 parameters_schema 或内部注入白名单中的参数，并返回警告。

        Returns:
            tuple[Any, str]: (执行结果, 警告信息)
        """
        import inspect

        # 1. 确定允许的参数列表
        # 来源 A: 工具定义的参数 Schema
        schema = self.parameters_schema
        allowed_params = []
        if isinstance(schema, dict) and "properties" in schema:
            allowed_params = list(schema["properties"].keys())

        # 来源 B: 系统内部注入参数（不应被视为幻觉）
        internal_injection_whitelist = ["on_token", "message_context", "iterator_step_consumption", "_agent_messages"]

        # 2. 识别并移除幻觉参数
        final_args = kwargs.copy()
        warning = ""

        # 所有不在允许列表中的参数都被视为幻觉
        hallucinated_keys = [k for k in kwargs.keys() if k not in allowed_params and k not in internal_injection_whitelist]

        if hallucinated_keys:
            for k in hallucinated_keys:
                del final_args[k]
            warning = f"The following hallucinated keys were removed from your '{self.name}' call: {hallucinated_keys}. Please stick to the tool's schema."

        # 3. 执行工具
        try:
            import asyncio

            # 获取实际 execute 的签名，仅传入它能接受的参数
            # (处理某些自定义工具可能没写 **kwargs 的情况)
            sig = inspect.signature(self.execute)
            has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

            call_args = {}
            if has_kwargs:
                call_args = final_args
            else:
                for k, v in final_args.items():
                    if k in sig.parameters:
                        call_args[k] = v

            # 同步 execute() 会阻塞事件循环，导致健康检查等无法响应
            # 使用 asyncio.to_thread() 将同步调用卸载到线程池
            if inspect.iscoroutinefunction(self.execute):
                res = await self.execute(**call_args)
            else:
                res = await asyncio.to_thread(self.execute, **call_args)
            return res, warning
        except Exception as e:
            return f"Error executing {self.name}: {str(e)}", warning

    def get_status_message(self, **kwargs) -> str:
        """
        获取工具执行时的状态提示消息
        用于在 UI 层实时反馈 Agent 的动作。
        """
        return f"\n\n🔧 正在调用工具: {self.name}...\n"

    def to_openai_schema(self) -> Dict[str, Any]:
        """
        将工具转换为 OpenAI Function Calling 格式的 Schema
        用于在 API 调用中传递给 LLM。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema
            }
        }

    def to_schema(self) -> Dict[str, Any]:
        """Alias for to_openai_schema to satisfy ToolRegistry protocol."""
        return self.to_openai_schema()


