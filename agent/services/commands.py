"""Command handler implementations.

Each handler corresponds to a slash command defined in commands.json.
Handlers receive a lightweight CommandContext and an AgentServices reference
instead of the entire AgentLoop, keeping coupling minimal.
"""

from __future__ import annotations
import uuid
from typing import Any, Optional, Protocol, runtime_checkable
from loguru import logger

from agent.services.protocols import CommandContext, CommandResult


# ---------------------------------------------------------------------------
# i18n: lightweight message translations keyed by Config.LANGUAGE
# ---------------------------------------------------------------------------

_MESSAGES = {
    "sync_auth_error": {
        "zh": (
            "❌ Overleaf 同步失败：认证错误。\n\n"
            ".olauth 认证文件不存在或已过期，请在服务器上运行 ols login 重新登录。"
        ),
        "en": (
            "❌ Overleaf sync failed: authentication error.\n\n"
            "The .olauth cookie file is missing or expired. "
            "Please run 'ols login' on the server."
        ),
    },
    "sync_failed": {
        "zh": "❌ 同步失败：{detail}",
        "en": "❌ Sync failed: {detail}",
    },
    "sync_no_config": {
        "zh": (
            "❌ 同步失败：当前项目尚未关联 Overleaf。\n\n"
            "请按以下步骤操作：\n"
            "1. 输入 /sync list 查看你的 Overleaf 项目列表\n"
            "2. 找到要关联的项目 ID（Overleaf URL 中的那串字符）\n"
            "3. 告诉我项目 ID，我来帮你关联"
        ),
        "en": (
            "❌ Sync failed: this project is not linked to Overleaf.\n\n"
            "Follow these steps:\n"
            "1. Run /sync list to see your Overleaf projects\n"
            "2. Find the project ID (the string in the Overleaf URL)\n"
            "3. Tell me the project ID and I'll link it for you"
        ),
    },
    "sync_pull_progress": {
        "zh": "⏳ 正在从 Overleaf 拉取文件，请稍候...",
        "en": "⏳ Pulling files from Overleaf, please wait...",
    },
    "sync_push_progress": {
        "zh": "⏳ 正在推送文件到 Overleaf，请稍候...",
        "en": "⏳ Pushing files to Overleaf, please wait...",
    },
    "compile_progress": {
        "zh": "⏳ 正在编译 PDF，请稍候...",
        "en": "⏳ Compiling PDF, please wait...",
    },
    "compile_overleaf_linked": {
        "zh": "\n\n---\n该项目已关联 Overleaf。可以运行 /sync push 上传最新版本。",
        "en": "\n\n---\nThis project is linked to Overleaf. Consider running /sync push to upload the latest version.",
    },
    "compile_overleaf_no_auth": {
        "zh": "\n\n---\n该项目已关联 Overleaf，但认证未配置。请在服务器上运行 ols login 以启用同步。",
        "en": "\n\n---\nThis project is linked to Overleaf, but authentication is not set up. Run 'ols login' on the server to enable syncing.",
    },
    "compile_overleaf_not_linked": {
        "zh": "\n\n---\n该项目尚未关联 Overleaf。你可以关联它以实现在线协作同步。",
        "en": "\n\n---\nThis project is not linked to Overleaf. You can link it to sync your LaTeX files for online collaboration.",
    },
    "session_reset": {
        "zh": "🔄 会话已重置（ID: {sid}）。\n已清除近期对话历史。",
        "en": "🔄 Session has been reset (ID: {sid}).\nI have forgotten our recent conversation history.",
    },
    "already_chat_mode": {
        "zh": "你已经在聊天模式中。",
        "en": "You are already in Chat Mode.",
    },
    "switched_chat_mode": {
        "zh": "⬅️ 已切回聊天模式，项目上下文已清除。",
        "en": "⬅️ Switched back to CHAT mode. Project context cleared.",
    },
    "stop_signal": {
        "zh": "🛑 收到停止信号。本轮进行中的操作将完成，但不会开始新操作。",
        "en": "🛑 Received stop signal. Any pending actions for this turn will be completed, but no new ones will start.",
    },
    "summarize_result": {
        "zh": "📝 上下文摘要结果：\n{result}",
        "en": "📝 Context Summarization Result:\n{result}",
    },
    "no_projects_found": {
        "zh": "❌ 未找到任何项目。",
        "en": "❌ No projects found.",
    },
    "recommend_detected": {
        "zh": "🔍 [推荐] 检测到项目：`{name}`\n主题：*{topic}*\n正在启动调研...",
        "en": "🔍 [Recommend] Detected project: `{name}`\nTopic: *{topic}*\nInitiating research...",
    },
    "no_projects_to_pull": {
        "zh": "❌ 未找到可拉取的本地项目。",
        "en": "❌ No local projects found to pull.",
    },
    "err_no_project": {
        "zh": "[错误] 无项目上下文，请先切换到某个项目。",
        "en": "[ERROR] No project context. Switch to a project first.",
    },
    "err_no_project_available": {
        "zh": "[错误] 无可用项目上下文。",
        "en": "[ERROR] No project context available.",
    },
    "err_task_needs_project": {
        "zh": "[错误] Task 模式需要具体项目。请先用 /project 创建或切换到一个项目。",
        "en": "[ERROR] Task mode requires a concrete project. Use /project to create or switch to one first.",
    },
    "err_tool_context": {
        "zh": "[错误] 工具上下文不可用。",
        "en": "[ERROR] Tool context not available.",
    },
    "already_default": {
        "zh": "已在默认项目中。",
        "en": "Already in Default project.",
    },
    "returned_default": {
        "zh": "已返回默认项目。",
        "en": "Returned to Default project.",
    },
    "compile_success": {
        "zh": "PDF 编译完成：{name}（{duration:.0f}ms）",
        "en": "PDF compiled: {name} ({duration:.0f}ms)",
    },
    "compile_warnings": {
        "zh": "\n  {count} 个警告",
        "en": "\n  {count} warnings",
    },
    "compile_failed": {
        "zh": "编译失败",
        "en": "Compilation failed",
    },
    "pulled_files": {
        "zh": "已从 Overleaf 拉取 {count} 个文件",
        "en": "Pulled {count} files from Overleaf",
    },
    "pushed_files": {
        "zh": "已推送 {count} 个文件到 Overleaf",
        "en": "Pushed {count} files to Overleaf",
    },
    "sync_usage": {
        "zh": "用法：/sync [pull|push]",
        "en": "Usage: /sync [pull|push]",
    },
    "err_llm_provider": {
        "zh": "[错误] LLM 提供者不可用。",
        "en": "[ERROR] LLM provider not available.",
    },
    "err_no_session": {
        "zh": "[错误] 无可用会话上下文。",
        "en": "[ERROR] No session context available.",
    },
    "cleanup_done": {
        "zh": "子 Agent 工作目录已清理。",
        "en": "Subagent directories cleaned up.",
    },
}


def _t(key: str, **kwargs: Any) -> str:
    """Return a translated message based on Config.LANGUAGE."""
    try:
        from core.infra.config import Config
        lang = getattr(Config, "LANGUAGE", "en")
    except Exception:
        lang = "en"
    entry = _MESSAGES.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    if kwargs:
        text = text.format(**kwargs)
    return text


# ---------------------------------------------------------------------------
# Service interface that handlers can use (injected at registration time)
# ---------------------------------------------------------------------------

@runtime_checkable
class AgentServices(Protocol):
    """Minimal interface exposing AgentLoop capabilities to command handlers."""

    async def switch_mode(self, mode: str, project_id: str | None = None,
                          session_id: str | None = None) -> None: ...

    async def summarize_context(self, chat_id: str, limit: int = 50, on_token: Any = None) -> str: ...

    async def log_reset(self, channel: str, chat_id: str) -> None: ...

    async def execute_tool_by_name(
        self, tool_name: str, arguments: dict,
        on_token: Any = None, message_context: dict | None = None,
    ) -> dict[str, str]: ...

    async def handle_recommend(self, on_token: Any = None) -> Optional[tuple[str, Any, str]]: ...

    @property
    def current_mode(self) -> str: ...

    @property
    def project_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...


# ---------------------------------------------------------------------------
# Base class with services injection
# ---------------------------------------------------------------------------

class BaseCommandHandler:
    """Base class that holds a reference to AgentServices."""

    def __init__(self, services: AgentServices | None = None):
        self.services = services

    def bind(self, services: AgentServices) -> None:
        self.services = services

    async def _send_progress(self, ctx: CommandContext, message: str) -> None:
        """Send an intermediate progress message to the user during long operations."""
        if ctx.publish_outbound:
            from bus.events import OutboundMessage
            await ctx.publish_outbound(OutboundMessage(
                channel=ctx.channel,
                chat_id=ctx.chat_id,
                content=message,
            ))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

class ResetHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        await self.services.log_reset(ctx.channel, ctx.chat_id)
        from core.session import generate_session_id
        project_root = self.services.workspace / self.services.project_id if hasattr(self.services, 'workspace') else None
        new_session = generate_session_id(project_root)
        await self.services.switch_mode(
            self.services.current_mode,
            self.services.project_id,
            new_session,
        )
        res = _t("session_reset", sid=self.services.session_id)
        return CommandResult(response=res)


class ExitHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        if self.services.current_mode == "CHAT":
            res = _t("already_chat_mode")
        else:
            await self.services.switch_mode("CHAT")
            res = _t("switched_chat_mode")
        return CommandResult(response=res)


class StopHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        res = _t("stop_signal")
        return CommandResult(response=res)


class SummarizeHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        summary_result = await self.services.summarize_context(
            ctx.chat_id,
            limit=50,
            on_token=ctx.publish_chunk,
        )
        res = _t("summarize_result", result=summary_result)
        return CommandResult(response=res)


class RecommendHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project_info = await self.services.handle_recommend(on_token=ctx.publish_chunk)
        if not project_info:
            return CommandResult(response=_t("no_projects_found"))

        project_name, project_path, topic = project_info
        macro_prompt = (
            f"I have detected your latest project: **{project_name}**.\n"
            f"**Extracted Topic**: {topic}\n\n"
            f"I will now use my tools to research the latest (2024-2025) SOTA and developments for this field and provide a report."
        )

        # Pre-send detection confirmation
        res = _t("recommend_detected", name=project_name, topic=topic)
        if ctx.publish_chunk:
            ctx.publish_chunk(res)

        # Fall through to LLM with rewritten message
        return CommandResult(should_continue=True, modified_message=macro_prompt)


class PullProjectHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project_info = await self.services.handle_recommend(on_token=ctx.publish_chunk)
        if not project_info:
            return CommandResult(response=_t("no_projects_to_pull"))

        project_name, project_path, _ = project_info
        macro_prompt = (
            f"I will now Sync the project **{project_name}** with Overleaf.\n"
            f"Steps:\n"
            f"1. Check `overleaf(action='list')` to find a matching project.\n"
            f"2. If found, run `overleaf(action='sync', project_name='{project_name}')`.\n"
            f"3. If successful, run `git add .` and `git commit -m 'Sync {project_name} from Overleaf'` in the project folder.\n"
            "Please execute this workflow now."
        )

        res = f"🔄 [Pull-Project] Target: `{project_name}`\nInitiating Overleaf Sync & Git Commit workflow..."
        if ctx.publish_chunk:
            ctx.publish_chunk(res)

        return CommandResult(should_continue=True, modified_message=macro_prompt)


class TaskHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        # Parse flags
        raw = args.strip()
        e2e_mode = False
        fresh_mode = False
        if raw.startswith("--e2e"):
            e2e_mode = True
            raw = raw[len("--e2e"):].strip()
        if raw.startswith("--new"):
            fresh_mode = True
            raw = raw[len("--new"):].strip()

        # Interactive mode: register task tools into main agent
        project = getattr(self.services, '_project', None)
        if not project:
            return CommandResult(response=_t("err_no_project"))
        if project.id == "Default":
            return CommandResult(response=_t("err_task_needs_project"))

        tool_context = getattr(self.services, '_tool_context', None)
        if not tool_context:
            return CommandResult(response=_t("err_tool_context"))

        # Check if already in task mode
        context_manager = getattr(self.services, 'context', None)
        if context_manager and context_manager._task_session:
            phase = context_manager._task_session.phase.value.upper()
            return CommandResult(response=f"已在 Task 模式中（阶段: {phase}）。输入 /done 退出。")

        # Create or restore TaskSession
        from agent.task_agent import TaskSession
        session = getattr(self.services, '_session', None)
        state_path = session.metadata / "task_state.json" if session else None

        if fresh_mode or not state_path:
            task_session = TaskSession()
        else:
            task_session = TaskSession.load(state_path) or TaskSession()

        # E2E / batch auto-execution mode: skip all interactive confirmation gates
        if e2e_mode:
            task_session.auto_mode = True
            fresh_mode = True  # always start fresh in e2e mode
            task_session = TaskSession(auto_mode=True)

        # Pre-set goal if user provided args
        if raw and not task_session.goal:
            task_session.goal = raw

        # Switch to project_task_agent profile (rebuild registry with base tools only)
        self.services.profile = "project_task_agent"
        from agent.tools.registry import ToolRegistry
        self.services.tools = ToolRegistry()
        self.services._register_default_tools()

        # Block git in task mode (auto-commit handles versioning)
        bash_tool = self.services.tools.get("bash")
        if bash_tool:
            bash_tool.block_git = True

        # Register task tools on the new registry (they depend on TaskSession, can't be in profile)
        from agent.tools.task_tools import TaskProposeTool, TaskBuildTool, TaskModifyTool, TaskExecuteTool, TaskCommitTool
        tools_registry = self.services.tools

        task_tools = [
            TaskProposeTool(session=task_session, ctx=tool_context),
            TaskBuildTool(session=task_session, ctx=tool_context),
            TaskModifyTool(session=task_session, ctx=tool_context),
            TaskExecuteTool(session=task_session, ctx=tool_context),
            TaskCommitTool(session=task_session, ctx=tool_context),
        ]
        for tool in task_tools:
            tools_registry.register(tool)

        if context_manager:
            context_manager._task_session = task_session

        phase = task_session.phase.value.upper()
        goal_info = f"\n目标: {task_session.goal}" if task_session.goal else ""
        plan_info = ""
        if task_session.task_graph:
            n = len(task_session.task_graph.tasks)
            plan_info = f"\n已有计划: {n} 个任务"

        restored_hint = ""
        if not fresh_mode and state_path and state_path.exists():
            restored_hint = "\n\n已恢复上次的任务会话。如需重新开始，请用 `/task --new`。"

        intro = (
            f"[Task 模式] 进入交互式任务会话。\n"
            f"当前阶段: {phase}{goal_info}{plan_info}{restored_hint}\n\n"
            f"Task 模式共 5 个阶段:\n"
            f"  1. PROPOSE  — 生成方案 → 你确认或提修改意见\n"
            f"  2. PLAN     — 生成执行计划 → 你确认后输入 /start\n"
            f"  3. EXECUTE  — 子 Agent 并行执行（自动，等待即可）\n"
            f"  4. FINALIZE — 整合产出到项目文件（自动）\n"
            f"  5. DONE     — 提交完成，自动退出\n\n"
            f"可用命令: /start (确认计划) | /done (退出 task 模式)"
        )

        # Build modified message for LLM to process
        if task_session.goal:
            if task_session.auto_mode:
                modified = (
                    f"[Task Mode Activated — Auto E2E Mode]\n"
                    f"用户目标: {task_session.goal}\n"
                    f"当前阶段: {phase}\n\n"
                    f"这是自动化执行模式，无需等待用户确认，请按顺序完成全部阶段：\n"
                    f"1. 用 ls 和 read_file 充分了解项目结构和所有相关文件内容\n"
                    f"2. 调用 task_propose 生成方案\n"
                    f"3. 立即调用 task_build 构建任务图（无需等待确认）\n"
                    f"4. 立即调用 task_execute 执行所有任务（无需 /start）\n"
                    f"5. 用 read_file 查看 _task_workers/ 下的产出，用 write_file/str_replace 合并到核心文件\n"
                    f"6. 调用 task_commit 提交所有变更\n\n"
                    f"开始执行，不要停下来等待用户输入。"
                )
            else:
                modified = (
                    f"[Task Mode Activated]\n"
                    f"用户目标: {task_session.goal}\n"
                    f"当前阶段: {phase}\n"
                    f"请先用 ls 和 read_file 了解项目结构，然后调用 task_propose 生成 Proposal。"
                )
        else:
            modified = None

        if ctx.publish_chunk:
            ctx.publish_chunk(intro)

        if modified:
            return CommandResult(should_continue=True, modified_message=modified)
        return CommandResult(response=intro)


class TaskStartHandler(BaseCommandHandler):
    """/start — confirm plan and transition PLAN -> EXECUTE."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        context_manager = getattr(self.services, 'context', None)
        task_session = context_manager._task_session if context_manager else None

        if not task_session:
            return CommandResult(response="当前不在 Task 模式。请先 /task 进入。")

        from agent.task_agent import TaskPhase
        if task_session.phase != TaskPhase.PLAN:
            return CommandResult(
                response=f"/start 仅在 PLAN 阶段可用（当前: {task_session.phase.value}）。"
            )

        task_session.phase = TaskPhase.EXECUTE
        return CommandResult(
            should_continue=True,
            modified_message="用户已确认计划。请调用 task_execute(action='run') 开始执行。",
        )


class TaskDoneHandler(BaseCommandHandler):
    """/done — exit task mode, unregister tools, generate summary."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        context_manager = getattr(self.services, 'context', None)
        task_session = context_manager._task_session if context_manager else None

        if not task_session:
            # Not in task mode — fall back to ExitHandler behavior
            if self.services.current_mode == "CHAT":
                return CommandResult(response=_t("already_chat_mode"))
            else:
                await self.services.switch_mode("CHAT")
                return CommandResult(response=_t("switched_chat_mode"))

        summary = self.services._exit_task_mode()
        return CommandResult(response=f"退出 Task 模式。\n\n{summary}")


class BackToDefaultHandler(BaseCommandHandler):
    """Return to Default project."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        if hasattr(self.services, '_project') and self.services._project:
            if self.services._project.is_default:
                return CommandResult(response=_t("already_default"))
        await self.services.switch_mode("CHAT")
        res = _t("returned_default")
        return CommandResult(response=res)


class CompileHandler(BaseCommandHandler):
    """Compile LaTeX to PDF."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, '_project', None)
        if not project:
            return CommandResult(response=_t("err_no_project_available"))
        await self._send_progress(ctx, _t("compile_progress"))
        result = project.compile_pdf()
        if result.success:
            res = _t("compile_success", name=result.pdf_path.name, duration=result.duration_ms)
            if result.warnings:
                res += _t("compile_warnings", count=len(result.warnings))
            try:
                from config.diagnostics import is_overleaf_logged_in
                ol_cfg = getattr(project.config, "overleaf", None)
                has_overleaf = ol_cfg and getattr(ol_cfg, "project_id", None)
                if has_overleaf:
                    if is_overleaf_logged_in():
                        res += _t("compile_overleaf_linked")
                    else:
                        res += _t("compile_overleaf_no_auth")
                else:
                    res += _t("compile_overleaf_not_linked")
            except Exception:
                pass
        else:
            res = _t("compile_failed")
            for e in result.errors[:5]:
                res += f"\n  {e}"
        return CommandResult(response=res)


class SyncHandler(BaseCommandHandler):
    """Overleaf sync (pull or push)."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, '_project', None)
        if not project:
            return CommandResult(response=_t("err_no_project_available"))

        action = args.strip().lower() or "pull"
        if action == "pull":
            await self._send_progress(ctx, _t("sync_pull_progress"))
            try:
                result = project.sync_from_overleaf()
            except Exception as e:
                err_text = str(e).lower()
                if any(kw in err_text for kw in ("unauthenticated", "cookie", "login", "401", "olauth")):
                    res = _t("sync_auth_error")
                else:
                    res = _t("sync_failed", detail=str(e))
                return CommandResult(response=res)
            if not result.success:
                errors_text = ', '.join(result.errors)
                if "No Overleaf config" in errors_text or "overleaf" in errors_text.lower() and "not" in errors_text.lower():
                    res = _t("sync_no_config")
                else:
                    res = _t("sync_failed", detail=errors_text)
            else:
                res = _t("pulled_files", count=len(result.pulled))
                if result.conflicts:
                    res += f"\n  Conflicts: {', '.join(result.conflicts)}"
                refreshed_legacy = False
                refreshed_unified = False
                try:
                    from core.profile import ProjectKnowledgeStore

                    ProjectKnowledgeStore(project).refresh_default_profiles()
                    refreshed_legacy = True
                except Exception:
                    pass
                try:
                    from core.memory import ProjectMemoryStore

                    ProjectMemoryStore(project).refresh_profiles()
                    refreshed_unified = True
                except Exception:
                    pass

                if refreshed_legacy and refreshed_unified:
                    res += "\n  Profiles refreshed (legacy + unified)"
                elif refreshed_legacy:
                    res += "\n  Profiles refreshed (legacy)"
                elif refreshed_unified:
                    res += "\n  Profiles refreshed (unified)"

                try:
                    from core.automation.bootstrap import ensure_project_automation_jobs
                    from agent.radar_autopilot import RadarAutoplanService

                    bootstrap = ensure_project_automation_jobs(project)
                    created = int((bootstrap.get("radar_applied") or {}).get("created", 0))
                    created_autoplan = bool(bootstrap.get("created_autoplan"))
                    if created_autoplan or created > 0:
                        res += (
                            f"\n  Default radar jobs initialized "
                            f"(autoplan={created_autoplan}, created={created})"
                        )

                    provider = getattr(self.services, "provider", None)
                    model = getattr(self.services, "model", None)
                    if provider:
                        autoplan_service = RadarAutoplanService(provider=provider, model=model)
                        autoplan_result = await autoplan_service.reconcile_project(
                            project,
                            actor_job_id="radar.autoplan",
                            on_token=ctx.publish_chunk,
                        )
                        applied = autoplan_result.get("applied") or {}
                        res += (
                            "\n  Initial autoplan applied "
                            f"(upserted={int(applied.get('upserted', 0))}, "
                            f"disabled={int(applied.get('disabled', 0))}, "
                            f"skipped={int(applied.get('skipped', 0))})"
                        )

                    runtime = getattr(self.services, "automation_runtime", None)
                    if runtime:
                        await runtime.reschedule_project(project)
                        res += "\n  Scheduler reloaded for this project"
                except Exception as e:
                    logger.debug(f"Automation bootstrap on /sync pull skipped: {e}")
        elif action == "push":
            await self._send_progress(ctx, _t("sync_push_progress"))
            try:
                result = project.sync_to_overleaf()
            except Exception as e:
                err_text = str(e).lower()
                if any(kw in err_text for kw in ("unauthenticated", "cookie", "login", "401", "olauth")):
                    res = _t("sync_auth_error")
                else:
                    res = _t("sync_failed", detail=str(e))
                return CommandResult(response=res)
            if not result.success:
                res = _t("sync_failed", detail=', '.join(result.errors))
            else:
                res = _t("pushed_files", count=len(result.pushed))
        else:
            res = _t("sync_usage")

        return CommandResult(response=res)


class GitHandler(BaseCommandHandler):
    """进入 Git 版本管理子会话。"""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, '_project', None)
        if not project or not project.git:
            return CommandResult(response="当前项目未启用 Git。")

        from agent.git_agent import GitAgent
        provider = getattr(self.services, 'provider', None)
        model = getattr(self.services, 'model', None)
        if not provider:
            return CommandResult(response=_t("err_llm_provider"))

        git_agent = GitAgent(project=project, provider=provider, model=model)

        history = project.git.log(5)
        intro = f"🔧 [Git 模式] 进入版本管理。输入 /done 退出。\n最近提交：\n{history}"

        return CommandResult(response=intro, subagent=git_agent)


class RadarHandler(BaseCommandHandler):
    """Manage automation-based research radar jobs."""

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, "_project", None)
        if not project or project.is_default:
            return CommandResult(response="请先切换到具体项目后再使用 /radar。")

        from core.automation.bootstrap import ensure_project_automation_jobs
        from core.automation.settings import (
            GC_PROTECT_JOB_STATE_REFS,
            MIRROR_LEGACY_MEMORY,
            USE_UNIFIED_MEMORY_FOR_AUTOMATION,
        )
        from core.automation.store_fs import FSAutomationStore
        from core.memory import ProjectMemoryStore
        from core.profile import ProjectKnowledgeStore

        store = FSAutomationStore(project)
        ensure_project_automation_jobs(project)

        raw = args.strip()
        parts = raw.split()
        sub = parts[0].lower() if parts else "status"

        if sub == "status":
            jobs = store.list_jobs()
            if USE_UNIFIED_MEMORY_FOR_AUTOMATION:
                memory_store = ProjectMemoryStore(project)
                runs = memory_store.list_recent_entries(kind="job_run", limit=5)
            else:
                legacy_store = ProjectKnowledgeStore(project)
                scopes = legacy_store.list_scopes(domain="job", intent="job_progress", limit=50)
                runs = []
                for row in scopes:
                    runs.append(
                        {
                            "id": row.get("latest_id", ""),
                            "scope": row.get("scope", ""),
                            "updated_at": row.get("last_updated", ""),
                        }
                    )
                runs.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
                runs = runs[:5]
            subs = store.get_subscriptions()
            state_count = sum(1 for j in jobs if store.get_job_state(j.id))
            lines = [
                f"Radar Status ({project.id})",
                f"- jobs: {len(jobs)}",
                f"- job_states: {state_count}",
                f"- subscriptions: {sum(len(v) for v in subs.values())}",
                f"- recent_job_runs: {len(runs)}",
            ]
            if subs:
                lines.append("- channels: " + ", ".join(f"{k}({len(v)})" for k, v in sorted(subs.items())))
            if runs:
                scope = str(runs[0].get("scope", "")).strip()
                job_id = scope.split(":", 1)[1] if scope.startswith("job:") else "?"
                lines.append("- last_run: " + f"{job_id} [{runs[0].get('id', '?')}]")
            return CommandResult(response="\n".join(lines))

        if sub == "subscribe":
            store.add_subscription(ctx.channel, ctx.chat_id)
            return CommandResult(response=f"已订阅当前项目推送：{ctx.channel}:{ctx.chat_id}")

        if sub == "jobs":
            jobs = store.list_jobs()
            if not jobs:
                return CommandResult(response="当前项目没有任务。")
            lines = []
            for j in jobs:
                origin = (j.metadata or {}).get("origin", "-")
                frozen_tag = " [FROZEN]" if j.frozen else ""
                lines.append(
                    f"- {j.id} | type={j.type} | {'on' if j.enabled else 'off'}{frozen_tag} | "
                    f"managed_by={j.managed_by} | origin={origin} | "
                    f"cron={j.schedule.cron} ({j.schedule.timezone})"
                )
            return CommandResult(response="Automation Jobs:\n" + "\n".join(lines))

        if sub == "bootstrap":
            from core.automation.radar_defaults import apply_default_radar_jobs

            replace_mode = len(parts) >= 2 and parts[1].lower() in {"replace", "reset", "--replace"}
            applied = apply_default_radar_jobs(
                store,
                overwrite_existing=True,
                disable_other_system_radar_jobs=replace_mode,
            )
            return CommandResult(
                response=(
                    f"已安装默认雷达模板任务（{project.id}）。\n"
                    f"- created: {applied.get('created', 0)}\n"
                    f"- updated: {applied.get('updated', 0)}\n"
                    f"- disabled: {applied.get('disabled', 0)}\n"
                    f"- skipped: {applied.get('skipped', 0)}\n"
                    f"- mode: {'replace' if replace_mode else 'merge'}"
                )
            )

        def _build_run_note(job: Any, run: Any, trigger: str) -> str:
            summary = (run.output_excerpt or "").strip()
            error = (run.error or "").strip()
            lines = [
                f"Job: {job.id} ({job.name})",
                f"Trigger: {trigger}",
                f"Run ID: {run.run_id}",
                f"Started: {run.started_at}",
                f"Ended: {run.ended_at}",
                f"Status: {run.status}",
                "",
            ]
            if summary:
                lines.extend(["Run Summary:", summary[:3000], ""])
            if error:
                lines.extend(["Run Error:", error[:1200]])
            return "\n".join(lines).strip()

        def _safe_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except Exception:
                return default

        def _parse_iso_time(value: Any):
            raw = str(value or "").strip()
            if not raw:
                return None
            try:
                from datetime import datetime
                return datetime.fromisoformat(raw)
            except Exception:
                return None

        async def _run_job(job_id: str, trigger: str) -> CommandResult:
            from core.automation.executor import AutomationExecutor
            from agent.radar_autopilot import RadarAutoplanService

            job = store.get_job(job_id)
            if not job:
                return CommandResult(response=f"[ERROR] Job not found: {job_id}")

            executor = AutomationExecutor(
                provider=getattr(self.services, "provider", None),
                workspace=getattr(self.services, "workspace", None),
                model=getattr(self.services, "model", None),
                config=getattr(self.services, "config", None),
                brave_api_key=getattr(self.services, "brave_api_key", None),
                s2_api_key=getattr(self.services, "s2_api_key", None),
            )

            run = await executor.execute_job(project, job, trigger=trigger)

            old_state = store.get_job_state(job.id)
            last_entry_id = str(old_state.get("last_entry_id", "")).strip()
            stamp = run.ended_at or run.started_at or ""

            if USE_UNIFIED_MEMORY_FOR_AUTOMATION:
                try:
                    memory_store = ProjectMemoryStore(project)
                    last_entry_id = memory_store.add(
                        kind="job_run",
                        intent="job_progress",
                        scope=f"job:{job.id}",
                        title=f"{job.id} run @ {stamp[:16]}",
                        content=_build_run_note(job, run, trigger),
                        tags=["automation", f"job:{job.id}", f"status:{run.status}", f"run:{run.run_id}"],
                        source="radar_command",
                        ttl="30d",
                        created_at=run.started_at,
                    )
                    memory_store.gc(protect_job_state_refs=GC_PROTECT_JOB_STATE_REFS)
                except Exception:
                    pass

                if MIRROR_LEGACY_MEMORY:
                    try:
                        legacy = ProjectKnowledgeStore(project)
                        legacy.add_entry(
                            kind="job_run",
                            intent="job_progress",
                            scope=f"job:{job.id}",
                            title=f"{job.id} run @ {stamp[:16]}",
                            content=_build_run_note(job, run, trigger),
                            tags=["automation", f"job:{job.id}", f"status:{run.status}", f"run:{run.run_id}"],
                            source="radar_command_mirror",
                        )
                    except Exception:
                        pass
            else:
                try:
                    legacy = ProjectKnowledgeStore(project)
                    last_entry_id = legacy.add_entry(
                        kind="job_run",
                        intent="job_progress",
                        scope=f"job:{job.id}",
                        title=f"{job.id} run @ {stamp[:16]}",
                        content=_build_run_note(job, run, trigger),
                        tags=["automation", f"job:{job.id}", f"status:{run.status}", f"run:{run.run_id}"],
                        source="radar_command",
                    )
                except Exception:
                    pass

            if str(run.status).lower() == "failed":
                consecutive_failures = _safe_int(old_state.get("consecutive_failures"), 0) + 1
            else:
                consecutive_failures = 0

            started_at = run.started_at or ""
            ended_at = run.ended_at or run.started_at or ""
            started_dt = _parse_iso_time(started_at)
            ended_dt = _parse_iso_time(ended_at)
            if started_dt and ended_dt and ended_dt >= started_dt:
                last_duration_seconds = int((ended_dt - started_dt).total_seconds())
            else:
                last_duration_seconds = 0
            total_duration_seconds = _safe_int(old_state.get("total_duration_seconds"), 0) + last_duration_seconds

            store.update_job_state(
                job.id,
                {
                    "last_started_at": started_at,
                    "last_ended_at": ended_at,
                    "last_run_at": ended_at,
                    "last_status": run.status,
                    "last_entry_id": last_entry_id,
                    "run_count": _safe_int(old_state.get("run_count"), 0) + 1,
                    "last_duration_seconds": last_duration_seconds,
                    "total_duration_seconds": total_duration_seconds,
                    "consecutive_failures": consecutive_failures,
                },
            )

            autoplan_applied = None
            if job_id == "radar.autoplan" and run.status == "success":
                service = RadarAutoplanService(
                    provider=getattr(self.services, "provider", None),
                    model=getattr(self.services, "model", None),
                )
                autoplan_applied = await service.reconcile_project(
                    project,
                    actor_job_id=job_id,
                    on_token=ctx.publish_chunk,
                )

            status = run.status
            excerpt = (run.output_excerpt or "").strip()
            if len(excerpt) > 300:
                excerpt = excerpt[:300] + "..."
            res = f"Job `{job_id}` finished with status: {status}"
            if run.error:
                res += f"\nError: {run.error}"
            if excerpt:
                res += f"\nOutput: {excerpt}"
            if autoplan_applied:
                res += f"\nAutoplan applied: {autoplan_applied.get('applied', {})}"
            return CommandResult(response=res)

        if sub == "run":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar run <job_id>")
            return await _run_job(parts[1], "manual")

        if sub == "autoplan":
            if len(parts) >= 2 and parts[1].lower() == "run":
                return await _run_job("radar.autoplan", "manual")
            return CommandResult(response="Usage: /radar autoplan run")

        if sub == "freeze":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar freeze <job_id>")
            target_id = parts[1].strip()
            if store.freeze_job(target_id):
                return CommandResult(response=f"已锁定任务 {target_id}，autoplan 将不再修改它。")
            return CommandResult(response=f"[ERROR] 任务不存在: {target_id}")

        if sub == "unfreeze":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar unfreeze <job_id>")
            target_id = parts[1].strip()
            if store.unfreeze_job(target_id):
                return CommandResult(response=f"已解锁任务 {target_id}，autoplan 可以再次管理它。")
            return CommandResult(response=f"[ERROR] 任务不存在: {target_id}")

        if sub in {"freeze-all-autoplan", "freeze_all_autoplan"}:
            frozen_ids = []
            for j in store.list_jobs():
                if (j.metadata or {}).get("origin") == "autoplan" and not j.frozen:
                    store.freeze_job(j.id)
                    frozen_ids.append(j.id)
            if frozen_ids:
                return CommandResult(response=f"已锁定 {len(frozen_ids)} 个 autoplan 任务: {', '.join(frozen_ids)}")
            return CommandResult(response="没有需要锁定的 autoplan 任务。")

        if sub == "disable":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar disable <job_id>")
            target_id = parts[1].strip()
            job = store.get_job(target_id)
            if not job:
                return CommandResult(response=f"[ERROR] 任务不存在: {target_id}")
            job.enabled = False
            job.frozen = True
            store.upsert_job(job)
            return CommandResult(response=f"已禁用并锁定任务 {target_id}。")

        if sub == "enable":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar enable <job_id>")
            target_id = parts[1].strip()
            job = store.get_job(target_id)
            if not job:
                return CommandResult(response=f"[ERROR] 任务不存在: {target_id}")
            job.enabled = True
            store.upsert_job(job)
            return CommandResult(response=f"已启用任务 {target_id}。")

        if sub == "push":
            # Read the most recent job_run entry and push via NotifyPushTool
            limit = 1

            memory_store = ProjectMemoryStore(project)
            entries = memory_store.list_recent_entries(kind="job_run", limit=limit)
            if not entries:
                return CommandResult(response="没有最近的 radar 运行记录可推送。")

            # Build push message with full content
            lines = [f"📡 Radar 最近扫描报告 ({project.id})"]
            for entry in entries:
                mem_id = str(entry.get("id", "")).strip()
                updated = str(entry.get("updated_at", "")).strip()[:16]
                scope = str(entry.get("scope", "")).strip()
                job_id = scope.split(":", 1)[1] if scope.startswith("job:") else scope
                full = memory_store.get(mem_id) if mem_id else None
                content = str(full.get("content", "")).strip() if full else ""
                lines.append(f"\n--- [{job_id}] {updated} ---")
                if content:
                    lines.append(content)
                else:
                    summary = str(entry.get("summary", "")).strip()
                    lines.append(summary or "(无内容)")
            push_text = "\n".join(lines)

            # Reuse NotifyPushTool for consistent delivery
            from agent.tools.notify import NotifyPushTool
            tool_context = getattr(self.services, "_tool_context", None)
            if not tool_context:
                return CommandResult(response=f"[推送上下文不可用] 报告内容：\n{push_text}")
            notifier = NotifyPushTool(tool_context)
            result = await notifier.execute(content=push_text)
            return CommandResult(response=result)

        return CommandResult(
            response=(
                "Usage:\n"
                "/radar status\n"
                "/radar subscribe\n"
                "/radar jobs\n"
                "/radar bootstrap [replace]\n"
                "/radar run <job_id>\n"
                "/radar push [limit]\n"
                "/radar autoplan run\n"
                "/radar freeze <job_id>\n"
                "/radar unfreeze <job_id>\n"
                "/radar freeze-all-autoplan\n"
                "/radar disable <job_id>\n"
                "/radar enable <job_id>"
            )
        )


class CleanupHandler(BaseCommandHandler):
    """Clean up subagent working directories."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        session = getattr(self.services, '_session', None)
        if not session:
            return CommandResult(response=_t("err_no_session"))
        session.cleanup_all_subagents()
        res = _t("cleanup_done")
        return CommandResult(response=res)


def build_help_text(in_project: bool = False) -> str:
    """Build user-facing help text with available commands."""
    lines = ["📖 可用命令：", ""]

    lines.append("通用：")
    lines.append("  /help        — 显示本帮助信息")
    lines.append("  /reset       — 重置会话，清除对话历史")
    lines.append("  /stop        — 停止当前操作")
    lines.append("  /exit        — 退出当前模式，返回聊天")
    lines.append("")

    lines.append("项目：")
    lines.append("  /switch <名称> — 切换到指定项目")
    lines.append("  /back         — 返回默认项目")
    if in_project:
        lines.append("  /compile      — 编译 LaTeX 生成 PDF")
        lines.append("  /sync pull    — 从 Overleaf 拉取最新文件")
        lines.append("  /sync push    — 推送本地修改到 Overleaf")
        lines.append("  /git          — 进入 Git 版本管理模式")
    else:
        lines.append("  /compile      — 编译 LaTeX（需先进入项目）")
        lines.append("  /sync [pull|push] — Overleaf 同步（需先进入项目）")
        lines.append("  /git          — Git 版本管理（需先进入项目）")
    lines.append("")

    lines.append("研究：")
    lines.append("  /task <目标>  — 启动交互式任务会话")
    lines.append("  /recommend    — 检测项目并调研最新进展")
    lines.append("  /radar        — 管理研究雷达自动化任务")
    lines.append("")

    lines.append("💡 直接用自然语言对话也可以，我会自动调用合适的工具。")
    return "\n".join(lines)


class HelpHandler(BaseCommandHandler):
    """Show available commands — delegates to LLM for localized output."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult(
            should_continue=True,
            modified_message=(
                "The user asked for help. List all available commands with brief descriptions, "
                "and mention core capabilities. Reply in the same language the user has been using."
            ),
        )


# ---------------------------------------------------------------------------
# Handler registry (name -> class mapping----------------------------------------------------------------------

HANDLER_CLASSES: dict[str, type[BaseCommandHandler]] = {
    "/help": HelpHandler,
    "/reset": ResetHandler,
    "/exit": ExitHandler,
    "/stop": StopHandler,
    "/summarize": SummarizeHandler,
    "/recommend": RecommendHandler,
    "/pull-project": PullProjectHandler,
    "/task": TaskHandler,
    "/start": TaskStartHandler,
    "/done": TaskDoneHandler,
    # Project commands
    "/back": BackToDefaultHandler,
    "/compile": CompileHandler,
    "/sync": SyncHandler,
    "/git": GitHandler,
    "/radar": RadarHandler,
    "/cleanup": CleanupHandler,
}
