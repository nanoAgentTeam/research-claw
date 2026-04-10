import json
from typing import Optional, Any
from loguru import logger

from agent.scheduler.schema import TaskGraph, ResearchTask, TaskType, TaskStatus
from core.llm.engine import AgentEngine
from core.llm.types import SystemPromptConfig
from core.prompts import render as render_prompt

class PlannerAgent:
    """
    Generates a TaskGraph from a user request.
    """

    def __init__(self):
        from config.loader import load_config
        from providers.openai_provider import OpenAIProvider
        config = load_config()
        api_key = config.get_api_key()
        api_base = config.get_api_base()
        model = config.get_api_model()

        provider = OpenAIProvider(api_key=api_key, api_base=api_base, default_model=model)
        self.engine = AgentEngine(
            strategies=[],
            provider=provider,
            model=model
        )


    async def create_plan(self, user_request: str, project_root: Optional[Any] = None) -> Optional[TaskGraph]:
        """
        Analyze request and return a TaskGraph.
        """
        logger.info(f"Planning task for: {user_request}")

        from pathlib import Path

        _PLANNER_FALLBACK = (
            "You are a Senior Research Planner.\n"
            "Your goal is to break down the user's research request into a Directed Acyclic Graph (DAG) of tasks.\n"
            "Return ONLY a JSON object representing the plan.\n"
            "\n"
            "JSON Format:\n"
            "{{\n"
            "  \"project_id\": \"string\",\n"
            "  \"tasks\": [\n"
            "    {{\n"
            "      \"id\": \"task_1\",\n"
            "      \"title\": \"string\",\n"
            "      \"description\": \"detailed instructions\",\n"
            "      \"type\": \"research|code|writing\",\n"
            "      \"dependencies\": [],\n"
            "      \"spec\": \"acceptance criteria\",\n"
            "      \"assigned_agent\": \"researcher|coder\",\n"
            "      \"output_dir\": \"task_1\"\n"
            "    }}\n"
            "  ]\n"
            "}}\n"
            "Keep dependencies logical. Research tasks usually come first. 'output_dir' should be a simple directory name."
        )
        prompt = render_prompt("scheduler_planner.txt", _PLANNER_FALLBACK)

        messages = [{"role": "user", "content": f"Request: {user_request}"}]

        full_response = ""
        try:
            # 这里的 run 已经变为 async generator
            async for event in self.engine.run(
                messages=messages,
                system_config=SystemPromptConfig(base_prompt=prompt),
                tools=[],
                max_iterations=1,
                return_full_history=False
            ):
                if event.type == "token":
                    full_response += event.data["delta"]

            # Clean up JSON (remove markdown blocks if any)
            full_response = full_response.strip()
            if full_response.startswith("```json"):
                full_response = full_response[7:]
            if full_response.endswith("```"):
                full_response = full_response[:-3]

            # Parse JSON
            data = json.loads(full_response)

            # Convert to TaskGraph object
            graph = TaskGraph(project_id=data.get("project_id", "default"))
            for t_data in data.get("tasks", []):
                task = ResearchTask(**t_data)
                graph.add_task(task)

            logger.info(f"Plan created with {len(graph.tasks)} tasks.")

            # Persist the plan to disk
            try:
                # Determine plan directory
                if project_root:
                    plan_dir = project_root / "plans"
                else:
                    plan_dir = Path("workspace/tasks")

                if not plan_dir.exists():
                     plan_dir.mkdir(parents=True, exist_ok=True)

                # Save JSON
                plan_file_base = plan_dir / "latest_plan"
                with open(f"{plan_file_base}.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                # Save Markdown Visualization
                md_content = f"# Project Plan: {graph.project_id}\n\n"
                md_content += "## Task Dependency Graph\n\n"
                md_content += "```mermaid\ngraph TD\n"
                for t in graph.tasks.values():
                    # Sanitize title for mermaid
                    safe_title = t.title.replace('"', '').replace(':', ' -')
                    if len(safe_title) > 30: safe_title = safe_title[:27] + "..."
                    md_content += f'    {t.id}["{t.id}: {safe_title}"]\n'
                    for dep in t.dependencies:
                        md_content += f"    {dep} --> {t.id}\n"
                md_content += "```\n\n"

                md_content += "## Task Details\n\n"
                for t in graph.tasks.values():
                    md_content += f"### {t.id}: {t.title}\n"
                    md_content += f"- **Type**: {t.type}\n"
                    md_content += f"- **Agent**: {t.assigned_agent}\n"
                    md_content += f"- **Output**: `{t.output_dir}`\n"
                    md_content += f"- **Dependencies**: {', '.join(t.dependencies) if t.dependencies else 'None'}\n"
                    md_content += f"- **Description**: {t.description}\n\n"

                with open(f"{plan_file_base}.md", "w", encoding="utf-8") as f:
                    f.write(md_content)

                logger.info(f"Plan persisted to {plan_file_base}.md")

            except Exception as e:
                logger.error(f"Failed to persist plan: {e}")

            return graph

        except Exception as e:
            logger.error(f"Planning failed: {e}")
            logger.debug(f"Raw response: {full_response}")
            return None
