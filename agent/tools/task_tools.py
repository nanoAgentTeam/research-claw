"""Task-mode tools for the main AgentLoop.

Five BaseTool subclasses that get dynamically registered into the main agent's
ToolRegistry when the user enters `/task` mode, and unregistered on `/done`.

All tools share a single TaskSession instance for state coordination.
Each tool enforces phase constraints in its execute() method.
"""

from __future__ import annotations

import json
import asyncio
import os
import re
from typing import Any, Dict, Optional, TYPE_CHECKING

from loguru import logger

from core.tools.base import BaseTool
from config.i18n import t
from agent.task_agent import TaskPhase, TaskSession, has_cycle, format_plan_display

if TYPE_CHECKING:
    from agent.services.tool_context import ToolContext


def _get_planner_provider_and_model(ctx):
    """Get provider and model for planning phase (task_propose, task_build).

    Currently returns ctx.provider/ctx.model directly.
    To use a separate planner model, add get_provider_for_role('planner') to Config.
    """
    return ctx.provider, ctx.model


_PHASE_ENTER_KEY = {
    TaskPhase.PROPOSE: "phase.propose.enter",
    TaskPhase.PLAN: "phase.plan.enter",
    TaskPhase.EXECUTE: "phase.execute.enter",
    TaskPhase.FINALIZE: "phase.finalize.enter",
}

_PHASE_DONE_KEY = {
    TaskPhase.PROPOSE: "phase.propose.done",
    TaskPhase.PLAN: "phase.plan.done",
    TaskPhase.EXECUTE: "phase.execute.done",
    TaskPhase.FINALIZE: "phase.finalize.done",
}


async def _notify_phase_enter(on_token: Any, phase: TaskPhase,
                              bus=None, message_context: dict = None) -> None:
    """Push a phase-enter notification to the user.

    IM channels receive a standalone notification message (bypasses buffer).
    CLI receives the hint via on_token callback.
    """
    key = _PHASE_ENTER_KEY.get(phase)
    if not key:
        return
    hint = t(key)
    # IM notification
    if bus and message_context:
        from bus.events import OutboundMessage
        await bus.publish_outbound(OutboundMessage(
            channel=message_context.get("channel", ""),
            chat_id=message_context.get("chat_id", ""),
            content=hint,
            is_notification=True,
        ))
    # CLI fallback
    if on_token:
        if asyncio.iscoroutinefunction(on_token):
            await on_token(hint)
        else:
            on_token(hint)


async def _notify_phase_done(on_token: Any, phase: TaskPhase,
                             bus=None, message_context: dict = None) -> None:
    """Push a phase-done notification to the user.

    IM channels receive a standalone notification message (bypasses buffer).
    CLI receives the hint via on_token callback.
    """
    key = _PHASE_DONE_KEY.get(phase)
    if not key:
        return
    hint = t(key)
    # IM notification
    if bus and message_context:
        from bus.events import OutboundMessage
        await bus.publish_outbound(OutboundMessage(
            channel=message_context.get("channel", ""),
            chat_id=message_context.get("chat_id", ""),
            content=hint,
            is_notification=True,
        ))
    # CLI fallback
    if on_token:
        if asyncio.iscoroutinefunction(on_token):
            await on_token(hint)
        else:
            on_token(hint)


# ---------------------------------------------------------------------------
# TaskProposeTool
# ---------------------------------------------------------------------------

class TaskProposeTool(BaseTool):
    """Generate a Proposal (natural language plan) from project context and user goal."""

    def __init__(self, session: TaskSession, ctx: "ToolContext"):
        self._session = session
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "task_propose"

    @property
    def description(self) -> str:
        return (
            "Generate a Proposal for the user's goal. Reads project context and "
            "produces a structured plan in natural language. "
            "Available in UNDERSTAND and PROPOSE phase (call again in PROPOSE to revise). "
            "When revising, pass the user's specific feedback in revision_notes so the planner "
            "knows exactly what to change. "
            "IMPORTANT: After calling this tool, you MUST show the proposal to the user "
            "and wait for their feedback. Do NOT call any other task tool until the user responds."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The user's goal to plan for.",
                },
                "revision_notes": {
                    "type": "string",
                    "description": "User's feedback on the previous proposal. Only provide when revising an existing proposal.",
                },
            },
            "required": ["goal"],
        }

    async def execute(self, goal: str = "", revision_notes: str = "", _agent_messages: list = None, on_token: Any = None, message_context: dict = None, **kwargs) -> str:
        # Phase gate: allowed in UNDERSTAND and PROPOSE (for revisions)
        if self._session.phase not in (TaskPhase.UNDERSTAND, TaskPhase.PROPOSE):
            return t("task.propose_blocked", phase=self._session.phase.value)

        if not goal:
            goal = self._session.goal
        if not goal:
            return t("task.no_goal")
        self._session.goal = goal

        project = self._ctx.session.project if self._ctx.session else None
        if not project:
            return t("task.no_project_context")

        # Collect project context
        tree = project.file_tree(max_depth=2)
        tree_str = "\n".join(tree) if tree else "(empty project)"
        memory = project.load_memory()

        context_block = (
            f"Project files:\n{tree_str}\n"
            f"Project memory:\n{memory[:1000] if memory else '(none)'}\n"
        )

        # Inject conversation context from agent messages
        conversation_context = _extract_conversation_context(_agent_messages)
        if conversation_context:
            context_block += f"\nConversation context (user's recent discussion):\n{conversation_context}\n"

        # Build previous proposal block for revision
        previous_proposal_block = ""
        if self._session.proposal:
            previous_proposal_block = f"Previous Proposal:\n{self._session.proposal}\n\n"
            if revision_notes:
                previous_proposal_block += f"User's revision request:\n{revision_notes}\n\nRevise the previous proposal according to the user's feedback. Keep unchanged parts intact, only modify what the user requested.\n\n"
            else:
                previous_proposal_block += "User requested a revision but did not specify details. Regenerate with improvements.\n\n"

        # Try external template, fall back to inline
        from core.prompts import render as render_prompt
        _PROPOSE_FALLBACK = (
            "You are a research planning expert.\n\n"
            "User Goal: {goal}\n\n"
            "Project Context:\n{context_block}\n\n"
            "{previous_proposal_block}"
            "Generate a Proposal in Chinese that includes:\n"
            "1. Objective (目标)\n"
            "2. Scope (范围)\n"
            "3. Constraints (约束)\n"
            "4. Methodology (方法)\n"
            "5. Expected deliverables (预期产出)\n\n"
            "For '预期产出', use a numbered list with format: filename — description\n"
            "Example:\n"
            "1. main.tex — 完整的 LaTeX 研究报告\n"
            "2. figures/ — 论文中使用的所有图表\n\n"
            "Do NOT add any conversational text after the proposal. "
            "Output ONLY the proposal content."
        )
        proposal_prompt = render_prompt("task_propose_generate.txt", _PROPOSE_FALLBACK,
                                        goal=goal, context_block=context_block,
                                        previous_proposal_block=previous_proposal_block)

        await _notify_phase_enter(on_token, TaskPhase.PROPOSE,
                                  bus=self._ctx.bus, message_context=message_context)

        planner_provider, planner_model = _get_planner_provider_and_model(self._ctx)
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                resp = await planner_provider.chat(
                    messages=[{"role": "user", "content": proposal_prompt}],
                    model=planner_model,
                    temperature=0.4,
                    on_token=on_token,
                )
                self._session.proposal = resp.content.strip()
                # Parse deliverables from proposal
                self._session.expected_deliverables = self._parse_deliverables(self._session.proposal)
                if self._session.expected_deliverables:
                    break  # Successfully parsed
                # Retry: deliverables section not in expected format
                logger.warning(f"task_propose attempt {attempt+1}: deliverables parse returned empty, retrying...")
                proposal_prompt += (
                    "\n\n[RETRY] Your previous output did NOT contain a properly formatted '预期产出' section. "
                    "The '预期产出' MUST be a numbered list, one item per line. Example:\n"
                    "1. main.tex — 完整的 LaTeX 研究报告\n"
                    "2. figures/ — 论文图表\n"
                    "Fix this and regenerate the FULL proposal."
                )
            except Exception as e:
                if attempt < max_attempts - 1:
                    continue
                return t("task.propose_failed", error=e)

        self._session.phase = TaskPhase.PROPOSE
        await _notify_phase_done(on_token, TaskPhase.PROPOSE,
                                 bus=self._ctx.bus, message_context=message_context)

        if self._session.auto_mode:
            # Simulate a conservative, affirmative user review
            try:
                review_resp = await planner_provider.chat(
                    messages=[
                        {"role": "system", "content": "你是一个保守、认可方案的用户审阅者。你的回复要简短（1-2句），整体倾向认可，不提出大改动，直接表示同意并希望继续执行。"},
                        {"role": "user", "content": f"请审阅以下方案并给出简短的认可回复：\n\n{self._session.proposal}"},
                    ],
                    model=planner_model,
                    temperature=0.2,
                    on_token=on_token,
                )
                virtual_user_reply = review_resp.content.strip()
            except Exception:
                virtual_user_reply = t("task.proposal_auto_confirm")

            return (
                f"[Proposal]\n\n{self._session.proposal}\n\n"
                f"[Virtual User Review]: {virtual_user_reply}\n\n"
                f"[INSTRUCTION] The virtual user approved the proposal. "
                f"Immediately call task_build() to generate the TaskGraph. Do not wait."
            )

        return (
            f"[Proposal]\n\n{self._session.proposal}\n\n"
            f"[INSTRUCTION] Show the proposal above to the user. "
            f"At the end, remind the user: they can reply with feedback to revise, "
            f"or confirm to proceed."
        )

    @staticmethod
    def _parse_deliverables(proposal: str) -> list[dict]:
        """Extract deliverable items from the '预期产出' section of a proposal.
        Returns list of {"name": str, "description": str}."""
        lines = proposal.splitlines()
        in_section = False
        deliverables = []
        for line in lines:
            stripped = line.strip()
            # Detect start of deliverables section
            if re.search(r"预期产出|Expected [Dd]eliverables|交付物", stripped):
                in_section = True
                continue
            # Detect next section header (end of deliverables)
            if in_section and re.match(r"^(\*\*)?[#\d]+[.、]?\s*.{2,}(目标|范围|约束|方法|风险|时间|步骤|备注|Objective|Scope|Constraint|Method|Risk|Timeline|Step|Note)", stripped):
                break
            if in_section and re.match(r"^#{1,3}\s", stripped):
                break
            # Bold section headers like **Next Steps** or **备注**
            if in_section and re.match(r"^\*\*[^*]+\*\*\s*$", stripped) and not re.match(r"^[\d]+[.、)\]]", stripped):
                break
            # Parse numbered/bulleted items
            if in_section:
                m = re.match(r"^[\d]+[.、)\]]\s*(.+)", stripped)
                if not m:
                    m = re.match(r"^[-*•]\s*(.+)", stripped)
                if m:
                    raw = m.group(1).strip()
                    # Split "name — description" or "name – description" or "name - description"
                    # Require spaces around hyphen to avoid splitting filenames like "lit-review.md"
                    parts = re.split(r"\s*[—–]\s*|\s+-\s+", raw, maxsplit=1)
                    if len(parts) == 2:
                        name = parts[0].strip()
                        desc = parts[1].strip()
                    else:
                        name = raw
                        desc = ""
                    # Validate: name must look like a filename (has extension) or directory (ends with /)
                    clean_name = re.sub(r"\*+", "", name).strip()
                    if re.search(r"\.\w{1,10}$", clean_name) or clean_name.endswith("/"):
                        deliverables.append({"name": clean_name, "description": desc})
        return deliverables


# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------

def _extract_conversation_context(messages: list | None, max_chars: int = 4000) -> str:
    """Compress agent messages into a text summary for LLM context injection."""
    if not messages:
        return ""
    parts = []
    total = 0
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue
        content = msg.get("content", "")
        if role == "tool":
            tool_name = msg.get("name", "tool")
            snippet = f"[Tool:{tool_name}] {str(content)[:300]}"
        elif role == "assistant" and msg.get("tool_calls"):
            calls = msg["tool_calls"]
            names = [tc.get("function", {}).get("name", "?") if isinstance(tc, dict) else getattr(getattr(tc, 'function', None), 'name', '?') for tc in calls[:3]]
            snippet = f"[Assistant called: {', '.join(names)}]"
        else:
            snippet = f"[{role}] {str(content)[:200]}"
        if total + len(snippet) > max_chars:
            break
        parts.append(snippet)
        total += len(snippet)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# TaskBuildTool
# ---------------------------------------------------------------------------

class TaskBuildTool(BaseTool):
    """Convert the current Proposal into a TaskGraph DAG."""

    def __init__(self, session: TaskSession, ctx: "ToolContext"):
        self._session = session
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "task_build"

    @property
    def description(self) -> str:
        return (
            "Convert the current Proposal into a TaskGraph (DAG of executable tasks). "
            "Only available in PROPOSE phase, after a proposal has been generated and user is satisfied. "
            "IMPORTANT: After calling this tool, you MUST show the TaskGraph to the user "
            "and wait for their confirmation. User must input /start to begin execution. "
            "Do NOT call task_execute yourself."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, _agent_messages: list = None, on_token: Any = None, message_context: dict = None, **kwargs) -> str:
        # Phase gate
        if self._session.phase != TaskPhase.PROPOSE:
            return t("task.build_blocked", phase=self._session.phase.value)

        if not self._session.proposal:
            return t("task.no_proposal")

        project = self._ctx.session.project if self._ctx.session else None
        if not project:
            return t("task.no_project_context")

        # Collect project context
        tree = project.file_tree(max_depth=2)
        tree_str = "\n".join(tree) if tree else "(empty project)"
        memory = project.load_memory()

        context_block = (
            f"Project files:\n{tree_str}\n"
            f"Project memory:\n{memory[:1000] if memory else '(none)'}\n"
        )

        # Inject conversation context from agent messages
        conversation_context = _extract_conversation_context(_agent_messages)
        if conversation_context:
            context_block += f"\nConversation context:\n{conversation_context}\n"

        # Try external template, fall back to inline
        from core.prompts import render as render_prompt
        _BUILD_FALLBACK = (
            "Based on this proposal, generate a TaskGraph as JSON.\n\n"
            "Proposal:\n{proposal}\n\n"
            "Project Context:\n{context_block}\n\n"
            "Requirements:\n"
            "- Each task should be a meaningful, self-contained unit.\n"
            "- Each task must have: id, title, description, type (research/code/review/analysis/writing), "
            "dependencies (list of task IDs), spec (acceptance criteria), assigned_agent, output_dir.\n"
            "- output_dir: use the task id as label (e.g. 't1'). Each Worker runs in its own isolated directory.\n"
            "- Use task IDs like 't1', 't2', etc.\n"
            "- Ensure the DAG has no cycles.\n"
            "- File names in description should match the proposal's deliverables.\n\n"
            'Return ONLY valid JSON in this format:\n'
            '{{"project_id": "{project_id}", "tasks": {{"t1": {{...}}, "t2": {{...}}}}}}\n'
        )
        graph_prompt = render_prompt("task_build_generate.txt", _BUILD_FALLBACK,
                                     proposal=self._session.proposal,
                                     context_block=context_block,
                                     project_id=project.id)

        await _notify_phase_enter(on_token, TaskPhase.PLAN,
                                  bus=self._ctx.bus, message_context=message_context)

        planner_provider, planner_model = _get_planner_provider_and_model(self._ctx)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = await planner_provider.chat(
                    messages=[{"role": "user", "content": graph_prompt}],
                    model=planner_model,
                    temperature=0.2,
                    on_token=on_token,
                )
                raw = resp.content.strip()
                if "```json" in raw:
                    raw = raw.split("```json")[1].split("```")[0].strip()
                elif "```" in raw:
                    raw = raw.split("```")[1].split("```")[0].strip()

                data = json.loads(raw)
                from agent.scheduler.schema import TaskGraph
                graph = TaskGraph(**data)

                if has_cycle(graph):
                    graph_prompt += "\n\n[RETRY] Previous graph had cycles. Fix the dependencies."
                    continue

                self._session.task_graph = graph

                display = format_plan_display(graph)
                if self._session.auto_mode:
                    # Simulate conservative user plan confirmation
                    try:
                        confirm_resp = await planner_provider.chat(
                            messages=[
                                {"role": "system", "content": "你是一个保守、认可任务计划的用户。你的回复要简短（1句），整体倾向确认计划合理，并表示同意开始执行。"},
                                {"role": "user", "content": f"请确认以下任务计划并表示同意执行：\n\n{display}"},
                            ],
                            model=planner_model,
                            temperature=0.2,
                            on_token=on_token,
                        )
                        virtual_confirm = confirm_resp.content.strip()
                    except Exception:
                        virtual_confirm = t("task.plan_auto_confirm")

                    # Skip PLAN phase — go straight to EXECUTE
                    self._session.phase = TaskPhase.EXECUTE
                    await _notify_phase_done(on_token, TaskPhase.PLAN,
                                             bus=self._ctx.bus, message_context=message_context)
                    await _notify_phase_enter(on_token, TaskPhase.EXECUTE,
                                              bus=self._ctx.bus, message_context=message_context)
                    return (
                        f"{display}\n\n"
                        f"[Virtual User Confirmation]: {virtual_confirm}\n\n"
                        f"[INSTRUCTION] The virtual user confirmed the plan. "
                        f"Immediately call task_execute() to run all tasks. Do not wait."
                    )

                self._session.phase = TaskPhase.PLAN
                await _notify_phase_done(on_token, TaskPhase.PLAN,
                                         bus=self._ctx.bus, message_context=message_context)
                return (
                    f"{display}\n\n"
                    f"[INSTRUCTION] The plan above is already formatted. "
                    f"Show it as-is to the user. "
                    f"At the end, remind the user: they can request changes, "
                    f"or type /start to begin execution."
                )

            except json.JSONDecodeError as e:
                if attempt < max_retries - 1:
                    graph_prompt += f"\n\n[RETRY] JSON parse error: {e}. Return valid JSON only."
                    continue
                return t("task.graph_parse_failed", max_retries=max_retries, error=e)
            except Exception as e:
                if attempt < max_retries - 1:
                    graph_prompt += f"\n\n[RETRY] Validation error: {e}. Fix and retry."
                    continue
                return t("task.graph_validation_failed", error=e)

        return t("task.graph_generation_failed")


# ---------------------------------------------------------------------------
# TaskModifyTool
# ---------------------------------------------------------------------------

class TaskModifyTool(BaseTool):
    """Incrementally modify the current task plan (add/remove/update tasks)."""

    def __init__(self, session: TaskSession, ctx: "ToolContext"):
        self._session = session
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "task_modify"

    @property
    def description(self) -> str:
        return (
            "Modify the current task plan. Only available in PLAN phase.\n"
            "After modifying, show the updated plan to the user and wait for feedback.\n\n"
            "Examples:\n"
            '  add_task:    {"action":"add_task", "task_data":{"title":"写摘要","description":"...","type":"writing","spec":"...","assigned_agent":"writer","output_dir":"t6"}}\n'
            '  remove_task: {"action":"remove_task", "task_id":"t3"}\n'
            '  update_task: {"action":"update_task", "task_id":"t2", "task_data":{"title":"新标题","description":"新描述"}}\n'
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add_task", "remove_task", "update_task"],
                    "description": "The modification action.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (required for remove_task and update_task, e.g. 't3').",
                },
                "task_data": {
                    "type": "object",
                    "description": (
                        "Task fields for add_task / update_task. "
                        "Available fields: "
                        "title (str, required for add), "
                        "description (str, required for add — Worker's instruction), "
                        "type (str, required for add — one of: research/code/review/analysis/writing), "
                        "spec (str, required for add — Reviewer's acceptance criteria), "
                        "assigned_agent (str, required for add — subagent name), "
                        "output_dir (str, optional — defaults to task id), "
                        "dependencies (list[str], optional — e.g. ['t1','t2']). "
                        "For update_task, only include fields you want to change."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str = "", task_id: str = "", task_data: dict = None, **kwargs) -> str:
        # Phase gate
        if self._session.phase != TaskPhase.PLAN:
            return t("task.modify_blocked", phase=self._session.phase.value)

        if not self._session.task_graph:
            return t("task.no_plan")

        task_data = task_data or {}
        graph = self._session.task_graph

        if action == "add_task":
            from agent.scheduler.schema import ResearchTask
            if not task_data.get("id"):
                existing = set(graph.tasks.keys())
                i = len(existing) + 1
                while f"t{i}" in existing:
                    i += 1
                task_data["id"] = f"t{i}"
            # Ensure output_dir
            if "output_dir" not in task_data:
                task_data["output_dir"] = task_data['id']
            task = ResearchTask(**task_data)
            graph.add_task(task)
            # Cycle detection: rollback if adding this task creates a cycle
            if has_cycle(graph):
                del graph.tasks[task.id]
                return t("task.cycle_error", task_id=task.id)
            result = t("task.added", task_id=task.id, title=task.title)
            return result + "\n\n" + format_plan_display(graph)

        elif action == "remove_task":
            if task_id not in graph.tasks:
                return t("task.not_found", task_id=task_id) + t("task.current_list") + format_plan_display(graph)
            for tsk in graph.tasks.values():
                if task_id in tsk.dependencies:
                    tsk.dependencies.remove(task_id)
            del graph.tasks[task_id]
            result = t("task.deleted", task_id=task_id)
            return result + "\n\n" + format_plan_display(graph)

        elif action == "update_task":
            if task_id not in graph.tasks:
                return t("task.not_found", task_id=task_id) + t("task.current_list") + format_plan_display(graph)
            task_obj = graph.tasks[task_id]
            # Save old dependencies for rollback if cycle detected
            old_deps = list(task_obj.dependencies) if task_obj.dependencies else []
            for key, val in task_data.items():
                if hasattr(task_obj, key):
                    setattr(task_obj, key, val)
            # Cycle detection: rollback if update creates a cycle
            if has_cycle(graph):
                task_obj.dependencies = old_deps
                return t("task.update_cycle_error", task_id=task_id)
            result = t("task.updated", task_id=task_id)
            return result + "\n\n" + format_plan_display(graph)

        return t("task.unknown_action", action=action)


# ---------------------------------------------------------------------------
# TaskExecuteTool
# ---------------------------------------------------------------------------

class TaskExecuteTool(BaseTool):
    """Execute all tasks in the plan until completion."""

    def __init__(self, session: TaskSession, ctx: "ToolContext"):
        self._session = session
        self._ctx = ctx
        self._batch_runner = None

    @property
    def name(self) -> str:
        return "task_execute"

    @property
    def description(self) -> str:
        return (
            "Execute all tasks in the plan. Runs batch by batch following DAG dependencies "
            "until all tasks complete. Retries are handled internally. "
            "Only available in EXECUTE phase (user must /start first). "
            "IMPORTANT: After execution completes, show the results summary to the user. "
            "Then use bash/read_file to review worker outputs in _task_workers/ before merging to core. "
            "Do NOT call task_commit without showing results to the user first."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, on_token: Any = None, message_context: dict = None, **kwargs) -> str:
        # Phase gate
        if not self._session.task_graph:
            return t("task.execute_no_plan")
        if self._session.phase == TaskPhase.PLAN:
            return t("task.execute_not_confirmed")
        if self._session.phase != TaskPhase.EXECUTE:
            return t("task.execute_blocked", phase=self._session.phase.value)

        await _notify_phase_enter(on_token, TaskPhase.EXECUTE,
                                  bus=self._ctx.bus, message_context=message_context)

        # Restore _task_workers/ permissions from previous round's FINALIZE chmod
        project_core = self._ctx.session.project.core if self._ctx.session else None
        if project_core:
            tw_root = project_core / "_task_workers"
            if tw_root.exists():
                for f in tw_root.rglob("*"):
                    os.chmod(f, 0o755 if f.is_dir() else 0o644)
                os.chmod(tw_root, 0o755)

        # Lazy-init BatchRunner (reset when task_graph changes)
        if not self._batch_runner or self._batch_runner.graph is not self._session.task_graph:
            from agent.scheduler.executor import SDDExecutor
            from agent.scheduler.batch_runner import BatchRunner

            executor = SDDExecutor(self._ctx)
            executor.project_root = self._ctx.session.project.root if self._ctx.session else None
            executor.round_id = self._session.round_id
            executor._task_graph = self._session.task_graph
            self._batch_runner = BatchRunner(
                task_graph=self._session.task_graph,
                executor=executor,
                proposal=self._session.proposal,
            )

        import time
        from agent.scheduler.schema import TaskStatus

        graph = self._session.task_graph
        total_tasks = len(graph.tasks)

        # Helper: emit a progress line to terminal
        def emit(msg: str):
            if on_token:
                on_token(msg + "\n")

        def notify_im(text: str):
            """Send a standalone notification to IM channel."""
            if not self._ctx.bus or not message_context:
                return
            try:
                from bus.events import OutboundMessage
                _loop = asyncio.get_running_loop()
                _loop.create_task(self._ctx.bus.publish_outbound(OutboundMessage(
                    channel=message_context.get("channel", ""),
                    chat_id=message_context.get("chat_id", ""),
                    content=text,
                    is_notification=True,
                )))
            except RuntimeError:
                pass

        # ── Execution header ──
        emit(f"\n{'='*50}")
        emit(t("task.execute_start", total_tasks=total_tasks))
        emit(f"{'='*50}")
        task_lines = []
        for _tid, _task in graph.tasks.items():
            _deps_str = f" <- [{', '.join(_task.dependencies)}]" if _task.dependencies else ""
            emit(f"  ⏳ [{_tid}] {_task.title}{_deps_str}")
            task_lines.append(f"⏳ [{_tid}] {_task.title}{_deps_str}")
        emit("")
        # IM: one message with execution start + task list
        notify_im(t("task.execute_start", total_tasks=total_tasks) + "\n" + "\n".join(task_lines))

        exec_start = time.time()

        # ── Condensed progress wrapper ──
        # Filters verbose worker output, only passes key events to terminal
        _last_log_time = [time.time()]

        def condensed_log(msg: str):
            if not on_token or not msg:
                return
            # Always pass through lines containing status emojis (anywhere in the msg)
            for marker in ['⏳', '✅', '❌', '⚠️', '🧐', '🎉', '💡', '🔄']:
                if marker in msg:
                    on_token("    " + msg.strip() + "\n")
                    _last_log_time[0] = time.time()
                    return
            # Pass through step headers (condensed: step number + tool name only)
            if '**Step ' in msg or 'Step ' in msg:
                m = re.search(r'Step (\d+)/(\d+)', msg)
                tool_m = re.search(r'Tool: `(\w+)`', msg)
                if m:
                    step_info = f"Step {m.group(1)}/{m.group(2)}"
                    tool_name = f" -> {tool_m.group(1)}" if tool_m else ""
                    on_token(f"    📌 {step_info}{tool_name}\n")
                    _last_log_time[0] = time.time()
                return
            # Pass through tool result summaries (tool name only)
            if 'Tool result:' in msg:
                m = re.search(r'Tool result: `(\w+)`', msg)
                if m:
                    on_token(f"    ✓ {m.group(1)} done\n")
                    _last_log_time[0] = time.time()
                return
            # Suppress everything else (raw tokens, verbose JSON args, etc.)
            # But if nothing has been logged for 15s, show a dot as activity indicator
            if time.time() - _last_log_time[0] > 15:
                on_token("    .\n")
                _last_log_time[0] = time.time()

        # ── Heartbeat: periodic progress during long silences ──
        _hb_running = [True]

        async def _heartbeat():
            while _hb_running[0]:
                await asyncio.sleep(20)
                if not _hb_running[0]:
                    break
                elapsed = time.time() - exec_start
                completed = sum(1 for _tk in graph.tasks.values() if _tk.status == TaskStatus.COMPLETED)
                running = sum(1 for _tk in graph.tasks.values() if _tk.status == TaskStatus.RUNNING)
                _hb_msg = t("task.execute_progress", completed=completed, total_tasks=total_tasks, running=running, elapsed=elapsed)
                emit(_hb_msg)
                notify_im(_hb_msg)

        heartbeat_task = asyncio.ensure_future(_heartbeat())

        # Run all batches until completion
        total_run = []
        total_failed = []
        all_logs = []
        batch_num = 0

        try:
            while True:
                batch_num += 1
                ready = self._batch_runner._get_ready_tasks()

                if ready:
                    elapsed = time.time() - exec_start
                    ready_names = ", ".join(tk.id for tk in ready)
                    emit(f"\n{'─'*40}")
                    _batch_header = t("task.batch_start", batch_num=batch_num, count=len(ready), names=ready_names)
                    emit(_batch_header)
                    _batch_tasks = []
                    for _rt in ready:
                        emit(f"    🔄 [{_rt.id}] {_rt.title}")
                        _batch_tasks.append(f"🔄 [{_rt.id}] {_rt.title}")
                    emit(t("task.batch_elapsed", elapsed=elapsed))
                    emit("")
                    # IM: one message with batch start info
                    notify_im(_batch_header + "\n" + "\n".join(_batch_tasks))

                batch_start = time.time()
                result = await self._batch_runner.run_next_batch(on_log=condensed_log)
                batch_elapsed = time.time() - batch_start

                if result.tasks_run:
                    total_run.extend(result.tasks_run)
                    completed = sum(1 for _tk in graph.tasks.values() if _tk.status == TaskStatus.COMPLETED)
                    _complete_msg = t("task.batch_complete", batch_num=batch_num, batch_elapsed=batch_elapsed, completed=completed, total_tasks=total_tasks)
                    emit(_complete_msg)
                    notify_im(_complete_msg)
                if result.failed:
                    total_failed.extend(result.failed)
                    _fail_msg = t("task.batch_failed", batch_num=batch_num, failed=', '.join(result.failed))
                    emit(_fail_msg)
                    notify_im(_fail_msg)
                if result.logs:
                    all_logs.extend(result.logs)
                if result.all_complete:
                    total_elapsed = time.time() - exec_start
                    emit(f"\n{'='*50}")
                    _done_msg = t("task.all_complete", total_elapsed=total_elapsed)
                    emit(_done_msg)
                    emit(f"{'='*50}\n")
                    notify_im(_done_msg)
                    break
                # No more ready tasks but not all complete — stuck (deps failed)
                if not result.tasks_run:
                    total_elapsed = time.time() - exec_start
                    _stuck_msg = t("task.no_executable", total_elapsed=total_elapsed)
                    emit(_stuck_msg)
                    notify_im(_stuck_msg)
                    break
        finally:
            _hb_running[0] = False
            heartbeat_task.cancel()

        # Transition to FINALIZE
        self._session.phase = TaskPhase.FINALIZE
        await _notify_phase_done(on_token, TaskPhase.EXECUTE,
                                 bus=self._ctx.bus, message_context=message_context)
        await _notify_phase_enter(on_token, TaskPhase.FINALIZE,
                                  bus=self._ctx.bus, message_context=message_context)

        # Build result summary
        graph = self._session.task_graph
        completed = sum(1 for _tk in graph.tasks.values() if _tk.status == TaskStatus.COMPLETED)
        failed = sum(1 for _tk in graph.tasks.values() if _tk.status == TaskStatus.FAILED)
        total = len(graph.tasks)

        lines = [t("task.execution_summary", completed=completed, total=total)]
        if failed:
            failed_ids = [tid for tid, _tk in graph.tasks.items() if _tk.status == TaskStatus.FAILED]
            lines.append(t("task.failed_list", failed_ids=', '.join(failed_ids)))
        lines.append("")
        lines.append(t("task.finalize_intro"))
        if self._session.auto_mode:
            lines.append("[INSTRUCTION] Auto mode — use read_file to review each worker output, "
                         "then write_file/str_replace to merge content into the core project files, "
                         "then immediately call task_commit() to finalize.\n"
                         "[TIP] If worker outputs contain TikZ figure .tex files, "
                         "try compiling them to PDF via bash and use "
                         "\\includegraphics{figures/xxx.pdf} instead of \\input{figures/xxx.tex} in main.tex. "
                         "This is more portable and avoids package conflicts with the main document.")
        else:
            lines.append(t("task.finalize_instructions"))

        # Per-task output summary
        project_core = self._ctx.session.project.core if self._ctx.session else None

        # Protect _task_workers/ as read-only (prevent Finalize Agent from modifying worker outputs)
        if project_core and (project_core / "_task_workers").exists():
            tw_root = project_core / "_task_workers"
            for f in tw_root.rglob("*"):
                if f.is_file():
                    os.chmod(f, 0o444)
                elif f.is_dir():
                    os.chmod(f, 0o555)
            os.chmod(tw_root, 0o555)

        if project_core:
            lines.append("")
            for tid, task in graph.tasks.items():
                icon = "✅" if task.status == TaskStatus.COMPLETED else "❌"
                lines.append(f"{icon} [{tid}] {task.title}")
                if task.status == TaskStatus.COMPLETED:
                    task_dir = project_core / "_task_workers" / f"{tid}_r{self._session.round_id}"
                    if task_dir.exists():
                        for f in sorted(task_dir.rglob("*")):
                            if not f.is_file() or any(p.startswith(".") for p in f.relative_to(task_dir).parts):
                                continue
                            rel = str(f.relative_to(task_dir))
                            size = f.stat().st_size
                            size_str = f"{size/1024:.1f}KB" if size >= 1024 else f"{size}B"
                            lines.append(f"    - {rel} ({size_str})")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TaskCommitTool
# ---------------------------------------------------------------------------

class TaskCommitTool(BaseTool):
    """Commit merged outputs to git and reset for next round."""

    def __init__(self, session: TaskSession, ctx: "ToolContext"):
        self._session = session
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "task_commit"

    @property
    def description(self) -> str:
        return (
            "Signal that FINALIZE merge is complete. The system will automatically "
            "git-add all changes, generate a commit summary via LLM, and commit. "
            "Only available in FINALIZE phase. Call this when you have finished "
            "merging all worker outputs to the project root."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, on_token: Any = None, **kwargs) -> str:
        # Phase gate
        if self._session.phase != TaskPhase.FINALIZE:
            return t("task.commit_blocked", phase=self._session.phase.value)

        project = self._ctx.session.project if self._ctx.session else None
        if not project:
            return t("task.no_project_context")

        # Restore _task_workers/ permissions before commit
        tw_root = project.core / "_task_workers"
        if tw_root.exists():
            for f in tw_root.rglob("*"):
                os.chmod(f, 0o755 if f.is_dir() else 0o644)
            os.chmod(tw_root, 0o755)

        # Rescue missing referenced files from worker outputs before LLM fix
        self._rescue_missing_references(project, on_token=on_token)

        # Holistic LaTeX fix: compile → LLM self-review → fix → repeat
        await self._holistic_latex_fix(project, on_token=on_token)

        # Generate commit message via LLM
        message = await self._generate_commit_summary(on_token=on_token)

        # git add -A + commit
        commit_info = "(git not available)"
        if project.git:
            result = project.git.commit(message)
            commit_info = result.output if result.success else result.error

        # Clean up _task_workers/ from core after commit
        if tw_root.exists():
            import shutil
            shutil.rmtree(tw_root, ignore_errors=True)

        # Mark as committed (no more multi-round reset; task mode will auto-exit)
        self._session.committed = True

        # Persist state
        if self._ctx.session:
            state_path = self._ctx.session.metadata / "task_state.json"
            self._session.save(state_path)

        # Build actual core file listing so the main agent has ground truth
        core_listing = self._list_core_files(project)

        return (
            t("task.committed", commit_info=commit_info)
            + core_listing
            + "<task_done/>"
        )

    @staticmethod
    def _rescue_missing_references(project, on_token=None) -> None:
        """Find files referenced in main.tex that are missing from core
        but exist in _task_workers/. Copy them to core automatically."""
        import re as _re
        import shutil

        core = project.core
        main_tex = project.config.main_tex if hasattr(project.config, 'main_tex') else "main.tex"
        tex_path = core / main_tex
        if not tex_path.exists():
            return

        tw_root = core / "_task_workers"
        if not tw_root.exists():
            return

        tex_content = tex_path.read_text(encoding="utf-8", errors="replace")

        # Collect all referenced file paths from main.tex
        ref_patterns = [
            _re.compile(r'\\input\{([^}]+)\}'),
            _re.compile(r'\\include\{([^}]+)\}'),
            _re.compile(r'\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}'),
        ]
        missing_refs = []
        for pattern in ref_patterns:
            for match in pattern.finditer(tex_content):
                ref = match.group(1)
                candidates = [core / ref]
                if not ref.endswith('.tex'):
                    candidates.append(core / (ref + '.tex'))
                if not any(c.exists() for c in candidates):
                    missing_refs.append(ref)

        if not missing_refs:
            return

        # Search _task_workers/ for each missing file
        rescued = []
        for ref in missing_refs:
            # Normalize: "figures/foo.tex" → search for any file ending with this relative path
            ref_path = Path(ref)
            found = None
            for f in tw_root.rglob(ref_path.name):
                if not f.is_file():
                    continue
                # Check the relative structure matches (e.g. figures/foo.tex)
                try:
                    rel = f.relative_to(f.parents[len(ref_path.parts) - 1])
                    if str(rel) == ref or str(rel) == ref + '.tex':
                        found = f
                        break
                except (ValueError, IndexError):
                    pass
            # Fallback: just match by filename
            if not found:
                for f in tw_root.rglob(ref_path.name):
                    if f.is_file():
                        found = f
                        break
                # Also try with .tex extension
                if not found and not ref.endswith('.tex'):
                    for f in tw_root.rglob(ref_path.name + '.tex'):
                        if f.is_file():
                            found = f
                            break

            if found:
                target = core / ref
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(found), str(target))
                rescued.append(f"{ref} ← {found.relative_to(tw_root)}")
                logger.info(f"Rescued missing file: {ref} from {found}")

        if rescued and on_token:
            on_token("\n📦 Rescued missing files from worker outputs:\n"
                     + "\n".join(f"  - {r}" for r in rescued) + "\n")

    @staticmethod
    def _list_core_files(project) -> str:
        """List actual files in project core for ground-truth context."""
        core = project.core
        if not core.exists():
            return ""
        skip_ext = {'.log', '.aux', '.blg', '.out', '.bbl', '.toc',
                    '.lof', '.lot', '.fls', '.fdb_latexmk', '.synctex.gz'}
        skip_dirs = {'.git', '_task_workers', '__pycache__', '.bot'}
        files = []
        for f in sorted(core.rglob("*")):
            if not f.is_file():
                continue
            rel_parts = f.relative_to(core).parts
            if any(p in skip_dirs for p in rel_parts):
                continue
            if f.suffix in skip_ext:
                continue
            rel = str(f.relative_to(core))
            size = f.stat().st_size
            size_str = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
            files.append(f"  {rel} ({size_str})")
        if not files:
            return ""
        return "\n\n[Core files (ground truth)]\n" + "\n".join(files)

    async def _holistic_latex_fix(self, project, on_token: Any = None) -> None:
        """Run holistic LaTeX review/fix on the merged core before commit."""
        main_tex = project.config.main_tex if hasattr(project.config, 'main_tex') else "main.tex"
        tex_path = project.core / main_tex
        if not tex_path.exists():
            return

        provider, model = _get_planner_provider_and_model(self._ctx)
        if not provider:
            return

        try:
            from agent.tools.academic.latex_tool import LaTeXCompileTool
            tool = LaTeXCompileTool(
                project=project,
                provider=provider,
                model=model,
            )
            result = await tool.holistic_fix(main_file=main_tex, on_token=on_token)
            logger.info(f"Holistic LaTeX fix result: {result[:200]}")
        except Exception as e:
            logger.warning(f"Holistic LaTeX fix failed (non-blocking): {e}")

    async def _generate_commit_summary(self, on_token: Any = None) -> str:
        """Use LLM to generate a concise commit message from task context."""
        from agent.scheduler.schema import TaskStatus

        parts = []
        if self._session.goal:
            parts.append(f"Goal: {self._session.goal}")
        if self._session.task_graph:
            tasks = self._session.task_graph.tasks
            completed = sum(1 for t in tasks.values() if t.status == TaskStatus.COMPLETED)
            parts.append(f"Tasks: {completed}/{len(tasks)} completed")
            for tid, t in tasks.items():
                parts.append(f"  [{tid}] {t.title} — {t.status.value}")
        if self._session.expected_deliverables:
            names = [d["name"] if isinstance(d, dict) else str(d) for d in self._session.expected_deliverables]
            parts.append("Deliverables: " + ", ".join(names))

        context = "\n".join(parts)

        from core.prompts import render as render_prompt
        _COMMIT_FALLBACK = (
            "Based on the following task execution context, generate a concise git commit message "
            "(1-2 sentences, in the language matching the goal). No prefix, no quotes.\n\n"
            "{context}"
        )
        prompt = render_prompt("task_commit_summary.txt", _COMMIT_FALLBACK, context=context)

        planner_provider, planner_model = _get_planner_provider_and_model(self._ctx)
        try:
            resp = await planner_provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=planner_model,
                temperature=0.2,
                on_token=on_token,
            )
            msg = resp.content.strip().strip('"').strip("'")
            if msg:
                return msg
        except Exception as e:
            logger.warning(f"LLM commit summary failed: {e}")

        # Fallback
        fallback = f"Task round {self._session.round_id}"
        if self._session.goal:
            fallback += f": {self._session.goal[:80]}"
        return fallback
