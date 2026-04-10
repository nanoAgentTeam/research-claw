"""Subagent tools for creating and managing sub-agents."""

from __future__ import annotations
import uuid
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING
from core.prompts import render as render_prompt

from loguru import logger

from agent.tools.registry import ToolRegistry
from bus.queue import MessageBus
from providers.base import LLMProvider

if TYPE_CHECKING:
    from agent.services.tool_context import ToolContext


class CreateSubagentTool:
    """
    Tool to create a new sub-agent configuration.
    """
    name = "create_subagent"
    description = "Create a custom subagent with specific system prompt and name for reuse. Use this to define a specialist role (e.g., 'researcher', 'python_coder') before assigning tasks."

    def __init__(self, tool_context: "ToolContext"):
        self.ctx = tool_context

    def to_schema(self) -> dict[str, Any]:
        return self.to_openai_schema()

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Unique name for this agent (e.g., 'researcher', 'writer')"
                        },
                        "system_prompt": {
                            "type": "string",
                            "description": "The system prompt defining the agent's role, capabilities, and personality."
                        }
                    },
                    "required": ["name", "system_prompt"]
                }
            }
        }

    async def execute(self, name: str, system_prompt: str) -> str:
        """Create a new sub-agent configuration via Session registry."""
        if self.ctx.session:
            existing = self.ctx.session.get_subagent(name)
            if existing:
                return f"Agent '{name}' already exists. Use it directly with the 'assign_task' tool."
            self.ctx.session.register_subagent(name, {"system_prompt": system_prompt})
        return f"Successfully created agent '{name}'. You can now use it with 'assign_task'."


class TaskTool:
    """
    Tool to assign a task to a sub-agent.
    """
    name = "assign_task"
    description = "Launch a sub-agent to execute a specific task. The sub-agent runs in its own context and returns the final result."

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        parent_tools: Optional[ToolRegistry] = None,
        tool_context: Optional["ToolContext"] = None,
    ):
        self.workspace = workspace # Global Root
        self.provider = provider
        self.model = model
        self.parent_tools = parent_tools
        self.ctx = tool_context

    def to_schema(self) -> dict[str, Any]:
        return self.to_openai_schema()

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "Name of the agent to use (must be created via create_subagent first)"
                        },
                        "task_description": {
                            "type": "string",
                            "description": "Detailed description of the task for the sub-agent."
                        },
                        "preserve_context": {
                            "type": "boolean",
                            "description": "If true, the sub-agent's working directory will NOT be cleaned up. Default: false.",
                            "default": False
                        }
                    },
                    "required": ["agent_name", "task_description"]
                }
            }
        }

    async def execute(self, agent_name: str, task_description: str, preserve_context: bool = False, on_token: Optional[Any] = None, on_event: Optional[Any] = None) -> str:
        """Execute a task using a sub-agent."""
        # Lookup agent config from Session registry
        agent_config = None
        if self.ctx and self.ctx.session:
            agent_config = self.ctx.session.get_subagent(agent_name)
        if not agent_config:
            return f"[ERROR] Agent '{agent_name}' not found. Please create it first using 'create_subagent'."

        system_prompt = agent_config["system_prompt"]

        # Avoid circular imports
        from agent.loop import AgentLoop

        # Create a new bus for the sub-agent (isolated events)
        sub_bus = MessageBus()

        # Create Isolated Workspace for Task via Session
        task_id = f"{agent_name}_{uuid.uuid4().hex[:8]}"

        parent_session = self.ctx.session_id if self.ctx else "default"
        parent_research = self.ctx.research_id if self.ctx else None
        project_id = self.ctx.project_id if self.ctx else "Default"

        if parent_research:
            sub_session_id = f"{parent_session}/{parent_research}/subagents/{task_id}"
        else:
            sub_session_id = f"{parent_session}/subagents/{task_id}"

        # Create child Session for isolation (role_type derived from profile)
        child_session = None
        child_project = None
        if self.ctx.session:
            child_project = self.ctx.session.project
            from agent.tools.loader import ToolLoader
            _profile_data = ToolLoader._load_profile("project_mode_subagent")
            child_session = child_project.session(sub_session_id, role_type=_profile_data.get("role_type", "Worker"))
            if child_session._role_type == "Worker":
                child_session.init_overlay()

        logger.info(f"Launching sub-agent '{agent_name}' for task: {task_description[:50]}...")

        # Instantiate sub-agent with inherited context and nested session isolation
        sub_agent = AgentLoop(
            bus=sub_bus,
            provider=self.provider,
            workspace=self.workspace,
            project_id=project_id,
            research_id=parent_research,
            model=self.model,
            system_prompt=system_prompt + "\n\n" + render_prompt(
                "swarm_task_isolation.txt",
                "[IMPORTANT]: You are a specialist sub-agent. You work in an isolated session. Execute the task DIRECTLY using your tools.\n"
                "[CRITICAL]: Your project core is at `./{project_id}/`. Write intermediate files to your sandbox root.\n"
                "[SECURITY]: Use ABSOLUTE paths or RELATIVE paths starting with './' when using tools.",
                project_id=project_id
            ),
            max_iterations=15,
            profile="project_mode_subagent",
            role_name=agent_name,
            session_id=sub_session_id,
            mode="CHAT",
            allow_recursion=False,
            project=child_project,
            session=child_session,
        )

        # Define a callback to wrap sub-agent tokens with a role prefix for visibility
        def sub_on_token(token: str):
            if not on_token:
                return
            on_token(token)

        # Define event callback that tags events with the subagent role
        def sub_on_event(event):
            if not on_event:
                return
            event.role = agent_name
            on_event(event)

        try:
            # Run the sub-agent directly using process_direct
            result = await sub_agent.process_direct(
                task_description,
                on_token=sub_on_token if on_token else None,
                on_event=sub_on_event if on_event else None,
            )

            # Auto-Harvesting: merge child session results back to parent
            # merge_to_core 从 config/agents.json 读取全局配置
            merge_to_core = True
            try:
                import json as _json
                _agents_cfg = Path("config/agents.json")
                if _agents_cfg.exists():
                    _cfg = _json.loads(_agents_cfg.read_text(encoding="utf-8"))
                    merge_to_core = _cfg.get("merge_to_core", True)
            except Exception:
                pass
            artifact_manifest = []
            merge_target = "project core" if merge_to_core else "_subagent_results/"
            try:
                if self.ctx.session and child_session:
                    merge_report = self.ctx.session.merge_child(
                        child_session, agent_name, merge_to_core=merge_to_core,
                        diff_only=(child_session._role_type == "Worker"),
                    )
                    for f in merge_report.merged:
                        artifact_manifest.append(f"- [FILE] {f}")
            except Exception as harvest_err:
                logger.warning(f"Failed to harvest subagent results: {harvest_err}")

            # Truncation
            MAX_CHARS = 50000
            if len(result) > MAX_CHARS:
                result = (
                    f"{result[:MAX_CHARS]}\n"
                    f"... [OUTPUT TRUNCATED] (Original length: {len(result)} chars)."
                )

            report = f"Sub-agent '{agent_name}' completed task in `{task_id}`.\n"
            if artifact_manifest:
                report += f"\n### 📦 Produced Artifacts (merged to {merge_target}):\n" + "\n".join(artifact_manifest) + "\n"

            report += f"\n### 📝 Final Report:\n{result}"

            if preserve_context and child_session:
                report += f"\n\n[CONTEXT]: Working directory preserved at: {child_session.root}"

            return report
        except Exception as e:
            return f"Sub-agent '{agent_name}' failed: {str(e)}"
