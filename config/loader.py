"""Configuration loading utilities."""

import json
from pathlib import Path
from typing import Any, Optional

from config.schema import Config


def get_project_root() -> Path:
    """Get the project root directory (where settings.json lives)."""
    # Try current directory first
    if Path("settings.json").exists():
        return Path(".").resolve()
    # Fallback to the directory containing the 'config' package
    return Path(__file__).parent.parent.resolve()


def get_config_path() -> Path:
    """Get the configuration file path. Prioritizes local settings.json in project root."""
    local_config = get_project_root() / "settings.json"
    if local_config.exists():
        return local_config
    return Path.home() / ".open_research_claw" / "config.json"


def get_bot_dir() -> Path:
    """Get the internal bot data directory."""
    return get_project_root() / ".bot"


def get_data_dir() -> Path:
    """Get the data directory (Session Memory)."""
    # Prioritize local hidden .bot/memory folder
    local_data = get_bot_dir() / "memory"
    if local_data.exists():
        return local_data
    return Path.home() / ".open_research_claw" / "data"


def get_features_path() -> Path:
    """Get the features configuration file path."""
    return get_project_root() / "config" / "features.json"


def load_config(config_path: Optional[Path] = None) -> Config:
    """
    Load configuration from file or create default.
    Also loads features.json if it exists.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    config_data = {}
    if path.exists():
        try:
            with open(path) as f:
                config_data = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")

    # Load features separately
    features_path = get_features_path()
    if features_path.exists():
        try:
            with open(features_path) as f:
                features_data = json.load(f)
                config_data["features"] = features_data
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load features from {features_path}: {e}")

    config = Config()
    if config_data:
        try:
            config = Config.model_validate(convert_keys(config_data))
        except ValueError as e:
            print(f"Warning: Config validation failed: {e}")
            print("Using default configuration.")

    # Sync legacy providers/channels from unified llm/im config
    config.sync_from_unified_config()

    return config


def save_config(config: Config, config_path: Optional[Path] = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    import traceback
    from loguru import logger

    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to camelCase format, excluding legacy providers/channels
    data = config.model_dump(exclude={"providers", "channels"})
    data = convert_to_camel(data)

    # Log who is saving and warn if critical sections are empty
    caller = traceback.extract_stack(limit=3)[0]
    provider_count = len(config.provider.instances) if config.provider else 0
    account_count = len(config.channel.accounts) if config.channel else 0
    logger.info(f"save_config: providers={provider_count} accounts={account_count} "
                f"caller={caller.filename}:{caller.lineno}")
    if provider_count == 0 and account_count == 0 and path.exists():
        logger.warning(f"save_config: saving EMPTY config (no providers, no accounts) — "
                       f"this may overwrite existing settings! caller={caller.filename}:{caller.lineno}")

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def convert_keys(data: Any) -> Any:
    """Convert camelCase keys to snake_case for Pydantic."""
    if isinstance(data, dict):
        return {camel_to_snake(k): convert_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def convert_to_camel(data: Any) -> Any:
    """Convert snake_case keys to camelCase."""
    if isinstance(data, dict):
        return {snake_to_camel(k): convert_to_camel(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_to_camel(item) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class ConfigService:
    """Service to manage configuration lifecycle and hot-reloading."""
    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigService, cls).__new__(cls)
        return cls._instance

    @property
    def config(self) -> Config:
        """Get the current configuration."""
        if self._config is None:
            self._config = load_config()
        return self._config

    def refresh(self) -> None:
        """Reload configuration from disk."""
        self._config = load_config()

    def save(self, config: Optional[Config] = None) -> None:
        """Save the current or provided configuration to disk."""
        to_save = config or self._config
        if to_save:
            save_config(to_save)
            self._config = to_save

    def update_llm(self, instance: Any) -> None:
        """Add or update an LLM instance."""
        # This will be used by the wizard and API
        existing = next((i for i in self.config.provider.instances if i.id == instance.id), None)
        if existing:
            self.config.provider.instances.remove(existing)
        self.config.provider.instances.append(instance)
        self.save()

    def update_im(self, account: Any) -> None:
        """Add or update an IM account."""
        existing = next((a for a in self.config.channel.accounts if a.id == account.id), None)
        if existing:
            self.config.channel.accounts.remove(existing)
        self.config.channel.accounts.append(account)
        self.save()


def get_config_service() -> ConfigService:
    """Get the global configuration service instance."""
    return ConfigService()
