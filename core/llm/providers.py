"""
LLM 供应商抽象层

提供统一的 LLM 客户端创建接口，屏蔽不同供应商的差异。

主要功能：
    - 自动加载配置：从 Config 读取 LLM 访问配置
    - 客户端工厂：创建 OpenAI 兼容的客户端
    - 模型名称获取：提取配置中的模型标识

设计理念：
    - 工厂模式：统一创建接口
    - 配置驱动：所有参数从 settings.json 加载
    - 静默降级：缺少依赖或配置时返回 None
    - 日志记录：错误和警告通过 Logger 输出

依赖关系：
    - 依赖: core.infra.config.Config, core.utils.logger.Logger, openai (可选)
    - 被依赖: core.llm.engine.AgentEngine

典型配置示例：
    settings.json:
    {
      "features": {
        "chat_llm": "openai_gpt4",
        "analysis_llm": "deepseek"
      },
      "llm_access": {
        "openai_gpt4": {
          "api_key": "sk-...",
          "base_url": "https://api.openai.com/v1",
          "model": "gpt-4-turbo"
        },
        "deepseek": {
          "api_key": "sk-...",
          "base_url": "https://api.deepseek.com/v1",
          "model": "deepseek-chat"
        }
      }
    }

使用流程：
    1. 调用 create_client(provider_key="qwen")
    2. LLMFactory 从 Config.get_provider_config("qwen") 获取配置
    3. LLMFactory 创建 OpenAI(api_key=..., base_url=...)
    4. 返回客户端实例

兼容性：
    - 支持所有 OpenAI 兼容的 API（OpenAI, Azure, DeepSeek, 通义千问等）
    - 如果未安装 openai 包，create_client 返回 None
"""

import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI, AsyncOpenAI

try:
    from openai import OpenAI, AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None  # type: ignore
    AsyncOpenAI = None # type: ignore

# Try to import Langfuse OpenAI wrapper only if not disabled
LangfuseOpenAI = None
if os.environ.get("DISABLE_LANGFUSE", "").lower() != "true":
    try:
        from langfuse.openai import AsyncOpenAI as LangfuseOpenAI
    except ImportError:
        LangfuseOpenAI = None

from core.infra.config import Config
from core.utils.logger import Logger


class LLMFactory:
    """
    LLM 客户端工厂类
    
    提供静态方法创建 LLM 客户端和获取模型配置。
    
    工厂模式优势：
        - 统一接口：所有 LLM 客户端通过相同的方法创建
        - 配置解耦：客户端创建逻辑与业务逻辑分离
        - 易于测试：可以 mock 整个工厂类
    
    典型用法：
        >>> # 创建默认供应商的客户端
        >>> client = LLMFactory.create_client()
        >>> if client:
        ...     model = LLMFactory.get_model_name()
        ...     response = client.chat.completions.create(
        ...         model=model,
        ...         messages=[{"role": "user", "content": "Hello"}]
        ...     )
        
        >>> # 创建特定供应商的客户端
        >>> qwen_client = LLMFactory.create_client("qwen")
    
    错误处理：
        - 缺少 openai 包：记录 ERROR 日志，返回 None
        - 缺少 API Key：记录 WARNING 日志，返回 None
        - 配置不存在：Config.get_llm_config 返回 {}，后续逻辑返回 None
    """
    
    @staticmethod
    def create_client(provider_key: Optional[str] = None, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: float = 60.0) -> Optional[AsyncOpenAI]:
        """
        创建 LLM 客户端 (Async)
        
        Args:
            provider_key: 供应商标识符（对应 settings.json 中的 llm_access 键）。
                          如果为 None，则使用 Config.DEFAULT_PROVIDER。
            api_key: 可选的 API Key 覆盖 (如果不为 None, 则忽略配置)
            base_url: 可选的 Base URL 覆盖 (如果不为 None, 则忽略配置)
        """
        if not HAS_OPENAI:
            Logger.error("OpenAI package not installed.")
            return None
            
        key = provider_key or Config.DEFAULT_PROVIDER
        
        # Priority: Explicit args > Config
        if not api_key:
            llm_config = Config.get_provider_config(key)
            api_key = llm_config.get("api_key")
            # Only override base_url from config if not provided explicitly
            if not base_url:
                base_url = llm_config.get("base_url")
        else:
            # If explicit key provided but no base_url, try to get base_url from config
            # or just leave it None (OpenAI default)
             if not base_url:
                llm_config = Config.get_provider_config(key)
                base_url = llm_config.get("base_url")

        if not api_key:
            Logger.error(f"LLM API Key missing for provider '{key}'")
            return None
        
        # 调试日志：脱敏打印使用的 Key
        masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
        model_name = Config.get_provider_config(key).get("model", "unknown")
        print(f"[LLM] Creating client for provider: {key}, model: {model_name}, base_url: {base_url}, key: {masked_key}")

        # 归一化 base_url：去掉尾部 /v1 后再补回（OpenAI SDK 需要 /v1）
        if base_url:
            base_url = base_url.rstrip("/")
            if not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"

        # Langfuse keys (from Config first, fallback env). Only enable when both exist and not disabled.
        lf_public = Config.LANGFUSE_PUBLIC_KEY or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        lf_secret = Config.LANGFUSE_SECRET_KEY or os.environ.get("LANGFUSE_SECRET_KEY", "")
        disable_lf = os.environ.get("DISABLE_LANGFUSE", "").lower() == "true"
        
        # 设置合理的超时时间，防止网络波动导致的 timeout
        if not disable_lf and lf_public and lf_secret and LangfuseOpenAI:
            return LangfuseOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        
        return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @staticmethod
    def get_api_key(provider_key: Optional[str] = None) -> Optional[str]:
        """获取供应商 API Key"""
        key = provider_key or Config.DEFAULT_PROVIDER
        llm_config = Config.get_provider_config(key)
        return llm_config.get("api_key")

    @staticmethod
    def get_base_url(provider_key: Optional[str] = None) -> Optional[str]:
        """获取供应商 Base URL"""
        key = provider_key or Config.DEFAULT_PROVIDER
        llm_config = Config.get_provider_config(key)
        return llm_config.get("base_url")

    @staticmethod
    def get_model_name(provider_key: Optional[str] = None) -> str:
        """
        获取模型名称
        """
        key = provider_key or Config.DEFAULT_PROVIDER
        llm_config = Config.get_provider_config(key)
        return llm_config.get("model", "none")

