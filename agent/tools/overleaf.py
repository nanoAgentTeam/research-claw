"""Overleaf tool for syncing projects using pyoverleaf."""

import os
import sys
import json
import pickle
import zipfile
import time
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple, Union

from loguru import logger
from core.tools.base import BaseTool

# Try to import pyoverleaf
try:
    import pyoverleaf
    HAS_PYOVERLEAF = True
except ImportError:
    HAS_PYOVERLEAF = False

class OverleafTool(BaseTool):
    """
    Tool to interact with Overleaf projects using pyoverleaf.
    Supports: list, pull (from Overleaf), push (to Overleaf).
    """

    def __init__(self, workspace: Path, work_dir: Optional[Path] = None, project: Any = None):
        self.workspace = workspace
        self.work_dir = work_dir or workspace
        self.projects_root = self.workspace
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self._project = project  # Core Project instance for sync delegation
        
        # Cookie search paths
        repo_root = Path(__file__).resolve().parent.parent
        self.cookie_paths = [
            # Repo root - consistent with cli/main.py login output
            repo_root / ".olauth",

            # Check .bot_data (System Home)
            self.workspace.parent / ".bot_data" / ".olauth",

            # Fallback paths
            self.workspace / ".olauth",
            Path.home() / ".olauth",
            self.workspace.parent / ".olauth",
        ]

    @property
    def name(self) -> str:
        return "overleaf"

    @property
    def description(self) -> str:
        return (
            "Interact with Overleaf.\n"
            "- list: List all Overleaf projects.\n"
            "- pull: Pull latest files from Overleaf to local (requires active project linked to Overleaf).\n"
            "- push: Push local changes to Overleaf (requires active project linked to Overleaf)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform.",
                    "enum": ["list", "pull", "push"]
                },
                "project_name": {
                    "type": "string",
                    "description": "Project name. Required for 'create_project'."
                }
            },
            "required": ["action"]
        }

    def execute(self, action: str, project_id: str = None, project_name: str = None, **kwargs) -> str:
        """Execute the Overleaf action."""
        api = self._get_api()
        if not api:
            return "[ERROR] No valid Overleaf cookie found. Please run `ols login` (or equivalent) to generate a .olauth file."

        try:
            if action == "list":
                return self._list_projects(api)
            
            elif action == "pull":
                # Pull latest files from Overleaf (same as /sync pull)
                if not self._project:
                    return (
                        "[ERROR] Cannot download: no active project. "
                        "Use project_manager(action='switch', project_name='...') to enter a project first."
                    )
                if self._project.is_default:
                    return "[ERROR] Cannot download in Default project."
                result = self._project.sync_from_overleaf()
                if not result.success:
                    errors = ', '.join(result.errors) if result.errors else 'unknown'
                    return f"[ERROR] Pull failed: {errors}"
                pulled = len(result.pulled) if result.pulled else 0
                msg = f"Pulled {pulled} files from Overleaf."
                if result.pulled:
                    for f in result.pulled:
                        msg += f"\n  + {f}"
                if result.deleted:
                    msg += f"\n  Deleted {len(result.deleted)} file(s) no longer on remote."
                return msg
            
            elif action == "push":
                # Delegate to Project.sync_to_overleaf() — the canonical push path
                if not self._project:
                    return (
                        "[ERROR] Cannot sync: no active project in current session. "
                        "You are in Default mode. To sync, you MUST first call "
                        "project_manager(action='switch', project_name='...') to enter the project, "
                        "then retry overleaf(action='push'). "
                        "Do NOT attempt to sync via bash or read .overleaf.json as a workaround."
                    )
                if self._project.is_default:
                    return (
                        "[ERROR] Cannot sync Default project. "
                        "Use project_manager(action='switch', project_name='...') to enter a specific project first."
                    )
                result = self._project.sync_to_overleaf()
                pushed = len(result.pushed) if result.pushed else 0
                if not result.success and pushed == 0:
                    errors = ', '.join(result.errors) if result.errors else 'unknown'
                    return f"[ERROR] Sync failed: {errors}"
                msg = f"Pushed {pushed} files to Overleaf."
                if result.errors:
                    failed = len(result.errors)
                    msg += f"\n[WARNING] {failed} file(s) failed:"
                    for err in result.errors:
                        msg += f"\n  - {err}"
                return msg

            else:
                return f"Unknown action: {action}"

        except Exception as e:
            import traceback
            return f"[ERROR] executing {action}: {str(e)}\n{traceback.format_exc()}"

    # =========================================================================
    # Helpers: Auth & API
    # =========================================================================

    def _load_cookie(self) -> Optional[dict]:
        for path in self.cookie_paths:
            if path.exists():
                try:
                    with open(path, 'rb') as f:
                        cookie = pickle.load(f)
                    logger.info(f"Found cookie at: {path}")
                    return cookie
                except Exception as e:
                    logger.warning(f"Could not load cookie from {path}: {e}")
        return None

    def _get_overleaf_base_url(self) -> str:
        """Read overleaf base_url from settings.json, fallback to .olauth instance."""
        try:
            from config.loader import get_config_service
            url = get_config_service().config.overleaf.base_url
            if url:
                return url
        except Exception:
            pass
        # Auto-detect from .olauth
        cookie = self._load_cookie()
        if isinstance(cookie, dict) and "instance" in cookie:
            url = cookie["instance"].get("base_url", "")
            if url:
                return url
        return "https://www.overleaf.com"

    def _get_api(self):
        try:
            base_url = self._get_overleaf_base_url()
            is_official = "overleaf.com" in base_url
            cookie = self._load_cookie()
            if not cookie:
                return None

            if is_official:
                api = pyoverleaf.Api()
                api.login_from_cookies(cookie.get("cookie"))
                return api
            else:
                from core.overleaf_compat import CompatOverleafApi
                api = CompatOverleafApi()
                for path in self.cookie_paths:
                    if path.exists():
                        api.login_from_olauth(path)
                        return api
                return None
        except Exception as e:
            logger.error(f"Failed to init Overleaf API: {e}")
        return None

    # =========================================================================
    # Action: List
    # =========================================================================

    def _list_projects(self, api) -> str:
        projects = api.get_projects()
        # active = [p for p in projects if not getattr(p, 'archived', False) and not getattr(p, 'trashed', False)]
        
        if not projects:
            return "No projects found (active, archived, or trashed)."

        lines = ["--- Overleaf Projects ---"]
        active_count = 0
        
        # Sort by last updated
        sorted_projects = sorted(projects, key=lambda x: getattr(x, 'last_updated', ''), reverse=True)

        for p in sorted_projects:
            updated = str(getattr(p, 'last_updated', 'Unknown'))[:16]
            flags = []
            if getattr(p, 'archived', False): flags.append("ARCHIVED")
            if getattr(p, 'trashed', False): flags.append("TRASHED")
            
            flag_str = f" [{' '.join(flags)}]" if flags else ""
            
            # Show all, or maybe limit to 50?
            if active_count < 30:
                lines.append(f"- [{updated}] {p.name} (ID: {p.id}){flag_str}")
                active_count += 1
        
        if len(projects) > 30:
            lines.append(f"... and {len(projects) - 30} more.")
            
        return "\n".join(lines)

    # =========================================================================
    # Action: Create Project (Fallback to requests)
    # =========================================================================

    def _create_project(self, api, project_name: str) -> str:
        """Create a new blank project using requests (pyoverleaf fallback)."""
        import requests
        import re
        from bs4 import BeautifulSoup
        
        cookie = self._load_cookie()
        if not cookie:
            return "[ERROR] No auth cookie."
        cookie_jar = cookie.get('cookie')

        try:
            session = requests.Session()
            session.cookies.update(cookie_jar)
            
            # Get CSRF from project list page
            r = session.get(f"{self._get_overleaf_base_url()}/project")
            soup = BeautifulSoup(r.content, 'html.parser')
            csrf = None
            meta_csrf = soup.find('meta', {'name': 'ol-csrfToken'})
            if meta_csrf:
                csrf = meta_csrf.get('content')
            
            if not csrf:
                match = re.search(r'window\.csrfToken\s*=\s*"([^"]+)"', r.text)
                if match:
                    csrf = match.group(1)
            
            if not csrf:
                # Try new API blob
                try:
                    meta = soup.find('meta', {'name': 'ol-prefetchedProjectsBlob'})
                    if meta and meta.get('content'):
                        data = json.loads(meta.get('content'))
                        if data.get('csrfToken'):
                             csrf = data.get('csrfToken')
                except:
                    pass

            if not csrf:
                return f"[ERROR] Could not find CSRF token for project creation. Please run `ols login` to refresh session."
            
            # Create project
            _base = self._get_overleaf_base_url()
            create_url = f"{_base}/project/new"
            headers = {
                "x-csrf-token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{_base}/project",
                "Content-Type": "application/json"
            }
            json_payload = {
                "projectName": project_name,
                "template": "blank"
            }
            
            resp = session.post(create_url, json=json_payload, headers=headers)
            
            if resp.status_code in [200, 201]:
                try:
                    data = resp.json()
                    pid = data.get('project_id') or data.get('id')
                    if pid:
                        return f"✅ Successfully created project '{project_name}' (ID: {pid})."
                except:
                    pass
            
            # Fallback for some endpoints returning redirect
            if resp.status_code == 200 and "/project/" in resp.url:
                 return f"✅ Successfully created project '{project_name}' (Redirected)."

            return f"[ERROR] creating project: HTTP {resp.status_code}. Response: {resp.text[:100]}"
            
        except Exception as e:
            return f"[ERROR] creating project: {e}"

    # =========================================================================
    # Action: Download
    # =========================================================================

    def _download_project(self, api, project_id: str, project_name: str) -> str:
        safe_name = project_name.replace("/", "_").replace(" ", "_")
        project_folder = self.projects_root / safe_name
        project_folder.mkdir(parents=True, exist_ok=True)
        
        zip_path = project_folder / "_temp.zip"
        
        # Download ZIP
        try:
            api.download_project(project_id, str(zip_path))
        except Exception as e:
            return f"Download failed: {e}"

        # Unzip
        tex_files_count = 0
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(project_folder)
            tex_files_count = len([n for n in zf.namelist() if n.endswith('.tex')])
        
        os.remove(zip_path)
        
        # Build Metadata (.overleaf.json)
        # We need to fetch file structure from API to get IDs
        try:
            root_folder = api.project_get_files(project_id)
            files_tree = self._build_file_tree(root_folder)
            self._save_metadata(project_folder, project_id, project_name, getattr(root_folder, 'id', ''), files_tree)
        except Exception as e:
            logger.warning(f"Failed to build metadata during download: {e}")
            return f"Downloaded project files, but failed to init sync metadata: {e}"

        return (f"✅ Successfully downloaded project '{project_name}' to: {project_folder}\n"
                f"📂 Contains {len(files_tree)} files tracked (including {tex_files_count} .tex files).\n"
                f"You can now edit files locally and run action='sync' to push changes.")

    def _build_file_tree(self, folder, prefix: str = "") -> List[dict]:
        files = []
        # PyOverleaf folder structure
        children = getattr(folder, 'children', [])
        for child in children:
            name = getattr(child, 'name', 'unknown')
            rel_path = f"{prefix}{name}" if prefix else name
            
            if hasattr(child, 'children'):
                files.extend(self._build_file_tree(child, f"{rel_path}/"))
            else:
                files.append({
                    "path": rel_path,
                    "id": getattr(child, 'id', None),
                    "type": getattr(child, 'type', 'file'),
                    "name": name
                })
        return files

    def _save_metadata(self, project_folder: Path, project_id: str, project_name: str, root_id: str, files: List[dict]):
        files_with_mtime = {}
        for f in files:
            local_path = project_folder / f["path"]
            mtime = local_path.stat().st_mtime if local_path.exists() else 0
            files_with_mtime[f["path"]] = {
                "id": f["id"],
                "type": f["type"],
                "mtime": mtime
            }
        
        metadata = {
            "project_id": project_id,
            "project_name": project_name,
            "last_synced": datetime.now().isoformat(),
            "root_folder_id": root_id,
            "files": files_with_mtime
        }
        
        with open(project_folder / ".overleaf.json", 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    # =========================================================================
    # Action: Sync (Upload + Delete)
    # =========================================================================

    def _scan_local_projects(self) -> List[dict]:
        projects = []
        if not self.projects_root.exists():
            return projects
            
        for path in self.projects_root.iterdir():
            if path.is_dir():
                meta_path = path / ".overleaf.json"
                if meta_path.exists():
                    try:
                        with open(meta_path, 'r', encoding='utf-8') as f:
                            metadata = json.load(f)
                        projects.append({"folder": str(path), "metadata": metadata})
                    except:
                        pass
        return projects

    def _find_local_folder(self, name_or_folder: str) -> Optional[str]:
        # Exact match folder name
        path = self.projects_root / name_or_folder
        if path.exists() and (path / ".overleaf.json").exists():
            return str(path.name)
        
        # Match project name in metadata
        for p in self._scan_local_projects():
            if p['metadata'].get('project_name') == name_or_folder:
                return os.path.basename(p['folder'])
        return None

    def _sync_project_by_folder(self, api, folder_name_or_path: Union[str, Path], is_absolute: bool = False) -> str:
        if is_absolute:
            project_folder = Path(folder_name_or_path)
        else:
            project_folder = self.projects_root / folder_name_or_path
            
        meta_path = project_folder / ".overleaf.json"
        
        if not meta_path.exists():
            return f"[ERROR] Metadata file missing in {project_folder}. Cannot sync."
            
        with open(meta_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
            
        project_id = metadata.get("project_id")
        if not project_id:
            return "[ERROR] Metadata corrupted (no project_id)."

        # Pre-check: verify remote project still exists
        try:
            remote_projects = api.get_projects()
            if not any(getattr(p, 'id', None) == project_id for p in remote_projects):
                return (
                    "[ERROR] Overleaf project '%s' (ID: %s) not found on remote. "
                    "It may have been deleted from Overleaf. "
                    "Use overleaf(action='list') to verify, "
                    "or overleaf(action='create_project') to create a new one."
                    % (metadata.get('project_name', ''), project_id)
                )
        except Exception as e:
            logger.warning("Failed to verify remote project existence: %s", e)

        # 1. Detect Changes
        changed_files = self._get_changed_files(project_folder, metadata)
        deleted_files = self._get_deleted_files(project_folder, metadata)
        
        if not changed_files and not deleted_files:
            folder_display = str(folder_name_or_path)
            return f"No changes detected for project '{metadata.get('project_name', folder_display)}'."

        logs = []
        logs.append(f"Syncing project '{metadata.get('project_name')}' (ID: {project_id}):")
        logs.append(f"  - {len(changed_files)} files to upload")
        logs.append(f"  - {len(deleted_files)} files to delete")

        # 2. Upload Changes
        success_up = 0
        uploaded_info = []
        
        for rel_path in changed_files:
            if success_up < 50: # Log first 50 files
                logs.append(f"  > Uploading: {rel_path}...")
            elif success_up == 50:
                logs.append(f"  > ... (logging suppressed for remaining uploads)")
                
            ok, file_id = self._upload_file(api, project_id, project_folder, rel_path, metadata.get("root_folder_id"))
            if ok:
                success_up += 1
                uploaded_info.append((rel_path, file_id))
            else:
                logs.append(f"    FAILED to upload {rel_path}")

        # Update metadata for uploads
        if uploaded_info:
            self._update_metadata_uploads(project_folder, metadata, uploaded_info)

        # 3. Delete Files
        success_del = 0
        for rel_path in deleted_files:
            if success_del < 50:
                logs.append(f"  > Deleting remote: {rel_path}...")
            elif success_del == 50:
                logs.append(f"  > ... (logging suppressed for remaining deletions)")

            file_info = metadata["files"].get(rel_path)
            if file_info and file_info.get("id"):
                if self._delete_entity_by_id(api, project_id, file_info["id"]):
                    success_del += 1
                    # Remove from metadata
                    del metadata["files"][rel_path]
                else:
                    logs.append(f"    FAILED to delete {rel_path}")
            else:
                 logs.append(f"    Skipped delete (no ID found): {rel_path}")
                 # Still remove from metadata to avoid sync loop
                 if rel_path in metadata["files"]:
                     del metadata["files"][rel_path]

        # Save final metadata
        self._update_metadata_final(project_folder, metadata)

        logs.append(f"Sync complete. Uploaded: {success_up}/{len(changed_files)}. Deleted: {success_del}/{len(deleted_files)}.")
        return "\n".join(logs)

    # --- Sync Helpers ---

    def _get_changed_files(self, project_folder: Path, metadata: dict) -> List[str]:
        changed = []
        files_info = metadata.get("files", {})
        
        # Check modified existing files
        for rel_path, info in files_info.items():
            local = project_folder / rel_path
            if local.exists():
                if local.stat().st_mtime > info.get("mtime", 0):
                    changed.append(rel_path)
        
        # Check new files
        for root, dirs, files in os.walk(project_folder):
            dirs[:] = [d for d in dirs if not d.startswith('.')] # Ignore .git, etc
            for file in files:
                if file.startswith('.') or file == ".overleaf.json":
                    continue
                path = Path(root) / file
                rel = str(path.relative_to(project_folder))
                if rel not in files_info and rel not in changed:
                    changed.append(rel)
        return changed

    def _get_deleted_files(self, project_folder: Path, metadata: dict) -> List[str]:
        deleted = []
        for rel_path in metadata.get("files", {}):
            if not (project_folder / rel_path).exists():
                deleted.append(rel_path)
        return deleted

    def _upload_file(self, api, project_id: str, project_folder: Path, rel_path: str, root_id: str) -> Tuple[bool, str]:
        local_path = project_folder / rel_path
        try:
            with open(local_path, 'rb') as f:
                content = f.read()
            
            folder_path = os.path.dirname(rel_path)
            folder_id = self._ensure_folder(api, project_id, root_id, folder_path)
            
            if not folder_id:
                return False, ""
                
            filename = os.path.basename(rel_path)
            # project_upload_file returns a File object
            file_obj = api.project_upload_file(project_id, folder_id, filename, content)
            return True, getattr(file_obj, "id", "")
        except Exception as e:
            logger.error(f"Upload failed for {rel_path}: {e}")
            return False, ""

    def _ensure_folder(self, api, project_id: str, root_id: str, folder_path: str) -> Optional[str]:
        """Find or create folder recursively."""
        if not folder_path or folder_path == ".":
            # If we don't know root_id, we need to fetch it. 
            # But we passed it. If it's None, we try to fetch root.
            if root_id: return root_id
            return getattr(api.project_get_files(project_id), 'id', None)

        # Simplified folder finding/creation
        # We need the root folder structure to traverse
        current_id = root_id
        if not current_id:
             current_id = getattr(api.project_get_files(project_id), 'id', None)

        # This logic is complex because we need to check existence at each level
        # PyOverleaf project_get_files returns the tree. 
        # For efficiency, we should cache the tree, but here we might just re-fetch or optimistically create.
        # Let's re-fetch root for traversal
        current_folder = api.project_get_files(project_id)
        
        parts = folder_path.split("/")
        for part in parts:
            found = None
            children = getattr(current_folder, 'children', [])
            for child in children:
                if getattr(child, 'name', '') == part and hasattr(child, 'children'):
                    found = child
                    break
            
            if found:
                current_folder = found
            else:
                # Create
                current_folder = api.project_create_folder(project_id, current_folder.id, part)
        
        return getattr(current_folder, 'id', None)

    def _delete_entity_by_id(self, api, project_id: str, entity_id: str) -> bool:
        try:
            # We need the entity object to delete it?
            # api.project_delete_entity(project_id, entity)
            # We need to find the entity first in the tree
            root = api.project_get_files(project_id)
            entity = self._find_entity_by_id(root, entity_id)
            if entity:
                api.project_delete_entity(project_id, entity)
                return True
        except Exception as e:
            logger.error(f"Delete failed: {e}")
        return False

    def _find_entity_by_id(self, folder, target_id):
        if getattr(folder, 'id', None) == target_id:
            return folder
        for child in getattr(folder, 'children', []):
            res = self._find_entity_by_id(child, target_id)
            if res: return res
        return None

    def _update_metadata_uploads(self, project_folder: Path, metadata: dict, uploaded_info: List[Tuple[str, str]]):
        for rel_path, file_id in uploaded_info:
            local = project_folder / rel_path
            if local.exists():
                mtime = local.stat().st_mtime
                if rel_path not in metadata["files"]:
                    metadata["files"][rel_path] = {"type": "file"}
                
                metadata["files"][rel_path]["mtime"] = mtime
                if file_id:
                    metadata["files"][rel_path]["id"] = file_id
    
    def _update_metadata_final(self, project_folder: Path, metadata: dict):
        metadata["last_synced"] = datetime.now().isoformat()
        with open(project_folder / ".overleaf.json", 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
