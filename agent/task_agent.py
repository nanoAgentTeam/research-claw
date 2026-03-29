"""Task session data models for collaborative task planning and execution.

Provides TaskPhase, TaskSession and helper utilities used by task_tools.py
and the command handlers (TaskHandler, TaskStartHandler, TaskDoneHandler).
"""

from __future__ import annotations

import json
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TaskPhase(str, Enum):
    UNDERSTAND = "understand"
    PROPOSE = "propose"
    PLAN = "plan"
    EXECUTE = "execute"
    FINALIZE = "finalize"


@dataclass
class TaskSession:
    """Persistent state for one interactive task session."""
    goal: str = ""
    phase: TaskPhase = TaskPhase.UNDERSTAND
    proposal: str = ""
    expected_deliverables: list[dict] = field(default_factory=list)  # [{"name": str, "description": str}]
    task_graph: Optional[Any] = None  # TaskGraph instance
    execution_state: dict = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    committed: bool = False
    round_id: int = 1  # multi-round plan counter
    auto_mode: bool = False  # skip interactive gates (for E2E / batch use)

    # -- Serialization --

    def to_dict(self) -> dict:
        tg = None
        if self.task_graph:
            tg = self.task_graph.model_dump() if hasattr(self.task_graph, "model_dump") else None
        return {
            "goal": self.goal,
            "phase": self.phase.value,
            "proposal": self.proposal,
            "expected_deliverables": self.expected_deliverables,
            "task_graph": tg,
            "execution_state": self.execution_state,
            "artifacts": self.artifacts,
            "committed": self.committed,
            "round_id": self.round_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskSession":
        phase = TaskPhase(data.get("phase", "understand"))
        # Migrate expected_deliverables from old list[str] to list[dict]
        raw_deliverables = data.get("expected_deliverables", [])
        deliverables = []
        for item in raw_deliverables:
            if isinstance(item, dict):
                deliverables.append(item)
            elif isinstance(item, str):
                deliverables.append({"name": item, "description": ""})
        session = cls(
            goal=data.get("goal", ""),
            phase=phase,
            proposal=data.get("proposal", ""),
            expected_deliverables=deliverables,
            execution_state=data.get("execution_state", {}),
            artifacts=data.get("artifacts", []),
            committed=data.get("committed", False),
            round_id=data.get("round_id", 1),
        )
        tg_data = data.get("task_graph")
        if tg_data:
            from agent.scheduler.schema import TaskGraph
            session.task_graph = TaskGraph(**tg_data)
            _normalize_task_graph_for_resume(session.task_graph)
        return session

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def save_to_metadata(self, metadata_root: Path) -> None:
        self.save(metadata_root / "task_state.json")

    @classmethod
    def load(cls, path: Path) -> Optional["TaskSession"]:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load TaskSession: {e}")
            return None


# ---------------------------------------------------------------------------
# Helpers (used by task_tools.py)
# ---------------------------------------------------------------------------

def has_cycle(graph) -> bool:
    """Detect cycles in the task dependency graph using DFS."""
    visited = set()
    rec_stack = set()

    def dfs(node_id: str) -> bool:
        visited.add(node_id)
        rec_stack.add(node_id)
        task = graph.tasks.get(node_id)
        if task:
            for dep in task.dependencies:
                if dep not in visited:
                    if dfs(dep):
                        return True
                elif dep in rec_stack:
                    return True
        rec_stack.discard(node_id)
        return False

    for tid in graph.tasks:
        if tid not in visited:
            if dfs(tid):
                return True
    return False


def format_plan_display(graph) -> str:
    """Render a TaskGraph into a fixed-format display string."""
    lines = []
    lines.append(f"📋 任务计划（共 {len(graph.tasks)} 个任务）")
    lines.append("=" * 50)

    for tid, task in graph.tasks.items():
        deps_str = ", ".join(task.dependencies) if task.dependencies else "无"
        lines.append("")
        lines.append(f"[{tid}] {task.title}")
        iters_str = f"  |  迭代预算: {task.max_iterations}" if task.max_iterations else ""
        lines.append(f"  类型: {task.type.value}  |  执行者: {task.assigned_agent}{iters_str}")
        lines.append(f"  描述: {task.description}")
        lines.append(f"  验收: {task.spec}")
        lines.append(f"  产出: {task.output_dir}")
        lines.append(f"  依赖: {deps_str}")

    lines.append("")
    lines.append("=" * 50)

    roots = [tid for tid, t in graph.tasks.items() if not t.dependencies]
    lines.append(f"起始任务: {', '.join(roots)}")

    return "\n".join(lines)


def _normalize_task_graph_for_resume(graph) -> None:
    """Convert stale in-flight task states to INTERRUPTED on load."""
    from agent.scheduler.schema import TaskStatus

    for task in graph.tasks.values():
        if task.status in {TaskStatus.RUNNING, TaskStatus.REVIEWING}:
            task.status = TaskStatus.INTERRUPTED


def get_recoverable_tasks(task_session: TaskSession) -> list[Any]:
    from agent.scheduler.schema import TaskStatus

    if not task_session.task_graph:
        return []
    recoverable = []
    for task in task_session.task_graph.tasks.values():
        if task.status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
            recoverable.append(task)
    return recoverable


def get_ready_pending_tasks(task_session: TaskSession) -> list[Any]:
    """Return currently executable PENDING tasks for an interrupted EXECUTE phase."""
    from agent.scheduler.schema import TaskStatus

    if not task_session.task_graph or task_session.phase != TaskPhase.EXECUTE:
        return []

    ready = []
    for task in task_session.task_graph.tasks.values():
        if task.status != TaskStatus.PENDING:
            continue
        deps_met = all(
            (dep := task_session.task_graph.get_task(dep_id))
            and dep.status == TaskStatus.COMPLETED
            for dep_id in task.dependencies
        )
        if deps_met:
            ready.append(task)
    return ready


def resolve_recoverable_task(task_session: TaskSession, selector: str) -> tuple[Optional[Any], Optional[str]]:
    """Resolve task by 1-based index, exact task id, or case-insensitive title substring."""
    recoverable = get_recoverable_tasks(task_session)
    if not recoverable:
        return None, "No recoverable tasks."

    value = selector.strip()
    if not value:
        return None, "Task selector is required."

    if value.isdigit():
        index = int(value) - 1
        if 0 <= index < len(recoverable):
            return recoverable[index], None
        return None, f"No recoverable task at index {value}."

    exact = [task for task in recoverable if task.id == value]
    if len(exact) == 1:
        return exact[0], None

    lowered = value.lower()
    matches = [task for task in recoverable if lowered in task.title.lower()]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "Multiple recoverable tasks match that name. Use the index or task id."
    return None, f"No recoverable task matches '{value}'."


def reset_task_for_resume(task_session: TaskSession, task_id: str, force: bool = False) -> list[str]:
    """Reset the selected task and all unfinished descendants to PENDING."""
    from agent.scheduler.schema import TaskStatus

    if not task_session.task_graph:
        raise ValueError("Task session has no task graph to resume.")

    graph = task_session.task_graph
    task = graph.get_task(task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found.")
    if task.status not in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
        raise ValueError(f"Task '{task_id}' is not resumable from status '{task.status.value}'.")

    descendants = graph.get_descendant_ids(task_id)
    completed_descendants = [
        desc_id
        for desc_id in descendants
        if graph.get_task(desc_id) and graph.get_task(desc_id).status == TaskStatus.COMPLETED
    ]
    if completed_descendants and not force:
        raise ValueError(
            "Completed downstream tasks detected: "
            + ", ".join(completed_descendants)
            + ". Re-run with --force to reset them too."
        )

    reset_ids = [task_id]
    for desc_id in descendants:
        desc_task = graph.get_task(desc_id)
        if not desc_task:
            continue
        if desc_task.status != TaskStatus.COMPLETED or force:
            reset_ids.append(desc_id)

    for reset_id in reset_ids:
        reset_task = graph.get_task(reset_id)
        if not reset_task:
            continue
        reset_task.status = TaskStatus.PENDING
        reset_task.retry_count = 0
        reset_task.feedback_history = []

    task_session.phase = TaskPhase.EXECUTE
    task_session.committed = False
    return reset_ids
