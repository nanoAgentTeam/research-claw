"""
数据模型基类模块

提供所有数据模型的基类，实现通用的序列化功能。

设计理念：
    - 简单的字典转换接口
    - JSON 序列化支持
    - 自动过滤私有属性（以 _ 开头）
    - 轻量级实现，无额外依赖

典型用法：
    >>> class MyModel(BaseModel):
    ...     def __init__(self, name, value):
    ...         self.name = name
    ...         self.value = value
    >>>
    >>> model = MyModel("test", 123)
    >>> model.to_dict()
    {'name': 'test', 'value': 123}
    >>> model.to_json()
    '{"name": "test", "value": 123}'

依赖关系：
    - 依赖: 无（仅标准库）
    - 被依赖: Behavior, Intent, Goal

设计决策：
    - 不使用 dataclass：保持灵活性，支持动态属性
    - 不使用 Pydantic：避免重量级依赖
    - 简单的字典转换：适合大多数场景
"""

import json
from typing import Dict, Any


class BaseModel:
    """
    数据模型基类

    提供基本的序列化功能，所有数据模型都应继承此类。

    方法：
        to_dict(): 将模型转换为字典
        to_json(): 将模型转换为 JSON 字符串

    属性过滤：
        以下划线 (_) 开头的属性被视为私有属性，
        不会包含在序列化结果中。

    使用示例：
        >>> class User(BaseModel):
        ...     def __init__(self, name, age):
        ...         self.name = name
        ...         self.age = age
        ...         self._internal = "hidden"
        >>>
        >>> user = User("Alice", 30)
        >>> user.to_dict()
        {'name': 'Alice', 'age': 30}  # _internal 被过滤

    注意事项：
        - 不支持嵌套对象的自动序列化
        - 循环引用会导致 JSON 序列化失败
        - datetime 等特殊类型需要在子类中处理
    """

    def to_dict(self) -> Dict[str, Any]:
        """
        将模型转换为字典

        自动过滤以下划线开头的私有属性。

        Returns:
            dict: 包含所有公开属性的字典

        示例：
            >>> model.name = "test"
            >>> model._private = "hidden"
            >>> model.to_dict()
            {'name': 'test'}

        注意：
            - 嵌套的 BaseModel 对象不会自动转换
            - 如需嵌套转换，子类应重写此方法
        """
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def to_json(self) -> str:
        """
        将模型转换为 JSON 字符串

        使用 to_dict() 获取字典，然后序列化为 JSON。

        Returns:
            str: JSON 格式的字符串

        Raises:
            TypeError: 如果属性包含不可序列化的对象

        示例：
            >>> model.name = "测试"
            >>> model.to_json()
            '{"name": "测试"}'

        注意：
            - 使用 ensure_ascii=False 保留 Unicode 字符
            - datetime 等对象需要在子类中先转换为字符串
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)
