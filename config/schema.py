"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""
    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames


class FeishuConfig(BaseModel):
    """Feishu (Lark) channel configuration."""
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""  # Optional for WS, needed for webhook
    encrypt_key: str = ""         # Optional
    allow_from: list[str] = Field(default_factory=list)  # Allowed open_ids


class QQConfig(BaseModel):
    """QQ channel configuration."""
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allow_from: list[str] = Field(default_factory=list)


class DingTalkConfig(BaseModel):
    """DingTalk channel configuration."""
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    robot_code: str = ""
    corp_id: str = ""
    agent_id: str = ""
    allow_from: list[str] = Field(default_factory=list)
    dm_policy: str = "open"
    group_policy: str = "open"


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""
    enable_channel: str = ""  # 启用的通道: "qq", "dingtalk", "feishu", "telegram", "", 等
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)


class AgentDefaults(BaseModel):
    """Default agent configuration."""
    workspace: str = "./workspace"
    model: str = "anthropic/claude-3-5-sonnet-20240620"
    max_tokens: Optional[int] = None
    temperature: float = 0.7
    max_tool_iterations: int = 20


class AgentsConfig(BaseModel):
    """Agent configuration."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    api_key: str = ""
    api_base: Optional[str] = None
    model: Optional[str] = None


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    step: ProviderConfig = Field(default_factory=ProviderConfig)
    qwen: ProviderConfig = Field(default_factory=ProviderConfig)
    ltcraft: ProviderConfig = Field(default_factory=ProviderConfig)


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""
    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""
    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(BaseModel):
    """Web tools configuration."""
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class AcademicConfig(BaseModel):
    """Academic tools configuration."""
    semanticscholar_api_key: str = ""


class ToolsConfig(BaseModel):
    """Tools configuration."""
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    academic: AcademicConfig = Field(default_factory=AcademicConfig)


class HistoryConfig(BaseModel):
    """History management configuration."""
    enabled: bool = True
    max_recent_messages: int = 20
    summary_threshold: int = 20
    summary_limit: int = 10


class MemoryConfig(BaseModel):
    """Memory loading configuration."""
    short_term_enabled: bool = True
    long_term_enabled: bool = True


class AgentFeatureConfig(BaseModel):
    """Agent execution features."""
    max_iterations: int = 100
    auto_summarize: bool = True
    token_limit_padding: int = 1000


class ProjectFeatureConfig(BaseModel):
    """Project behavior features."""
    cleanup_age_hours: int = 24
    git_auto_commit: bool = True


class ToolsFeatureConfig(BaseModel):
    """Tool-level feature toggles."""
    search_max_results: int = 10
    browser_headless: bool = True


class FeaturesConfig(BaseModel):
    """Feature toggle configuration."""
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agent: AgentFeatureConfig = Field(default_factory=AgentFeatureConfig)
    project: ProjectFeatureConfig = Field(default_factory=ProjectFeatureConfig)
    tools: ToolsFeatureConfig = Field(default_factory=ToolsFeatureConfig)


from typing import Literal, Dict, Optional as _Optional


class ProviderInstance(BaseModel):
    """Configuration for a specific provider instance."""
    id: str = "default"
    provider: str = "anthropic"  # anthropic, openai, gemini, etc.
    model_name: str = "claude-3-5-sonnet-20240620"
    api_key: str = ""
    api_base: Optional[str] = None
    enabled: bool = True


class ChannelAccount(BaseModel):
    """Configuration for a channel account."""
    id: str
    platform: Literal["feishu", "telegram", "whatsapp", "qq", "dingtalk"]
    enabled: bool = True
    credentials: Dict[str, str] = Field(default_factory=dict)


class ProviderInstancesConfig(BaseModel):
    """Configuration for provider instances management."""
    active_id: str = "default"
    instances: list[ProviderInstance] = Field(default_factory=list)


class ChannelAccountsConfig(BaseModel):
    """Configuration for channel accounts management."""
    accounts: list[ChannelAccount] = Field(default_factory=list)


class PushSubscription(BaseModel):
    """Push subscription entry for automation notifications."""
    id: str
    channel: str
    chat_id: str = ""
    params: Dict[str, str] = Field(default_factory=dict)
    apprise_url: str = ""
    enabled: bool = True
    remark: str = ""


class PushSubscriptionsConfig(BaseModel):
    """Global push subscription settings."""
    items: list[PushSubscription] = Field(default_factory=list)


class SmtpProfile(BaseModel):
    """SMTP profile managed from Web UI."""
    id: str
    name: str = ""
    provider: str = "custom"
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = ""
    use_tls: bool = True
    enabled: bool = True
    is_default: bool = False


class SmtpConfig(BaseModel):
    """SMTP profile settings."""
    profiles: list[SmtpProfile] = Field(default_factory=list)


class OverleafSettings(BaseModel):
    """Global Overleaf instance configuration. Set via `python cli/main.py login`."""
    base_url: str = ""  # Empty means not configured. e.g. "https://www.overleaf.com" or "https://latex.cstcloud.cn"
    login_path: str = "/login"
    cookie_names: list[str] = Field(default_factory=lambda: ["overleaf_session2"])


class UserInfoConfig(BaseModel):
    """User information and preferences."""
    language: str = "zh"
    llm_language: str = "auto"  # LLM 回复语言: auto, zh, en, ja, ko, etc.


_PROVIDER_DEFAULT_BASES: Dict[str, str] = {
    "qwen":       "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "step":       "https://api.stepfun.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ltcraft":    "https://ai.ltcraft.cn:12000/v1",
}

_PROVIDER_FALLBACK_ORDER = [
    "ltcraft", "qwen", "step", "openai", "openrouter",
    "anthropic", "zhipu", "groq", "vllm",
]


class Config(BaseSettings):
    """Root configuration for Research Claw."""
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)

    # Explicit default provider name, e.g. "qwen", "step", "openai"
    default_provider: str = ""

    # New unified config fields (renamed from llm/im to provider/channel)
    provider: ProviderInstancesConfig = Field(default_factory=ProviderInstancesConfig)
    channel: ChannelAccountsConfig = Field(default_factory=ChannelAccountsConfig)
    push_subscriptions: PushSubscriptionsConfig = Field(default_factory=PushSubscriptionsConfig)
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)
    user_info: UserInfoConfig = Field(default_factory=UserInfoConfig)
    overleaf: OverleafSettings = Field(default_factory=OverleafSettings)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path. Resolves relative paths against project root."""
        from config.loader import get_project_root
        workspace = self.agents.defaults.workspace
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            path = (get_project_root() / path).resolve()
        return path

    def get_active_provider(self) -> Optional[ProviderInstance]:
        """Get the currently active provider instance."""
        for instance in self.provider.instances:
            if instance.id == self.provider.active_id:
                return instance if instance.enabled else None
        return None

    def _resolve_provider(self) -> tuple:
        """Return (api_key, api_base, model) all from ONE consistent provider.

        Priority:
          1. Unified ProviderInstance (provider.instances) — main path, unchanged.
          2. Explicit default_provider name in settings.
          3. Consistent fallback chain (same order for all three fields).
        """
        # 1. Unified provider config — highest priority, main path
        active = self.get_active_provider()
        if active and active.enabled:
            return active.api_key, active.api_base, active.model_name

        # 2. Explicit default_provider
        if self.default_provider:
            p = getattr(self.providers, self.default_provider, None)
            if p and p.api_key:
                base = p.api_base or _PROVIDER_DEFAULT_BASES.get(self.default_provider, "")
                model = p.model or self.agents.defaults.model
                return p.api_key, base, model

        # 3. Consistent fallback — same priority order for key / base / model
        for name in _PROVIDER_FALLBACK_ORDER:
            p = getattr(self.providers, name, None)
            if p and p.api_key:
                base = p.api_base or _PROVIDER_DEFAULT_BASES.get(name, "")
                model = getattr(p, "model", None) or self.agents.defaults.model
                return p.api_key, base, model

        return None, None, self.agents.defaults.model

    def get_api_key(self) -> Optional[str]:
        key, _, _ = self._resolve_provider()
        return key

    def get_api_base(self) -> Optional[str]:
        _, base, _ = self._resolve_provider()
        return base

    def get_api_model(self) -> Optional[str]:
        _, _, model = self._resolve_provider()
        return model

    def sync_from_unified_config(self) -> None:
        """
        Sync legacy providers/channels from unified provider/channel config.

        This populates the legacy fields for backward compatibility with
        existing code that uses config.providers and config.channels.
        """
        # Sync providers from provider config
        for instance in self.provider.instances:
            provider_name = instance.provider
            if hasattr(self.providers, provider_name):
                provider = getattr(self.providers, provider_name)
                provider.api_key = instance.api_key
                provider.api_base = instance.api_base
                if instance.model_name:
                    # For step provider, also set the model field
                    if hasattr(provider, "model"):
                        provider.model = instance.model_name

        # Sync channels from channel config
        for account in self.channel.accounts:
            if account.platform == "feishu" and account.enabled:
                self.channels.feishu.enabled = True
                self.channels.feishu.app_id = account.credentials.get("app_id", "")
                self.channels.feishu.app_secret = account.credentials.get("app_secret", "")
            elif account.platform == "qq" and account.enabled:
                self.channels.qq.enabled = True
                self.channels.qq.app_id = account.credentials.get("app_id", "")
                self.channels.qq.app_secret = account.credentials.get("app_secret", "")
            elif account.platform == "telegram" and account.enabled:
                self.channels.telegram.enabled = True
                self.channels.telegram.token = account.credentials.get("token", "")
            elif account.platform == "whatsapp" and account.enabled:
                self.channels.whatsapp.enabled = True
                self.channels.whatsapp.bridge_url = account.credentials.get("bridge_url", "")
            elif account.platform == "dingtalk" and account.enabled:
                self.channels.dingtalk.enabled = True
                self.channels.dingtalk.client_id = account.credentials.get("client_id", "")
                self.channels.dingtalk.client_secret = account.credentials.get("client_secret", "")
                self.channels.dingtalk.robot_code = account.credentials.get("robot_code", "")
                self.channels.dingtalk.corp_id = account.credentials.get("corp_id", "")
                self.channels.dingtalk.agent_id = account.credentials.get("agent_id", "")

    class Config:
        env_prefix = "CONTEXT_BOT_"
        env_nested_delimiter = "__"
