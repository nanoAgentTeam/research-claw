"""
工具装饰器模块

本模块提供了一系列用于增强工具能力的装饰器，包括参数校验、安全沙箱和输出优化。
通过装饰器模式，我们可以将通用的防御性逻辑与具体的工具业务逻辑解耦。

主要装饰器：
    - @schema_strict_validator: 严格校验输入参数是否符合工具定义的 Schema。
    - @environment_guard: 安全守卫，防止 Agent 通过工具访问敏感路径或消耗过多资源。
    - @output_sanitizer: 输出清理器，自动格式化结果并对超长输出进行截断，保护 LLM 上下文。
"""

import functools
import json
import time
from typing import Any, Dict, List, Callable, Optional
from core.utils.logger import Logger


def schema_strict_validator(func: Callable):
    """
    输入参数校验装饰器

    在工具执行前，根据工具类中定义的 `parameters_schema` 校验输入参数。
    支持必填项检查和基础类型校验。

    如果校验失败，将直接返回错误信息字符串，不再执行被装饰的方法。
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        schema = getattr(self, "parameters_schema", {})
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # 检查必填字段
        for field in required:
            if field not in kwargs:
                return f"Error: Missing required parameter '{field}'."


        # 基础类型校验
        for key, value in kwargs.items():
            if key in properties:
                expected_type = properties[key].get("type")
                if expected_type == "string" and not isinstance(value, str):
                    return f"Error: Parameter '{key}' must be a string."
                elif expected_type == "integer" and not isinstance(value, int):
                    return f"Error: Parameter '{key}' must be an integer."
                elif expected_type == "boolean" and not isinstance(value, bool):
                    return f"Error: Parameter '{key}' must be a boolean."
                elif expected_type == "array" and not isinstance(value, list):
                    return f"Error: Parameter '{key}' must be an array."
                elif expected_type == "object" and not isinstance(value, dict):
                    return f"Error: Parameter '{key}' must be an object."

        return func(self, **kwargs)
    return wrapper


def environment_guard(func: Callable):
    """
    环境安全守卫装饰器

    提供基础的安全沙箱功能，防止 Agent 执行危险操作：
    1. 路径安全检查：拦截对系统关键路径（如 /etc, /var）的访问，拦截包含 '..' 的路径遍历攻击。
    2. 执行时间监控：监控工具执行时长，如果超过阈值（如10秒）则记录警告日志，用于识别可能的资源枯竭风险。
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # 1. 路径安全性检查
        path_keys = ['path', 'directory', 'filename', 'filepath', 'content_path', 'agent_path']
        for key in path_keys:
            if key in kwargs and isinstance(kwargs[key], str):
                p = kwargs[key]
                # 拦截敏感系统目录
                if p.startswith(('/etc', '/var', '/root', '/proc', '/sys')):
                    return f"Error: Access to system path '{p}' is prohibited for security reasons."
                # 拦截路径遍历
                if '..' in p:
                    return f"Error: Relative paths with '..' are not allowed."

        # 2. 执行时间监控
        start_time = time.time()
        result = func(self, *args, **kwargs)
        duration = time.time() - start_time

        if duration > 10.0:  # 10 秒超时告警
            Logger.warning(f"Tool {self.name} took {duration:.2f}s to execute.")

        return result
    return wrapper


def output_sanitizer(max_length: int = 2000):
    """
    输出清理与截断装饰器

    负责将工具的原始执行结果转化为适合 LLM 消费的格式：
    1. 格式化：将 dict/list 自动转换为 JSON 字符串。
    2. 错误处理：捕获工具内部未处理的异常，并记录日志。
    3. 截断：如果输出内容过长，自动截断并附加说明，防止消耗过多的上下文 Token。

    Args:
        max_length: 最大允许的输出字符长度，默认为 2000。
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                result = func(self, *args, **kwargs)

                # 自动转换为字符串
                if isinstance(result, (dict, list)):
                    result_str = json.dumps(result, ensure_ascii=False, indent=2)
                else:
                    result_str = str(result)

                # 超长截断
                if len(result_str) > max_length:
                    result_str = result_str[:max_length] + f"\n\n[Output truncated due to length... original size: {len(result_str)} characters]"

                return result_str
            except Exception as e:
                Logger.error(f"Error in tool {self.name}: {e}")
                return f"Error during execution: {str(e)}"
        return wrapper
    return decorator


