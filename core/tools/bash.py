# DEPRECATED: Use agent.tools.bash.BashTool instead.
# This file is kept for backward compatibility and will be removed in a future release.
import os
from typing import Dict, Any, Optional
from core.tools.base import BaseTool
from core.infra.environment import Environment

class BashTool(BaseTool):
    """
    执行 Bash 命令的工具。
    现在是环境感知的 (Environment-Aware)，通过注入的 Environment 实例执行命令。
    """
    def __init__(self, env: Optional[Environment] = None):
        """
        Args:
            env: Optional environment instance. Can be injected later via configure.
        """
        super().__init__()
        self.env = env

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Execute a bash command. Use this to run specialized CLI tools like bibval, codex, or arxiv research scripts."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The full bash command to execute."
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional. The working directory to execute the command in. Defaults to the environment's current working directory."
                }
            },
            "required": ["command"]
        }

    def configure(self, context: Dict[str, Any]):
        """Inject environment from context."""
        if "env" in context and isinstance(context["env"], Environment):
            self.env = context["env"]

    def execute(self, command: str = None, cmd: str = None, cwd: Optional[str] = None) -> str:
        # Support 'cmd' as alias for 'command'
        final_command = command
        if not final_command:
            return "Error: Command is required."

        if not self.env:
            return "Error: No execution environment configured for BashTool."

        # Delegate execution to the environment
        # The environment implementation handles safety checks (Local) or API calls (E2B/Docker)
        return self.env.run_command(final_command, cwd=cwd)
