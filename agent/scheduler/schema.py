from enum import Enum
from typing import List, Dict, Optional
from pydantic import BaseModel, Field

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"

class TaskType(str, Enum):
    RESEARCH = "research"
    CODE = "code"
    REVIEW = "review"
    ANALYSIS = "analysis"
    WRITING = "writing"

class ResearchTask(BaseModel):
    """
    A single unit of work in the Research Pipeline.
    """
    id: str
    title: str
    description: str
    type: TaskType
    
    # Dependency Management
    dependencies: List[str] = Field(default_factory=list, description="IDs of tasks that must complete first")
    
    # Execution Context
    spec: str = Field(..., description="Acceptance criteria for the Reviewer")
    assigned_agent: str = Field(..., description="Name of the subagent to execute this task")
    
    # Sandbox Configuration
    output_dir: str = Field(..., description="Relative path to the dedicated workspace for this task")
    
    # State
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    artifacts: List[str] = Field(default_factory=list, description="Paths to generated files relative to workspace root")
    
    # Execution Budget
    max_iterations: Optional[int] = Field(default=None, description="Max worker iterations for this task. Minimum 80. If not set, uses global default.")

    # Review Loop
    feedback_history: List[str] = Field(default_factory=list, description="History of reviewer feedback")
    retry_count: int = 0

class TaskGraph(BaseModel):
    """
    The entire research plan as a DAG.
    """
    project_id: str
    tasks: Dict[str, ResearchTask] = Field(default_factory=dict)
    
    def add_task(self, task: ResearchTask):
        self.tasks[task.id] = task
        
    def get_task(self, task_id: str) -> Optional[ResearchTask]:
        return self.tasks.get(task_id)
        
    def get_dependencies(self, task_id: str) -> List[ResearchTask]:
        task = self.tasks.get(task_id)
        if not task:
            return []
        return [self.tasks[dep_id] for dep_id in task.dependencies if dep_id in self.tasks]

    def get_dependents(self, task_id: str) -> List[ResearchTask]:
        return [
            task
            for task in self.tasks.values()
            if task_id in task.dependencies
        ]

    def get_descendant_ids(self, task_id: str) -> List[str]:
        descendants: list[str] = []
        seen: set[str] = set()
        queue = [task_id]

        while queue:
            current = queue.pop(0)
            for dependent in self.get_dependents(current):
                if dependent.id in seen:
                    continue
                seen.add(dependent.id)
                descendants.append(dependent.id)
                queue.append(dependent.id)

        return descendants
