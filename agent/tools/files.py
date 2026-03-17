"""File system tools."""

from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Any, Dict
from loguru import logger

from core.tools.base import BaseTool


class ReadFileTool(BaseTool):
    """Tool to read files from the workspace using Session."""

    def __init__(self, session: Any = None, workspace: Any = None, **kwargs):
        self.session = session
        self.workspace = Path(workspace) if workspace else None
        # MarkItDown for rich format conversion (PDF, DOCX, PPTX, images, etc.)
        self._md = None
        try:
            from markitdown import MarkItDown
            self._md = MarkItDown()
        except ImportError:
            pass

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the content of a file with line numbers. "
            "Supports text files, PDF, DOCX, PPTX, XLSX, images, and other formats. "
            "For large text files (>200 lines), use start_line/end_line to read specific sections. "
            "Supports workspace-relative paths and absolute paths for user-uploaded media files. "
            "Always read all relevant files before making edits."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative to workspace)."
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line number (1-based, inclusive). Omit to read from the beginning."
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line number (1-based, inclusive). Omit to read to the end."
                }
            },
            "required": ["path"]
        }

    # Whitelist directories for absolute path access
    _MEDIA_DIR = str(Path.home() / ".open_research_claw" / "media")
    _CACHE_DIR = str(Path.home() / ".open_research_claw" / "cache")

    # Extensions that need rich conversion instead of plain text read
    _RICH_EXTS = {
        ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff",
        ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi", ".mkv",
        ".zip", ".tar", ".gz", ".rar", ".7z",
        ".bin", ".exe", ".dll", ".so", ".dylib", ".rtf", ".epub",
    }

    def _read_rich_file(self, resolved: Path, path: str) -> str:
        """Read binary/rich-format files, converting to text where possible."""
        ext = resolved.suffix.lower()
        size = resolved.stat().st_size
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"

        # 1) Excel: use pandas for best table rendering
        if ext == ".xlsx":
            try:
                import pandas as pd
                sheets = pd.read_excel(str(resolved), sheet_name=None, engine="openpyxl")
                if sheets:
                    parts = []
                    for name, df in sheets.items():
                        parts.append(f"### Sheet: {name}\n")
                        try:
                            parts.append(df.to_markdown(index=False))
                        except Exception:
                            parts.append(df.to_string(index=False))
                        parts.append("")
                    content = "\n".join(parts)
                    return f"[FILE] {path} ({size_str}, {len(sheets)} sheet(s))\n\n{content}"
            except Exception as e:
                logger.debug(f"pandas excel read failed: {e}")

        # 2) MarkItDown: covers PDF, DOCX, PPTX, XLSX fallback, images, etc.
        if self._md:
            try:
                result = self._md.convert(str(resolved))
                if result and result.text_content and result.text_content.strip():
                    text = result.text_content.strip()
                    if len(text) > 15000:
                        text = text[:15000] + "\n\n... [truncated, content too long]"
                    return f"[FILE] {path} ({ext}, {size_str})\n\n{text}"
            except Exception as e:
                logger.debug(f"MarkItDown conversion failed for {path}: {e}")

        # 3) Fallback: return metadata only
        return (
            f"[FILE] {path}\n"
            f"  Type: {ext or 'unknown'} (binary)\n"
            f"  Size: {size_str}\n"
            f"  [NOTE] Could not extract text content. "
            f"Install markitdown (`pip install markitdown`) for rich format support."
        )

    def _resolve(self, path: str) -> Path:
        """Resolve path: session > workspace fallback."""
        # Allow absolute paths within whitelisted directories (media uploads, cache)
        if os.path.isabs(path):
            normed = os.path.normpath(path)
            if normed.startswith(self._MEDIA_DIR) or normed.startswith(self._CACHE_DIR):
                return Path(path)
        if self.session:
            return self.session.resolve(path)
        if self.workspace:
            p = Path(path)
            if ".." in p.parts:
                raise PermissionError("Path traversal blocked: %s" % path)
            if p.is_absolute():
                raise PermissionError("Absolute path blocked: %s" % path)
            return self.workspace / path
        raise RuntimeError("No session or workspace configured for path resolution.")

    def execute(self, path: str, start_line: int | None = None, end_line: int | None = None, on_token: Any | None = None, **kwargs) -> str:
        """Read a file via Session, optionally a specific line range."""
        try:
            if on_token:
                on_token(f"Reading {path}...\n")
            resolved = self._resolve(path)
            if os.path.isabs(path) and not resolved.is_file():
                return f"[ERROR] Media file not found: {path}"
            if resolved.is_dir():
                return f"[ERROR] '{path}' is a directory, not a file. Use bash to run `ls \"{path}\"` to list its contents."

            # Rich format files: use MarkItDown / pandas conversion
            if resolved.suffix.lower() in self._RICH_EXTS:
                return self._read_rich_file(resolved, path)

            # Plain text files: original logic with line numbers
            try:
                raw = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Unknown binary format not in _RICH_EXTS, try rich conversion
                return self._read_rich_file(resolved, path)

            all_lines = raw.splitlines()
            total = len(all_lines)

            # Determine the range to return
            if start_line is not None or end_line is not None:
                start = max(1, start_line or 1) - 1
                end = min(end_line or total, total)
            else:
                start = 0
                end = total

            # Build numbered output
            numbered = []
            for i in range(start, end):
                numbered.append(f"{i+1:>4}| {all_lines[i]}")
            content = "\n".join(numbered)

            # Append file metadata
            if start > 0 or end < total:
                content += f"\n[FILE] {path} — showing lines {start+1}-{end} of {total}"
            else:
                content += f"\n[FILE] {path} — {total} lines"

            if total > 200 and start == 0 and end == total:
                content += "\n[NOTE] Large file. Use start_line/end_line for targeted reads."

            return content
        except FileNotFoundError:
            return f"[ERROR] File not found: {path}"
        except Exception as e:
            return f"[ERROR] {str(e)}"

class WriteFileTool(BaseTool):
    """Tool to write content to a file via Session."""

    def __init__(self, session: Any = None, workspace: Any = None, **kwargs):
        self.session = session
        self.workspace = Path(workspace) if workspace else None

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file. Handles automatic isolation and proposals for Overleaf core."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write."
                },
                "content": {
                    "type": "string",
                    "description": "The content to write."
                }
            },
            "required": ["path", "content"]
        }

    def execute(self, path: str, content: str, on_token: Any | None = None, **kwargs) -> str:
        """Write to a file via Session."""
        try:
            # [ROBUSTNESS] Intercept Shell Brace Expansion
            if re.search(r'\{.*,.*\}', path):
                return (
                    f"[ERROR] Shell brace expansion (e.g., '{{a,b}}') is not supported in file paths. "
                    f"Please write each file individually."
                )

            if on_token:
                on_token(f"Writing to {path}...\n")

            if self.session:
                # Assistant uses project.write_file(), Worker uses session.write_target()
                if self.session._role_type == "Worker":
                    target = self.session.write_target(path)
                    target.write_text(content, encoding="utf-8")
                    return f"Written: {path}"
                return self.session.project.write_file(path, content)

            # Fallback: write relative to workspace
            if self.workspace:
                p = Path(path)
                if ".." in p.parts:
                    return "[ERROR] Path traversal blocked."
                if p.is_absolute():
                    return "[ERROR] Absolute path blocked."
                target = self.workspace / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return f"Written: {path}"
            return "[ERROR] No session or workspace context available."
        except Exception as e:
            return f"[ERROR] {str(e)}"

class StrReplaceTool(BaseTool):
    """Tool to replace string in a file via Session."""

    def __init__(self, session: Any = None, workspace: Any = None, **kwargs):
        self.session = session
        self.workspace = Path(workspace) if workspace else None

    @property
    def name(self) -> str:
        return "str_replace"

    @property
    def description(self) -> str:
        return (
            "Replace a specific string with a new string in a file. "
            "The old_string must match exactly ONE occurrence in the file. "
            "If it matches 0 or more than 1, the operation fails with an error. "
            "After a successful replacement, verify the change with read_file if the edit is critical."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to modify."
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact string to be replaced. Must match exactly one occurrence."
                },
                "new_string": {
                    "type": "string",
                    "description": "The new string to replace it with."
                }
            },
            "required": ["path", "old_string", "new_string"]
        }

    def _resolve(self, path: str) -> Path:
        """Resolve path: session > workspace fallback."""
        if self.session:
            return self.session.resolve(path)
        if self.workspace:
            p = Path(path)
            if ".." in p.parts:
                raise PermissionError("Path traversal blocked: %s" % path)
            if p.is_absolute():
                raise PermissionError("Absolute path blocked: %s" % path)
            return self.workspace / path
        raise RuntimeError("No session or workspace configured for path resolution.")

    def execute(self, path: str, old_string: str, new_string: str, on_token: Any | None = None, **kwargs) -> str:
        """Replace string in a file via Session."""
        try:
            resolved = self._resolve(path)
            content = resolved.read_text(encoding="utf-8")
            lines = content.splitlines()

            count = content.count(old_string)
            if count == 0:
                # Provide the first 30 lines as context so the agent can find the correct string
                preview = "\n".join(f"  {i+1}: {l}" for i, l in enumerate(lines[:30]))
                hint = f"\n[FILE PREVIEW — first 30 lines of '{path}']\n{preview}"
                if len(lines) > 30:
                    hint += f"\n  ... ({len(lines)} lines total)"
                return (
                    f"[ERROR] old_string NOT FOUND in '{path}'. No replacement was made.\n"
                    f"[ALERT] Read the file carefully with read_file before retrying. "
                    f"Make sure old_string matches the file content exactly (including whitespace and newlines).{hint}"
                )

            if count > 1:
                # Show each match location so the agent can add more context
                match_locations = []
                search_start = 0
                for i in range(count):
                    idx = content.index(old_string, search_start)
                    line_num = content[:idx].count("\n") + 1
                    match_locations.append(f"  Match {i+1}: line {line_num}")
                    search_start = idx + 1
                return (
                    f"[ERROR] old_string found {count} times in '{path}'. No replacement was made.\n"
                    f"[ALERT] Include more surrounding context in old_string to make it unique.\n"
                    + "\n".join(match_locations)
                )

            if on_token:
                on_token(f"Replacing content in {path}...\n")

            # Perform the single replacement
            match_idx = content.index(old_string)
            match_line = content[:match_idx].count("\n") + 1
            new_content = content.replace(old_string, new_string, 1)

            # Build context preview (2 lines before/after the replacement)
            new_lines = new_content.splitlines()
            ctx_start = max(0, match_line - 3)
            ctx_end = min(len(new_lines), match_line + new_string.count("\n") + 3)
            context_preview = "\n".join(
                f"  {i+1}: {new_lines[i]}" for i in range(ctx_start, ctx_end)
            )

            if self.session:
                if self.session._role_type == "Worker":
                    target = self.session.write_target(path)
                    target.write_text(new_content, encoding="utf-8")
                    return f"Replaced in: {path} (line {match_line})\n[CONTEXT]\n{context_preview}"
                write_result = self.session.project.write_file(path, new_content)
                return f"{write_result} (line {match_line})\n[CONTEXT]\n{context_preview}"

            # Fallback: write directly via resolved path
            resolved.write_text(new_content, encoding="utf-8")
            return f"Replaced in: {path} (line {match_line})\n[CONTEXT]\n{context_preview}"
        except Exception as e:
            return f"[ERROR] {str(e)}"

