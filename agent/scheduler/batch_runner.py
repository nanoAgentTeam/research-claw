"""BatchRunner: batch-oriented execution engine for interactive TaskAgent.

Unlike SchedulerEngine.run() which runs autonomously, BatchRunner gives
control back to the caller after each batch so the user can inspect
progress, retry failures, or adjust the plan.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from agent.scheduler.schema import TaskGraph, ResearchTask
    from agent.scheduler.executor import SDDExecutor


@dataclass
class BatchResult:
    """Result of a single batch execution."""
    tasks_run: list[str] = field(default_factory=list)
    all_complete: bool = False
    failed: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


class BatchRunner:
    """Execute tasks from a TaskGraph one batch at a time.

    Each call to ``run_next_batch()`` finds all currently-ready tasks
    (dependencies met), executes them in parallel via the provided
    executor, and returns a BatchResult.
    """

    def __init__(
        self,
        task_graph: "TaskGraph",
        executor: "SDDExecutor",
        proposal: str = "",
    ):
        self.graph = task_graph
        self.executor = executor
        self.proposal = proposal

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def _get_ready_tasks(self) -> list["ResearchTask"]:
        """Return PENDING tasks whose dependencies are all COMPLETED."""
        from agent.scheduler.schema import TaskStatus

        ready = []
        for task in self.graph.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                (dep := self.graph.get_task(d)) and dep.status == TaskStatus.COMPLETED
                for d in task.dependencies
            )
            if deps_met:
                ready.append(task)
        return ready

    async def run_next_batch(
        self,
        on_log: Optional[Callable[..., Any]] = None,
    ) -> BatchResult:
        """Execute the next batch of ready tasks and return results."""
        from agent.scheduler.schema import TaskStatus

        ready = self._get_ready_tasks()
        result = BatchResult()

        if not ready:
            # Check if everything is done
            result.all_complete = all(
                t.status == TaskStatus.COMPLETED
                for t in self.graph.tasks.values()
            )
            if not result.all_complete:
                result.logs.append("No ready tasks. Some may be blocked by failures.")
            return result

        async def _run_one(task: "ResearchTask") -> tuple[str, bool]:
            task.status = TaskStatus.RUNNING
            try:
                success = await self.executor.execute_task(task, on_log=on_log, proposal=self.proposal)
                if not success:
                    task.status = TaskStatus.FAILED
                return task.id, success
            except Exception as e:
                logger.error(f"BatchRunner: task {task.id} raised: {e}")
                task.status = TaskStatus.FAILED
                return task.id, False

        outcomes = await asyncio.gather(*[_run_one(t) for t in ready])

        for task_id, success in outcomes:
            result.tasks_run.append(task_id)
            if not success:
                result.failed.append(task_id)

        # Re-check global completion
        result.all_complete = all(
            t.status == TaskStatus.COMPLETED
            for t in self.graph.tasks.values()
        )

        return result

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def get_status_summary(self) -> str:
        """Format a human-readable status of all tasks."""
        from agent.scheduler.schema import TaskStatus

        status_icons = {
            TaskStatus.PENDING: "⏳",
            TaskStatus.RUNNING: "🔄",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.FAILED: "❌",
            TaskStatus.BLOCKED: "🚫",
            TaskStatus.REVIEWING: "🧐",
        }
        lines = []
        for tid, task in self.graph.tasks.items():
            icon = status_icons.get(task.status, "❓")
            deps = f" (deps: {', '.join(task.dependencies)})" if task.dependencies else ""
            lines.append(f"{icon} [{tid}] {task.title} — {task.status.value}{deps}")

        completed = sum(1 for t in self.graph.tasks.values() if t.status == TaskStatus.COMPLETED)
        total = len(self.graph.tasks)
        lines.append(f"\nProgress: {completed}/{total} tasks completed.")
        return "\n".join(lines)

    def get_task_artifacts(self) -> dict[str, list[str]]:
        """Return {task_id: [artifact_paths]} for completed tasks."""
        from agent.scheduler.schema import TaskStatus

        return {
            tid: task.artifacts
            for tid, task in self.graph.tasks.items()
            if task.status == TaskStatus.COMPLETED and task.artifacts
        }
