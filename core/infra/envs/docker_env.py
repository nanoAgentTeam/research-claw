import os
import tempfile
import shutil
from typing import Optional, Dict, Any
from core.infra.environment import Environment

class DockerEnvironment(Environment):
    """
    Docker Container Environment.
    Wraps docker SDK.
    """
    def __init__(self, image: str, mount_map: Optional[Dict[str, Dict[str, str]]] = None):
        """
        Args:
            image: Docker image name (e.g., 'python:3.9-slim').
            mount_map: Dictionary for volume mounts.
                       Format: {'/local/path': {'bind': '/container/path', 'mode': 'rw'}}
        """
        self.image = image
        self.mount_map = mount_map or {}
        self.client = None
        self.container = None
        self._workdir = "/root"

        try:
            import docker
            self.client = docker.from_env()

            # Start container detached
            print(f"[DockerEnv] Starting container from image: {self.image}")
            self.container = self.client.containers.run(
                self.image,
                detach=True,
                volumes=self.mount_map,
                tty=True,
                command="tail -f /dev/null" # Keep alive
            )
            print(f"[DockerEnv] Container started: {self.container.short_id}")

        except ImportError:
            print("Error: docker SDK not installed.")
        except Exception as e:
            print(f"Error initializing Docker Env: {e}")

    @property
    def workdir(self) -> str:
        return self._workdir

    def run_command(self, command: str, cwd: Optional[str] = None, env_vars: Optional[Dict[str, str]] = None, timeout: int = 60) -> str:
        if not self.container:
            return "Error: Docker container is not active."

        target_cwd = cwd or self.workdir

        try:
            # exec_run
            # env needs to be passed if supported, otherwise export in shell
            cmd_to_run = ["bash", "-c", command]

            # Docker SDK exec_run supports environment dict
            exit_code, output_bytes = self.container.exec_run(
                cmd=cmd_to_run,
                workdir=target_cwd,
                environment=env_vars,
                demux=True
            )

            stdout = output_bytes[0].decode('utf-8', errors='replace') if output_bytes[0] else ""
            stderr = output_bytes[1].decode('utf-8', errors='replace') if output_bytes[1] else ""

            output = stdout
            if stderr:
                output += f"\nSTDERR:\n{stderr}"

            if exit_code != 0:
                output = f"Command failed with exit code {exit_code}\n{output}"

            return output if output else "Command executed successfully."

        except Exception as e:
            return f"Error executing command in Docker: {str(e)}"

    def read_file(self, path: str) -> str:
        if not self.container: return "Error: Container not active."

        # Simple implementation: cat the file
        # For more robust binary handling, uses get_archive but that returns tar stream
        # cat is fine for text
        res = self.run_command(f"cat {path}")
        if "Command failed" in res and "No such file" in res:
             return f"Error: File '{path}' not found."
        return res

    def write_file(self, path: str, content: str) -> str:
        if not self.container: return "Error: Container not active."

        # Write using shell redirection. Be careful with escaping.
        # Ideally use copy mechanism or encoded write
        # Simple approach: write to temp local then copy?
        # But we want to avoid dependency on local fs state if possible.
        # Base64 approach is robust for shell writing
        import base64
        b64_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        cmd = f"echo '{b64_content}' | base64 -d > {path}"
        return self.run_command(cmd)

    def file_exists(self, path: str) -> bool:
        res = self.run_command(f"test -e {path} && echo 'yes' || echo 'no'")
        return "yes" in res

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        # Use docker put_archive? Requires tar format.
        # Or if mount_map is used, just copy to local mount point.
        # Assuming general case without mount:
        if not self.container: return False
        try:
            import tarfile
            import io

            # Create tar stream
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as tar:
                tar.add(local_path, arcname=os.path.basename(remote_path))
            stream.seek(0)

            # Put archive
            # Note: put_archive extracts to the directory, so we need dirname of remote_path
            remote_dir = os.path.dirname(remote_path)
            self.container.put_archive(remote_dir, stream)
            return True
        except Exception as e:
            print(f"Docker upload failed: {e}")
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        if not self.container: return False
        try:
            bits, stat = self.container.get_archive(remote_path)
            # This yields a tar stream
            import tarfile
            import io

            stream = io.BytesIO()
            for chunk in bits:
                stream.write(chunk)
            stream.seek(0)

            # Extract
            with tarfile.open(fileobj=stream, mode='r') as tar:
                # We want to extract just the file content to local_path
                # Tar extraction preserves name.
                # Simplification: extract to temp and move
                with tempfile.TemporaryDirectory() as tmpdir:
                    tar.extractall(tmpdir)
                    extracted_name = tar.getnames()[0]
                    shutil.move(os.path.join(tmpdir, extracted_name), local_path)
            return True
        except Exception as e:
            print(f"Docker download failed: {e}")
            return False

    def close(self):
        if self.container:
            self.container.stop()
            self.container.remove()
            self.container = None
