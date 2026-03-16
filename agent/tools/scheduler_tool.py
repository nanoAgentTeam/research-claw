import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING
from loguru import logger

from core.tools.base import BaseTool
from config.i18n import t
from agent.scheduler.planner import PlannerAgent
from agent.scheduler.executor import SDDExecutor
from agent.scheduler.engine import SchedulerEngine
from bus.events import OutboundMessage

if TYPE_CHECKING:
    from agent.services.tool_context import ToolContext

class OpenTaskPlannerTool(BaseTool):
    """
    User-facing tool to trigger Task Research.
    Parses user request, creates a plan, and launches the background scheduler.
    """

    # [T2] Class-level execution lock to prevent concurrent task launches
    _running_lock = asyncio.Lock()
    _is_running = False

    def __init__(self, tool_context: "ToolContext"):
        self.ctx = tool_context

    @property
    def name(self) -> str:
        return "task_planner"

    @property
    def description(self) -> str:
        return (
            "Plan and execute a complex research or engineering task. "
            "Use this tool when the user asks for a multi-step investigation, coding project, or paper writing task. "
            "This tool will run in the background and report progress."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The user's high-level request or goal."
                },
                "message_context": {
                    "type": "object",
                    "description": "Hidden context injected by the system (chat_id, channel). Do not provide this manually."
                }
            },
            "required": ["request"]
        }

    async def execute(self, request: str, message_context: Optional[Dict[str, Any]] = None, on_token: Any | None = None) -> str:
        """Execute the planning and launch scheduler."""
        # [T2] Prevent concurrent task launches
        if OpenTaskPlannerTool._is_running:
            return t("scheduler.task_running")

        if not message_context:
            return t("scheduler.no_context")

        chat_id = message_context.get("chat_id")
        channel = message_context.get("channel")

        if not chat_id or not channel:
            return t("scheduler.invalid_context")

        if on_token:
            on_token(t("scheduler.analyzing"))
        logger.info(f"🤔 Analyzing request and generating plan for: {request[:50]}...")

        # 1. Create a dedicated project directory for this research (Session-Centric)
        import datetime
        import re
        from pathlib import Path
        
        # Clean up request for safe filename
        safe_req = re.sub(r'[^a-zA-Z0-9]+', '_', request).strip('_')[:30]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        project_name = f"{timestamp}_{safe_req}".strip("_")
        
        # [NEW] Session-Centric Path: session/{id}/{research_id}
        # Refactored: Removed redundant 'research/' folder to match VFS expected structure
        project_root = self.ctx.get_virtual_root() / project_name
        project_root.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Created Task Research Project Root: {project_root}")

        # 2. Generate Plan
        logger.info(f"Instantiating PlannerAgent for request: {request}")
        planner = PlannerAgent()

        graph = await planner.create_plan(request, project_root=project_root)

        if not graph or not graph.tasks:
            return t("scheduler.plan_failed")

        # 3. Setup Executor and Scheduler
        executor = SDDExecutor(self.ctx)
        
        # Pass this project root to the scheduler/executor context
        executor.project_root = project_root
        
        # Track active tasks for smart card splitting
        active_tasks = set()
        
        # Callback to push updates to the bus
        async def status_callback(msg: str, stream_id: str = "progress"):
            # 1. Log to console for visibility (Consolidated)
            # Token-level logs are noisy; only log significant events or full lines
            if any(marker in msg for marker in ["Starting task:", "Task completed:", "Task failed:", "Iteration", "Tool:"]):
                logger.info(f"📋 [Scheduler] [{stream_id}] {msg}")
            else:
                logger.debug(f"📋 [Scheduler] [{stream_id}] {msg}")
            
            # 2. Determine if we need a new card
            force_new_card = False
            
            if "Starting task:" in msg and stream_id != "progress":
                # Extract task ID from stream_id (e.g., progress_task_1)
                task_id = stream_id.replace("progress_", "")
                
                # If no tasks are currently active, this is a new sequential phase -> New Card
                if not active_tasks:
                    force_new_card = True
                
                active_tasks.add(task_id)
                
            elif "Task completed:" in msg or "Task failed:" in msg:
                # Remove task from active set
                task_id = stream_id.replace("progress_", "")
                if task_id in active_tasks:
                    active_tasks.remove(task_id)

            # 3. Push to message bus
            # [FIX] Remove trailing newline to allow CLI streaming to append correctly
            outbound = OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=msg, # No forced newline
                is_chunk=True,
                stream_id=stream_id, 
                new_message=force_new_card 
            )
            await self.ctx.bus.publish_outbound(outbound)
            
        scheduler = SchedulerEngine(graph, executor.execute_task, on_task_update=status_callback)
        
        # 3. Launch in Background
        # We use asyncio.create_task to run the scheduler concurrently with the main loop
        OpenTaskPlannerTool._is_running = True

        async def _run_and_cleanup():
            try:
                await scheduler.run()
            finally:
                OpenTaskPlannerTool._is_running = False

        bg_task = asyncio.create_task(_run_and_cleanup())
        
        # Register with AgentLoop for management (e.g. cancellation)
        self.ctx.register_background_task(bg_task)
        
        # Format task list for display
        task_list_str = "\n".join([
            f"- {t.id}: {t.title} (Deps: {', '.join(t.dependencies) if t.dependencies else 'None'})" 
            for t in graph.tasks.values()
        ])
        
        return t(
            "scheduler.plan_created",
            count=len(graph.tasks),
            task_list=task_list_str
        )
