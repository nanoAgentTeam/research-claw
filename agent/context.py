"""Unified Context Manager for assembling agent prompts and maintaining memory."""

from __future__ import annotations
from pathlib import Path
from typing import Any, List, Dict, Optional, TYPE_CHECKING
from datetime import datetime
import platform
from loguru import logger

from agent.prompt_builder import PromptBuilder
from core.prompts import render as render_prompt

if TYPE_CHECKING:
    from config.registry import ConfigRegistry

class ContextManager:
    """
    Unified manager for Agent Context.
    Handles:
    1. Prompt Building
    2. Context Summarization & Compression
    3. Memory Path Management
    """

    def __init__(
        self,
        metadata_root: Path,
        workspace_root: Path,
        provider: Any = None, # LLMProvider for summarization
        model: str | None = None,
        project_id: str = "Default",
        mode: str = "CHAT",
        role: str = "Assistant",
        role_type: str = "Assistant",
        profile: str = "chat_mode_agent",
        task_id: str | None = None,
        system_prompt: str | None = None,
        global_metadata_root: Optional[Path] = None,
        config: Any = None,
        registry: Optional["ConfigRegistry"] = None,
        project: Any = None,  # core.project.Project
        session: Any = None,  # core.session.Session
    ):
        self.metadata_root = metadata_root
        self.workspace_root = workspace_root
        self.provider = provider
        self.model = model
        self.project_id = project_id
        self.mode = mode.upper()
        self.role = role # Display name (Synthesizer, Coder, etc.)
        self.role_type = role_type # Permission type (Assistant, Worker)
        self.profile = profile # Agent profile (determines tool set)
        self.task_id = task_id
        self.system_prompt = system_prompt
        self.global_metadata_root = global_metadata_root
        self._registry = registry
        self._project = project
        self._session = session
        self._task_session = None  # Set by TaskHandler when entering /task mode

        # Prompt builder & section overrides
        self.prompt_builder = PromptBuilder()
        self._section_overrides: dict[str, str | None] = {}

        # Configuration
        from config.schema import Config
        self.config = config or Config()

        # Resolve memory paths from registry or use defaults
        if registry:
            local_mem_rel = registry.get_memory_path("local_memory", "memory")
            key_mem_rel = registry.get_memory_path("key_memory", "memory/MEMORY.md")
            active_ctx_rel = registry.get_memory_path("active_context", "memory/active_context.md")
        else:
            local_mem_rel = "memory"
            key_mem_rel = "memory/MEMORY.md"
            active_ctx_rel = "memory/active_context.md"

        # Local Memory paths (Session-specific)
        self.local_memory_dir = metadata_root / local_mem_rel
        self.local_key_memory_file = metadata_root / key_mem_rel
        self.context_memory_file = metadata_root / active_ctx_rel

        # Ensure memory dir exists
        self.local_memory_dir.mkdir(parents=True, exist_ok=True)

        # History Logger
        from agent.memory.logger import HistoryLogger
        self.history_logger = HistoryLogger(workspace_root, registry=registry)

        # Global Memory paths
        if self.global_metadata_root:
            if registry:
                global_mem_rel = registry.get_memory_path("global_memory_dir", "memory/global")
                global_key_rel = registry.get_memory_path("global_key_memory", "memory/global/GLOBAL_MEMORY.md")
            else:
                global_mem_rel = "memory/global"
                global_key_rel = "memory/global/GLOBAL_MEMORY.md"

            self.global_memory_dir = self.global_metadata_root / global_mem_rel
            self.global_memory_dir.mkdir(parents=True, exist_ok=True)
            self.global_key_memory_file = self.global_metadata_root / global_key_rel
            self.project_index_file = self.global_memory_dir / "PROJECT_INDEX.md"
    
    def _read_file_safe(self, path: Path) -> str:
        if path and path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _write_file_safe(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _build_env_context(self) -> str:
        """Build a compact environment context string for the system prompt."""
        now = datetime.now().astimezone()
        date_str = now.strftime("%Y-%m-%d")
        weekday = now.strftime("%A")
        time_str = now.strftime("%H:%M:%S")
        tz_name = now.strftime("%Z") or now.strftime("%z")
        os_name = platform.system()  # Darwin / Linux / Windows
        arch = platform.machine()    # arm64 / x86_64 / etc.
        os_display = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}.get(os_name, os_name)
        # Include current project state prominently
        is_default = self._project.is_default if self._project else (self.project_id == "Default")
        if is_default:
            project_line = "Current Project: Default (no active project)"
        else:
            project_line = f"Current Project: {self.project_id}"
        return (
            f"[ENVIRONMENT]\n"
            f"Date: {date_str} ({weekday})  Time: {time_str} {tz_name}\n"
            f"OS: {os_display} ({arch})\n"
            f"{project_line}"
        )

    @staticmethod
    def _build_commands_block(registry) -> str:
        """Build an [AVAILABLE COMMANDS] block from commands.json for the system prompt."""
        if not registry:
            return ""
        try:
            all_cmds = registry.get_all_commands()
            if not all_cmds:
                return ""
            lines = []
            for name, cmd_def in all_cmds.items():
                desc = getattr(cmd_def, "description", "") or ""
                aliases = getattr(cmd_def, "aliases", []) or []
                usage = getattr(cmd_def, "args_usage", "") or ""
                line = f"  - {name}: {desc}"
                if aliases:
                    line += f" (aliases: {', '.join(aliases)})"
                if usage:
                    line += f"  Usage: {usage}"
                lines.append(line)
            return (
                "[AVAILABLE COMMANDS]\n"
                "Users can type these slash commands in chat. "
                "This is the COMPLETE list — do NOT invent or suggest commands not listed here:\n"
                + "\n".join(lines)
            )
        except Exception:
            return ""

    # --- Builder Logic (Prompt Construction) ---

    def build_system_prompt(self, include_context: bool = False, session_id: str = "default") -> str:
        """
        Build the system prompt with explicit project-centric guidance and anchoring.
        Uses prompt templates from config/prompts/ when ConfigRegistry is available.
        """
        self._populate_default_sections(include_context, session_id)
        return self.prompt_builder.build()

    def _populate_default_sections(self, include_context: bool, session_id: str) -> None:
        """Fill prompt_builder with default sections based on current state."""
        pb = self.prompt_builder
        # Reset for a clean build each time
        pb.clear()

        # 1. Base Prompt Assembly
        if self.system_prompt:
            pb.set("base", self.system_prompt)
        else:
            reg = self._registry
            is_default = self._project.is_default if self._project else (self.project_id == "Default")

            if reg:
                identity = reg.render_prompt("ctx_identity.txt", role=self.role, role_type=self.role_type)
                if is_default:
                    project_guidance = reg.load_prompt_template("project_default.txt")
                else:
                    project_guidance = reg.render_prompt("project_active.txt", project_id=self.project_id)
                if not project_guidance:
                    mode_template = f"mode_{self.mode.lower()}.txt"
                    project_guidance = reg.load_prompt_template(mode_template)
                if not project_guidance:
                    project_guidance = f"[PROJECT: {self.project_id}]"

                if is_default:
                    thinking_pattern = "- Feel free to record thoughts and brainstorm."
                else:
                    thinking_pattern = "- Small steps, iterative progress. Provide immediate feedback."

                perm_template = f"permissions_{self.role_type.lower()}.txt"
                permission_instruction = reg.render_prompt(perm_template, project_id=self.project_id) or ""

                memory_protocol = reg.load_prompt_template("ctx_memory_protocol.txt")
            else:
                identity = f"You are ContextBot, a project-centric research assistant. [IDENTITY: {self.role} (Role Type: {self.role_type})]"
                if is_default:
                    project_guidance = "[PROJECT: Default (General Research & Exploration)]\n- Focus on discovery, indexing, and general Q&A."
                    thinking_pattern = "- Feel free to record thoughts and brainstorm."
                else:
                    project_guidance = f"[PROJECT: {self.project_id} (Active Development)]\n- Focus on iterative development and task fulfillment."
                    thinking_pattern = "- Small steps, iterative progress. Provide immediate feedback."

                permission_instruction = ""

                memory_protocol = """[MEMORY PROTOCOL]
1. **Chat Memory**: Stores cross-project summaries and user preferences.
2. **Project Memory**: Stores session-specific tools calls and deep investigation notes."""

            pb.set("identity", identity)
            pb.set("env_context", self._build_env_context())
            # Inject Overleaf status so LLM can guide users on sync/collaboration
            try:
                from config.diagnostics import get_overleaf_status_snippet
                ol_snippet = get_overleaf_status_snippet(project=self._project)
                if ol_snippet:
                    pb.set("overleaf_status", f"[OVERLEAF STATUS]\n{ol_snippet}")
            except Exception:
                pass
            pb.set("project_guidance", project_guidance)
            if not is_default:
                radar_skill = reg.load_prompt_template("skill_radar.txt") if reg else ""
                if radar_skill:
                    pb.set("radar_skill", radar_skill)
            # Inject available skills metadata block (filtered by profile, same as activate_skill tool)
            try:
                from agent.skills.registry import SkillRegistry
                from agent.tools.loader import ToolLoader
                _profile_data = ToolLoader._load_profile(self.profile)
                _sr = SkillRegistry(allowed=_profile_data.get("skills"))
                if not _sr.is_empty():
                    _meta = _sr.get_skills_metadata()
                    _entries = "\n".join(
                        f'  - name: "{s["name"]}"\n    description: "{s["description"]}"'
                        for s in _meta
                    )
                    _skills_block = (
                        "[AVAILABLE SKILLS]\n"
                        "Use `activate_skill` to load a skill's full SOP when your task requires domain-specific expertise.\n"
                        "skills:\n" + _entries
                    )
                    pb.set("available_skills", _skills_block)
            except Exception:
                pass
            # Inject available commands
            commands_block = self._build_commands_block(reg)
            if commands_block:
                pb.set("available_commands", commands_block)
            pb.set("thinking_pattern", f"[THINKING PATTERN]\n{thinking_pattern}")
            pb.set("permissions", f"[PERMISSIONS]\n{permission_instruction}" if permission_instruction else "")
            pb.set("media_guidance", (
                "[FILE & ATTACHMENT HANDLING]\n"
                "When users send files (images, PDFs, documents, etc.), the message will contain markers like "
                "[image: /path/to/file], [file: /path/to/file], or [attachment: /path/to/file].\n"
                "- These are user-uploaded files already downloaded to the local disk.\n"
                "- Use the read_file tool with the full path to read the file content.\n"
                "- Use the send_file tool to send files back to the user.\n\n"
                "[REPLY / QUOTE MESSAGES]\n"
                "When users reply to (quote) a previous message, their message will start with:\n"
                '  [Replying to: "quoted text here..."]\n'
                "- This means the user is responding to or referencing that specific earlier message.\n"
                "- Consider the quoted context when formulating your response.\n"
                "- If the quoted message is yours, the user may be asking for clarification or follow-up."
            ))
            pb.set("memory_protocol", memory_protocol or "")

            # Greeting / capability inquiry guidance
            greeting_guidance = (
                "[GREETING & CAPABILITY INQUIRY]\n"
                "When the user sends a short greeting (e.g. hello, hi, 你好) or asks about your capabilities "
                "(e.g. \"what can you do?\", \"who are you?\", \"怎么用\"), respond with a brief, friendly self-introduction "
                "and list your core capabilities. Always reply in the same language the user used. "
                "Do not call any tools for simple greetings — just respond directly.\n"
                "Core capabilities to mention:\n"
                "- Write and edit LaTeX papers\n"
                "- Compile PDF and sync with Overleaf\n"
                "- Research latest developments in a field\n"
                "- Manage project versions and collaboration\n"
                "- Mention that the user can type /help to see available commands"
            )
            pb.set("greeting_guidance", greeting_guidance)

            # Language instruction for LLM replies
            try:
                from core.infra.config import Config as InfraConfig
                llm_lang = getattr(InfraConfig, 'LLM_LANGUAGE', 'auto')
            except Exception:
                llm_lang = 'auto'
            if llm_lang and llm_lang != 'auto':
                lang_names = {
                    'zh': 'Chinese (简体中文)',
                    'en': 'English',
                    'ja': 'Japanese (日本語)',
                    'ko': 'Korean (한국어)',
                    'fr': 'French (Français)',
                    'de': 'German (Deutsch)',
                    'es': 'Spanish (Español)',
                    'ru': 'Russian (Русский)',
                    'pt': 'Portuguese (Português)',
                    'ar': 'Arabic (العربية)',
                }
                lang_display = lang_names.get(llm_lang, llm_lang)
                pb.set("language_instruction",
                    f"[LANGUAGE INSTRUCTION]\n"
                    f"You MUST always reply in {lang_display}. "
                    f"Regardless of the language the user writes in, "
                    f"all your responses must be in {lang_display}."
                )

        # 2. Memory Assembly
        is_default_project = self._project.is_default if self._project else (self.project_id == "Default")
        memory_sections = []

        # Progressive disclosure memory:
        # Inject profile snapshot + compact memory index into system prompt.
        # Full details should be resolved via memory_get/memory_search tools.
        if not is_default_project and self._project:
            try:
                from core.memory import ProjectMemoryStore

                store = ProjectMemoryStore(self._project)

                # Inject research profile as historical reference
                research = store.read_profile("research_core")
                if research:
                    topic = research.get("topic", "")
                    stage = research.get("stage", "")
                    keywords = research.get("keywords", [])
                    if not isinstance(keywords, list):
                        keywords = []
                    kw = ", ".join(str(k) for k in keywords[:8] if str(k).strip())
                    updated = research.get("updated_at", "")
                    profile_lines = [
                        "### Project Research Profile (historical reference, generated by radar)",
                        "以下信息由自动化任务（radar.profile.refresh）从论文内容中提取，仅供参考。",
                        f"- topic: {topic or '-'}",
                        f"- stage: {stage or '-'}",
                        f"- keywords: {kw or '-'}",
                    ]
                    if updated:
                        profile_lines.append(f"- last updated: {updated}")
                    memory_sections.append("\n".join(profile_lines))

                brief = store.render_system_memory_brief(index_limit=20)
                if brief:
                    memory_sections.append(
                        "### Auto-loaded Memory Brief (progressive disclosure)\n"
                        + brief
                    )
            except Exception as e:
                logger.debug(f"Auto-loaded memory brief unavailable: {e}")

        if self.config.features.memory.long_term_enabled and self.global_metadata_root:
            global_memory = self._read_file_safe(self.global_key_memory_file)
            if global_memory:
                memory_sections.append(f"### Global User Preferences\n{global_memory}")

            if is_default_project:
                project_index = self._read_file_safe(self.project_index_file)
                if project_index:
                    memory_sections.append(f"### Project Index\n{project_index}")

        if self.config.features.memory.short_term_enabled and not is_default_project:
            local_memory = self._read_file_safe(self.local_key_memory_file)
            if local_memory:
                memory_sections.append(f"### Project Specific Data\n{local_memory}")

            if self._project:
                pm_content = self._project.load_memory()
                if pm_content:
                    memory_sections.append(f"### Project Long-Term Memory\n{pm_content}")
            else:
                try:
                    project_root = self.metadata_root.parent.parent
                    pm_file = project_root / ".project_memory" / "MEMORY.md"
                    if pm_file.exists():
                        pm_content = self._read_file_safe(pm_file)
                        if pm_content:
                            memory_sections.append(f"### Project Long-Term Memory\n{pm_content}")
                except Exception:
                    pass

        memory_all = "\n\n".join(memory_sections)
        if memory_all:
            pb.set("consolidated_memory", f"[CONSOLIDATED MEMORY]\n{memory_all}")

        if self.config.features.memory.short_term_enabled and include_context:
            context_memory = self._read_file_safe(self.context_memory_file)
            if context_memory:
                pb.set("active_context", f"[ACTIVE SESSION CONTEXT]\n{context_memory}")

        # Task mode phase injection
        if self._task_session:
            task_phase = self._build_task_phase_prompt()
            if task_phase:
                pb.set("task_phase", task_phase)

        # Apply section overrides last
        for key, value in self._section_overrides.items():
            if value is None:
                pb.remove(key)
            else:
                pb.set(key, value)
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        media: list[str] | None = None,
        include_context: bool = False,
        session_id: str = "default"
    ) -> list[dict[str, Any]]:
        messages = []
        system_prompt = self.build_system_prompt(include_context=include_context, session_id=session_id)
        messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": current_message})
        return messages

    def add_tool_result(self, messages: list[dict[str, Any]], tool_call_id: str, tool_name: str, result: str) -> list[dict[str, Any]]:
        MAX_TOOL_OUTPUT_CHARS = 100000 
        if len(result) > MAX_TOOL_OUTPUT_CHARS:
            result = f"{result[:MAX_TOOL_OUTPUT_CHARS]}\n... [SYSTEM MESSAGE: Output truncated.]"
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages
    
    def add_assistant_message(self, messages: list[dict[str, Any]], content: str | None, tool_calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls: msg["tool_calls"] = tool_calls
        messages.append(msg)
        return messages

    # --- Manager Logic (Memory Maintenance) ---

    async def summarize(self, chat_id: str, limit: int = 50, on_token: Any = None) -> str:
        """Summarize current history into active_context.md."""
        if not self.provider:
            return "Error: Summarization requires an LLM Provider."
            
        current_context = self._read_file_safe(self.context_memory_file)
        history = await self.history_logger.get_recent_history(chat_id=chat_id, limit=limit)
        
        if not history:
            return "No recent history to summarize."
            
        history_text = ""
        for msg in history:
            role, content = msg.get("role", "unknown"), msg.get("content", "")
            if role == "tool":
                history_text += f"[Tool Result]: {str(content)[:200]}...\n"
            elif role == "assistant" and "tool_calls" in msg:
                 history_text += f"[Assistant Tool Call]: {msg.get('tool_calls')}\n"
            else:
                history_text += f"[{role}]: {content}\n"

        prompt = ""
        if self._registry:
            prompt = self._registry.render_prompt(
                "ctx_summarize.txt",
                current_context=current_context or "(Empty)",
                history_text=history_text,
            )
        if not prompt:
            prompt = f"""Update the "Active Context" summary based on history.
[Current Active Context]
{current_context or "(Empty)"}
[Recent History]
{history_text}
Output ONLY the new summary (bullet points)."""
        
        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                on_token=on_token,
            )
            if response.content:
                self._write_file_safe(self.context_memory_file, response.content)
                return "Context memory updated."
            return "Empty response from provider."
        except Exception as e:
            return f"Summarization failed: {e}"

    async def smart_compress(self, chat_id: str, messages: List[Dict[str, Any]], on_token: Any = None) -> List[Dict[str, Any]]:
        """Summarize middle messages to stay within context limits."""
        if len(messages) <= 15 or not self.provider:
            return messages
        
        logger.info(f"🚀 Compressing context for {chat_id}")
        system_message = messages[0]
        recent_messages = messages[-6:]
        middle_messages = messages[1:-6]

        history_text = "\n".join([f"[{m.get('role')}]: {str(m.get('content'))[:200]}" for m in middle_messages])
        prompt = f"Summarize these middle steps of a conversation:\n{history_text}"

        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                on_token=on_token,
            )
            if response.content:
                return [system_message, {"role": "system", "content": f"[Summary of earlier steps]:\n{response.content}"}] + recent_messages
            return messages
        except Exception as e:
            logger.warning(f"Context compression failed, returning original messages: {e}")
            return messages

    # --- Task Mode Support ---

    def _build_task_phase_prompt(self) -> str:
        """Build task mode phase guidance for system prompt injection."""
        ts = self._task_session
        if not ts:
            return ""

        from agent.task_agent import TaskPhase

        phase = ts.phase
        goal_line = f"目标: {ts.goal}\n" if ts.goal else ""
        round_line = f"Round: {ts.round_id}\n" if ts.round_id > 1 else ""

        _kw = dict(goal_line=goal_line, round_line=round_line)

        _UNDERSTAND_FB = "[Task Mode - UNDERSTAND]\n{goal_line}{round_line}用 ls、read_file、bash 探索项目结构，理解上下文后调用 task_propose(goal=\"...\") 生成 Proposal。"
        _PROPOSE_FB = "[Task Mode - PROPOSE]\n{goal_line}{round_line}Proposal 已生成，向用户展示。用户可以讨论修改。\n如果用户要求修改 Proposal，再次调用 task_propose 重新生成。\n用户满意后调用 task_build 生成 TaskGraph。"
        _PLAN_FB = (
            "[Task Mode - PLAN]\n{goal_line}{round_line}"
            "向用户展示计划，等待确认。用 task_modify 调整。\n"
            "用户输入 /start 后才能开始执行，你不能自行跳过这一步。\n"
            "注意：计划中的 task 已经存在，不要重复添加。\n"
            "完整计划已注入到你的上下文中，可以直接参考各 task 的 description 和 spec 来决定修改。"
        )
        _EXECUTE_FB = "[Task Mode - EXECUTE]\n{goal_line}{round_line}调用 task_execute 执行所有任务（自动按 DAG 依赖顺序批量执行，内部处理重试）。\n所有 task 完成后自动进入 FINALIZE。"
        _FINALIZE_FB = (
            "[Task Mode - FINALIZE]\n{goal_line}{round_line}"
            "所有 task 已完成。Worker 产出在 _task_workers/ 目录下（只读归档，禁止修改）。\n\n"
            "{deliverables_checklist}"
            "你的任务是将所有 Worker 产出整合为最终交付物，写入主工作区根目录。\n"
            "⚠️ 关键要求：\n"
            "- 不要保留 _task_workers/ 中的原始目录结构（如 t1_r1/, t2_r1/ 等）\n"
            "- 不要简单复制中间产物，而是重新组织、合并内容\n"
            "- 最终交付物应该是干净的、面向读者的文件（如 report.md, paper.tex 等）\n"
            "- 如果多个 Worker 产出属于同一主题，合并到一个文件中\n"
            "- Worker 产出目录格式: _task_workers/{{tid}}_r{{round}}/ (如 _task_workers/t1_r1/)\n\n"
            "步骤：\n"
            "1. 用 bash 执行 `ls _task_workers` 查看所有 Worker 产出目录\n"
            "2. 用 read_file 逐个阅读 Worker 产出，理解内容\n"
            "3. 规划最终交付物的文件结构（尽量精简）\n"
            "4. 用 write_file 将整合后的内容写入主工作区根目录\n"
            "5. 完成后调用 task_commit 提交"
        )
        _POST_COMMIT_FB = "[Task Mode - POST-COMMIT]\n{goal_line}{round_line}上一轮已提交。你可以：\n- 描述新目标，继续 task_propose 开始新一轮\n- 输入 /done 退出 task 模式"

        # Build deliverables checklist for FINALIZE
        deliverables_checklist = self._build_deliverables_checklist(ts)
        _finalize_kw = dict(**_kw, deliverables_checklist=deliverables_checklist)

        phase_guides = {
            TaskPhase.UNDERSTAND: render_prompt("task_phase_understand.txt", _UNDERSTAND_FB, **_kw),
            TaskPhase.PROPOSE: render_prompt("task_phase_propose.txt", _PROPOSE_FB, **_kw),
            TaskPhase.PLAN: self._build_plan_phase_prompt(ts, _PLAN_FB, _kw),
            TaskPhase.EXECUTE: render_prompt("task_phase_execute.txt", _EXECUTE_FB, **_kw),
            TaskPhase.FINALIZE: render_prompt("task_phase_finalize.txt", _FINALIZE_FB, **_finalize_kw),
        }

        # POST-COMMIT: phase is UNDERSTAND but committed=True
        if phase == TaskPhase.UNDERSTAND and ts.committed:
            guide = render_prompt("task_phase_post_commit.txt", _POST_COMMIT_FB, **_kw)
        else:
            guide = phase_guides.get(phase, "")

        return f"\n\n{guide}" if guide else ""

    @staticmethod
    def _build_deliverables_checklist(ts) -> str:
        """Build a numbered checklist from expected_deliverables for FINALIZE prompt."""
        if not ts.expected_deliverables:
            return ""
        lines = ["交付物清单（逐项核对）："]
        for i, item in enumerate(ts.expected_deliverables, 1):
            if isinstance(item, dict):
                name = item.get("name", "")
                desc = item.get("description", "")
                entry = f"{name} — {desc}" if desc else name
            else:
                entry = str(item)
            lines.append(f"  ☐ {i}. {entry}")
        lines.append("")
        return "\n".join(lines) + "\n"

    def _build_plan_phase_prompt(self, ts, fallback: str, kw: dict) -> str:
        """Build PLAN phase prompt with injected task graph for visibility."""
        base = render_prompt("task_phase_plan.txt", fallback, **kw)
        if ts.task_graph:
            from agent.task_agent import format_plan_display
            graph_display = format_plan_display(ts.task_graph)
            return f"{base}\n\n[当前完整计划]\n{graph_display}"
        return base

    def summarize_task_session(self) -> str:
        """Generate a structured summary from TaskSession data. No LLM call."""
        ts = self._task_session
        if not ts:
            return ""

        lines = ["[Task Session Summary]"]
        if ts.goal:
            lines.append(f"目标: {ts.goal}")

        if ts.task_graph:
            from agent.scheduler.schema import TaskStatus
            tasks = ts.task_graph.tasks
            total = len(tasks)
            completed = sum(1 for t in tasks.values() if t.status == TaskStatus.COMPLETED)
            lines.append(f"计划: 共 {total} 个任务，已完成 {completed} 个")
            lines.append("任务列表:")
            for tid, task in tasks.items():
                lines.append(f"  - [{tid}] {task.title} — {task.status.value}")
            # Worker output dirs
            dirs = [f"_task_workers/{tid}_r{ts.round_id}" for tid in tasks]
            lines.append(f"产出目录: {', '.join(dirs)}")

        status = "已提交到 core" if ts.committed else "未提交"
        lines.append(f"状态: {status}")

        return "\n".join(lines)
