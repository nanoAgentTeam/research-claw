from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class AgentEvent:
    """
    Agent 引擎产生的结构化事件

    用于在流式输出中区分不同类型的数据（Token、消息、工具调用、结束信号等）。
    """
    type: str  # "token", "tool_call", "tool_result", "message", "finish", "error"
    data: Any

    # 辅助方法：判断是否是结束事件
    @property
    def is_finish(self):
        return self.type == "finish"
