# DEPRECATED: Use agent.tools.files.WriteFileTool instead.
# This file is kept for backward compatibility and will be removed in a future release.
from typing import Dict, Any, Optional
import os
import csv
from core.tools.base import BaseTool
from core.llm.decorators import schema_strict_validator
from core.infra.environment import Environment

class WriteFileTool(BaseTool):
    """
    WriteFileTool: Write content to a file in the environment.
    Supports a wide range of formats including text, structured data, and rich documents.
    """
    def __init__(self, env: Optional[Environment] = None):
        super().__init__()
        self.env = env

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file in the environment. Supported formats:\n"
            "- Text: .txt, .md, .yaml, .yml\n"
            "- Data: .json, .csv (comma-separated), .tsv (tab-separated)\n"
            "- Documents: .docx (Word), .pdf\n"
            "- Spreadsheet: .xlsx (Excel)"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to write. Can be absolute or relative to the current working directory."
                },
                "content": {
                    "type": "string",
                    "description": "The content to write. For .csv or .tsv, providing a markdown-style table or comma-separated rows is recommended."
                },
                "append": {
                    "type": "boolean",
                    "description": "If true, append content to the file instead of overwriting it. (Only supported for text-based files, .csv, and .tsv)",
                    "default": False
                }
            },
            "required": ["file_path", "content"]
        }

    def configure(self, context: Dict[str, Any]):
        """Inject environment"""
        if "env" in context and isinstance(context["env"], Environment):
            self.env = context["env"]

    @schema_strict_validator
    def execute(self, file_path: str, content: str, append: bool = False) -> str:
        if not self.env:
            return "Error: No execution environment configured."

        try:
            # Handle Append Logic manually if environment doesn't support 'append' natively in write_file
            # Environment.write_file usually overwrites.
            # So for append, we read -> concat -> write, or use shell append.
            # Using shell append is more efficient for remote envs but risky with escaping.
            # Reading and writing is safer but slower.
            # Let's try shell append for efficiency if env supports run_command.

            if append:
                # Basic text append via shell
                # Note: This is a bit fragile with complex content.
                # Let's check if we can simply read+write.
                if self.env.file_exists(file_path):
                    existing = self.env.read_file(file_path)
                    if not existing.startswith("Error"):
                        content = existing + content

                # Proceed to overwrite with new total content
                return self.env.write_file(file_path, content)

            # Special formats handling
            # Note: Generating PDF/Docx/Excel usually requires libraries.
            # If we run this logic locally (in the Agent process), we can generate the binary content
            # and then write it to the environment.
            # This is "Control Plane" generation, "Execution Plane" storage.

            ext = os.path.splitext(file_path)[1].lower()

            if ext == ".docx":
                return self._write_docx(file_path, content)
            elif ext == ".pdf":
                return self._write_pdf(file_path, content)
            elif ext == ".xlsx":
                return self._write_xlsx(file_path, content)
            elif ext in [".csv", ".tsv"]:
                return self._write_structured_data(file_path, content, ext, append)
            else:
                # Standard text write
                return self.env.write_file(file_path, content)

        except Exception as e:
            return f"Error writing file '{file_path}': {str(e)}"

    def _write_structured_data(self, file_path: str, content: str, ext: str, append: bool) -> str:
        # Process content locally to CSV format
        delimiter = '\t' if ext == ".tsv" else ','

        lines = content.strip().split('\n')
        processed_rows = []
        for line in lines:
            if line.startswith('|') and line.endswith('|'): # Markdown table row
                cells = [c.strip() for c in line.split('|')[1:-1]]
                if all(c.startswith('-') for c in cells): continue # Skip separator row
                processed_rows.append(cells)
            else:
                reader = csv.reader([line], delimiter=delimiter)
                processed_rows.extend(list(reader))

        import io
        output = io.StringIO()
        writer = csv.writer(output, delimiter=delimiter)
        writer.writerows(processed_rows)
        final_csv = output.getvalue()

        # Write/Append
        if append and self.env.file_exists(file_path):
             existing = self.env.read_file(file_path)
             if not existing.startswith("Error"):
                 final_csv = existing + final_csv

        self.env.write_file(file_path, final_csv)
        action = "Appended to" if append else "Successfully wrote to"
        return f"{action} {ext[1:].upper()} file at '{file_path}'."

    def _write_docx(self, file_path: str, content: str) -> str:
        try:
            from docx import Document
            import io
            doc = Document()
            for para in content.split('\n\n'):
                if para.strip():
                    doc.add_paragraph(para.strip())

            # Save to buffer
            buffer = io.BytesIO()
            doc.save(buffer)
            buffer.seek(0)

            # Upload binary
            # Env.write_file interface expects str?
            # We need to update Environment.write_file to support bytes or add write_bytes
            # For now, let's assume we need to change Env interface or encode?
            # Let's fail for now or implementing write_bytes in Env later.
            return "Error: Binary file writing not yet supported in new architecture."
        except ImportError:
            return "Error: 'python-docx' library is not installed."
        except Exception as e:
            return f"Error generating DOCX: {str(e)}"

    def _write_pdf(self, file_path: str, content: str) -> str:
        # Similar binary issue
        return "Error: Binary file writing not yet supported in new architecture."

    def _write_xlsx(self, file_path: str, content: str) -> str:
        # Similar binary issue
        return "Error: Binary file writing not yet supported in new architecture."

    def get_status_message(self, **kwargs) -> str:
        file_path = kwargs.get('file_path', 'file')
        return f"\n\n💾 正在写入文件: {os.path.basename(file_path)}...\n"
