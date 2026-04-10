import os
from typing import Optional, Dict, Any
from core.infra.environment import Environment

class E2BEnvironment(Environment):
    """
    E2B Cloud Sandbox Environment.
    Wraps e2b_code_interpreter SDK.
    """
    def __init__(self, api_key: str, sandbox_id: Optional[str] = None):
        """
        Args:
            api_key: E2B API Key.
            sandbox_id: Optional ID to connect to existing sandbox. If None, creates new.
        """
        self.api_key = api_key
        self.sandbox = None
        self._workdir = "/home/user" # Default E2B workdir

        try:
            from e2b_code_interpreter import Sandbox
            if sandbox_id:
                 # Reconnect logic if supported by SDK or just new
                 # For now, we assume we create new or pass existing object if we refactor differently
                 # But sticking to the plan: wrapper.
                 # Note: Reconnecting by ID might need specific SDK method,
                 # defaulting to create new for simplicity if ID not provided.
                 pass

            # Create the sandbox instance
            self.sandbox = Sandbox.create(api_key=self.api_key)
            print(f"[E2BEnv] Sandbox created: {self.sandbox.sandbox_id}")

            # Ensure directories exist
            self.run_command("mkdir -p /home/user/files/data /home/user/output /home/user/tmp")

        except ImportError:
            print("Error: e2b_code_interpreter not installed.")
        except Exception as e:
            print(f"Error initializing E2B Sandbox: {e}")

    @property
    def workdir(self) -> str:
        return self._workdir

    def run_command(self, command: str, cwd: Optional[str] = None, env_vars: Optional[Dict[str, str]] = None, timeout: int = 60) -> str:
        if not self.sandbox:
            return "Error: E2B Sandbox is not active."

        target_cwd = cwd or self.workdir

        # Chain cd if cwd is different from default, because execution is stateless
        final_cmd = command
        if target_cwd:
            final_cmd = f"cd {target_cwd} && {command}"

        try:
            # We can pass env_vars if SDK supports it, or export them in shell
            if env_vars:
                exports = " ".join([f"export {k}='{v}';" for k, v in env_vars.items()])
                final_cmd = f"{exports} {final_cmd}"

            execution = self.sandbox.commands.run(final_cmd, timeout=timeout)

            output = ""
            if execution.stdout:
                output += execution.stdout
            if execution.stderr:
                output += f"\nSTDERR:\n{execution.stderr}"

            if execution.exit_code != 0:
                output = f"Command failed with exit code {execution.exit_code}\n{output}"

            return output if output else "Command executed successfully."

        except Exception as e:
            return f"Error executing command in E2B: {str(e)}"

    def read_file(self, path: str) -> str:
        if not self.sandbox:
            return "Error: Sandbox not active."
        try:
            content = self.sandbox.files.read(path)
            # SDK might return bytes or str.
            if isinstance(content, bytes):
                return content.decode('utf-8')
            return content
        except Exception as e:
            return f"Error reading file '{path}': {str(e)}"

    def write_file(self, path: str, content: str) -> str:
        if not self.sandbox:
            return "Error: Sandbox not active."
        try:
            self.sandbox.files.write(path, content)
            return f"Successfully wrote to '{path}'."
        except Exception as e:
            return f"Error writing file '{path}': {str(e)}"

    def file_exists(self, path: str) -> bool:
        # E2B SDK doesn't have direct exists check usually, use ls or stat
        res = self.run_command(f"stat {path}")
        return "No such file" not in res and "Command failed" not in res

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        if not self.sandbox: return False
        try:
            with open(local_path, "rb") as f:
                self.sandbox.files.write(remote_path, f.read())
            return True
        except Exception as e:
            print(f"Upload failed: {e}")
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        if not self.sandbox: return False
        try:
            content = self.sandbox.files.read(remote_path, format="bytes")
            with open(local_path, "wb") as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Download failed: {e}")
            return False

    def close(self):
        if self.sandbox:
            self.sandbox.kill()
            self.sandbox = None
