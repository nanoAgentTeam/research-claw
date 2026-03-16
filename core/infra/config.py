"""
全局配置管理模块

提供系统的全局配置管理，包括路径、超参数、LLM 配置等。
所有配置从 settings.json 加载，并提供类属性访问接口。

设计理念：
- 单一配置源（settings.json）
- 启动时一次性加载（不支持热更新）
- 类属性访问（无需实例化）
- 自动路径解析和目录创建

配置结构：
    settings.json
    ├── paths: 文件路径配置
    ├── user_info: 用户信息
    ├── hyperparameters: AI 分析超参数
    ├── llm_access: LLM 供应商配置
    ├── features: 功能与 LLM 的映射
    └── external_services: 外部服务配置

典型用法：
    >>> from core.infra.config import Config
    >>> Config.ensure_dirs()  # 确保目录存在
    >>> log_path = Config.LOG_PATH
    >>> llm_config = Config.get_llm_config("chat")

依赖关系：
    - 依赖: settings.json 配置文件
    - 被依赖: 几乎所有其他模块

注意事项：
    - 该类在模块导入时即完成初始化
    - 修改 settings.json 后需要重启服务才能生效
    - 路径使用绝对路径，基于 BASE_DIR 计算
"""

import os
import json
from typing import Dict, Any


class Config:
    """
    全局配置类
    
    管理文件路径、目录结构、系统常量和 LLM 配置。
    
    属性分类：
        路径相关：
            - BASE_DIR: 项目根目录
            - LOG_PATH: 日志文件路径
            - DB_PATH: 数据库文件路径
            - DATA_PATH: 历史记录路径
            - CONTENT_BASE: 内容存储根目录
            - ...更多内容子目录
        
        用户信息：
            - USER_ID: 用户 ID
            - AVATAR_ID: 头像/化身 ID
            - LANGUAGE: 语言设置
        
        超参数：
            - LAYER2_HISTORY_LEN: Layer 2 历史长度
            - LAYER3_INTENT_HISTORY_LEN: Layer 3 意图历史长度
            - HISTORY_LOAD_LIMIT_LAYER1/2: 启动时加载的历史数量
        
        服务配置：
            - SEARCH_PROVIDER: 搜索引擎供应商
            - JINA_READER_KEY: Jina Reader API 密钥
    
    方法：
        get_llm_config(feature_name): 获取特定功能的 LLM 配置
        ensure_dirs(): 确保所有必要的目录存在
    
    线程安全性：
        该类仅使用类属性，在模块导入时初始化。
        如果多线程同时导入，可能会有竞态条件，但通常不会出现问题。
        读取操作是线程安全的（只读）。
    
    注意：
        - 所有路径都是绝对路径
        - 数据目录结构：data/{user_id}/{avatar_id}/
        - 如果 settings.json 不存在或解析失败，会使用空字典（导致部分配置缺失）
    """
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 加载 settings.json
    _settings_path = os.path.join(BASE_DIR, 'settings.json')
    _data = {}
    try:
        if os.path.exists(_settings_path):
            with open(_settings_path, 'r', encoding='utf-8') as f:
                _data = json.load(f)
    except Exception:
        pass

    _paths = _data.get('paths', {})
    _user_info = _data.get('user_info', {})
    _hyperparams = _data.get('hyperparameters', {})
    
    # New Config Structure
    _llm_access = _data.get('llm_access', {})
    DEFAULT_PROVIDER = _data.get('default_provider', 'openai')
    _external_services = _data.get('external_services', {})

    # Jina Reader Key
    JINA_READER_KEY = _external_services.get('jina', {}).get('api_key', '')

    # Langfuse Config
    _langfuse_config = _external_services.get('langfuse', {})
    LANGFUSE_ENABLED = _langfuse_config.get('enabled', True)
    LANGFUSE_HOST = _langfuse_config.get('host', 'https://cloud.langfuse.com')
    LANGFUSE_PUBLIC_KEY = "" # To be loaded from keys.json
    LANGFUSE_SECRET_KEY = "" # To be loaded from keys.json

    # Hyperparameters
    LAYER2_HISTORY_LEN = _hyperparams.get('layer2_history_len', 3)
    LAYER2_FUTURE_LEN = _hyperparams.get('layer2_future_len', 5)

    # Agent Path
    AGENTS_DIR = os.path.join(BASE_DIR, "agents")

    # 加载 prompts.json
    PROMPTS = {}

    # 用户信息
    USER_ID = _user_info.get('user_id', 'default_user')
    AVATAR_ID = _user_info.get('avatar_id', 'default_avatar')
    LANGUAGE = _user_info.get('language', 'zh') # 默认中文
    LLM_LANGUAGE = _user_info.get('llm_language', 'auto')  # LLM 回复语言

    @classmethod
    def get_reply_language(cls):
        """Return the effective language for auto-replies.

        LLM_LANGUAGE takes priority; 'auto' falls back to LANGUAGE.
        """
        llm_lang = getattr(cls, 'LLM_LANGUAGE', 'auto')
        if llm_lang and llm_lang != 'auto':
            return llm_lang
        return getattr(cls, 'LANGUAGE', 'zh')

    @classmethod
    def load_runtime_keys(cls, keys_path: str):
        """显式加载运行时密钥文件并更新配置"""
        if not keys_path or not os.path.exists(keys_path):
            return 
            
        try:
            with open(keys_path, 'r', encoding='utf-8') as f:
                keys_data = json.load(f)
                
            # 将密钥合并到现有的供应商配置中
            for provider, key in keys_data.items():
                if provider == "langfuse_public_key":
                    cls.LANGFUSE_PUBLIC_KEY = key
                elif provider == "langfuse_secret_key":
                    cls.LANGFUSE_SECRET_KEY = key
                elif provider == "langfuse_host":
                    cls.LANGFUSE_HOST = key
                elif provider in cls._llm_access:
                    cls._llm_access[provider]['api_key'] = key
                else:
                    # 如果 settings.json 里没配置这个供应商，也允许直接通过 keys.json 注入
                    cls._llm_access[provider] = {'api_key': key}
        except Exception:
            pass

    @classmethod
    def initialize(cls, keys_path: str = None):
        """
        全系统配置初始化入口。
        优先级：1. 显式传入的 keys_path 2. 自动探测 keys.json 3. 环境变量 (最高优先级)
        """
        # 1) 显式路径优先
        if keys_path and os.path.exists(keys_path):
            cls.load_runtime_keys(keys_path)

        # 2) 自动探测默认 keys.json（作为兜底）
        elif not keys_path:
            auto_keys = os.path.join(cls.BASE_DIR, "keys.json")
            if os.path.exists(auto_keys):
                cls.load_runtime_keys(auto_keys)

        # 3) 环境变量覆盖（必须始终执行，且放在最后以保证最高优先级）
        cls._apply_env_overrides()

    @classmethod
    def _apply_env_overrides(cls):
        """从环境变量中提取配置进行覆盖"""
        if os.environ.get("LANGFUSE_PUBLIC_KEY"):
            cls.LANGFUSE_PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
        if os.environ.get("LANGFUSE_SECRET_KEY"):
            cls.LANGFUSE_SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
        
        # 允许通过环境变量注入 LLM 密钥，例如 QWEN_API_KEY
        # Iterate over both configured providers AND env vars matching pattern
        # This allows adding new providers via ENV even if not in settings.json
        
        # 1. Update existing
        for provider in cls._llm_access.keys():
            env_var = f"CONTEXT_BOT_PROVIDERS__{provider.upper()}__API_KEY"
            # Support both short (PROVIDER_API_KEY) and nested format for backward compatibility
            short_env = f"{provider.upper()}_API_KEY"
            
            if os.environ.get(env_var):
                cls._llm_access[provider]['api_key'] = os.environ[env_var]
            elif os.environ.get(short_env):
                cls._llm_access[provider]['api_key'] = os.environ[short_env]

        # 2. Discover new providers from ENV (CONTEXT_BOT_PROVIDERS__*__API_KEY)
        prefix = "CONTEXT_BOT_PROVIDERS__"
        suffix = "__API_KEY"
        for key, value in os.environ.items():
            if key.startswith(prefix) and key.endswith(suffix):
                provider = key[len(prefix):-len(suffix)].lower()
                if provider not in cls._llm_access:
                    cls._llm_access[provider] = {'api_key': value}
                    
                # Auto-inject base_url for known providers if missing
                if provider == "step":
                    if "base_url" not in cls._llm_access[provider]:
                        cls._llm_access[provider]["base_url"] = "https://api.stepfun.com/v1"
                    if "model" not in cls._llm_access[provider]:
                        cls._llm_access[provider]["model"] = "step-3.5-flash"


    @classmethod
    def get_provider_config(cls, provider_key: str) -> Dict[str, Any]:
        """
        直接通过供应商标识符获取配置
        
        Args:
            provider_key: 供应商标识符，如 "qwen", "openai"
            
        Returns:
            dict: 包含 api_key, model, base_url 等的配置字典
        """
        return cls._llm_access.get(provider_key, {})

    @classmethod
    def get_llm_config(cls, feature_name: str) -> Dict[str, Any]:
        """
        获取指定功能的 LLM 配置（已废弃，建议使用 get_provider_config）
        
        注意：现在 features 映射已移除，默认返回 DEFAULT_PROVIDER 的配置。
        """
        return cls.get_provider_config(cls.DEFAULT_PROVIDER)

    # Search Configuration
    SEARCH_PROVIDER = _data.get('search', {}).get('provider', 'duckduckgo')

    # 从配置或默认值获取路径
    _LOG_ROOT = _paths.get('log_root', './logs')
    _DATA_ROOT = _paths.get('data_root', './data')
    _RESOURCES_ROOT = _paths.get('resources_root', './resources')

    _RUN_LOG_FILE = _paths.get('run_log_file', 'run.log')
    _DB_FILE = _paths.get('db_file', 'context_bot.db')
    
    _CONTENT_SUBDIR = _paths.get('content_subdir', 'content')

    # 构建基础数据目录: data/user_id/avatar_id/
    BASE_DATA_PATH = os.path.join(BASE_DIR, _DATA_ROOT, USER_ID, AVATAR_ID)
    
    # 运行日志路径 (logs/run.log)
    LOG_PATH = os.path.join(BASE_DIR, _LOG_ROOT, _RUN_LOG_FILE)
    
    # 数据库路径 (数据根目录下)
    DB_PATH = os.path.join(BASE_DATA_PATH, _DB_FILE)

    # 内容目录 (content 子目录)
    CONTENT_BASE = os.path.join(BASE_DATA_PATH, _CONTENT_SUBDIR)
    
    # 内容存储目录
    PDF_DIR = os.path.join(CONTENT_BASE, 'pdfs')              # PDF 文件
    
    # Global User Config
    GLOBAL_USER_CONFIG_FILE = os.path.join(BASE_DIR, 'global_user_config.json')
    # User Config (stored under user_id/avatar_id)
    USER_CONFIG_FILE = os.path.join(BASE_DATA_PATH, 'user_config.json')

    @classmethod
    def ensure_dirs(cls) -> None:
        """
        确保所有必要的目录存在
        """
        # 确保日志目录存在
        log_dir = os.path.dirname(cls.LOG_PATH)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # 确保用户数据目录存在
        if not os.path.exists(cls.BASE_DATA_PATH):
            os.makedirs(cls.BASE_DATA_PATH)
            
        # 确保 content 及其子目录存在
        for d in [cls.CONTENT_BASE, cls.PDF_DIR]:
            if not os.path.exists(d):
                os.makedirs(d)

# 自动加载初始化配置 (keys.json & env vars)
Config.initialize()

# 搜索引擎参数映射
# 用于从 URL 中提取搜索关键词
SEARCH_PARAMS = {
    'google.': 'q', 
    'bing.com': 'q', 
    'baidu.com': 'wd',
    'duckduckgo.com': 'q', 
    'youtube.com': 'search_query',
    'sogou.com': 'query', 
    'so.com': 'q'
}
