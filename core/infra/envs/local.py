import os
import subprocess
import shutil
import shlex
import sys
from typing import Optional, Dict, List
from core.infra.environment import Environment

class LocalEnvironment(Environment):
    """
    Local environment implementation using subprocess and os.
    Includes security checks (audit guard and dangerous token detection).
    """
    def __init__(self, workspace_root: str):
        """
        Args:
            workspace_root: The root directory for this environment (sandbox root).
        """
        self._workdir = os.path.abspath(workspace_root)
        self.sandbox_root = self._workdir

    @property
    def workdir(self) -> str:
        return self._workdir

    def run_command(self, command: str, cwd: Optional[str] = None, env_vars: Optional[Dict[str, str]] = None, timeout: int = 60) -> str:
        target_cwd = cwd or self.workdir

        if not os.path.exists(target_cwd):
            return f"Error: The provided cwd '{target_cwd}' does not exist."

        # Security Check
        if not self._check_safety(command, target_cwd):
            return "Error: Command execution denied by user (Security Policy)."

        # Prepare Environment
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)

        # Mock bin path injection (for testing consistency)
        # We assume the mock_bin is at <project_root>/skill_evaluator/mock_bin
        # Current file: backend/infra/envs/local.py
        # Project root: ../../../
        # project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
        # mock_bin_path = os.path.join(project_root, "skill_evaluator/mock_bin")
        # if os.path.exists(mock_bin_path):
        #     env["PATH"] = f"{mock_bin_path}:{env.get('PATH', '')}"

        # Inject Audit Hook for Python
        self._inject_audit_hook(command, env)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=target_cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"

            if result.returncode != 0:
                output = f"Command failed with exit code {result.returncode}\n{output}"

            return output if output else "Command executed successfully with no output."

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds."
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def read_file(self, path: str) -> str:
        if not os.path.exists(path):
            return f"Error: File '{path}' not found."
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
             return f"Error: File '{path}' is binary or not valid UTF-8."
        except Exception as e:
            return f"Error reading file '{path}': {str(e)}"

    def write_file(self, path: str, content: str) -> str:
        # Security Check for Write
        abs_path = os.path.abspath(path)
        if not abs_path.startswith(self.sandbox_root):
             print(f"\n\n⚠️  [SECURITY ALERT] Attempting to write OUTSIDE sandbox!")
             print(f"   Target:  {abs_path}")
             print(f"   Sandbox: {self.sandbox_root}")
             user_input = input("   >>> Allow this write operation? [y/N]: ").strip().lower()
             if user_input != 'y':
                 return f"Error: Write operation to '{path}' denied by user."
             print("   [Allowed by user]\n")

        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully wrote to file '{path}'."
        except Exception as e:
            return f"Error writing file '{path}': {str(e)}"

    def file_exists(self, path: str) -> bool:
        return os.path.exists(path)

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        # For LocalEnv, upload is just copy
        # We assume local_path is from 'outside' and remote_path is 'inside'
        try:
            shutil.copy2(local_path, remote_path)
            return True
        except Exception as e:
            print(f"Error copying file: {e}")
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        # For LocalEnv, download is just copy
        try:
            shutil.copy2(remote_path, local_path)
            return True
        except Exception as e:
            print(f"Error copying file: {e}")
            return False

    def _check_safety(self, command: str, cwd: str) -> bool:
        """
        Smart Security Check (Migrated from BashTool)
        """
        is_safe = True
        reason = ""

        dangerous_tokens = ["rm ", "mv ", "cp ", "chmod ", "chown ", "dd ", ">", ">>", "sudo ", "su "]
        has_dangerous_token = any(token in command for token in dangerous_tokens)

        try:
            parts = shlex.split(command)
        except Exception:
            parts = command.split()

        sandbox_abs = self.sandbox_root

        if has_dangerous_token:
            for part in parts:
                if part.startswith("/"):
                    part_abs = os.path.abspath(part)
                    if not part_abs.startswith(sandbox_abs):
                        is_safe = False
                        reason = f"Dangerous command targets outside sandbox: {part}"
                        break

            if ".." in command:
                is_safe = False
                reason = "Dangerous command contains path traversal ('..')"

        safe_read_commands = ["ls", "cat", "grep", "find", "pwd", "whoami", "tail", "head", "wc", "file", "du", "echo"]

        if is_safe and parts and parts[0] in safe_read_commands:
            pass # Allow safe read commands
        elif is_safe and not has_dangerous_token:
            pass # Allow unknown commands without dangerous tokens
        elif not is_safe:
             print(f"\n\n⚠️  [SECURITY ALERT] POTENTIALLY UNSAFE bash command!")
             print(f"   Command: {command}")
             print(f"   Reason:  {reason}")
             print(f"   CWD:     {cwd}")
             user_input = input("   >>> Allow execution? [y/N]: ").strip().lower()
             if user_input != 'y':
                 return False
             print("   [Allowed by user]\n")

        return True

    def _inject_audit_hook(self, command: str, env: Dict[str, str]):
        """Inject python audit hook if command is running python"""
        cmd_parts = command.strip().split()
        if not cmd_parts: return

        prog = cmd_parts[0]
        if prog.startswith("python") or prog.endswith("/python") or prog.endswith("/python3"):
             env["SANDBOX_ROOT"] = self.sandbox_root
             # Path to audit_guard.py relative to this file
             # backend/infra/envs/local.py -> backend/utils/audit_guard.py
             guard_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../utils/audit_guard.py"))

             if os.path.exists(guard_path):
                 env["PYTHONSTARTUP"] = guard_path
