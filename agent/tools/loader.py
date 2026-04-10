from __future__ import annotations
import json
import importlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

_PROFILES_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "agent_profiles"


class ToolLoader:
    """
    Dynamically loads and instantiates tools from a JSON configuration.
    Supports profile-based loading for agent-driven tool registration.
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self._config_cache: Optional[List[Dict[str, Any]]] = None

    def _load_config(self) -> List[Dict[str, Any]]:
        """Load and cache the tools.json configuration."""
        if self._config_cache is not None:
            return self._config_cache

        if not self.config_path.exists():
            logger.warning(f"Tool config not found at {self.config_path}")
            return []

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config_cache = json.load(f)
        except Exception as e:
            logger.error(f"Failed to parse tool config: {e}")
            return []

        return self._config_cache or []

    @staticmethod
    def _load_profile(profile_name: str) -> Dict[str, Any]:
        """Load an agent profile from config/agent_profiles/{profile_name}.json."""
        path = _PROFILES_DIR / f"{profile_name}.json"
        if not path.exists():
            logger.warning(f"Agent profile not found: {path}")
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to parse agent profile {path}: {e}")
            return {}

    def load_all(self, context: Dict[str, Any]) -> List[Any]:
        """Load all enabled tools from the configuration."""
        tools = []
        for entry in self._load_config():
            if not entry.get("enabled", True):
                continue
            tool = self._load_tool(entry, context)
            if tool:
                tools.append(tool)
        return tools

    def load_by_name(self, name: str, context: Dict[str, Any]) -> Optional[Any]:
        """Load a single tool by name from tools.json."""
        for entry in self._load_config():
            if entry.get("name") == name and entry.get("enabled", True):
                return self._load_tool(entry, context)
        return None

    def load_for_profile(self, profile_name: str, context: Dict[str, Any]) -> List[Any]:
        """
        Load tools for a specific agent profile.
        Falls back to load_all if profile not found.
        """
        profile = self._load_profile(profile_name)
        if not profile:
            logger.warning(f"Profile '{profile_name}' not found, falling back to load_all")
            return self.load_all(context)

        allowed = set(profile.get("tools", []))
        tools = []
        for entry in self._load_config():
            if entry.get("name") not in allowed:
                continue
            if not entry.get("enabled", True):
                continue
            tool = self._load_tool(entry, context)
            if tool:
                tools.append(tool)
        return tools

    def _load_tool(self, entry: Dict[str, Any], context: Dict[str, Any]) -> Optional[Any]:
        """
        Load a single tool entry with smart dependency injection.
        """
        import inspect
        class_path = entry.get("class")
        args_config = entry.get("args", {})

        if not class_path:
            logger.warning(f"Tool entry missing 'class': {entry}")
            return None

        try:
            # Import class
            module_name, class_name = class_path.rsplit('.', 1)
            module = importlib.import_module(module_name)
            tool_class = getattr(module, class_name)

            # 1. Inspect Constructor Signature
            signature = inspect.signature(tool_class.__init__)
            params = signature.parameters

            # 2. Smart Injection & Argument Resolution
            resolved_args = {}
            for param_name, _ in params.items():
                if param_name == "self":
                    continue

                # Priority 1: Explicit Override from tools.json
                if param_name in args_config:
                    val = args_config[param_name]
                    # Dynamic Context Resolution for {{placeholder}} syntax
                    if isinstance(val, str) and val.startswith("{{") and val.endswith("}}"):
                        context_key = val[2:-2].strip()
                        resolved_args[param_name] = context.get(context_key, val)
                    else:
                        resolved_args[param_name] = val

                # Priority 2: Implicit Injection from Agent Context
                elif param_name in context:
                    resolved_args[param_name] = context[param_name]
                    logger.debug(f"Successfully injected '{param_name}' into tool {class_name}")

                # Priority 3: Skip params with defaults (don't fail on optional session/vfs)
                elif _.default is not inspect.Parameter.empty:
                    continue

            # 3. Instantiate
            try:
                tool = tool_class(**resolved_args)
            except TypeError as e:
                logger.error(f"Injection mismatch for {class_name}: {e}. Resolved: {list(resolved_args.keys())}")
                return None

            # Attach metadata
            tool._name = entry.get("name", "")

            return tool

        except Exception as e:
            logger.error(f"Failed to load tool {class_name if 'class_name' in locals() else class_path}: {e}")
            return None
