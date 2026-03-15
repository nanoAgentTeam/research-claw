"""Tool for reading web content (PDFs via pymupdf4llm, HTML via Jina Reader)."""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pymupdf4llm
import requests
from loguru import logger

# Cache directory for downloaded files
_CACHE_DIR = Path.home() / ".context_bot" / "cache" / "papers"
_COMMON_CACHE_DIR = Path.home() / ".context_bot" / "cache" / "common"

# File extensions that should be downloaded and cached directly (not sent to Jina Reader)
_DOWNLOADABLE_EXTENSIONS = {
    ".tex", ".sty", ".cls", ".bst", ".bib",  # LaTeX
    ".md", ".txt", ".rst", ".csv", ".tsv",   # text
    ".zip", ".tar", ".gz", ".tgz", ".bz2",   # archives
    ".py", ".js", ".json", ".yaml", ".yml", ".toml", ".xml",  # code/config
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".eps",  # images
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",  # office
}


class WebReaderTool:
    """
    Tool to read web content.
    - specialized in converting PDFs to Markdown using pymupdf4llm (preserving layout, tables).
    - converts standard web pages to Markdown using Jina Reader.
    """
    name = "web_fetch"
    description = (
        "Fetch and read the content of a URL. "
        "Specialized for converting PDFs (via pymupdf4llm) and Web Pages to clean Markdown. "
        "For PDFs and other downloadable files (tex, sty, bib, md, zip, images, etc.), "
        "the original file is cached locally and can be sent to the user via send_file."
    )

    def __init__(self, session: Any = None, workspace: Any = None, config: Any | None = None, **kwargs):
        from config.schema import Config
        self.session = session
        self.workspace = Path(workspace) if workspace else None
        self.config = config or Config()

    def to_schema(self) -> dict:
        return self.to_openai_schema()

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to fetch (PDF or Web Page)."
                        }
                    },
                    "required": ["url"]
                }
            }
        }

    def execute(self, url: str, **kwargs) -> str:
        """Execute the fetch."""
        try:
            logger.info("Fetching URL: %s", url)

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").lower()

            # Handle PDF
            if "application/pdf" in content_type or url.lower().endswith(".pdf"):
                logger.info("Detected PDF content, using pymupdf4llm...")
                return self._process_pdf(response.content, url)

            # Handle downloadable files (tex, sty, bib, md, zip, images, etc.)
            url_ext = self._get_extension(url)
            if url_ext in _DOWNLOADABLE_EXTENSIONS:
                logger.info("Detected downloadable file (%s), caching...", url_ext)
                return self._process_downloadable(response.content, url, url_ext, content_type)

            # Handle HTML (using Jina Reader)
            logger.info("Detected HTML content, using Jina Reader...")
            jina_url = "https://r.jina.ai/%s" % url
            jina_response = requests.get(jina_url, timeout=30)
            if jina_response.status_code == 200:
                text = jina_response.text
                MAX_CHARS = 100000
                if len(text) > MAX_CHARS:
                    return (
                        "%s\n\n"
                        "... [CONTENT TRUNCATED] (Original length: %d chars). "
                        "Content exceeded %d characters limit."
                        % (text[:MAX_CHARS], len(text), MAX_CHARS)
                    )
                return text
            else:
                return "Failed to convert HTML to Markdown via Jina Reader. Raw Status: %d" % jina_response.status_code

        except Exception as e:
            return "Error fetching URL: %s" % str(e)

    def _process_pdf(self, pdf_content: bytes, url: str) -> str:
        """Process PDF content using pymupdf4llm.

        1. Cache the original PDF to ~/.context_bot/cache/papers/ for send_file.
        2. Convert to markdown and return in output (no markdown file saved).
        """
        try:
            # Determine a sensible filename from the URL
            filename = url.rstrip("/").split("/")[-1].split("?")[0]
            if not filename.endswith(".pdf"):
                filename = "document.pdf"

            # 1. Cache original PDF
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cached_pdf = _CACHE_DIR / filename
            cached_pdf.write_bytes(pdf_content)
            logger.info("Cached original PDF: %s", cached_pdf)

            # 2. Convert to markdown via pymupdf4llm
            # pymupdf4llm needs a file path; reuse the cached file
            md_text = pymupdf4llm.to_markdown(str(cached_pdf))

            # Split content into pages (heuristic based on '-----')
            pages = md_text.split("\n-----\n")
            total_pages = len(pages)

            # Header with original PDF path for agent to use with send_file
            header = (
                "✅ PDF downloaded and cached: `%s`\n"
                "   ↳ Use `send_file` with this path to send the original PDF to the user.\n"
                "📊 Total Pages: %d\n"
                % (str(cached_pdf), total_pages)
            )

            preview_pages = 10
            if total_pages > preview_pages:
                preview_content = "\n-----\n".join(pages[:preview_pages])
                return (
                    "%s"
                    "👀 Showing first %d pages below.\n\n"
                    "---\n\n%s\n\n"
                    "--- (End of Preview, %d more pages) ---"
                    % (header, preview_pages, preview_content, total_pages - preview_pages)
                )
            else:
                return "%s\n---\n\n%s" % (header, md_text)

        except Exception as e:
            return "Error processing PDF with pymupdf4llm: %s" % str(e)

    @staticmethod
    def _get_extension(url: str) -> str:
        """Extract file extension from URL (lowercase, ignoring query params)."""
        path = url.rstrip("/").split("?")[0].split("#")[0]
        name = path.split("/")[-1]
        dot = name.rfind(".")
        if dot == -1:
            return ""
        return name[dot:].lower()

    def _process_downloadable(self, content: bytes, url: str, ext: str, content_type: str) -> str:
        """Cache a downloadable file and return its content (text) or a download summary (binary)."""
        filename = url.rstrip("/").split("/")[-1].split("?")[0].split("#")[0]
        if not filename:
            filename = "download" + ext

        _COMMON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached_path = _COMMON_CACHE_DIR / filename
        cached_path.write_bytes(content)
        logger.info("Cached file: %s", cached_path)

        # For text-readable files, return content inline
        _TEXT_EXTENSIONS = {
            ".tex", ".sty", ".cls", ".bst", ".bib",
            ".md", ".txt", ".rst", ".csv", ".tsv",
            ".py", ".js", ".json", ".yaml", ".yml", ".toml", ".xml",
        }
        if ext in _TEXT_EXTENSIONS:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("latin-1")
            MAX_CHARS = 100000
            header = (
                "✅ File downloaded and cached: `%s`\n"
                "   ↳ Use `send_file` with this path to send the file to the user.\n"
                "📄 Type: %s | Size: %d bytes\n\n---\n\n"
                % (str(cached_path), ext, len(content))
            )
            if len(text) > MAX_CHARS:
                return (
                    "%s%s\n\n... [CONTENT TRUNCATED] (Original length: %d chars)."
                    % (header, text[:MAX_CHARS], len(text))
                )
            return "%s%s" % (header, text)

        # For binary files (images, archives, office docs), just report the cached path
        return (
            "✅ File downloaded and cached: `%s`\n"
            "   ↳ Use `send_file` with this path to send the file to the user.\n"
            "📦 Type: %s | Size: %d bytes\n"
            "   (Binary file, content not displayed.)"
            % (str(cached_path), ext, len(content))
        )
