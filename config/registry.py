"""Typed configuration registry for all externalized config.

Loads commands.json, roles.json, vfs.json and prompt templates.
Provides a single source of truth for all configuration that was previously hardcoded.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field
from loguru import logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_front_matter(text: str) -> str:
    """Strip YAML front matter (--- ... ---) from template text."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    body_start = end + 4
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    return text[body_start:]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandDef:
    """Single command definition from commands.json."""
    name: str
    handler: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    requires_args: bool = False
    requires_mode: Optional[str] = None
    require_default: bool = False
    require_project: bool = False
    args_usage: str = ""
    tool_name: str = ""
    tool_arg_key: str = ""


@dataclass(frozen=True)
class ModeDef:
    """Single mode definition from modes.json."""
    name: str
    label: str = ""
    guidance: str = ""
    thinking_pattern: str = ""
    default_project_id: Optional[str] = None
    default_session_id: Optional[str] = None
    requires_project_id: bool = False
    requires_session_id: bool = False
    vfs_layers: tuple[str, ...] = ("session",)


@dataclass(frozen=True)
class RolePermissions:
    """Permission set for a role type."""
    project_core_write: bool = False
    can_switch_mode: bool = False
    can_spawn_agents: bool = False
    git_commit: bool = False


@dataclass(frozen=True)
class RoleTypeDef:
    """Single role type definition from roles.json."""
    name: str
    display_name: str = ""
    is_privileged: bool = False
    permissions: RolePermissions = field(default_factory=RolePermissions)
    prompt_template: str = ""


@dataclass(frozen=True)
class VFSPathConfig:
    """Path configuration from vfs.json (used for memory_paths)."""
    session_dirs: tuple[str, ...] = (".bot", "artifacts", "shared")
    non_worker_dirs: tuple[str, ...] = ("subagents",)
    research_dirs: tuple[str, ...] = ("artifacts", "subagents", "tasks")
    special_paths: dict[str, str] = field(default_factory=dict)
    memory_paths: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ConfigRegistry:
    """
    Central registry that loads and caches all externalized configuration.

    Usage:
        registry = ConfigRegistry()  # loads from config/ directory
        cmd = registry.get_command("/reset")
        mode = registry.get_mode("CHAT")
        registry.is_privileged_role("Commander")  # True
    """

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path(__file__).parent
        self._config_dir = config_dir
        self._prompts_dir = config_dir / "prompts"

        self._commands: dict[str, CommandDef] = {}
        self._command_aliases: dict[str, str] = {}
        self._modes: dict[str, ModeDef] = {}
        self._role_types: dict[str, RoleTypeDef] = {}
        self._privileged_roles: set[str] = set()
        self._leader_roles: set[str] = set()
        self._vfs_config: Optional[VFSPathConfig] = None

        self._load_commands()
        self._load_modes()
        self._load_roles()
        self._load_vfs()

    # ---- internal loaders ----

    def _read_json(self, filename: str) -> dict[str, Any]:
        path = self._config_dir / filename
        if not path.exists():
            logger.warning(f"Config file not found: {path}")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_commands(self) -> None:
        data = self._read_json("commands.json")
        for entry in data.get("commands", []):
            cmd = CommandDef(
                name=entry["name"],
                handler=entry.get("handler", ""),
                description=entry.get("description", ""),
                aliases=tuple(entry.get("aliases", [])),
                requires_args=entry.get("requires_args", False),
                requires_mode=entry.get("requires_mode"),
                require_default=entry.get("require_default", False),
                require_project=entry.get("require_project", False),
                args_usage=entry.get("args_usage", ""),
                tool_name=entry.get("tool_name", ""),
                tool_arg_key=entry.get("tool_arg_key", ""),
            )
            self._commands[cmd.name] = cmd
            for alias in cmd.aliases:
                self._command_aliases[alias] = cmd.name

    def _load_modes(self) -> None:
        data = self._read_json("modes.json")
        for name, entry in data.get("modes", {}).items():
            key = name.upper()
            self._modes[key] = ModeDef(
                name=key,
                label=entry.get("label", ""),
                guidance=entry.get("guidance", ""),
                thinking_pattern=entry.get("thinking_pattern", ""),
                default_project_id=entry.get("default_project_id"),
                default_session_id=entry.get("default_session_id"),
                requires_project_id=entry.get("requires_project_id", False),
                requires_session_id=entry.get("requires_session_id", False),
                vfs_layers=tuple(entry.get("vfs_layers", ["session"])),
            )

    def _load_roles(self) -> None:
        data = self._read_json("roles.json")
        for name, entry in data.get("role_types", {}).items():
            perms_data = entry.get("permissions", {})
            perms = RolePermissions(**{
                k: v for k, v in perms_data.items()
                if k in RolePermissions.__dataclass_fields__
            })
            self._role_types[name] = RoleTypeDef(
                name=name,
                display_name=entry.get("display_name", name),
                is_privileged=entry.get("is_privileged", False),
                permissions=perms,
                prompt_template=entry.get("prompt_template", ""),
            )
        self._privileged_roles = set(data.get("privileged_roles", []))
        self._leader_roles = set(data.get("leader_roles", []))

    def _load_vfs(self) -> None:
        data = self._read_json("vfs.json")
        ds = data.get("directory_structure", {})
        self._vfs_config = VFSPathConfig(
            session_dirs=tuple(ds.get("session_dirs", [".bot", "artifacts", "shared"])),
            non_worker_dirs=tuple(ds.get("non_worker_dirs", ["subagents"])),
            research_dirs=tuple(ds.get("research_dirs", ["artifacts", "subagents", "tasks"])),
            special_paths=data.get("special_paths", {}),
            memory_paths=data.get("memory_paths", {}),
        )

    # ---- public API: commands ----

    def get_command(self, name: str) -> Optional[CommandDef]:
        """Resolve a command name (or alias) to its definition."""
        canonical = self._command_aliases.get(name, name)
        return self._commands.get(canonical)

    def get_all_commands(self) -> dict[str, CommandDef]:
        return dict(self._commands)

    def list_command_names(self) -> list[str]:
        """All command names including aliases."""
        names = list(self._commands.keys())
        names.extend(self._command_aliases.keys())
        return sorted(set(names))

    # ---- public API: modes ----

    def get_mode(self, name: str) -> Optional[ModeDef]:
        return self._modes.get(name.upper())

    def get_all_modes(self) -> dict[str, ModeDef]:
        return dict(self._modes)

    # ---- public API: roles ----

    def get_role_type(self, name: str) -> Optional[RoleTypeDef]:
        return self._role_types.get(name)

    def is_privileged_role(self, role_name: str) -> bool:
        return role_name in self._privileged_roles

    def is_leader_role(self, role_name: str) -> bool:
        return role_name in self._leader_roles

    @property
    def privileged_roles(self) -> set[str]:
        return set(self._privileged_roles)

    @property
    def leader_roles(self) -> set[str]:
        return set(self._leader_roles)

    # ---- public API: VFS ----

    @property
    def vfs_config(self) -> VFSPathConfig:
        if self._vfs_config is None:
            return VFSPathConfig()
        return self._vfs_config

    def get_special_path(self, key: str, default: str = "") -> str:
        """Get a special path from vfs.json, e.g. 'shared_notes' -> 'shared/SHARED_NOTES.md'."""
        return self.vfs_config.special_paths.get(key, default)

    def get_memory_path(self, key: str, default: str = "") -> str:
        """Get a memory path from vfs.json, e.g. 'history_dir' -> 'memory/history'."""
        return self.vfs_config.memory_paths.get(key, default)

    # ---- public API: prompts ----

    def load_prompt_template(self, template_name: str) -> str:
        """Load a raw prompt template file from config/prompts/."""
        path = self._prompts_dir / template_name
        if not path.exists():
            logger.debug(f"Prompt template not found: {path}")
            return ""
        return _strip_front_matter(path.read_text(encoding="utf-8"))

    def render_prompt(self, template_name: str, **kwargs: Any) -> str:
        """Load and render a prompt template with variable substitution."""
        template = self.load_prompt_template(template_name)
        if not template:
            return ""
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError) as e:
            logger.warning(f"Template variable error ({type(e).__name__}: {e}) in {template_name}")
            return template
