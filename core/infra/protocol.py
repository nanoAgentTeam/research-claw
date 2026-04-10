"""
Chrome Native Messaging 协议模块

实现与 Chrome 浏览器扩展的通信协议，遵循 Chrome Native Messaging 标准。

协议规范：
    Chrome Native Messaging 使用二进制协议进行通信：

    消息格式: [4字节长度前缀 (Little Endian)] + [UTF-8 编码的 JSON]

    - 长度前缀：32位无符号整数，表示后续 JSON 消息的字节长度
    - 字节序：使用 Native Byte Order (@I in struct)
    - 编码：消息体必须是 UTF-8 编码的有效 JSON

    限制：
    - 单条消息最大长度：1MB (Chrome 限制)
    - stdin/stdout 必须以二进制模式打开
    - 发送后必须 flush，否则消息可能被缓冲

参考文档：
    https://developer.chrome.com/docs/apps/nativeMessaging/

典型用法：
    # 作为 Native Host 使用
    while True:
        msg = NativeMessagingProtocol.read_message()
        if msg is None:
            break
        # 处理消息...
        response = {"status": "ok"}
        NativeMessagingProtocol.send_message(response)

依赖关系：
    - 依赖: core.utils.logger.Logger (错误日志)
    - 被依赖: chrome-extension/native-host/run.py

注意事项：
    - 该模块专为 Native Messaging 设计，stdin/stdout 被占用后，
      不能用于其他用途（如 print 或 input）
    - 所有日志必须通过 Logger 写入文件，不能输出到控制台
"""

import sys
import struct
import json
from typing import Dict, Any, Optional
from core.utils.logger import Logger


class NativeMessagingProtocol:
    """
    Chrome Native Messaging 协议封装类

    处理与 Chrome 扩展之间的标准输入/输出通信。

    协议格式：[4字节长度前缀][JSON消息体]

    属性：
        无（纯静态方法类）

    线程安全性：
        该类使用全局的 sys.stdin/stdout，不是线程安全的。
        如果需要多线程处理，应该在单一线程中读取消息，
        然后分发到工作线程处理。

    错误处理：
        - 读取/发送失败时会记录错误日志
        - read_message 返回 None 表示失败或流结束
        - send_message 失败时静默（仅记录日志）
    """

    # Chrome Native Messaging 单条消息最大长度（1MB）
    MAX_MESSAGE_SIZE = 1024 * 1024

    @staticmethod
    def read_message() -> Optional[Dict[str, Any]]:
        """
        从 stdin 读取一条 Native Messaging 格式的消息

        读取流程：
            1. 读取 4 字节长度前缀
            2. 解析为无符号整数
            3. 读取指定长度的 JSON 数据
            4. 解码并解析 JSON

        Returns:
            dict: 解析后的 JSON 消息对象
            None: 如果读取失败、流结束或解析错误

        异常：
            所有异常都会被捕获并记录，返回 None

        注意：
            - 返回 None 可能表示流结束（正常退出）或错误
            - 如果长度前缀指示的大小超过 MAX_MESSAGE_SIZE，仍会尝试读取
              （但这可能表示协议错误）
            - 必须在二进制模式下使用 stdin.buffer

        示例：
            >>> msg = NativeMessagingProtocol.read_message()
            >>> if msg:
            ...     print(msg.get('type'))
        """
        try:
            # 读取前4个字节（长度前缀）
            raw_length = sys.stdin.buffer.read(4)
            if len(raw_length) == 0:
                # 流结束，正常退出
                return None

            if len(raw_length) < 4:
                Logger.error(f"协议错误：长度前缀不完整（仅 {len(raw_length)} 字节）")
                return None

            # 解包长度 (Native Byte Order, Unsigned Int)
            message_length = struct.unpack('@I', raw_length)[0]

            # 安全检查：防止恶意超大消息
            if message_length > NativeMessagingProtocol.MAX_MESSAGE_SIZE:
                Logger.error(f"消息过大：{message_length} 字节（最大 {NativeMessagingProtocol.MAX_MESSAGE_SIZE}）")
                return None

            # 读取指定长度的消息内容
            message_bytes = sys.stdin.buffer.read(message_length)
            if len(message_bytes) < message_length:
                Logger.error(f"消息截断：期望 {message_length} 字节，实际 {len(message_bytes)}")
                return None

            # 解码 UTF-8 并解析 JSON
            message_str = message_bytes.decode('utf-8')
            return json.loads(message_str)

        except json.JSONDecodeError as e:
            Logger.error(f"JSON 解析错误: {e}")
            return None
        except UnicodeDecodeError as e:
            Logger.error(f"UTF-8 解码错误: {e}")
            return None
        except Exception as e:
            Logger.error(f"消息读取错误: {e}")
            return None

    @staticmethod
    def send_message(message_content: Dict[str, Any]) -> bool:
        """
        向 stdout 发送一条 Native Messaging 格式的消息

        发送流程：
            1. 将消息序列化为 JSON
            2. 编码为 UTF-8 字节
            3. 写入 4 字节长度前缀
            4. 写入消息体
            5. 刷新缓冲区

        Args:
            message_content: 要发送的 JSON 可序列化对象（通常是 dict）

        Returns:
            bool: True 表示发送成功，False 表示失败

        异常：
            所有异常都会被捕获并记录，返回 False

        注意：
            - 消息必须可以被 json.dumps() 序列化
            - 发送后会自动 flush，确保 Chrome 立即收到
            - 如果消息超过 MAX_MESSAGE_SIZE，仍会尝试发送（但 Chrome 可能拒绝）

        示例：
            >>> success = NativeMessagingProtocol.send_message({
            ...     "type": "response",
            ...     "data": {"status": "ok"}
            ... })
            >>> if not success:
            ...     print("Failed to send message")
        """
        try:
            # 序列化为 JSON 并编码为 UTF-8
            encoded_content = json.dumps(message_content, ensure_ascii=False).encode('utf-8')

            # 检查消息大小
            content_length = len(encoded_content)
            if content_length > NativeMessagingProtocol.MAX_MESSAGE_SIZE:
                Logger.error(f"消息过大：{content_length} 字节（最大 {NativeMessagingProtocol.MAX_MESSAGE_SIZE}）")
                return False

            # 写入长度前缀 (4 bytes, Native Byte Order, Unsigned Int)
            sys.stdout.buffer.write(struct.pack('@I', content_length))

            # 写入消息内容
            sys.stdout.buffer.write(encoded_content)

            # 必须刷新缓冲区，否则 Chrome 可能收不到
            sys.stdout.buffer.flush()

            return True

        except TypeError as e:
            Logger.error(f"JSON 序列化错误（对象不可序列化）: {e}")
            return False
        except Exception as e:
            Logger.error(f"消息发送错误: {e}")
            return False
