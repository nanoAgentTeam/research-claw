"""
日志工具模块

提供全局的日志记录功能，所有日志输出到文件而非控制台。
这是因为在 Native Messaging 环境下，stdout/stderr 被用于与浏览器通信。

功能特性：
- 线程安全的文件写入（每次写入都打开/关闭文件）
- 自动时间戳
- 错误日志级别区分
- 静默失败（如果日志文件写入失败，不影响主程序运行）

典型用法：
    >>> from core.utils.logger import Logger
    >>> Logger.log("Application started")
    >>> Logger.error("Failed to connect to database")

依赖关系：
- 依赖: core.infra.config.Config (获取日志文件路径)
- 被依赖: 所有其他模块
"""

import datetime
import sys
from core.infra.config import Config


class Logger:
    """
    简易日志工具类

    将日志输出到文件而不是控制台（因为控制台 stdout 被 Native Messaging 占用）。

    设计考量：
    - 使用静态方法，无需实例化
    - 每次写入都打开文件，避免长时间持有文件句柄
    - 异常静默处理，确保日志失败不影响主流程

    线程安全性：
        文件追加写入在 CPython 中对于单行写入是原子性的，
        但在高并发场景下可能出现日志交错。如需严格顺序，
        建议使用 logging 模块的 ThreadHandler。
    """

    @staticmethod
    def log(message: str) -> None:
        """
        记录一条普通日志

        日志格式: {timestamp} - {message}

        Args:
            message: 要记录的日志内容

        Returns:
            None

        注意:
            - 如果日志文件不存在，会自动创建
            - 如果写入失败（权限不足、磁盘满等），会静默忽略
            - 日志文件路径由 Config.LOG_PATH 指定

        示例:
            >>> Logger.log("User logged in")
            >>> Logger.log("Processing 100 records")
        """
        try:
            with open(Config.LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.datetime.now()} - {message}\n")
        except Exception:
            # 如果日志写入都失败，我们无能为力，只能忽略
            pass

    @staticmethod
    def info(message: str) -> None:
        """记录一条信息日志 (log 的别名)"""
        Logger.log(message)

    @staticmethod
    def error(message: str) -> None:
        """
        记录一条错误日志

        错误日志会添加 "ERROR: " 前缀，便于过滤和查找。

        Args:
            message: 要记录的错误信息

        Returns:
            None

        示例:
            >>> Logger.error("Database connection failed")
            >>> Logger.error(f"Invalid config: {e}")
        """
        Logger.log(f"ERROR: {message}")

    @staticmethod
    def warning(message: str) -> None:
        """
        记录一条警告日志

        警告日志会添加 "WARNING: " 前缀。

        Args:
            message: 要记录的警告信息
        """
        Logger.log(f"WARNING: {message}")

    @staticmethod
    def debug(message: str) -> None:
        """
        记录一条调试日志

        调试日志会添加 "DEBUG: " 前缀。

        Args:
            message: 要记录的调试信息
        """
        Logger.log(f"DEBUG: {message}")
