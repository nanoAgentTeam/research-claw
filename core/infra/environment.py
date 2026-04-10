from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class Environment(ABC):
    """
    Abstract base class for Agent execution environments.
    Defines the standard interface for interacting with the external world (filesystem, shell).
    """

    @property
    @abstractmethod
    def workdir(self) -> str:
        """Get the current working directory."""
        pass

    @abstractmethod
    def run_command(self, command: str, cwd: Optional[str] = None, env_vars: Optional[Dict[str, str]] = None, timeout: int = 60) -> str:
        """
        Execute a shell command.

        Args:
            command: The command to execute.
            cwd: Working directory. If None, uses self.workdir.
            env_vars: Environment variables to set.
            timeout: Timeout in seconds.

        Returns:
            Combined stdout and stderr (or error message).
        """
        pass

    @abstractmethod
    def read_file(self, path: str) -> str:
        """
        Read file content as string.

        Args:
            path: Absolute or relative path to the file.

        Returns:
            File content.
        """
        pass

    @abstractmethod
    def write_file(self, path: str, content: str) -> str:
        """
        Write content to file.

        Args:
            path: Absolute or relative path to the file.
            content: String content to write.

        Returns:
            Success message or error message.
        """
        pass

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if a file exists."""
        pass

    @abstractmethod
    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload a local file to the environment."""
        pass

    @abstractmethod
    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download a file from the environment to local."""
        pass

    def close(self):
        """Clean up resources."""
        pass
