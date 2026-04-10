# DEPRECATED: Use agent.tools.files.ReadFileTool instead.
# This file is kept for backward compatibility and will be removed in a future release.
from typing import Dict, Any, Optional
import os
from core.tools.base import BaseTool
from core.llm.decorators import schema_strict_validator, output_sanitizer
from core.infra.environment import Environment

class ReadFileTool(BaseTool):
    """
    ReadFileTool：从环境中读取文件内容。
    Environment-Aware.
    """

    def __init__(self, env: Optional[Environment] = None):
        super().__init__()
        self.env = env
        try:
            from markitdown import MarkItDown
            self.md = MarkItDown()
        except ImportError:
            self.md = None
            print("[Warn] MarkItDown not installed, ReadFileTool will only support basic text reading.")

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read files of various formats (PDF, DOCX, PPTX, XLSX, images, etc.) from the environment and convert them to Markdown."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to read."
                }
            },
            "required": ["file_path"]
        }

    def configure(self, context: Dict[str, Any]):
        """Inject environment"""
        if "env" in context and isinstance(context["env"], Environment):
            self.env = context["env"]

    @schema_strict_validator
    @output_sanitizer(max_length=15000)
    def execute(self, file_path: str) -> str:
        if not self.env:
            return "Error: No execution environment configured."

        # Read content from environment
        # Note: Environment.read_file returns string content.
        # If the file is binary (PDF, etc), Env might return encoded string or fail?
        # Our E2BEnv implementation returns utf-8 decoded string or bytes-as-string.
        # But MarkItDown works on LOCAL files usually.

        # Strategy:
        # 1. Download file from Environment to Local Temp
        # 2. Use MarkItDown locally to convert
        # 3. Return text

        import tempfile

        try:
            if not self.env.file_exists(file_path):
                return f"Error: File '{file_path}' not found in environment."

            # Create local temp file to download to
            ext = os.path.splitext(file_path)[1]
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                local_tmp_path = tmp.name

            # Download
            if self.env.download_file(file_path, local_tmp_path):
                # Convert/Read locally
                return self._process_local_file(local_tmp_path)
            else:
                return f"Error: Failed to download file '{file_path}' for processing."

        except Exception as e:
            return f"Error reading file: {e}"
        finally:
            # Cleanup
            if 'local_tmp_path' in locals() and os.path.exists(local_tmp_path):
                os.remove(local_tmp_path)

    def _process_local_file(self, local_path: str) -> str:
        # Reuse old logic but on local path
        ext = os.path.splitext(local_path)[1].lower()

        # Excel Handling
        if ext == ".xlsx":
            try:
                import pandas as pd
                sheets_dict = pd.read_excel(local_path, sheet_name=None, engine='openpyxl')
                if sheets_dict:
                    output = []
                    for sheet_name, df in sheets_dict.items():
                        output.append(f"### Sheet: {sheet_name}\n")
                        try:
                            output.append(df.to_markdown(index=False))
                        except Exception:
                            output.append(df.to_string(index=False))
                        output.append("\n")
                    return "\n".join(output)
            except Exception:
                pass

        # MarkItDown
        try:
            if self.md:
                result = self.md.convert(local_path)
                if result and result.text_content:
                    return result.text_content
                else:
                    return "Warning: No content extracted."
            else:
                with open(local_path, 'r', encoding='utf-8', errors='replace') as f:
                    return f.read()
        except Exception as e:
            return f"Error converting file: {e}"

    def get_status_message(self, **kwargs) -> str:
        file_path = kwargs.get('file_path', 'file')
        return f"\n\n📂 正在读取文件: {os.path.basename(file_path)}...\n"
