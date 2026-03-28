"""Command handler implementations.

Each handler corresponds to a slash command defined in commands.json.
Handlers receive a lightweight CommandContext and an AgentServices reference
instead of the entire AgentLoop, keeping coupling minimal.
"""

from __future__ import annotations
import re
import uuid
from typing import Any, Optional, Protocol, runtime_checkable
from loguru import logger

from agent.services.protocols import CommandContext, CommandResult
from config.i18n import t


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
# Shared helpers
# ---------------------------------------------------------------------------

def _list_overleaf_projects(services: "AgentServices") -> CommandResult:
    """Shared implementation for listing remote Overleaf projects."""
    try:
        import pyoverleaf  # noqa: F401
    except ImportError:
        return CommandResult(response="[ERROR] pyoverleaf 未安装，无法连接 Overleaf。")

    from agent.tools.overleaf import OverleafTool
    from pathlib import Path

    workspace = getattr(services, 'workspace', None)
    if workspace is None:
        return CommandResult(response=t("err_no_project"))
    ol_tool = OverleafTool(workspace=Path(workspace))
    api = ol_tool._get_api()
    if not api:
        return CommandResult(
            response="[ERROR] 未找到 Overleaf 认证信息。请先运行 `ols login` 生成 .olauth 文件。"
        )
    try:
        projects = api.get_projects()
    except Exception as e:
        return CommandResult(response=f"[ERROR] 获取 Overleaf 项目列表失败: {e}")
    if not projects:
        return CommandResult(response="Overleaf 上没有找到任何项目。")
    sorted_projects = sorted(
        projects, key=lambda x: getattr(x, 'last_updated', ''), reverse=True
    )
    lines = ["☁️  Overleaf 项目："]
    for idx, p in enumerate(sorted_projects[:30], 1):
        pid = getattr(p, 'id', '?')
        name = getattr(p, 'name', '?')
        flags = []
        if getattr(p, 'archived', False):
            flags.append("ARCHIVED")
        if getattr(p, 'trashed', False):
            flags.append("TRASHED")
        flag_str = f" [{' '.join(flags)}]" if flags else ""
        lines.append(f"  {idx}. [{pid[:10]}...] {name}{flag_str}")
    if len(projects) > 30:
        lines.append(f"  ... 以及另外 {len(projects) - 30} 个项目")
    lines.append("\n回复序号或项目名即可下载到本地。")
    return CommandResult(response="\n".join(lines))


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
        res = t("session_reset", sid=self.services.session_id)
        return CommandResult(response=res)


class StopHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        res = t("stop_signal")
        return CommandResult(response=res)


class SummarizeHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        summary_result = await self.services.summarize_context(
            ctx.chat_id,
            limit=50,
            on_token=ctx.publish_chunk,
        )
        res = t("summarize_result", result=summary_result)
        return CommandResult(response=res)


class RecommendHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project_info = await self.services.handle_recommend(on_token=ctx.publish_chunk)
        if not project_info:
            return CommandResult(response=t("no_projects_found"))

        project_name, project_path, topic = project_info
        macro_prompt = (
            f"I have detected your latest project: **{project_name}**.\n"
            f"**Extracted Topic**: {topic}\n\n"
            f"I will now use my tools to research the latest (2024-2025) SOTA and developments for this field and provide a report."
        )

        # Pre-send detection confirmation
        res = t("recommend_detected", name=project_name, topic=topic)
        if ctx.publish_chunk:
            ctx.publish_chunk(res)

        # Fall through to LLM with rewritten message
        return CommandResult(should_continue=True, modified_message=macro_prompt)


class PullProjectHandler(BaseCommandHandler):
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project_info = await self.services.handle_recommend(on_token=ctx.publish_chunk)
        if not project_info:
            return CommandResult(response=t("no_projects_to_pull"))

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


class ListProjectsHandler(BaseCommandHandler):
    """/list — list all local projects in the workspace."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        workspace = getattr(self.services, 'workspace', None)
        if workspace is None:
            return CommandResult(response=t("err_no_project"))
        from pathlib import Path
        workspace = Path(workspace)
        if not workspace.exists():
            return CommandResult(response="没有找到任何本地项目。")
        current = getattr(self.services, 'project_id', 'Default')
        projects = []
        idx = 0
        for d in sorted(workspace.iterdir()):
            if not d.is_dir() or d.name in ("Default",) or d.name.startswith("."):
                continue
            config_path = d / "project.yaml"
            overleaf_tag = ""
            if config_path.exists():
                try:
                    import yaml
                    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                    if cfg.get("overleaf", {}).get("project_id"):
                        overleaf_tag = " [Overleaf]"
                except Exception:
                    pass
            active_tag = " ← 当前" if d.name == current else ""
            idx += 1
            projects.append(f"  {idx}. {d.name}{overleaf_tag}{active_tag}")

        if not projects:
            return CommandResult(response="没有找到任何本地项目。\n使用自然语言告诉我创建新项目，或用 /olist 查看远端项目。")
        header = "📂 本地项目："
        return CommandResult(response=header + "\n" + "\n".join(projects))


class OverleafListHandler(BaseCommandHandler):
    """/olist — list remote Overleaf projects (shortcut for /overleaf list)."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        return _list_overleaf_projects(self.services)


class SwitchProjectHandler(BaseCommandHandler):
    """/switch <project_name> [session_id] — switch active project/session."""

    _SESSION_RE = re.compile(r"^\d{4}_\d{2}$")

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        raw = args.strip()
        if not raw:
            return CommandResult(response="Usage: /switch <project_name> [session_id]")

        workspace = getattr(self.services, "workspace", None)
        if workspace is None:
            return CommandResult(response=t("err_no_project"))

        # If last token matches session format (MMDD_NN), split it out
        tokens = raw.rsplit(maxsplit=1)
        if len(tokens) == 2 and self._SESSION_RE.match(tokens[1]):
            project_name, session_name = tokens
        else:
            project_name, session_name = raw, None

        target_root = workspace / project_name
        if not target_root.exists() or not target_root.is_dir():
            return CommandResult(response=f"[ERROR] Project '{project_name}' does not exist.")

        if hasattr(self.services, "switch_project"):
            await self.services.switch_project(project_name, session_name)
        else:
            await self.services.switch_mode("NORMAL", project_id=project_name, session_id=session_name)

        actual_project = getattr(self.services, "project_id", project_name)
        return CommandResult(
            response=t(
                "switch_project_success",
                project=actual_project,
            )
        )


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
            return CommandResult(response=t("err_no_project"))
        if project.id == "Default":
            return CommandResult(response=t("err_task_needs_project"))

        tool_context = getattr(self.services, '_tool_context', None)
        if not tool_context:
            return CommandResult(response=t("err_tool_context"))

        # Check if already in task mode
        context_manager = getattr(self.services, 'context', None)
        if context_manager and context_manager._task_session:
            phase = context_manager._task_session.phase.value.upper()
            return CommandResult(response=t("already_in_task", phase=phase))

        # Create or restore TaskSession
        from agent.task_agent import TaskSession
        session = getattr(self.services, '_session', None)
        state_path = session.metadata / "task_state.json" if session else None

        # Always start fresh — old task_state is unreliable after restart
        # (LLM context lost, workers dead, user likely wants a new task)
        task_session = TaskSession()

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
        goal_info = f"\n{t('task_goal_label')}: {task_session.goal}" if task_session.goal else ""
        plan_info = ""
        if task_session.task_graph:
            n = len(task_session.task_graph.tasks)
            plan_info = f"\n{t('task_plan_exists', count=n)}"

        restored_hint = ""
        if not fresh_mode and state_path and state_path.exists():
            restored_hint = f"\n\n{t('task_restored_hint')}"

        intro = t("task_intro", phase=phase, goal_info=goal_info, plan_info=plan_info, restored_hint=restored_hint)

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
            return CommandResult(response=t("not_in_task_mode"))

        from agent.task_agent import TaskPhase
        if task_session.phase != TaskPhase.PLAN:
            return CommandResult(
                response=t("start_only_plan_phase", phase=task_session.phase.value)
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
            return CommandResult(response=t("not_in_task_mode"))

        summary = self.services._exit_task_mode()
        return CommandResult(response=t("exit_task_mode", summary=summary))


class BackToDefaultHandler(BaseCommandHandler):
    """Return to Default project."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        if hasattr(self.services, '_project') and self.services._project:
            if self.services._project.is_default:
                return CommandResult(response=t("already_default"))
        await self.services.switch_mode("CHAT")
        res = t("returned_default")
        return CommandResult(response=res)


class CompileHandler(BaseCommandHandler):
    """Compile LaTeX to PDF."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, '_project', None)
        if not project:
            return CommandResult(response=t("err_no_project_available"))
        await self._send_progress(ctx, t("compile_progress"))
        result = project.compile_pdf()
        if result.success:
            res = t("compile_success", name=result.pdf_path.name, duration=result.duration_ms)
            if result.warnings:
                res += t("compile_warnings", count=len(result.warnings))
            try:
                from config.diagnostics import is_overleaf_logged_in
                ol_cfg = getattr(project.config, "overleaf", None)
                has_overleaf = ol_cfg and getattr(ol_cfg, "project_id", None)
                if has_overleaf:
                    if is_overleaf_logged_in():
                        res += t("compile_overleaf_linked")
                    else:
                        res += t("compile_overleaf_no_auth")
                else:
                    res += t("compile_overleaf_not_linked")
            except Exception:
                pass
        else:
            res = t("compile_failed")
            for e in result.errors[:5]:
                res += f"\n  {e}"
        return CommandResult(response=res)


class SyncHandler(BaseCommandHandler):
    """Overleaf sync (pull or push)."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, '_project', None)
        if not project:
            return CommandResult(response=t("err_no_project_available"))

        action = args.strip().lower() or "pull"
        if action == "pull":
            await self._send_progress(ctx, t("sync_pull_progress"))
            try:
                result = project.sync_from_overleaf()
            except Exception as e:
                err_text = str(e).lower()
                if any(kw in err_text for kw in ("unauthenticated", "cookie", "login", "401", "olauth")):
                    res = t("sync_auth_error")
                else:
                    res = t("sync_failed", detail=str(e))
                return CommandResult(response=res)
            if not result.success:
                errors_text = ', '.join(result.errors)
                if "No Overleaf config" in errors_text:
                    res = t("sync_no_config")
                else:
                    res = t("sync_failed", detail=errors_text)
            else:
                res = t("pulled_files", count=len(result.pulled))
                if result.pulled:
                    for f in result.pulled:
                        res += f"\n  + {f}"
                if result.deleted:
                    res += "\n" + t("sync_pull_deleted",
                                    count=len(result.deleted),
                                    files=", ".join(result.deleted))
                    for f in result.deleted:
                        res += f"\n  - {f}"
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
            await self._send_progress(ctx, t("sync_push_progress"))
            try:
                result = project.sync_to_overleaf()
            except Exception as e:
                err_text = str(e).lower()
                if any(kw in err_text for kw in ("unauthenticated", "cookie", "login", "401", "olauth")):
                    res = t("sync_auth_error")
                else:
                    res = t("sync_failed", detail=str(e))
                return CommandResult(response=res)
            if not result.success:
                res = t("sync_failed", detail=', '.join(result.errors))
            else:
                res = t("pushed_files", count=len(result.pushed))
                if result.pushed:
                    for f in result.pushed:
                        res += f"\n  + {f}"
                if result.deleted:
                    res += "\n" + t("sync_push_deleted",
                                    count=len(result.deleted),
                                    files=", ".join(result.deleted))
                    for f in result.deleted:
                        res += f"\n  - {f}"
        else:
            res = t("sync_usage")

        return CommandResult(response=res)


class GitHandler(BaseCommandHandler):
    """进入 Git 版本管理子会话。"""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, '_project', None)
        if not project or not project.git:
            return CommandResult(response=t("git_not_enabled"))

        from agent.git_agent import GitAgent
        provider = getattr(self.services, 'provider', None)
        model = getattr(self.services, 'model', None)
        if not provider:
            return CommandResult(response=t("err_llm_provider"))

        git_agent = GitAgent(project=project, provider=provider, model=model)

        history = project.git.log(5)
        intro = t("git_mode_intro", history=history)

        return CommandResult(response=intro, subagent=git_agent)


class RadarHandler(BaseCommandHandler):
    """Manage automation-based research radar jobs."""

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        project = getattr(self.services, "_project", None)
        if not project or project.is_default:
            return CommandResult(response=t("radar_need_project"))

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
            return CommandResult(response=t("radar_subscribed", channel=ctx.channel, chat_id=ctx.chat_id))

        if sub == "jobs":
            jobs = store.list_jobs()
            if not jobs:
                return CommandResult(response=t("radar_no_jobs"))
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
                response=t("radar_bootstrap_done", project_id=project.id,
                    created=applied.get('created', 0), updated=applied.get('updated', 0),
                    disabled=applied.get('disabled', 0), skipped=applied.get('skipped', 0),
                    mode='replace' if replace_mode else 'merge')
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
                return CommandResult(response=t("radar_frozen", target_id=target_id))
            return CommandResult(response=f"[ERROR] {t('radar_job_not_found', target_id=target_id)}")

        if sub == "unfreeze":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar unfreeze <job_id>")
            target_id = parts[1].strip()
            if store.unfreeze_job(target_id):
                return CommandResult(response=t("radar_unfrozen", target_id=target_id))
            return CommandResult(response=f"[ERROR] {t('radar_job_not_found', target_id=target_id)}")

        if sub in {"freeze-all-autoplan", "freeze_all_autoplan"}:
            frozen_ids = []
            for j in store.list_jobs():
                if (j.metadata or {}).get("origin") == "autoplan" and not j.frozen:
                    store.freeze_job(j.id)
                    frozen_ids.append(j.id)
            if frozen_ids:
                return CommandResult(response=t("radar_freeze_all_done", count=len(frozen_ids), ids=', '.join(frozen_ids)))
            return CommandResult(response=t("radar_freeze_all_none"))

        if sub == "disable":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar disable <job_id>")
            target_id = parts[1].strip()
            job = store.get_job(target_id)
            if not job:
                return CommandResult(response=f"[ERROR] {t('radar_job_not_found', target_id=target_id)}")
            job.enabled = False
            job.frozen = True
            store.upsert_job(job)
            return CommandResult(response=t("radar_disabled", target_id=target_id))

        if sub == "enable":
            if len(parts) < 2:
                return CommandResult(response="Usage: /radar enable <job_id>")
            target_id = parts[1].strip()
            job = store.get_job(target_id)
            if not job:
                return CommandResult(response=f"[ERROR] {t('radar_job_not_found', target_id=target_id)}")
            job.enabled = True
            store.upsert_job(job)
            return CommandResult(response=t("radar_enabled", target_id=target_id))

        if sub == "push":
            # Read the most recent job_run entry and push via NotifyPushTool
            limit = 1

            memory_store = ProjectMemoryStore(project)
            entries = memory_store.list_recent_entries(kind="job_run", limit=limit)
            if not entries:
                return CommandResult(response=t("radar_no_recent_runs"))

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
                    lines.append(summary or t("radar_no_content"))
            push_text = "\n".join(lines)

            # Reuse NotifyPushTool for consistent delivery
            from agent.tools.notify import NotifyPushTool
            tool_context = getattr(self.services, "_tool_context", None)
            if not tool_context:
                return CommandResult(response=f"[{t('radar_push_no_context')}]\n{push_text}")
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
            return CommandResult(response=t("err_no_session"))
        session.cleanup_all_subagents()
        res = t("cleanup_done")
        return CommandResult(response=res)


def build_help_text(in_project: bool = False) -> str:
    """Build user-facing help text dynamically from commands.json (single source of truth)."""
    try:
        from config.registry import ConfigRegistry
        registry = ConfigRegistry()
        cmds = registry.get_visible_commands()
    except Exception:
        # Fallback to static i18n text if registry fails
        return t("help.text_zh") if in_project else t("help.text_zh_no_project")

    general_lines = []
    project_lines = []
    task_lines = []

    for name, cmd in cmds.items():
        desc = cmd.description
        usage = cmd.args_usage.split("\n")[0] if cmd.args_usage else ""
        # Use args_usage first line as display if it starts with the command name
        if usage.startswith(name):
            line = f"  {usage}"
        elif cmd.requires_args:
            line = f"  {name} ... — {desc}"
        else:
            line = f"  {name}  — {desc}"

        if name in ("/task", "/start", "/done"):
            task_lines.append(line)
        elif cmd.require_project:
            project_lines.append(line)
        else:
            general_lines.append(line)

    sections = ["📖 Available Commands:\n"]
    if general_lines:
        sections.append("General:\n" + "\n".join(general_lines))
    if project_lines:
        label = "Project:" if in_project else "Project (enter a project first):"
        sections.append(label + "\n" + "\n".join(project_lines))
    if task_lines:
        sections.append("Task Mode:\n" + "\n".join(task_lines))
    sections.append("\n💡 You can also chat in natural language — I'll use the right tools automatically.")
    return "\n\n".join(sections)


def build_greeting_text() -> str:
    """Build a short self-introduction with core capabilities."""
    return t("greeting.text")


class HelpHandler(BaseCommandHandler):
    """Show available commands — direct output without LLM."""
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        in_project = hasattr(self.services, '_project') and self.services._project and not self.services._project.is_default
        return CommandResult(response=build_help_text(in_project=in_project))


# ---------------------------------------------------------------------------
# Handler registry (name -> class mapping----------------------------------------------------------------------

HANDLER_CLASSES: dict[str, type[BaseCommandHandler]] = {
    "/help": HelpHandler,
    "/reset": ResetHandler,
    "/stop": StopHandler,
    "/summarize": SummarizeHandler,
    "/recommend": RecommendHandler,
    "/list": ListProjectsHandler,
    "/olist": OverleafListHandler,
    "/switch": SwitchProjectHandler,
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
