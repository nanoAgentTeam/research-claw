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
        return session

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

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
