"""
Intent 数据模型（Layer 2）

表示从用户行为推断出的短期意图，是行为的语义抽象。

数据层级：
    Layer 1 (Behavior) → Layer 2 (Intent) → Layer 3 (Goal)

意图类型：
    - Information Seeking: 信息查找
    - Learning: 学习
    - Problem Solving: 问题解决
    - Entertainment: 娱乐
    - Work: 工作相关
    - Shopping: 购物
    - Social: 社交
    - Other: 其他

典型用法：
    >>> intent = Intent(
    ...     intent_id="i_123",
    ...     behavior_id="b_456",
    ...     intent_type="Learning",
    ...     description="学习 Python 异步编程",
    ...     confidence=0.85
    ... )
    >>> intent.to_dict()

依赖关系：
    - 依赖: BaseModel, Behavior (通过 behavior_id 关联)
    - 被依赖: Goal (通过 intent_id 关联)

数据来源：
    - AnalysisWorker 通过 LLM 从 Behavior 推断生成
"""

import json
from typing import Dict, Any

from core.infra.models.base import BaseModel


class Intent(BaseModel):
    """
    用户意图数据模型（Layer 2）

    从单个或多个行为推断出的用户短期意图。

    属性：
        intent_id: 意图唯一标识符（格式：i_{timestamp}_{random}）
        behavior_id: 关联的行为 ID（1对1关系）
        intent_type: 意图类型分类
        description: 意图的自然语言描述
        confidence: 置信度（0.0-1.0）

    关联关系：
        - 一个 Intent 对应一个 Behavior（1:1）
        - 多个 Intent 可以属于一个 Goal（N:1）

    分析流程：
        Behavior → LLM 分析 → Intent → 聚合到 Goal

    示例：
        >>> intent = Intent(
        ...     intent_id="i_1641024000_xyz",
        ...     behavior_id="b_1641024000_abc",
        ...     intent_type="Information Seeking",
        ...     description="查找 FastAPI 性能优化方法",
        ...     confidence=0.9
        ... )
    """

    def __init__(
        self,
        intent_id: str,
        behavior_id: str,
        intent_type: str,
        description: str,
        confidence: float = 0.0
    ):
        """
        初始化 Intent 实例

        Args:
            intent_id: 意图唯一标识符
            behavior_id: 关联的行为 ID
            intent_type: 意图类型（预定义分类）
            description: 意图的自然语言描述
            confidence: 置信度，范围 0.0-1.0（默认 0.0）
        """
        self.intent_id = intent_id
        self.behavior_id = behavior_id
        self.intent_type = intent_type
        self.description = description
        self.confidence = confidence
