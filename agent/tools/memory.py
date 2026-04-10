from __future__ import annotations
import datetime
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from agent.tools.registry import Tool
from core.services.storage import StorageService
from core.tools.base import BaseTool


class SaveMemoryTool(BaseTool):
    """
    Tool to save important facts or preferences to long-term memory.
    Reference: Qwen Agent 'save_memory' implementation.
    """

    # Class-level locks to synchronize writes across tool instances
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, metadata_root: Path):
        self.metadata_root = Path(metadata_root)
        # Standard layout: <metadata_root>/memory/memory/MEMORY.md
        self.memory_file = self.metadata_root / "memory" / "memory" / "MEMORY.md"

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return "Save a specific fact, preference, or piece of information to long-term memory. Use this when the user explicitly asks you to remember something, or when you learn a stable preference."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact or information to remember. Should be a clear, self-contained statement."
                },
                "category": {
                    "type": "string",
                    "description": "Optional category for the memory (e.g., 'preference', 'project', 'personal').",
                    "enum": ["preference", "project", "personal", "learning", "general"]
                }
            },
            "required": ["fact"]
        }

    def clone(self) -> "SaveMemoryTool":
        """Create a copy of this tool."""
        return SaveMemoryTool(self.metadata_root)

    def execute(self, fact: str, category: str = "general") -> str:
        """
        Execute the tool.
        """
        # Ensure memory directory exists
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)

        file_path_str = str(self.memory_file.absolute())
        if file_path_str not in self._locks:
            self._locks[file_path_str] = threading.Lock()

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"- [{timestamp}] [{category.upper()}] {fact}\n"

        try:
            with self._locks[file_path_str]:
                with open(self.memory_file, "a", encoding="utf-8") as f:
                    f.write(entry)

            # TODO: In the future, we should also call StorageService to index this
            # StorageService.index_text(fact, metadata={"type": "memory", "category": category})

            return f"Memory saved: {fact}"
        except Exception as e:
            return f"Error saving memory: {str(e)}"


class RetrieveMemoryTool(BaseTool):
    """
    Tool to retrieve information from long-term memory.
    Supports keyword search and recent history.
    """

    def __init__(self, metadata_root: Path):
        self.metadata_root = Path(metadata_root)
        self.memory_file = self.metadata_root / "memory" / "memory" / "MEMORY.md"

    @property
    def name(self) -> str:
        return "retrieve_memory"

    @property
    def description(self) -> str:
        return "Retrieve information from long-term memory. Use this when you need to recall past conversations, user preferences, or saved facts."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or keyword to look for."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5)."
                },
                "source": {
                    "type": "string",
                    "description": "Where to search: 'active_memory' (MEMORY.md) or 'archive' (LifeContext history).",
                    "enum": ["active_memory", "archive"]
                }
            },
            "required": ["query"]
        }

    def clone(self) -> "RetrieveMemoryTool":
        """Create a copy of this tool."""
        return RetrieveMemoryTool(self.metadata_root)

    def execute(self, query: str, limit: int = 5, source: str = "active_memory") -> str:
        """
        Execute the retrieval.
        """
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = 5

        results = []

        if source == "active_memory":
            if not self.memory_file.exists():
                return "No active memory file found."

            try:
                # Simple keyword search in MEMORY.md
                # TODO: Upgrade to vector search later
                content = self.memory_file.read_text(encoding="utf-8")
                lines = content.splitlines()

                # Filter lines containing the query (case-insensitive)
                matches = [line for line in lines if query.lower() in line.lower()]

                # Return most recent matches first
                recent_matches = matches[-limit:] if matches else []
                results = recent_matches

            except Exception as e:
                return f"Error reading memory file: {str(e)}"

        elif source == "archive":
            return "Archive search is not available."

        if not results:
            return f"No memories found matching '{query}' in {source}."

        formatted_results = "\n".join(results)
        return f"Found {len(results)} relevant memories:\n\n{formatted_results}"
