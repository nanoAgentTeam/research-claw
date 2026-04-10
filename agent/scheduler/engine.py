import asyncio
from typing import List, Dict, Any, Callable, Optional
from loguru import logger

from agent.scheduler.schema import TaskGraph, ResearchTask, TaskStatus

class SchedulerEngine:
    """
    Core engine for managing the TaskGraph execution lifecycle.
    """

    def __init__(self, task_graph: TaskGraph, executor_callback: Callable[[ResearchTask], Any], on_task_update: Optional[Callable[[str], Any]] = None):
        """
        Args:
            task_graph: The initial plan.
            executor_callback: Async function to execute a task (spawn worker).
            on_task_update: Optional callback for status updates (message string).
        """
        self.graph = task_graph
        self.executor = executor_callback
        self.on_task_update = on_task_update
        self._running = False
        self._active_tasks: List[str] = []

    def get_executable_tasks(self) -> List[ResearchTask]:
        """Identify tasks that are PENDING and have all dependencies COMPLETED."""
        executable = []
        for task_id, task in self.graph.tasks.items():
            if task.status != TaskStatus.PENDING:
                continue

            deps_met = True
            for dep_id in task.dependencies:
                dep_task = self.graph.get_task(dep_id)
                if not dep_task or dep_task.status != TaskStatus.COMPLETED:
                    deps_met = False
                    break

            if deps_met:
                executable.append(task)

        return executable

    async def run_tick(self):
        """Single tick of the scheduler loop."""
        ready_tasks = self.get_executable_tasks()

        for task in ready_tasks:
            # Check concurrency limit if needed (omitted for now)
            if task.id not in self._active_tasks:
                logger.info(f"Dispatching task: {task.id} ({task.title})")
                task.status = TaskStatus.RUNNING
                self._active_tasks.append(task.id)

                # Launch in background
                asyncio.create_task(self._execute_wrapper(task))

    async def _execute_wrapper(self, task: ResearchTask):
        """Wrapper to handle execution result and state updates."""
        # Create a task-specific logger helper
        # We need to construct a callback that injects stream_id=f"progress_{task.id}"

        async def task_log(msg: str):
            if self.on_task_update:
                # Check signature of on_task_update
                # If it supports stream_id (which we just added), use it
                import inspect
                sig = inspect.signature(self.on_task_update)
                kwargs = {}
                if 'stream_id' in sig.parameters:
                    kwargs['stream_id'] = f"progress_{task.id}"

                if asyncio.iscoroutinefunction(self.on_task_update):
                    await self.on_task_update(msg, **kwargs)
                else:
                    self.on_task_update(msg, **kwargs)

        try:
            await task_log(f"⏳ Starting task: {task.title}")

            # Execute the task (this usually involves spawning a subagent)
            # The executor should return result artifacts or success status
            # We pass the task_log callback to capture internal logs with correct stream_id
            result = await self.executor(task, on_log=task_log)

            # After execution, we usually enter a REVIEW phase
            # For simplicity in this engine, we'll assume executor handles the review loop internally
            # or returns a status indicating "Ready for Review" vs "Completed".

            # Let's assume executor returns boolean Success for now
            if result:
                task.status = TaskStatus.COMPLETED
                logger.info(f"Task {task.id} completed successfully.")
                await task_log(f"✅ Task completed: {task.title}")
            else:
                task.status = TaskStatus.FAILED
                logger.error(f"Task {task.id} failed.")
                await task_log(f"❌ Task failed: {task.title}")

        except Exception as e:
            logger.error(f"Error executing task {task.id}: {e}")
            task.status = TaskStatus.FAILED
            await task_log(f"❌ Task error: {task.title} - {str(e)}")
        finally:
            if task.id in self._active_tasks:
                self._active_tasks.remove(task.id)

    async def run(self):
        """Continuous run loop."""
        self._running = True
        logger.info("Scheduler engine started.")

        if self.on_task_update:
            msg = f"Task Research Started: {len(self.graph.tasks)} tasks planned."
            if asyncio.iscoroutinefunction(self.on_task_update):
                await self.on_task_update(msg)
            else:
                self.on_task_update(msg)

        while self._running:
            await self.run_tick()

            # Check completion
            all_complete = all(t.status == TaskStatus.COMPLETED for t in self.graph.tasks.values())
            if all_complete:
                logger.info("All tasks completed.")
                if self.on_task_update:
                    msg = "🎉 All research tasks completed successfully."
                    if asyncio.iscoroutinefunction(self.on_task_update):
                        await self.on_task_update(msg)
                    else:
                        self.on_task_update(msg)
                break

            # [G-H3] Check for failure/deadlock
            pending_tasks = [t for t in self.graph.tasks.values() if t.status == TaskStatus.PENDING]
            failed_tasks = [t for t in self.graph.tasks.values() if t.status == TaskStatus.FAILED]
            if not self._active_tasks and pending_tasks:
                # No tasks running but some still pending — deadlock or dependency on failed task
                if failed_tasks:
                    logger.error(f"Deadlock detected: {len(pending_tasks)} pending tasks blocked by {len(failed_tasks)} failed tasks.")
                    for t in pending_tasks:
                        t.status = TaskStatus.FAILED
                    if self.on_task_update:
                        msg = f"[ERROR] Deadlock: {len(pending_tasks)} tasks blocked by failures. Aborting."
                        if asyncio.iscoroutinefunction(self.on_task_update):
                            await self.on_task_update(msg)
                        else:
                            self.on_task_update(msg)
                    break

            await asyncio.sleep(1) # Polling interval

    def stop(self):
        """[G-H3] Stop scheduler and cancel active tasks."""
        self._running = False
        # Cancel any active asyncio tasks
        for task_id in list(self._active_tasks):
            logger.info(f"Cancelling active task: {task_id}")
        self._active_tasks.clear()
