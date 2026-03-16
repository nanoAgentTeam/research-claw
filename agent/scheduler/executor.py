import asyncio
import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, TYPE_CHECKING
from loguru import logger

from agent.scheduler.schema import ResearchTask, TaskStatus
from core.llm.types import SystemPromptConfig
from core.llm.middleware import StepCompressionMiddleware, infer_context_limit
from core.prompts import render as render_prompt

if TYPE_CHECKING:
    from agent.services.tool_context import ToolContext

class SDDExecutor:
    """
    Subagent-Driven Development (SDD) Executor.

    Orchestrates the "Worker -> Reviewer" loop for a single task.
    Worker uses standard Session overlay (identical to normal subagent).
    After loop completes, overlay is merged to core/_task_workers/{task_id}_r{round_id}/.
    """

    def __init__(self, tool_context: "ToolContext"):
        self.ctx = tool_context
        self.max_retries = 3
        self.project_root: Optional[Path] = None # Will be set by TaskExecuteTool
        self.round_id: int = 1  # Set by TaskExecuteTool from TaskSession
        self._worker_sessions: dict[str, Any] = {}  # task_id → worker Session
        self._injected_deps: dict[str, set[str]] = {}  # task_id → set of injected dep_id dirs
        self._task_graph: Optional[Any] = None  # Set by BatchRunner for dependency resolution

    async def execute_task(self, task: ResearchTask, on_log: Optional[Callable[[str], Any]] = None, proposal: str = "") -> bool:
        """
        Execute the task using SDD pattern.
        Worker uses standard Session overlay (identical to normal subagent).
        After loop completes, overlay is merged to core/_task_workers/{task_id}_r{round_id}/.
        """
        logger.info(f"SDDExecutor: Starting task {task.id}")

        # Helper to emit progress (handles both sync and async on_log)
        async def _emit(msg: str):
            if on_log:
                if asyncio.iscoroutinefunction(on_log):
                    await on_log(msg)
                else:
                    on_log(msg)

        if not self.ctx.session:
            logger.error(f"No session context for task {task.id}, cannot create worker overlay")
            return False

        await _emit(f"⏳ [{task.id}] 开始: {task.title}")

        # Create worker session — standard overlay, identical to normal subagent
        task_dir_name = f"{task.id}_r{self.round_id}"
        worker_session_id = f"{self.ctx.session.id}/_task_workers/{task_dir_name}"
        project = self.ctx.session.project
        from agent.tools.loader import ToolLoader
        _sdd_profile = ToolLoader._load_profile("sdd_worker")
        worker_session = project.session(worker_session_id, role_type=_sdd_profile.get("role_type", "Worker"))
        worker_session.init_overlay()
        self._worker_sessions[task.id] = worker_session
        work_dir = worker_session.root

        # Worker -> Reviewer loop
        for attempt in range(self.max_retries + 1):
            task.retry_count = attempt

            # --- Phase A: Worker ---
            await _emit(f"🔄 [{task.id}] Worker 工作中... (第 {attempt+1} 轮)")
            success = await self._run_worker(task, work_dir, on_log, proposal=proposal)
            if not success:
                logger.warning(f"Worker execution failed for task {task.id}, retrying...")
                await _emit(f"⚠️ [{task.id}] Worker 失败, 重试 ({attempt+1}/{self.max_retries})...")
                continue

            # --- Phase B: Reviewer ---
            await _emit(f"🧐 [{task.id}] 审核中...")
            review_passed, feedback = await self._run_reviewer(task, work_dir, on_log)

            if review_passed:
                logger.info(f"Task {task.id} PASSED review.")
                task.status = TaskStatus.COMPLETED
                self._merge_worker_to_core(task)
                await _emit(f"✅ [{task.id}] 完成: {task.title}")
                return True
            else:
                logger.info(f"Task {task.id} FAILED review. Feedback: {feedback}")
                task.feedback_history.append(feedback)
                await _emit(f"🧐 [{task.id}] 审核未通过: {feedback[:100]}...")

        logger.warning(f"Task {task.id} failed review after {self.max_retries} retries. Defaulting to PASS.")
        await _emit(f"⚠️ [{task.id}] 审核重试耗尽, 默认通过")

        task.status = TaskStatus.COMPLETED
        self._merge_worker_to_core(task)
        await _emit(f"✅ [{task.id}] 完成: {task.title}")
        return True

    async def _run_worker(self, task: ResearchTask, work_dir: Any, on_log: Optional[Callable[[str], Any]] = None, proposal: str = "") -> bool:
        """Run the Worker Agent."""
        project_root = self.project_root if self.project_root else self.ctx.workspace

        # --- Dependency injection: copy all ancestor outputs into overlay/{dep_id}/ ---
        # Restore permissions from previous attempt (retry scenario)
        self._restore_dep_permissions(task.id)
        injected = set()
        if not self.ctx.session:
            logger.warning(f"No session context for task {task.id}, dependency injection skipped")
        elif task.dependencies:
            import shutil
            project_core = self.ctx.session.project.core
            tw_root = project_core / "_task_workers"

            # Recursively collect ALL ancestor task IDs (not just direct deps)
            all_dep_ids = self._collect_all_ancestors(task.id)

            for dep_id in all_dep_ids:
                dep_dir_name = f"{dep_id}_r{self.round_id}"
                dep_source = tw_root / dep_dir_name
                if not dep_source.exists():
                    continue
                injected.add(dep_id)
                for f in dep_source.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(dep_source)
                    # Skip hidden dirs
                    if any(p.startswith(".") for p in rel.parts):
                        continue
                    target = work_dir / dep_id / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
        self._injected_deps[task.id] = injected

        # Layer 1 + 3: Set write protection on dependency directories
        if injected:
            worker_session = self._worker_sessions[task.id]
            worker_session._protected_prefixes = frozenset(injected)
            # OS-level read-only (blocks bash writes like `echo > t1/file.md`)
            for dep_id in injected:
                dep_dir = work_dir / dep_id
                if dep_dir.exists():
                    for f in dep_dir.rglob("*"):
                        if f.is_file():
                            os.chmod(f, 0o444)
                        elif f.is_dir():
                            os.chmod(f, 0o555)
                    os.chmod(dep_dir, 0o555)

        # Construct Prompt
        _WORKER_BASE_FALLBACK = (
            "You are a specialized worker agent: {agent}.\n"
            "Your Goal: {description}\n"
            "Your working directory contains the project files. "
            "You can read all existing project files and create new files.\n"
            "Your writes are isolated — they won't modify the original project files.\n"
            "Write your outputs using simple filenames (e.g. 'lit_review.md', 'derivation.tex').\n"
            "PERFORMANCE TIP: You support PARALLEL tool calls."
        )
        base_prompt = render_prompt("scheduler_worker_base.txt", _WORKER_BASE_FALLBACK,
                                    agent=task.assigned_agent, description=task.description)

        # Layer 2: Prompt-level warning about read-only dependency directories
        if injected:
            base_prompt += (
                f"\nIMPORTANT — Dependency directories are READ-ONLY:\n"
                f"  Directories: {', '.join(sorted(injected))}\n"
                f"  You CAN read files from them (read_file, bash cat).\n"
                f"  You CANNOT write or modify files inside them.\n"
                f"  To use dependency content: read it, then write your own version "
                f"to a new file in the current directory.\n"
                f"  All your outputs must be simple filenames in the current directory "
                f"(e.g. 'report.tex', 'results.json').\n"
            )

        # Inject proposal context if available
        if proposal:
            base_prompt += f"\n\n[TASK PROPOSAL / OVERALL PLAN]\n{proposal}\n"

        # Inject dependency outputs info (all ancestors copied into work_dir/{dep_id}/)
        all_dep_ids = self._collect_all_ancestors(task.id)
        if all_dep_ids:
            dep_info_parts = []
            for dep_id in all_dep_ids:
                dep_dir = work_dir / dep_id
                if not dep_dir.exists():
                    continue
                for f in dep_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(dep_dir)
                    if any(p.startswith(".") for p in rel.parts):
                        continue
                    rel_str = str(rel)
                    try:
                        size = f.stat().st_size
                        if size < 2048:
                            content = f.read_text(encoding="utf-8", errors="replace")
                            dep_info_parts.append(f"[{dep_id}/{rel_str}] (from dependency {dep_id}, inlined)\n{content}")
                        else:
                            dep_info_parts.append(f"[{dep_id}/{rel_str}] (from dependency {dep_id}, {size} bytes)")
                    except Exception:
                        pass
            if dep_info_parts:
                base_prompt += "\n\n[DEPENDENCY OUTPUTS - in your working directory under {dep_id}/ folders]\n" + "\n".join(dep_info_parts[:10]) + "\n"

        # Inject Session Context Summary
        try:
            context_file = self.ctx.context_manager.context_memory_file
            if context_file.exists():
                context_summary = context_file.read_text(encoding="utf-8")
                if context_summary:
                    base_prompt += f"\n\n[SESSION CONTEXT SUMMARY]\n{context_summary}\n"
        except Exception as e:
            logger.warning(f"Failed to inject context summary: {e}")

        if task.feedback_history:
            user_msg = (
                f"Your previous output was reviewed and REJECTED.\n"
                f"Reviewer feedback:\n{task.feedback_history[-1]}\n\n"
                f"FIX the issues identified by the reviewer. "
                f"Do NOT redo everything from scratch — only modify or add what's needed to address the feedback. "
                f"Use read_file to check your existing work, then use str_replace or write_file to fix specific issues."
            )
        else:
            user_msg = "Start working. Use tools to create artifacts."

        messages = [
            {"role": "system", "content": base_prompt},
            {"role": "user", "content": user_msg}
        ]

        from core.llm.engine import AgentEngine
        _ctx_limit = infer_context_limit(self.ctx.model)
        _compression_threshold = 0.65 if "step" in (self.ctx.model or "").lower() else 0.7
        temp_engine = AgentEngine(
            strategies=[StepCompressionMiddleware(
                model_context_limit=_ctx_limit,
                compression_threshold=_compression_threshold,
            )],
            provider=self.ctx.provider,
            model=self.ctx.model,
        )

        worker_tools = self._get_sandboxed_tools(task, profile="sdd_worker")

        logger.info(f"Worker {task.assigned_agent} starting...")
        token_buffer = ""
        trajectory: list[dict] = []  # Worker trace log

        try:
            current_iter = 0
            # Per-task budget > config default > 60, clamped to minimum 60
            _MIN_WORKER_ITERS = 60
            try:
                from config.loader import load_config
                _cfg = load_config()
                config_default = getattr(_cfg.features.agent, 'max_worker_iterations', _MIN_WORKER_ITERS)
            except Exception:
                config_default = _MIN_WORKER_ITERS
            max_worker_iters = max(task.max_iterations or config_default, _MIN_WORKER_ITERS)

            # Timeout scales with iterations: 40 iters → 10min, 60 iters → 15min (15s per iter, min 900s)
            worker_timeout = max(max_worker_iters * 15, 900)
            _worker_start = asyncio.get_event_loop().time()
            async for event in temp_engine.run(
                messages=messages,
                system_config=SystemPromptConfig(base_prompt=base_prompt),
                tools=worker_tools,
                max_iterations=max_worker_iters,
                return_full_history=False
            ):
                # Enforce worker_timeout
                if asyncio.get_event_loop().time() - _worker_start > worker_timeout:
                    logger.warning(f"Worker {task.assigned_agent} exceeded timeout ({worker_timeout}s)")
                    trajectory.append({"type": "error", "data": f"Timeout after {worker_timeout}s"})
                    self._save_trajectory(task, trajectory)
                    return False
                # Capture and log events
                if on_log:
                    log_msg = None
                    
                    if event.type == "token":
                        # Buffer tokens and only log if it contains a newline or is significant
                        token = event.data.get("delta", "")
                        token_buffer += token
                        if "\n" in token_buffer or len(token_buffer) > 200:
                            log_msg = token_buffer
                            token_buffer = ""
                    
                    if event.type == "tool_call":
                        current_iter += 1
                        remaining = max_worker_iters - current_iter
                        trajectory.append({"type": "tool_call", "iteration": current_iter, "total": max_worker_iters, "tools": [tc["function"]["name"] for tc in event.data["tool_calls"]]})
                        header = f"\n🔄 **Step {current_iter}/{max_worker_iters}**"
                        if remaining <= 5:
                            header += f"  ⚠️ {remaining} steps remaining — wrap up soon!"
                        header += "\n"
                        
                        tool_calls = event.data["tool_calls"]
                        tool_msgs = []
                        for tc in tool_calls:
                            try:
                                args = json.loads(tc["function"]["arguments"])
                                args_str = json.dumps(args, ensure_ascii=False, indent=2)
                                tool_msgs.append(f"🛠️ Tool: `{tc['function']['name']}`\nArguments:\n{args_str}")
                            except:
                                tool_msgs.append(f"🛠️ Tool: `{tc['function']['name']}` (raw args: {tc['function'].get('arguments')})")
                        
                        log_msg = (token_buffer + "\n") if token_buffer else ""
                        log_msg += f"{header}" + "\n".join(tool_msgs)
                        token_buffer = ""
                        
                    elif event.type == "tool_result":
                        result_str = str(event.data['result'])
                        trajectory.append({"type": "tool_result", "name": event.data['name'], "result": result_str[:500]})
                        if len(result_str) > 500: result_str = result_str[:500] + "... (truncated)"
                        log_msg = (token_buffer + "\n") if token_buffer else ""
                        log_msg += f"✅ Tool result: `{event.data['name']}` ->\n{result_str}"
                        token_buffer = ""
                        
                    elif event.type == "message" and event.data.get("role") == "assistant" and event.data.get("content"):
                        content = event.data["content"]
                        trajectory.append({"type": "assistant", "content": content[:500]})
                        if len(content) > 300:
                            log_msg = f"💡 Worker Thinking: {content[:300]}..."
                        else:
                            log_msg = f"💡 Worker Thinking: {content}"
                        if "Iteration" not in log_msg: 
                             log_msg = (token_buffer + "\n\n") if token_buffer else "\n"
                             log_msg += f"💡 Worker Thinking: {content}"
                        token_buffer = ""
                    
                    if log_msg:
                        if asyncio.iscoroutinefunction(on_log): await on_log(log_msg)
                        else: on_log(log_msg)

                if event.type == "error":
                    logger.error(f"Worker Error: {event.data}")
                    trajectory.append({"type": "error", "data": str(event.data)})
                    self._save_trajectory(task, trajectory)
                    return False

            self._save_trajectory(task, trajectory)
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Worker {task.assigned_agent} timed out after {worker_timeout}s")
            trajectory.append({"type": "error", "data": f"Timeout after {worker_timeout}s"})
            self._save_trajectory(task, trajectory)
            return False
        except Exception as e:
            logger.error(f"Worker exception: {e}")
            trajectory.append({"type": "error", "data": str(e)})
            self._save_trajectory(task, trajectory)
            return False

    def _save_trajectory(self, task: ResearchTask, trajectory: list[dict]):
        """Save worker trajectory to worker session metadata (overlay, not core)."""
        try:
            worker_session = self._worker_sessions.get(task.id)
            if not worker_session:
                return
            trace_dir = worker_session.metadata
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_file = trace_dir / "trajectory.json"
            from datetime import datetime
            data = {
                "task_id": task.id,
                "title": task.title,
                "agent": task.assigned_agent,
                "timestamp": datetime.now().isoformat(),
                "events": trajectory,
            }
            trace_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug(f"Saved worker trajectory for {task.id} ({len(trajectory)} events)")
        except Exception as e:
            logger.warning(f"Failed to save worker trajectory: {e}")

    def _merge_worker_to_core(self, task: ResearchTask):
        """Merge worker overlay outputs to core/_task_workers/{task_id}_r{round_id}/."""
        # Restore dependency directory permissions before merge (otherwise rmtree fails)
        self._restore_dep_permissions(task.id)

        worker_session = self._worker_sessions.get(task.id)
        if not worker_session:
            logger.warning(f"No worker session for {task.id}, skip merge")
            return

        import shutil
        from core.session import Session

        project_core = self.ctx.session.project.core
        task_dir_name = f"{task.id}_r{self.round_id}"
        target_dir = project_core / "_task_workers" / task_dir_name
        target_dir.mkdir(parents=True, exist_ok=True)

        merged = []
        injected = self._injected_deps.get(task.id, set())
        for f in Session._diff_overlay(worker_session):
            # Skip injected dependency directories (e.g. t1/, t2/)
            top_dir = Path(f.relative).parts[0] if Path(f.relative).parts else ""
            if top_dir in injected:
                continue
            target = target_dir / f.relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f.absolute, target)
            merged.append(f.relative)

        logger.info(f"Merged {len(merged)} files from worker overlay to {target_dir.relative_to(project_core)}")

    def _collect_all_ancestors(self, task_id: str) -> list[str]:
        """Recursively collect all ancestor task IDs from the TaskGraph DAG."""
        if not self._task_graph or task_id not in self._task_graph.tasks:
            return []

        ancestors = set()
        queue = list(self._task_graph.tasks[task_id].dependencies)
        while queue:
            dep_id = queue.pop(0)
            if dep_id in ancestors:
                continue
            ancestors.add(dep_id)
            dep_task = self._task_graph.tasks.get(dep_id)
            if dep_task:
                queue.extend(dep_task.dependencies)
        return sorted(ancestors)

    def _restore_dep_permissions(self, task_id: str):
        """Restore dependency directory permissions so cleanup/rmtree can succeed."""
        worker_session = self._worker_sessions.get(task_id)
        if not worker_session:
            return
        for dep_id in self._injected_deps.get(task_id, set()):
            dep_dir = worker_session.root / dep_id
            if dep_dir.exists():
                for f in dep_dir.rglob("*"):
                    os.chmod(f, 0o755 if f.is_dir() else 0o644)
                os.chmod(dep_dir, 0o755)

    def _get_sandboxed_tools(self, task: ResearchTask, profile: str = "sdd_worker") -> List[Any]:
        """Create a set of tools for the worker/reviewer via profile-based loading.

        The worker session is created in execute_task() and cached in _worker_sessions.
        Worker operates entirely within its overlay (copy-on-init from core).
        """
        from agent.tools.loader import ToolLoader

        worker_session = self._worker_sessions.get(task.id)
        # file_root/work_dir use project_root (not overlay) — this is the anchor for
        # LaTeXCompileTool (workspace_root) and OverleafTool (work_dir/projects_root).
        # File tools (read/write/bash) use the session param for overlay routing.
        project_root = self.project_root if self.project_root else self.ctx.workspace

        config_path = Path("config/tools.json")
        if not config_path.exists():
            config_path = self.ctx.workspace / "config" / "tools.json"

        loader = ToolLoader(config_path)
        context = {
            "session": worker_session,
            "workspace": self.ctx.workspace,
            "file_root": project_root,
            "work_dir": project_root,
            "provider": self.ctx.provider,
            "model": self.ctx.model,
            "config": self.ctx.config,
            "project": worker_session.project if worker_session else self.ctx.project,
        }

        return loader.load_for_profile(profile, context)

    async def _run_reviewer(self, task: ResearchTask, work_dir: Any, on_log: Optional[Callable[[str], Any]] = None) -> tuple[bool, str]:
        """Run the Reviewer Agent."""
        logger.info(f"Reviewer checking task {task.id}...")
        
        if on_log:
            msg = "🧐 Reviewing work..."
            if asyncio.iscoroutinefunction(on_log): await on_log(msg)
            else: on_log(msg)
        
        spec = task.spec

        # Prompt for Reviewer
        injected = self._injected_deps.get(task.id, set())
        dep_note = ""
        if injected:
            dep_note = (
                f"\nNote: Directories {{{', '.join(sorted(injected))}}} are READ-ONLY dependencies "
                f"from upstream tasks — NOT this worker's output. Ignore them when reviewing. "
                f"Only review files outside these directories.\n"
            )
        _REVIEWER_FALLBACK = (
            "You are a Quality Assurance Reviewer.\n"
            "Worker: {agent_name}\nTask: {task_title}\nGoal: {task_description}\n"
            "Spec/Criteria: {spec}\n{dep_note}"
            "Action: Use `bash` with `ls` to see the worker's output files, then `read_file` to inspect.\n"
            "Judgment guidelines:\n"
            "- Focus on CONTENT QUALITY.\n"
            "- Filename mismatches alone are NOT grounds for FAIL.\n"
            "- Only FAIL for substantive issues.\n\n"
            "You MUST end with exactly 'CONCLUSION: PASS' or 'CONCLUSION: FAIL: <reason>'."
        )
        prompt = render_prompt("scheduler_reviewer.txt", _REVIEWER_FALLBACK,
                               agent_name=task.assigned_agent, task_title=task.title,
                               task_description=task.description, spec=spec, dep_note=dep_note)
        
        from core.llm.engine import AgentEngine
        from core.llm.types import SystemPromptConfig
        _ctx_limit = infer_context_limit(self.ctx.model)
        temp_engine = AgentEngine(
            strategies=[StepCompressionMiddleware(model_context_limit=_ctx_limit)],
            provider=self.ctx.provider,
            model=self.ctx.model,
        )

        final_response = ""
        iteration_count = 0
        max_reviewer_iterations = 20
        token_buffer = ""
        
        async for event in temp_engine.run(
            messages=[{"role": "user", "content": "Review the worker's output now."}],
            system_config=SystemPromptConfig(base_prompt=prompt),
            tools=self._get_sandboxed_tools(task, profile="sdd_reviewer"),
            max_iterations=max_reviewer_iterations,
            return_full_history=False
        ):
            if event.type == "token":
                token = event.data.get("delta", "")
                token_buffer += token
                if "\n" in token_buffer or len(token_buffer) > 200:
                    if on_log:
                        if asyncio.iscoroutinefunction(on_log): await on_log(token_buffer)
                        else: on_log(token_buffer)
                    token_buffer = ""
            
            if event.type == "message" and event.data.get("role") == "assistant":
                content = event.data.get("content", "")
                if content:
                    final_response += f"\n{content}" # Accumulate response to handle multi-turn chattiness
                
                # Progress hint for the user
                iteration_count += 1
                if on_log:
                    # Flush buffer before status update
                    msg = (token_buffer + "\n") if token_buffer else ""
                    msg += f"🧐 Reviewing... (Step {iteration_count}/{max_reviewer_iterations})"
                    if asyncio.iscoroutinefunction(on_log): await on_log(msg)
                    else: on_log(msg)
                token_buffer = ""
        
        # Robust Parsing: Check for CONCLUSION or PASS in the accumulated history
        # We check the very end of the response for the conclusion first
        import re
        conclusion_match = re.search(r"CONCLUSION:\s*(PASS|FAIL:?.*)", final_response, re.IGNORECASE | re.DOTALL)
        
        if conclusion_match:
            verdict = conclusion_match.group(1).strip()
            if verdict.upper().startswith("PASS"):
                return True, "Passed"
            else:
                return False, verdict
        
        # Fallback to word-boundary keyword check if specific format missing
        tail = final_response.upper()[-200:]
        if re.search(r'\bPASS\b', tail) and not re.search(r'\bFAIL\b', tail):
            return True, "Passed (inferred)"
        else:
            return False, final_response.strip()
