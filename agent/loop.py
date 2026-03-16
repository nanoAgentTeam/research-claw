"""Agent loop: the core processing engine."""

from __future__ import annotations
import asyncio
import json
import re
import tempfile
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Callable, List, Optional

from loguru import logger

from bus.events import InboundMessage, OutboundMessage
from bus.queue import MessageBus
from agent.context import ContextManager
from agent.memory.logger import HistoryLogger
from agent.tools.registry import ToolRegistry

from config.loader import get_config_service
from config.registry import ConfigRegistry
from agent.services.command_router import CommandRouter
from core.prompts import render as render_prompt
from core.llm.middleware import (
    StepCompressionMiddleware, _estimate_tokens,
    infer_context_limit, MAX_TOOL_OUTPUT_LENGTH, TOOL_TRUNCATION_EXEMPT,
)
from agent.services.commands import HANDLER_CLASSES, BaseCommandHandler
from agent.services.protocols import CommandContext, CommandResult
from config.i18n import t

class ManualToolCall:
    """Represents a tool call triggered manually (e.g. via /command)."""
    def __init__(self, name: str, arguments: dict[str, Any], id: str = "manual_trigger"):
        self.name = name
        self.arguments = arguments
        self.id = id

# Tools whose output should not be truncated in log display (show full output)
_FULL_LOG_TOOLS = {"task_propose", "task_build", "task_modify", "task_execute"}
# Tools whose output should not be truncated in context (no 32K char limit)
_NO_TRUNCATE_TOOLS = {"task_propose", "task_build", "task_modify", "task_execute",
                       "write_file", "str_replace", "patch_file", "insert_content"}

class AgentLoop:
    """
    The agent loop is the core processing engine.
    """

    @property
    def model(self) -> str:
        """Get the current model name, prioritizing the active instance in config."""
        active = self._config_service.config.get_active_provider()
        if active:
            return active.model_name
        return self._model

    @model.setter
    def model(self, value):
        self._model = value

    def __init__(
        self,
        bus: MessageBus,
        provider: Any, # LLMProvider
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 100,
        brave_api_key: str | None = None,
        s2_api_key: str | None = None,
        system_prompt: str | None = None,
        working_directory: Path | None = None,
        file_tool_root: Path | None = None,
        is_terminated: Callable[[], bool] | None = None,
        role_name: str | None = None,
        profile: str = "chat_mode_agent", # Agent profile (determines tool set + role_type)
        project_id: str | None = None, # [NEW] Project ID
        session_id: str | None = None,
        research_id: str | None = None, # [NEW] Research ID
        task_id: str | None = None, # [NEW] Task/Worker ID
        mode: str = "CHAT",
        role_type: str | None = None,
        metadata_root: Path | None = None,
        allow_recursion: bool = True,
        config: Any = None,
        project: Any = None,  # core.project.Project instance
        session: Any = None,  # core.session.Session instance
    ):
        # Ensure string types (handle Typer OptionInfo)
        for attr_name, attr_val in [
            ("role_type", role_type),
            ("project_id", project_id),
            ("session_id", session_id),
            ("mode", mode),
            ("role_name", role_name)
        ]:
            if hasattr(attr_val, "default"):
                attr_val = str(attr_val.default)
            elif attr_val is not None and not isinstance(attr_val, str):
                attr_val = str(attr_val)

            if attr_name == "role_type": role_type = attr_val
            elif attr_name == "project_id": project_id = attr_val
            elif attr_name == "session_id": session_id = attr_val
            elif attr_name == "mode": mode = attr_val
            elif attr_name == "role_name": role_name = attr_val

        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self._config_service = get_config_service()
        # [NEW] Configuration
        from config.schema import Config
        self.config = config or Config()
        
        self.model = model or (
            provider.get_default_model()
            if provider and hasattr(provider, "get_default_model")
            else "gpt-3.5-turbo"
        )
        self.max_iterations = self.config.features.agent.max_iterations if hasattr(self.config, "features") else max_iterations
        self.brave_api_key = brave_api_key
        self.s2_api_key = s2_api_key
        self.is_terminated = is_terminated
        self.profile = profile
        # Derive role_type from profile JSON — profile is the single source of truth
        from agent.tools.loader import ToolLoader
        profile_data = ToolLoader._load_profile(self.profile)
        self.role_type = profile_data.get("role_type", "Assistant")
        self.role_name = role_name or self.role_type # Display name defaults to role type
        normalized_project_id = str(project_id or "").strip()
        if normalized_project_id == "default_project":
            normalized_project_id = "Default"
        self.project_id = normalized_project_id or "Default"
        self.session_id = session_id or "default"
        self.research_id = research_id
        self.task_id = task_id
        self.mode = mode.upper() if mode else "CHAT"
        self.allow_recursion = allow_recursion

        # [G-H6] Validate research_id/task_id format
        import re as _re
        _dangerous = _re.compile(r'(\.\.|^/|[\x00])')
        for _label, _val in [("research_id", self.research_id), ("task_id", self.task_id)]:
            if _val and _dangerous.search(str(_val)):
                raise ValueError(f"Invalid {_label}: '{_val}' contains dangerous path characters")

        # [NEW] Determine Metadata Root (.bot folder)
        # In new architecture, .bot metadata lives inside the session sandbox.
        # This keeps logs isolated per project/session.
        self.project_root = self.workspace / self.project_id
        self.session_root = self.project_root / self.session_id
        
        if metadata_root:
            self.metadata_root = metadata_root
        else:
            self.metadata_root = self.session_root / ".bot"
            
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        
        # [NEW] Action tracking for loop detection and meta-diagnosis
        self.action_history: List[Dict[str, Any]] = []
        self.MAX_ACTION_HISTORY = 10
        self.consecutive_deadlocks = 0

        self._project = project
        self._session = session
        self.SUMMARY_THRESHOLD = 5

        # Working Directory Strategy:
        # work_dir: physical OS directory for subprocess cwd (e.g. bash tool)
        # file_root: project core directory, used as anchor for file tool path resolution
        if self._session:
            self.file_root = self._project.core
            self.work_dir = working_directory or self.file_root
        else:
            self.file_root = self.session_root
            self.work_dir = working_directory or self.session_root
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.file_root.mkdir(parents=True, exist_ok=True)
        
        self._config_registry = ConfigRegistry()
        self._command_router = CommandRouter(self._config_registry)

        self.context = ContextManager(
            self.metadata_root,
            self.workspace,
            provider=self.provider,
            model=self.model,
            mode=self.mode,
            system_prompt=system_prompt,
            config=self.config,
            role=self.role_name,
            role_type=self.role_type,
            profile=self.profile,
            task_id=self.task_id,
            project=self._project,
            session=self._session,
            registry=self._config_registry,
        )
        self.history_logger = HistoryLogger(self.metadata_root)
        self.tools = ToolRegistry()

        self._running = False

        # Track active background tasks (e.g. Schedulers)
        self.active_background_tasks: set[asyncio.Task] = set()

        # Subagent pending handoff (e.g. GitAgent from /git command)
        self._pending_subagent = None
        self._init_command_handlers()

        self._register_default_tools()

    def _init_command_handlers(self) -> None:
        """Instantiate command handlers from HANDLER_CLASSES and register them."""
        for cmd_name, handler_cls in HANDLER_CLASSES.items():
            handler: BaseCommandHandler = handler_cls()
            handler.bind(self)  # AgentLoop satisfies AgentServices protocol
            self._command_router.register_handler(cmd_name, handler)

    # --- AgentServices protocol implementation ---
    # These thin adapters let command handlers call back into AgentLoop
    # through the narrow AgentServices interface instead of holding a
    # reference to the entire loop.

    @property
    def current_mode(self) -> str:
        return self.mode

    async def summarize_context(self, chat_id: str, limit: int = 50, on_token: Any = None) -> str:
        return await self.context.summarize(chat_id, limit=limit, on_token=on_token)

    async def log_reset(self, channel: str, chat_id: str) -> None:
        await self.history_logger.log_reset(channel, chat_id)

    async def execute_tool_by_name(
        self,
        tool_name: str,
        arguments: dict,
        on_token: Any = None,
        message_context: dict | None = None,
    ) -> dict[str, str]:
        """Execute a tool by constructing a ManualToolCall."""
        tool_call = ManualToolCall(name=tool_name, arguments=arguments)
        return await self._execute_tool(tool_call, on_token=on_token, message_context=message_context)

    async def handle_recommend(self, on_token: Any = None) -> Optional[tuple[str, Any, str]]:
        return await self._handle_recommend_command(on_token=on_token)

    def _exit_task_mode(self) -> str:
        """Exit task mode: generate summary, restore profile, clear session.

        Returns the summary string. Shared by TaskDoneHandler and auto-exit.
        """
        context_manager = self.context
        task_session = context_manager._task_session
        if not task_session:
            return ""

        summary = context_manager.summarize_task_session()

        # Restore project_mode_agent profile (rebuild registry with full tool set)
        self.profile = "project_mode_agent"
        from agent.tools.registry import ToolRegistry
        self.tools = ToolRegistry()
        self._register_default_tools()

        # Clear task session
        context_manager._task_session = None

        # Persist final state
        if self._session:
            state_path = self._session.metadata / "task_state.json"
            task_session.save(state_path)

        # Write summary to active_context.md
        if summary:
            context_manager._write_file_safe(context_manager.context_memory_file, summary)

        return summary

    def _build_command_context(self, msg: InboundMessage, publish_chunk: Any = None) -> CommandContext:
        """Build a CommandContext from an InboundMessage."""
        return CommandContext(
            chat_id=msg.chat_id,
            channel=msg.channel,
            sender_id=msg.sender_id,
            mode=self.mode,
            project_id=self.project_id,
            session_id=self.session_id,
            role_name=self.role_name,
            role_type=self.role_type,
            publish_chunk=publish_chunk,
            publish_outbound=self.bus.publish_outbound,
        )

    async def switch_mode(self, mode: str, project_id: Optional[str] = None, session_id: Optional[str] = None, task_id: Optional[str] = None):
        """Switch mode — delegates to switch_project for concrete projects."""
        if self._project and project_id and project_id != "Default":
            await self.switch_project(project_id, session_id)
            return

        # Fallback: return to Default project
        from core.project import Project
        self.project_id = "Default"
        self.session_id = session_id or "default"
        self.mode = mode.upper()
        self.task_id = task_id

        # Switch profile to chat_mode_agent for Default project
        self.profile = "chat_mode_agent"
        from agent.tools.loader import ToolLoader
        profile_data = ToolLoader._load_profile(self.profile)
        self.role_type = profile_data.get("role_type", "Assistant")

        self._project = Project("Default", self.workspace)
        self._session = self._project.session(self.session_id, role_type=self.role_type)

        # Update roots
        self.project_root = self.workspace / self.project_id
        self.session_root = self.project_root / self.session_id
        self.metadata_root = self.session_root / ".bot"
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        self.file_root = self._project.core
        self.work_dir = self.file_root

        logger.info(f"Switched to Default project (session: {self.session_id})")

        # Reload context and tools
        self.context = ContextManager(
            self.metadata_root,
            self.workspace,
            provider=self.provider,
            model=self.model,
            project_id=self.project_id,
            mode=self.mode,
            role=self.role_name or "Assistant",
            role_type=self.role_type,
            profile=self.profile,
            task_id=self.task_id,
            project=self._project,
            session=self._session,
        )
        self.tools = ToolRegistry()
        self._register_default_tools()

    async def switch_project(self, project_id: str, session_id: str = None) -> None:
        """Switch to a different project."""
        from core.project import Project
        from core.session import generate_session_id

        if not session_id:
            # Reuse the latest session for today, only generate new if none exists
            session_id = self._find_latest_session(project_id) or generate_session_id(self.workspace / project_id)

        old_id = self._project.id if self._project else "None"
        logger.info(f"Project switch: {old_id} -> {project_id}")

        # Switch profile based on project type
        if project_id == "Default":
            self.profile = "chat_mode_agent"
        else:
            self.profile = "project_mode_agent"
        from agent.tools.loader import ToolLoader
        profile_data = ToolLoader._load_profile(self.profile)
        self.role_type = profile_data.get("role_type", "Assistant")

        self._project = Project(project_id, self.workspace)
        self._session = self._project.session(session_id, role_type=self.role_type)

        self.project_id = project_id
        self.session_id = session_id

        # Update all path references
        self.project_root = self.workspace / project_id
        self.session_root = self.project_root / session_id
        self.metadata_root = self._session.metadata
        self.file_root = self._project.core
        self.work_dir = self.file_root

        # Note: Overleaf sync is handled explicitly via /sync command, not on switch

        # Rebuild context, history, and tools
        self.context = ContextManager(
            self.metadata_root,
            self.workspace,
            provider=self.provider,
            model=self.model,
            project_id=self.project_id,
            mode=self.mode,
            role=self.role_name or "Assistant",
            role_type=self.role_type,
            profile=self.profile,
            task_id=self.task_id,
            project=self._project,
            session=self._session,
        )
        self.history_logger = HistoryLogger(self.metadata_root)
        self.tools = ToolRegistry()
        self._register_default_tools()

    def _flush_commits(self, summary: str = None) -> None:
        """Flush pending writes to git at turn end."""
        if not self._project:
            return
        if not self._project.git or not self._project.config.git.auto_commit:
            return
        if not self._project._pending_writes:
            return
        self._project.flush_commits(summary=summary)

    def _find_latest_session(self, project_id: str) -> str | None:
        """Find the latest session for today in the given project. Returns session_id or None."""
        from datetime import datetime
        project_root = self.workspace / project_id
        if not project_root.exists():
            return None
        today = datetime.now().strftime("%m%d")
        prefix = f"{today}_"
        max_seq = 0
        found = False
        for d in project_root.iterdir():
            if d.is_dir() and d.name.startswith(prefix):
                try:
                    seq = int(d.name[len(prefix):])
                    if seq > max_seq:
                        max_seq = seq
                        found = True
                except ValueError:
                    pass
        return f"{prefix}{max_seq:02d}" if found else None

    async def _generate_turn_summary(self, on_token: Any | None = None) -> Optional[str]:
        """Ask LLM to generate a commit summary for this turn's changes."""
        if not self._project or not self._project._pending_writes:
            return None
        pending = list(set(self._project._pending_writes))
        if len(pending) <= self.SUMMARY_THRESHOLD:
            return None
        try:
            file_list = ", ".join(pending[:20])
            _COMMIT_SUMMARY_FALLBACK = "Summarize these file changes in one sentence for a git commit message (no prefix, English):\nFiles changed: {file_list}"
            prompt = render_prompt("loop_commit_summary.txt", _COMMIT_SUMMARY_FALLBACK, file_list=file_list)
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                on_token=on_token,
            )
            if response and response.content:
                return response.content.strip()[:200]
        except Exception as e:
            logger.warning(f"Turn summary generation failed: {e}")
        return None

    async def _recover_from_context_overflow(self, messages: list, step_boundary: int) -> list:
        """L2 emergency: keep context + first 2 + last 2 steps, discard middle."""
        logger.warning(f"🚀 Triggering L2 Emergency Recovery. Messages: {len(messages)}")
        context_msgs = messages[:step_boundary]
        steps = messages[step_boundary:]
        head, _middle, tail = StepCompressionMiddleware._split_steps(steps)
        new_messages = context_msgs + head + tail
        logger.info(f"✅ L2 Recovery: {len(messages)} → {len(new_messages)} msgs")
        return new_messages

    def _register_default_tools(self) -> None:
        """Register tools dynamically from tools.json, filtered by current mode."""
        from agent.tools.loader import ToolLoader
        from agent.services.tool_context import ToolContext

        # [G-M1] Unified config path — always load from config/tools.json relative to project root
        config_path = Path("config/tools.json")
        if not config_path.exists():
            # Fallback: try relative to workspace
            config_path = self.workspace / "config" / "tools.json"

        loader = ToolLoader(config_path)

        # Build ToolContext — the narrow facade that replaces raw agent_loop
        # Store as instance attribute so command handlers (e.g. TaskHandler) can access it
        self._tool_context = ToolContext(
            provider=self.provider,
            model=self.model,
            workspace=self.workspace,
            project_id=self.project_id,
            session_id=self.session_id,
            research_id=self.research_id,
            task_id=self.task_id,
            mode=self.mode,
            role_name=self.role_name,
            role_type=self.role_type,
            profile=self.profile,
            bus=self.bus,
            config=self.config,
            metadata_root=self.metadata_root,
            file_root=self.file_root,
            work_dir=self.work_dir,
            tools=self.tools,
            context_manager=self.context,
            active_background_tasks=self.active_background_tasks,
            switch_mode_fn=self.switch_mode,
            session=self._session,
            project=self._project,
            switch_project_fn=self.switch_project,
            automation_runtime=getattr(self, "automation_runtime", None),
        )
        tool_ctx = self._tool_context  # local alias for context dict below

        # Build SkillRegistry for this profile (filters by profile "skills" field if present)
        try:
            from agent.skills.registry import SkillRegistry
            _profile_data = ToolLoader._load_profile(self.profile)
            _skill_registry = SkillRegistry(allowed=_profile_data.get("skills"))
        except Exception:
            _skill_registry = None

        context = {
            "workspace": self.workspace,
            "file_root": self.file_root,
            "work_dir": self.work_dir,
            "provider": self.provider,
            "model": self.model,
            "tools": self.tools,
            "tool_context": tool_ctx,
            "role_name": self.role_name,
            "role_type": self.role_type,
            "metadata_root": self.metadata_root,
            "config": self.config,
            "session": self._session,
            "project": self._project,
            "skill_registry": _skill_registry,
        }

        # [NEW] Profile-based Tool Loading — agent declares which tools it needs
        for tool in loader.load_for_profile(self.profile, context):
            self.tools.register(tool)
            logger.debug(f"Registered tool: {tool.name}")

        # Bind project context for blacklist filtering
        if self._project:
            self.tools.bind_context(self._project)
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        # Task for consuming messages
        consumer_task = asyncio.create_task(self.bus.consume_inbound())
        # Task for processing the current message
        processor_task: asyncio.Task | None = None
        
        while self._running:
            try:
                # Prepare list of tasks to wait on
                active_tasks = [consumer_task]
                if processor_task:
                    active_tasks.append(processor_task)
                
                # Wait for something to happen
                done, pending = await asyncio.wait(
                    active_tasks,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # 1. Handle Message Arrival
                if consumer_task in done:
                    try:
                        msg = consumer_task.result()
                        
                        # Handle STOP command
                        if msg.content.strip() == "/stop":
                            logger.info("Received /stop command")
                            
                            # 1. Force hard cancellation of current processor
                            if processor_task and not processor_task.done():
                                logger.warning("🛑 FORCING HARD STOP: Cancelling active task...")
                                processor_task.cancel()
                            
                            # 2. Cancel active background tasks (Schedulers)
                            cancelled_count = 0
                            for task in list(self.active_background_tasks):
                                if not task.done():
                                    task.cancel()
                                    cancelled_count += 1
                            self.active_background_tasks.clear()
                            
                            if cancelled_count > 0:
                                logger.warning(f"🛑 Cancelled {cancelled_count} background tasks.")
                            
                            # Send confirmation immediately
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="🛑 Hard Stop triggered. Current operation and background tasks cancelled."
                            ))
                        
                        # Handle Normal Message
                        else:
                            if processor_task and not processor_task.done():
                                # Agent is busy. Notify user.
                                await self.bus.publish_outbound(OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="⏳ I am currently busy. Please wait or type `/stop` to interrupt."
                                ))
                            else:
                                # Start Processing (inbound logging handled by bus hook)
                                processor_task = asyncio.create_task(self._process_message(msg, on_token=None))
                        
                    except Exception as e:
                        logger.error(f"Consumer error: {e}")
                    
                    # Re-arm consumer immediately
                    consumer_task = asyncio.create_task(self.bus.consume_inbound())

                # 2. Handle Processing Completion (Success, Error, or Cancellation)
                if processor_task and processor_task in done:
                    try:
                        response = processor_task.result()
                        if response:
                            # outbound logging handled by bus hook
                            await self.bus.publish_outbound(response)
                    except asyncio.CancelledError:
                        logger.info("Processor task was successfully cancelled.")
                    except Exception as e:
                        logger.error(f"Processor task error: {e}")
                        # Optionally notify user
                    finally:
                        processor_task = None
                        
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(1) # Backoff

    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _diagnose_deadlock(self, history: List[Dict[str, Any]], on_token: Any | None = None) -> str:
        """[META-DIAGNOSIS] Use another model instance to diagnose and break the deadlock."""
        logger.info(f"🧠 [Meta-Diagnosis] Diagnosing deadlock for role: {self.role_name}...")
        
        # Build diagnosis prompt from recent history
        recent_actions = []
        for m in history[-10:]:
            role = m.get("role")
            content = m.get("content")
            tool_calls = m.get("tool_calls")
            if tool_calls:
                recent_actions.append(f"Agent tried tools: {[tc['function']['name'] for tc in tool_calls]}")
            elif role == "tool":
                # Truncate long tool outputs
                tool_out = str(content)[:200] + "..." if len(str(content)) > 200 else str(content)
                recent_actions.append(f"Tool Output: {tool_out}")

        _DIAGNOSIS_FALLBACK = """
        You are a Meta-Cognitive System Diagnostician. An AI agent named '{role_name}' is stuck in a repetitive loop.

        【RECENT ACTION HISTORY】:
        {recent_actions}

        【TASK】:
        1. Identify EXACTLY why the agent is looping (e.g., repeating a failing command, misinterpreting tool output, hallucinating parameters).
        2. Provide a CRITICAL INTERVENTION. Tell the agent exactly what it's doing wrong and give it a specific alternative command or strategy to follow.
        3. Be brief, authoritative, and direct.

        Your output will be injected as a high-priority system intervention.
        """
        diagnosis_prompt = render_prompt(
            "loop_meta_diagnosis.txt", _DIAGNOSIS_FALLBACK,
            role_name=self.role_name, recent_actions=chr(10).join(recent_actions)
        )
        
        try:
            # Fresh chat call with no history
            diag_response = await self.provider.chat(
                messages=[{"role": "user", "content": diagnosis_prompt}],
                model=self.model,
                on_token=on_token,
            )
            return diag_response.content
        except Exception as e:
            logger.error(f"Meta-diagnosis failed: {e}")
            return t("loop.system_intervention")

    async def _process_message(self, msg: InboundMessage, on_token: Any | None = None, on_event: Any | None = None) -> OutboundMessage | None:
        """
        Process a single inbound message.
        """
        logger.debug(f"Processing message from {msg.channel}:{msg.sender_id}")

        # Reset per-turn Overleaf sync hint dedup flag
        self._overleaf_hint_given = False
        self._overleaf_hint_pending = ""

        # Store current message context for tool-level progress feedback
        self._current_msg = msg

        # [TRACE] Initialize per-turn trace logger
        from agent.memory.trace import TraceLogger, AgentEvent, EventType
        trace = TraceLogger(
            events_dir=self.metadata_root / "memory" / "events",
            session_id=self.session_id,
        )
        trace.mark_turn_start()
        trace.emit(AgentEvent(type=EventType.TURN_START, data={"inbound": msg.content[:200]}))

        # [MODE TRACKING] Store mode at start of turn to detect switches
        start_of_turn_mode = self.mode
        start_of_turn_project = self.project_id
        # Only stream intermediate progress for CLI / explicit callback consumers.
        # IM channels should get concise final replies by default.
        stream_progress = bool(on_token or on_event or msg.channel == "cli")

        # Helper for bus/stream publishing
        def publish_chunk(token: str, new_message: bool = False, stream_id: str | None = None):
            if not stream_progress:
                return
            if on_token:
                on_token(token)

            chunk_msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=token,
                is_chunk=True,
                stream_id=stream_id
            )
            if new_message:
                chunk_msg.new_message = True

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.bus.publish_outbound(chunk_msg))
            except RuntimeError:
                pass

        # Task-specific on_token: always publishes to bus regardless of stream_progress.
        # Normal LLM streaming stays gated for IM channels; only task progress bypasses.
        def task_on_token(token: str, stream_id: str | None = None):
            if on_token:
                on_token(token)
            chunk_msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=token,
                is_chunk=True,
                stream_id=stream_id,
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.bus.publish_outbound(chunk_msg))
            except RuntimeError:
                pass

        # Command dispatch via CommandRouter
        ctx = self._build_command_context(msg, publish_chunk=publish_chunk if stream_progress else None)
        cmd_result = await self._command_router.dispatch(msg.content, ctx)

        if cmd_result is not None:
            # Command was recognized
            if cmd_result.subagent:
                # Subagent mode — store for CLI handoff, return intro
                self._pending_subagent = cmd_result.subagent
                content = cmd_result.response or ""
                if content and on_token:
                    on_token(content)
                trace.emit(AgentEvent(type=EventType.TURN_END, data={"reason": "subagent_handoff"}))
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content
                )
            if not cmd_result.should_continue:
                # Terminal command — return response directly
                content = cmd_result.response or ""
                if content and on_token:
                    on_token(content)
                trace.emit(AgentEvent(type=EventType.TURN_END, data={"reason": "terminal_command"}))
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content
                )
            # Fall-through command — rewrite message for LLM processing
            if cmd_result.modified_message:
                msg.content = cmd_result.modified_message

        # [GREETING / HELP INTERCEPTION]
        # Detect short greeting or help-like messages and respond directly without LLM.
        _text_lower = msg.content.strip().lower()
        _text_clean = _text_lower.rstrip("?？!！.。")
        _GREETING_PATTERNS = {
            "hello", "hi", "hey", "你好", "您好", "嗨", "哈喽",
        }
        _CAPABILITY_PATTERNS = {
            "你能做什么", "你会什么", "你可以做什么", "有什么功能",
            "你是谁", "你是什么", "介绍一下", "介绍下自己",
            "what can you do", "what do you do", "who are you",
            "怎么用", "怎么使用", "如何使用",
        }
        _HELP_ONLY_PATTERNS = {
            "help", "帮助",
        }
        _is_greeting = _text_lower in _GREETING_PATTERNS or _text_clean in _GREETING_PATTERNS
        _is_capability = _text_lower in _CAPABILITY_PATTERNS or _text_clean in _CAPABILITY_PATTERNS
        _is_help_only = _text_lower in _HELP_ONLY_PATTERNS or _text_clean in _HELP_ONLY_PATTERNS

        if _is_greeting or _is_capability or _is_help_only:
            from agent.services.commands import build_greeting_text, build_help_text
            in_project = bool(self._project and not self._project.is_default)
            if _is_help_only and not _is_greeting:
                # Pure help request (e.g. "help", "帮助") → command list only
                content = build_help_text(in_project=in_project)
            else:
                # Greeting or capability inquiry → intro + command list
                content = build_greeting_text() + "\n\n" + build_help_text(in_project=in_project)
            if on_token:
                on_token(content)
            trace.emit(AgentEvent(type=EventType.TURN_END, data={"reason": "greeting_intercept"}))
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
            )

        # 1. Load history for current turn
        try:
            history_enabled = self.config.features.history.enabled
            history_limit = self.config.features.history.max_recent_messages

            if history_enabled:
                history = await self.history_logger.get_recent_history(msg.chat_id, limit=history_limit)
            else:
                history = []
        except Exception as e:
            logger.error(f"Failed to load history: {e}")
            history = []

        # 2. [AUTO-SUMMARIZATION] Check total history count for background summarization
        try:
            summary_threshold = self.config.features.history.summary_threshold
            summary_limit = self.config.features.history.summary_limit

            total_count = await self.history_logger.count_messages(msg.chat_id)
            if total_count >= summary_threshold and total_count % 10 == 0:
                logger.debug(f"Total history has {total_count} msgs. Summarizing the last {summary_limit} to context.")
                asyncio.create_task(self.context.summarize(msg.chat_id, limit=summary_limit, on_token=on_token))
        except Exception as e:
            logger.warning(f"Auto-summarization check failed: {e}")
            total_count = 0

        messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            include_context=(total_count >= 20),
            session_id=self.session_id
        )

        # Step boundary: everything after this index is steps (assistant + tool pairs)
        step_boundary = len(messages)

        # Consecutive single-tool call tracking (non-concurrent: counts sequential LLM turns with same tool)
        _consecutive_tool_name: Optional[str] = None
        _consecutive_tool_count: int = 0

        # Agent loop instrumentation
        iteration = 0
        final_content = None
        trajectory_steps = [] # [NEW] Track the decision process
        
        # [REFACTORED] Helper for bus publishing now located at method top

        # Context for tool execution
        message_context = {
            "chat_id": msg.chat_id,
            "channel": msg.channel
        }

        # Token limit management
        MODEL_CONTEXT_LIMIT = infer_context_limit(self.model)

        while iteration < self.max_iterations:
            iteration += 1
            
            # [STEP LOGGING] Log current iteration status
            remaining = self.max_iterations - iteration
            trace.mark_step_start(iteration)
            trace.emit(AgentEvent(type=EventType.STEP_START, iteration=iteration, max_iterations=self.max_iterations))

            if on_event:
                on_event(AgentEvent(type=EventType.STEP_START, iteration=iteration, max_iterations=self.max_iterations))
            elif stream_progress:
                # Fallback: plain text for bus/channel consumers
                step_info = f"\n`[⏳ Step {iteration}/{self.max_iterations}]: {remaining} steps remaining`\n"
                if iteration > self.max_iterations - 20:
                    step_info = f"\n`[⚠️ Warning]: Step {iteration}/{self.max_iterations}. Only {remaining} steps left! Wrap up and commit soon.`\n"
                if iteration > self.max_iterations - 5:
                    step_info = f"\n`[🚨 Last Call]: Step {iteration}/{self.max_iterations}. Only {remaining} steps left! Commit NOW.`\n"
                if on_token:
                    on_token(step_info)
                else:
                    publish_chunk(step_info)

            # [TOKEN MANAGEMENT] L1 Proactive Compression (70% threshold)
            try:
                estimated_tokens = _estimate_tokens(messages)

                if estimated_tokens > (MODEL_CONTEXT_LIMIT * 0.7):
                    logger.warning(f"⚠️ Context token usage ({estimated_tokens}) exceeded 70% of limit. Triggering L1 compression...")
                    context_msgs = messages[:step_boundary]
                    steps = messages[step_boundary:]
                    head, middle, tail = StepCompressionMiddleware._split_steps(steps)
                    compressed = list(head)
                    if middle:
                        summary = StepCompressionMiddleware._rule_based_summary(middle)
                        compressed.append({"role": "system", "content": f"[Compressed steps summary]\n{summary}"})
                    compressed.extend(tail)
                    messages = context_msgs + compressed
            except Exception as e:
                logger.error(f"L1 compression failed: {e}")

            # Track how many 'steps' this iteration consumed (default 1)
            iteration_step_consumption = 1
            
            # Check for global termination signal
            if self.is_terminated and self.is_terminated():
                logger.info("🛑 Termination signal detected. Stopping agent loop.")
                final_content = t("loop.terminated")
                break

            # Setup iteration-specific streaming
            first_token_sent = False
            
            def iter_on_token(token: str, stream_id: str | None = None):
                nonlocal first_token_sent
                
                # Logic: Force new message bubble if this is a subsequent iteration
                # This separates "Thoughts/Tool Calls" (Iter 1) from "Final Answer" (Iter 2+)
                force_new = False
                if iteration > 1 and not first_token_sent:
                     force_new = True
                     first_token_sent = True
                
                if on_token:
                    # External callback (e.g. CLI), simple pass-through
                    # We might need to format stream_id for CLI if supported
                    if stream_id:
                        # For CLI, maybe prefix? But that breaks layout. 
                        # Just pass token for now or let CLI handle it if it updates on_token signature.
                        # Assuming CLI on_token is simple print.
                        on_token(token)
                    else:
                        on_token(token)
                else:
                    # Bus publishing with smart logic
                    publish_chunk(token, new_message=force_new, stream_id=stream_id)
            
            # Call LLM
            # When there are tool calls, we temporarily disable streaming to the user to avoid 
            # showing raw tool call XML/JSON in the chat window, unless it's intended.
            # However, for pure text streaming we need on_token.
            # The provider now handles XML extraction.
            
            tool_defs = self.tools.get_definitions()
            logger.debug(f"Sending {len(tool_defs)} tools to LLM: {[d['function']['name'] for d in tool_defs]}")

            # Call LLM with Post-Failure Recovery Loop
            _LLM_MAX_RETRIES = 3
            response = None
            llm_call_attempts = 0
            while llm_call_attempts < _LLM_MAX_RETRIES:
                llm_call_attempts += 1
                response = await self.provider.chat(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                    on_token=iter_on_token if stream_progress else None
                )
                
                if response.finish_reason == "error":
                    error_content = str(response.content).lower()
                    if "context_length_exceeded" in error_content or "too long" in error_content:
                        logger.error(f"❌ LLM Call failed due to context length. Attempting L2 recovery/retry {llm_call_attempts}/2...")
                        messages = await self._recover_from_context_overflow(messages, step_boundary)
                        continue
                    # Other errors: let the retry loop handle it
                    logger.error(f"❌ LLM error (attempt {llm_call_attempts}/{_LLM_MAX_RETRIES}): {response.content}")
                    continue

                # [NEW] Record this step's initial output (Thought)
                current_step = {
                    "iteration": iteration,
                    "thought": response.content,
                    "actions": []
                }

                if response.finish_reason == "length":
                    # Token limit reached during generation
                    warning_msg = "🚨 [SYSTEM WARNING]: Your response was TRUNCATED because it exceeded the configured maximum output tokens. If you were writing a file, it is incomplete. Please continue from where you left off or use str_replace for smaller edits."
                    logger.warning(warning_msg)
                    messages.append({"role": "system", "content": warning_msg})

                # Success or length — break retry loop
                break
            
            # If all retries exhausted and still error, terminate
            if response and response.finish_reason == "error":
                logger.error(f"❌ LLM call failed after {_LLM_MAX_RETRIES} retries. Terminating. Last error: {response.content}")
                error_event = AgentEvent(type=EventType.ERROR, data={"error": f"LLM call failed after {_LLM_MAX_RETRIES} retries: {response.content}"})
                trace.emit(error_event)
                if on_event:
                    on_event(error_event)
                return

            # [Log] Model Response (Real output)
            if response.content:
                logger.debug(f"🤖 Model Response received (length: {len(response.content)})")

            # [TRACE] Record step timing and token usage
            trace.mark_step_end(iteration)
            if response.usage:
                trace.record_llm_usage(response.usage)
                llm_end_event = AgentEvent(type=EventType.LLM_END, iteration=iteration, token_usage=response.usage)
                trace.emit(llm_end_event)
                if on_event:
                    on_event(llm_end_event)
            
            # Handle tool calls
            if hasattr(response, 'tool_calls') and response.tool_calls:
                 # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                
                # Show Tool Call in Chat
                for tc in response.tool_calls:
                     # [TRACE] Emit structured tool call event
                     tc_event = AgentEvent(
                         type=EventType.TOOL_CALL,
                         tool_name=tc.name,
                         tool_args=tc.arguments,
                         tool_id=tc.id,
                         iteration=iteration,
                     )
                     trace.emit(tc_event)

                     if on_event:
                         on_event(tc_event)
                     elif stream_progress:
                         # Fallback: plain text for bus/channel consumers
                         if tc.name == "assign_task":
                             agent_name = tc.arguments.get("agent_name", "subagent")
                             tool_info = f"\n---\n**🤖 Subagent '{agent_name}' Working:**\n"
                         elif tc.name == "create_subagent":
                             name = tc.arguments.get("name", "agent")
                             tool_info = f"\n`[👶 Create Agent]: {name}`\n"
                         elif "search" in tc.name:
                             query = tc.arguments.get("query", "...")
                             tool_info = f"\n`[🔎 Search]: {tc.name}('{query}')`\n"
                         else:
                             args_preview = json.dumps(tc.arguments, ensure_ascii=False)
                             if len(args_preview) > 100:
                                 args_preview = args_preview[:100] + "..."
                             tool_info = f"\n`[🛠️ Tool]: {tc.name}({args_preview})`\n"

                         if on_token:
                             on_token(tool_info)
                         else:
                             publish_chunk(tool_info)
                
                # Prepare tools for execution
                execution_tasks = []
                _this_iter_tools: list[str] = []  # Track which tools are dispatched this iteration
                for tc in response.tool_calls:
                    # [LOOP DETECTION]
                    action_fingerprint = {"name": tc.name, "args": tc.arguments}
                    repeat_count = 0
                    for hist in self.action_history:
                        if hist == action_fingerprint:
                            repeat_count += 1

                    self.action_history.append(action_fingerprint)
                    if len(self.action_history) > self.MAX_ACTION_HISTORY:
                        self.action_history.pop(0)

                    if repeat_count >= 2: # This is the 3rd identical call (lowered from 6)
                        self.consecutive_deadlocks += 1

                        if self.consecutive_deadlocks >= 3: # Deep intervention
                             async def meta_diagnosis_wrapper(tc_for_log=tc):
                                logger.error(f"🛑 [CRITICAL DEADLOCK] {self.role_name} failed to break loop. Initiating Meta-Diagnosis...")
                                diagnosis = await self._diagnose_deadlock(messages, on_token=iter_on_token)
                                warning_msg = f"🚨 [CRITICAL DEADLOCK]: Meta-Diagnosis performed. Instructions: {diagnosis}"
                                return {"output": warning_msg, "warning": "Context reset requested.", "meta_reset": diagnosis}
                             execution_tasks.append(meta_diagnosis_wrapper())
                        else:
                            warning_msg = t("loop.loop_detection", tool_name=tc.name, count=repeat_count + 1)
                            async def warning_wrapper(msg=warning_msg):
                                return {"output": msg, "warning": "Loop detected."}
                            execution_tasks.append(warning_wrapper())
                    else:
                        # Success: Add tool execution to tasks
                        _this_iter_tools.append(tc.name)
                        # Task tools always get task_on_token (bypasses stream_progress)
                        # so IM channels receive phase/progress updates.
                        _is_task_tool = tc.name.startswith("task_")
                        _needs_on_token = stream_progress or _is_task_tool
                        _token_fn = task_on_token if _is_task_tool else iter_on_token
                        execution_tasks.append(self._execute_tool(
                            tc,
                            on_token=_token_fn if _needs_on_token else None,
                            on_event=on_event,
                            message_context=message_context,
                            agent_messages=messages,
                        ))

                # Execute all tools in parallel
                if execution_tasks:
                    logger.info(f"⚡ [Parallel Execution]: Running {len(execution_tasks)} tools simultaneously...")
                    tool_results = await asyncio.gather(*execution_tasks)
                else:
                    tool_results = []
                
                # Check for meta-reset and loop warning tracking
                has_reset = False
                has_loop_warning = False
                for res in tool_results:
                    if res.get("meta_reset"):
                        has_reset = True
                        diagnosis = res["meta_reset"]
                        # Keep full context (system + history + user), discard steps
                        new_messages = messages[:step_boundary]
                        reset_instruction = f"🚨 [META-RECOVERY]: {diagnosis}"
                        new_messages.append({"role": "user", "content": reset_instruction})
                        messages = new_messages
                        self.consecutive_deadlocks = 0
                        self.action_history = []
                        break
                    if "Loop detected" in res.get("warning", ""):
                        has_loop_warning = True

                # Reset deadlock counter only if NO tool had a loop warning
                if not has_reset and not has_loop_warning:
                    self.consecutive_deadlocks = 0
                
                # [MODE SWITCH DETECTION]
                # If the mode has changed during tool execution (e.g. switch tool),
                # we MUST stop the current turn to prevent "history pollution".
                if self.mode != start_of_turn_mode:
                    logger.info(f"🔄 Clean Mode Switch Detected ({start_of_turn_mode} -> {self.mode}). Stopping current turn.")
                    final_content = t("loop.mode_switch_success")
                    break

                # [PROJECT SWITCH DETECTION]
                if self.project_id != start_of_turn_project:
                    logger.info(f"🔄 Project Switch Detected ({start_of_turn_project} -> {self.project_id}). Re-queuing message.")
                    # Notify user about the switch, then re-queue to process in new context
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=t("loop.project_switch", project_id=self.project_id),
                    ))
                    await self.bus.publish_inbound(msg)
                    return None  # the re-queued message will produce the real response

                # Add results to history
                for tc, res_obj in zip(response.tool_calls, tool_results):
                    result = res_obj["output"]
                    warning = res_obj["warning"]
                    
                    # [STEP CONSUMPTION] Check if tool signals extra step consumption
                    consume_match = re.search(r'<consume_steps>(\d+)</consume_steps>', result)
                    if consume_match:
                        try:
                            # Max consumption among parallel tools
                            consumption = int(consume_match.group(1))
                            iteration_step_consumption = max(iteration_step_consumption, consumption)
                            # Remove the internal tag from the output shown to LLM
                            result = re.sub(r'<consume_steps>\d+</consume_steps>', '', result).strip()
                        except:
                            pass

                    # Log the result for debugging/user visibility
                    try:
                        log_res = str(result)
                        if tc.name not in _FULL_LOG_TOOLS and len(log_res) > 500:
                            log_res = log_res[:500] + "... (truncated)"

                        full_log = f"✅ Tool '{tc.name}' output:\n{log_res}"
                        if warning:
                            full_log += f"\n⚠️ Warning: {warning}"

                        logger.info(full_log)

                        # [TRACE] Emit tool result event (use full result, not log-truncated version)
                        tr_event = AgentEvent(
                            type=EventType.TOOL_RESULT,
                            tool_name=tc.name,
                            tool_id=tc.id,
                            data={"output": str(result), "warning": warning},
                            iteration=iteration,
                        )
                        trace.emit(tr_event)
                        if on_event:
                            on_event(tr_event)
                    except Exception:
                        pass
                    
                    # Construct final result string for the LLM history
                    # Keep the output at the top for potential parsing, put warning at bottom
                    final_tool_output = result
                    if warning:
                        final_tool_output = f"{result}\n\n<system_warning>\n{warning}\n</system_warning>"
                        
                    messages = self.context.add_tool_result(
                        messages, tc.id, tc.name, final_tool_output
                    )
                    
                    # [NEW] Append structured action/result to current step
                    current_step["actions"].append({
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "output": result,
                        "warning": warning
                    })

                # [NEW] Close current step
                trajectory_steps.append(current_step)

                # [TASK AUTO-EXIT] If task_commit emitted <task_done/>, auto-exit task mode
                if self.context._task_session and any(
                    "<task_done/>" in r.get("output", "") for r in tool_results
                ):
                    logger.info("✅ [Task Auto-Exit] task_commit completed, auto-exiting task mode.")
                    summary = self._exit_task_mode()
                    _exit_msg_full = t("loop.task_auto_exit", summary=summary) if summary else t("loop.task_auto_exit_no_summary")
                    _exit_notice = _exit_msg_full
                    messages.append({"role": "user", "content": _exit_notice})
                    final_content = _exit_notice
                    break

                # [TOOL CONSECUTIVE INTERVENTION] Inject user message if same tool called back-to-back 5+ times
                # Only triggers on sequential (non-concurrent) consecutive calls to the same single tool.
                # Resets when multiple different tools are called in one turn.
                if _this_iter_tools:
                    _unique = set(_this_iter_tools)
                    if len(_unique) == 1:
                        _iter_tool = next(iter(_unique))
                        if _iter_tool == _consecutive_tool_name:
                            _consecutive_tool_count += 1
                        else:
                            _consecutive_tool_name = _iter_tool
                            _consecutive_tool_count = 1
                        if _consecutive_tool_count >= 5:
                            _intervention = t("loop.consecutive_intervention", tool_name=_consecutive_tool_name, count=_consecutive_tool_count)
                            messages.append({"role": "user", "content": _intervention})
                            logger.warning(f"🔔 Consecutive tool intervention: '{_consecutive_tool_name}' × {_consecutive_tool_count}")
                            _consecutive_tool_count = 0  # Reset after intervention
                    else:
                        # Multiple different tools called this turn — not a single-tool loop
                        _consecutive_tool_name = None
                        _consecutive_tool_count = 0

                # Apply extra step consumption (minus the 1 step already added at loop top)
                if iteration_step_consumption > 1:
                    extra_steps = iteration_step_consumption - 1
                    logger.info(f"⏳ Tool signaled extra step consumption. Adding {extra_steps} iterations (Total consumption for this turn: {iteration_step_consumption})")
                    iteration += extra_steps
            else:
                # No tool calls, we're done
                final_content = response.content
                break
        
        if final_content is None:
            final_content = t("loop.no_response")
        
        # [NEW] Finalize and persist full trajectory
        full_trajectory = {
            "chat_id": msg.chat_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "role": self.role_name,
            "role_type": self.role_type,
            "inbound": msg.content,
            "steps": trajectory_steps,
            "outbound": final_content,
            "timestamp": datetime.now().isoformat()
        }
        # [TRACE] Inject timing and token usage into trajectory
        full_trajectory = trace.enhance_trajectory(full_trajectory)
        await self.history_logger.log_trajectory(full_trajectory)

        # [TRACE] Emit turn end event (include final response so judge can see it)
        turn_end_event = AgentEvent(
            type=EventType.TURN_END,
            duration_ms=full_trajectory.get("duration_ms"),
            token_usage=full_trajectory.get("token_usage"),
            data={"outbound": final_content},
        )
        trace.emit(turn_end_event)
        if on_event:
            on_event(turn_end_event)

        # Flush pending writes to git
        try:
            summary = await self._generate_turn_summary(on_token=on_token)
            self._flush_commits(summary=summary)
        except Exception as e:
            logger.warning(f"Flush commits failed: {e}")

        # Append Overleaf sync hint to user-visible output (end of message, visually separated)
        pending_hint = getattr(self, '_overleaf_hint_pending', '')
        if pending_hint:
            final_content = f"{final_content}\n\n---\n{pending_hint}"
            self._overleaf_hint_pending = ""

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content
        )
    
    def _get_overleaf_sync_hint(self) -> str:
        """Return an Overleaf sync hint message based on project state.

        Returns empty string when no hint is needed (e.g. Default project).
        Three scenarios:
        1. Not linked to Overleaf → suggest linking
        2. Linked but not logged in → suggest logging in
        3. Linked and logged in → suggest /sync push
        """
        if not self._project or self._project.is_default:
            return ""
        cfg = getattr(self._project.config, "overleaf", None)
        has_overleaf = cfg and getattr(cfg, "project_id", None)
        if not has_overleaf:
            return t("loop.overleaf_not_linked")
        try:
            from config.diagnostics import is_overleaf_logged_in
            logged_in = is_overleaf_logged_in()
        except Exception:
            logged_in = False
        if not logged_in:
            return t("loop.overleaf_no_auth")
        return t("loop.overleaf_sync_hint")

    async def _execute_tool(self, tool_call: Any, on_token: Any | None = None, on_event: Any | None = None, message_context: Dict[str, Any] = None, agent_messages: list = None) -> Dict[str, str]:
        """
        Execute a single tool call.
        Returns: {"output": str, "warning": str}
        """
        # Fix for common LLM hallucination: wrapping arguments in a "raw" string
        # e.g. {"raw": "{\"path\": \"...\", \"content\": \"...\"}"}
        args = tool_call.arguments
        fix_warning = ""
        
        if isinstance(args, dict) and "raw" in args and len(args) == 1:
            raw_value = args["raw"]
            if isinstance(raw_value, str):
                try:
                    # Try to parse the inner JSON string
                    fixed_args = json.loads(raw_value)
                    if isinstance(fixed_args, dict):
                        logger.info(f"Fixed hallucinated 'raw' arguments for {tool_call.name}")
                        tool_call.arguments = fixed_args
                        args = fixed_args
                        fix_warning = t("loop.raw_args_warning", tool_name=tool_call.name)
                except json.JSONDecodeError:
                    # [ROBUSTNESS] If JSON is broken (e.g. truncated), try to rescue with regex
                    logger.warning(f"Detected broken 'raw' JSON for {tool_call.name}. Attempting regex rescue...")
                    
                    # [SAFETY] DO NOT rescue file-writing tools if they are truncated
                    if tool_call.name in ["write_file", "str_replace", "patch_file", "insert_content"]:
                        logger.error(f"❌ Refusing to rescue truncated {tool_call.name} to avoid file corruption.")
                        return {
                             "output": t("loop.truncated_write_error", tool_name=tool_call.name),
                             "warning": t("loop.truncated_write_warning")
                        }

                    resurrected_args = {}
                    keys_to_rescue = ["path", "content", "old_string", "new_string", "query", "topic", "fact"]
                    for key in keys_to_rescue:
                        # Match pattern like "key": "value"
                        # Handle both single line and multi-line (re.DOTALL for content)
                        if key in ["content", "old_string", "new_string", "fact"]:
                             # Use DOTALL for potentially long/multiline fields
                             # Greedy match until the last quote? No, JSON is hard to regex.
                             # Simple heuristic: "key"\s*:\s*"(.*)"
                             # This is fragile but it's a rescue attempt.
                             match = re.search(f'"{key}"\s*:\s*"(.*)', raw_value, re.DOTALL)
                             if match:
                                 val = match.group(1)
                                 # Cleanup trailing json structure if present
                                 if val.endswith('"}'): val = val[:-2]
                                 elif val.endswith('"'): val = val[:-1]
                                 # Unescape
                                 val = val.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                                 resurrected_args[key] = val
                        else:
                             # Simple single line match
                             match = re.search(f'"{key}"\s*:\s*"([^"]+)"', raw_value)
                             if match:
                                 resurrected_args[key] = match.group(1)

                    if resurrected_args:
                        logger.info(f"Rescued arguments via regex: {list(resurrected_args.keys())}")
                        tool_call.arguments = resurrected_args
                        args = resurrected_args
                        fix_warning = t("loop.malformed_rescue_warning", tool_name=tool_call.name)
                    else:
                        logger.error(f"Failed to rescue broken 'raw' arguments for {tool_call.name}")
                
        # Pretty print arguments for logging (Real output, no ascii escape)
        try:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False, indent=2)
        except:
            args_str = str(tool_call.arguments)

        logger.info(f"🛠️ Executing tool: {tool_call.name}\nArguments:\n{args_str}")

        # Send progress feedback for slow tools
        _TOOL_PROGRESS_KEY = {
            "latex_compile": "tool.latex_compile",
            "overleaf": None,
            "arxiv_search": "tool.arxiv_search",
            "pubmed_search": "tool.pubmed_search",
            "openalex_search": "tool.openalex_search",
            "semantic_scholar_search": "tool.semantic_scholar_search",
            "web_fetch": "tool.web_fetch",
            "browser_use": "tool.browser_use",
        }
        progress_key = _TOOL_PROGRESS_KEY.get(tool_call.name)
        progress_hint = t(progress_key) if progress_key else None
        if tool_call.name == "overleaf":
            action = args.get("action", "")
            if action == "download":
                progress_hint = t("tool.overleaf_download")
            elif action == "list":
                progress_hint = t("tool.overleaf_list")
            # sync 的进度提示不在这里发，因为工具内部可能直接报错（无项目等）
        if progress_hint:
            msg = getattr(self, '_current_msg', None)
            if msg:
                try:
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=progress_hint,
                    ))
                except Exception:
                    pass

        result = ""
        try:
            # Check if tool accepts on_token (e.g. TaskTool)
            tool = self.tools.get(tool_call.name)

            # [SECURITY] Project-restriction enforcement at execution time
            if tool and self._project:
                if not self.tools._is_authorized(tool):
                    is_manual = isinstance(tool_call, ManualToolCall)
                    if not is_manual:
                        logger.warning(f"[Security Block] Tool '{tool_call.name}' not authorized for project '{self._project.id}'")
                        return {
                            "output": t("loop.unauthorized_tool", tool_name=tool_call.name),
                            "warning": t("loop.unauthorized_tool_warning")
                        }

            if tool and tool_call.name == "assign_task":
                 # Special handling for subagents to support streaming
                 result = await tool.execute(**tool_call.arguments, on_token=on_token, on_event=on_event)
            else:
                # Use inspect to safely inject on_token and message_context
                import inspect
                if hasattr(tool, 'execute'):
                    sig = inspect.signature(tool.execute)
                    
                    # Copy args to avoid modifying original tool_call for retries if any
                    final_args = args.copy()
                    
                    # Safe injection of on_token
                    if 'on_token' in sig.parameters:
                        final_args["on_token"] = on_token

                    # Safe injection of on_event
                    if 'on_event' in sig.parameters:
                        final_args["on_event"] = on_event

                    # Safe injection of message_context
                    if message_context:
                        # Remove it first if it was added blindly above
                        if "message_context" in final_args:
                            del final_args["message_context"]
                            
                        if 'message_context' in sig.parameters or \
                           any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                            final_args["message_context"] = message_context
                    else:
                        # If no message_context provided, ensure we don't pass it even if we added it earlier
                        if "message_context" in final_args:
                            del final_args["message_context"]

                    # Safe injection of _agent_messages (for task tools)
                    if agent_messages and (
                        '_agent_messages' in sig.parameters or
                        any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                    ):
                        final_args["_agent_messages"] = agent_messages

                    # [ROBUSTNESS] Filter hallucinated keys - DELEGATED TO BASE TOOL
                    result, tool_warning = await self.tools.execute(tool_call.name, final_args)
                    if tool_warning:
                        if fix_warning: fix_warning += "\n"
                        fix_warning += tool_warning
                else:
                    result = await self.tools.execute(tool_call.name, args)
        except Exception as e:
            result = f"Error executing tool {tool_call.name}: {str(e)}"
            
        # [ROBUSTNESS] Truncate huge outputs to prevent context window explosion
        result_str = str(result)
        if len(result_str) > MAX_TOOL_OUTPUT_LENGTH and tool_call.name not in TOOL_TRUNCATION_EXEMPT and tool_call.name not in _NO_TRUNCATE_TOOLS:
            truncated_len = len(result_str)
            total_lines = result_str.count("\n") + 1
            approx_lines_shown = result_str[:MAX_TOOL_OUTPUT_LENGTH].count("\n") + 1
            # Save full content to a temp file so agent can read the rest
            tmp_dir = Path(tempfile.gettempdir()) / "agent_tool_outputs"
            tmp_dir.mkdir(exist_ok=True)
            tmp_file = tmp_dir / f"{tool_call.name}_{datetime.now().strftime('%H%M%S_%f')}.txt"
            tmp_file.write_text(result_str, encoding="utf-8")
            result = result_str[:MAX_TOOL_OUTPUT_LENGTH] + \
                f"\n\n... [OUTPUT TRUNCATED at line {approx_lines_shown}] " \
                f"(Total: {truncated_len} chars, {total_lines} lines). " \
                f"Full output saved to: {tmp_file} " \
                f"(lines {approx_lines_shown + 1}–{total_lines} not shown). " \
                f"Use read_file(\"{tmp_file}\", start_line={approx_lines_shown + 1}) to read the rest."
        
        # Overleaf sync hint after .tex file edits (once per turn)
        if (not getattr(self, '_overleaf_hint_given', False)
                and tool_call.name in ("write_file", "str_replace")):
            path_arg = args.get("path", "") or args.get("file_path", "")
            if isinstance(path_arg, str) and path_arg.endswith(".tex"):
                hint = self._get_overleaf_sync_hint()
                if hint:
                    self._overleaf_hint_pending = hint
                    self._overleaf_hint_given = True

        return {"output": str(result), "warning": fix_warning}

    async def _handle_recommend_command(self, on_token: Any = None) -> Optional[tuple[str, Path, str]]:
        """Finds the latest project and extracts topic."""
        projects_dir = self.workspace
        if not projects_dir.exists():
            return None

        projects = [d for d in projects_dir.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != "Default"]
        if not projects:
            return None
            
        # Find latest by mtime of any file inside
        latest_project = None
        max_mtime = 0
        
        for p in projects:
            # Check project dir itself
            curr_mtime = p.stat().st_mtime
            # Check files within (non-recursive for performance, or shallow recursive?)
            # Let's check common files
            for f in p.glob("*.tex"):
                curr_mtime = max(curr_mtime, f.stat().st_mtime)
            
            if curr_mtime > max_mtime:
                max_mtime = curr_mtime
                latest_project = p
                
        if not latest_project:
            return None
            
        # Extract Topic
        topic = await self._extract_topic_from_project(latest_project, on_token=on_token)
        return latest_project.name, latest_project, topic

    async def _extract_topic_from_project(self, project_path: Path, on_token: Any | None = None) -> str:
        """Scans all .tex files to extract a research topic summary."""
        tex_files = list(project_path.glob("*.tex"))
        if not tex_files:
            return project_path.name.replace("_", " ").title()
            
        # Prioritize files
        priority = ["main.tex", "abstract.tex", "intro.tex"]
        sorted_files = sorted(tex_files, key=lambda f: (f.name not in priority, priority.index(f.name) if f.name in priority else 999))
        
        combined_text = ""
        for f in sorted_files[:5]: # Take first 5 relevant tex files
            try:
                content = f.read_text(encoding="utf-8")[:1000] # Take first 1k chars
                combined_text += f"\n--- File: {f.name} ---\n{content}\n"
            except:
                continue
                
        if not combined_text:
            return project_path.name.replace("_", " ").title()
            
        # Call LLM to summarize topic
        _RESEARCH_TOPIC_FALLBACK = (
            "You are a research assistant. Based on the following snippets from a LaTeX project, "
            "extract a concise, professional research topic (1-2 sentences).\n"
            "Focus on the core technology, method, or problem being addressed.\n\n"
            "[Project Snippets]\n{combined_text}\n\n"
            "[Output Requirement]\nProvide only the topic description."
        )
        prompt = render_prompt("loop_research_topic.txt", _RESEARCH_TOPIC_FALLBACK, combined_text=combined_text)
        try:
            resp = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                tools=None,
                on_token=on_token,
            )
            return resp.content.strip()
        except:
            return project_path.name.replace("_", " ").title()

    async def process_direct(self, content: str, session_key: str = "cli:direct", on_token: Any | None = None, on_event: Any | None = None) -> str:
        """
        Process a message directly (for CLI usage).
        """
        msg = InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id=session_key,
            content=content
        )

        # Log inbound for CLI
        try:
            await self.history_logger.log_inbound(msg)
        except Exception as e:
            logger.error(f"Failed to log inbound message (CLI): {e}")

        response = await self._process_message(msg, on_token=on_token, on_event=on_event)
        
        if response:
            try:
                await self.history_logger.log_outbound(response)
            except Exception as e:
                logger.error(f"Failed to log outbound message (CLI): {e}")
        
        return response.content if response else ""
