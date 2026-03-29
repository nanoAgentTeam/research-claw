"""Compatible Overleaf API client for non-overleaf.com instances (e.g. CSTCloud).

Wraps CSTCloud's OverleafClient to expose an interface compatible with pyoverleaf.Api,
so that OverleafSync in core/project.py can use either backend transparently.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any, Optional

from loguru import logger


class CompatOverleafApi:
    """Overleaf API client compatible with pyoverleaf.Api interface.

    Delegates to CSTCloud's OverleafClient internally.
    Used for self-hosted or alternative Overleaf instances.
    """

    def __init__(self):
        self._client = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login_from_olauth(self, olauth_path: Path) -> None:
        """Load credentials from a .olauth pickle file and build client."""
        from core.olsync_cstcloud.olsync import load_store, build_client_from_store
        store = load_store(str(olauth_path))
        self._client = build_client_from_store(store)

    def login_from_cookies(self, cookie_jar) -> None:
        """Fallback: build client from a cookie dict/jar."""
        from core.olsync_cstcloud.olclient import OverleafClient
        if isinstance(cookie_jar, dict):
            self._client = OverleafClient(cookie=cookie_jar)
        elif hasattr(cookie_jar, "items"):
            self._client = OverleafClient(cookie=dict(cookie_jar.items()))

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def get_projects(self):
        """Return list of active projects as objects with .name, .id, .last_updated."""
        raw = self._client.all_projects()
        return [_ProjectProxy(p) for p in raw]

    # ------------------------------------------------------------------
    # Project file tree
    # ------------------------------------------------------------------

    def project_get_files(self, project_id: str) -> dict:
        """Get project file tree via Socket.IO."""
        return self._client.get_project_infos(project_id)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def project_upload_file(self, project_id: str, folder_id: str,
                            filename: str, content: bytes) -> Any:
        """Upload a file to a project folder."""
        project_infos = self._client.get_project_infos(project_id)
        return self._client.upload_file(
            project_id, project_infos, filename, len(content), io.BytesIO(content),
        )

    def project_create_folder(self, project_id: str, parent_folder_id: str,
                              folder_name: str) -> Optional[dict]:
        """Create a folder in a project."""
        return self._client.create_folder(project_id, parent_folder_id, folder_name)

    def project_delete_entity(self, project_id: str, entity_id: str,
                              entity_type: str = "doc") -> bool:
        """Delete a file from a project (by entity ID)."""
        # CSTCloud client deletes by file name, not entity ID.
        # This is a compatibility gap — OverleafSync uses entity IDs.
        # For now, use the raw HTTP delete.
        import requests as reqs
        headers = {"X-Csrf-Token": self._client._csrf}
        url = f"{self._client._base_url}/project/{project_id}/{entity_type}/{entity_id}"
        r = reqs.delete(url, cookies=self._client._cookie, headers=headers, json={})
        return r.ok

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_project(self, project_id: str, output_path: str | Path = None) -> Path:
        """Download project as ZIP."""
        content = self._client.download_project(project_id)
        if output_path:
            dest = Path(output_path)
        else:
            dest = Path(tempfile.mktemp(suffix=".zip"))
        dest.write_bytes(content)
        return dest


class _ProjectProxy:
    """Wraps a project dict to expose .name, .id, .last_updated attributes
    so existing code that uses getattr() works transparently."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def name(self) -> str:
        return self._data.get("name", "")

    @property
    def id(self) -> str:
        return self._data.get("id", self._data.get("_id", ""))

    @property
    def last_updated(self) -> str:
        return self._data.get("lastUpdated", "")

    @property
    def archived(self) -> bool:
        return bool(self._data.get("archived", False))

    @property
    def trashed(self) -> bool:
        return bool(self._data.get("trashed", False))

    def __getattr__(self, name: str):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Project has no attribute '{name}'")
