"""Project: 一等公民抽象。

持有身份、结构、配置、Git、Overleaf。
设计文档: bot_doc/design_doc/v2_project_abstraction/PROJECT_ABSTRACTION_DESIGN.md
"""

import json
import os
import shutil
import subprocess
import threading
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

import yaml
from loguru import logger


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GitConfig:
    enabled: bool = True
    auto_commit: bool = True
    auto_pull: bool = True
    commit_prefix: str = "[bot]"


@dataclass
class OverleafConfig:
    project_id: str = ""
    auto_pull_before_work: bool = True
    sync_interval_hours: int = 0


@dataclass
class AutoSearchConfig:
    enabled: bool = False
    interval_hours: int = 24
    keywords: list[str] = field(default_factory=list)


@dataclass
class AutomationAutoplanConfig:
    enabled: bool = True
    schedule: str = "0 */12 * * *"
    run_on_sync_pull: bool = True
    can_create: bool = True
    max_system_jobs: int = 8


@dataclass
class AutomationConfig:
    enabled: bool = True
    timezone: str = "UTC"
    autoplan: AutomationAutoplanConfig = field(default_factory=AutomationAutoplanConfig)


@dataclass
class RadarConfig:
    enabled: bool = True
    default_channels: list[str] = field(default_factory=list)


@dataclass
class LaTeXConfig:
    engine: str = "pdflatex"
    use_latexmk: bool = False
    bibtex: bool = True
    compile_passes: int = 2
    timeout_seconds: int = 120
    output_dir: Optional[str] = None
    extra_args: list[str] = field(default_factory=list)


@dataclass
class ProjectConfig:
    name: str = ""
    created: str = ""
    strategy: str = "interactive"
    main_tex: str = "main.tex"
    overleaf: Optional[OverleafConfig] = None
    auto_search: Optional[AutoSearchConfig] = None
    automation: Optional[AutomationConfig] = None
    radar: Optional[RadarConfig] = None
    git: GitConfig = field(default_factory=GitConfig)
    latex: Optional[LaTeXConfig] = None
    tools_blacklist: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GitResult:
    success: bool
    output: str
    error: str = ""


@dataclass
class CompileResult:
    success: bool
    pdf_path: Optional[Path] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log_excerpt: str = ""
    duration_ms: float = 0
    method: str = ""


@dataclass
class SyncResult:
    success: bool
    pulled: list[str] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# GitRepo
# ---------------------------------------------------------------------------

class GitRepo:
    """封装 project core 目录的 Git 操作（subprocess）。"""

    def __init__(self, core_path: Path, config: GitConfig):
        self.core_path = core_path
        self.config = config
        self._ensure_repo()

    def _ensure_repo(self):
        git_dir = self.core_path / ".git"
        if not git_dir.exists():
            self._run("git", "init")
            self._setup_gitignore()
            # Initial commit
            self._run("git", "add", "-A")
            self._run("git", "commit", "-m", "Initial commit by ContextBot")

    def _run(self, *args, timeout: int = 30) -> GitResult:
        try:
            result = subprocess.run(
                args,
                cwd=str(self.core_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return GitResult(
                success=result.returncode == 0,
                output=result.stdout.strip(),
                error=result.stderr.strip(),
            )
        except subprocess.TimeoutExpired:
            return GitResult(success=False, output="", error="Git command timed out")

    def _setup_gitignore(self):
        gitignore = self.core_path / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "# Subagent 临时产出\n"
                "_subagent_results/\n\n"
                "# Task worker 中间产出\n"
                "_task_workers/\n\n"
                "# Overleaf 同步元数据\n"
                ".overleaf.json\n\n"
                "# 系统文件\n"
                "__pycache__/\n.DS_Store\n"
            )

    # -- 查询 --

    def status(self) -> str:
        result = self._run("git", "status", "--short")
        return result.output or "Clean working tree"

    def diff(self, ref: str = "HEAD") -> str:
        result = self._run("git", "diff", ref)
        output = result.output
        if len(output) > 20480:
            output = output[:20480] + "\n... (truncated)"
        return output

    def log(self, n: int = 10) -> str:
        result = self._run("git", "log", f"-{n}", "--oneline", "--no-decorate")
        return result.output

    def preview_reset(self, ref: str = "HEAD~1") -> str:
        result = self._run("git", "log", f"{ref}..HEAD", "--oneline", "--no-decorate")
        return result.output or "Nothing to reset."

    # -- 修改 --

    def commit(self, message: str, files: list[str] = None) -> GitResult:
        if files:
            for f in files:
                self._run("git", "add", f)
        else:
            self._run("git", "add", "-A")
        return self._run("git", "commit", "-m", message)

    def reset(self, ref: str = "HEAD~1") -> GitResult:
        return self._run("git", "reset", "--hard", ref)

    def checkout_file(self, ref: str, path: str) -> GitResult:
        return self._run("git", "checkout", ref, "--", path)


# ---------------------------------------------------------------------------
# OverleafSync
# ---------------------------------------------------------------------------

class OverleafSync:
    """Overleaf 双向同步管理（SHA-256 content hash）。"""

    def __init__(self, core_path: Path, config: OverleafConfig):
        self.core_path = core_path
        self.config = config
        self.metadata_file = core_path / ".overleaf.json"
        self._api = None

    # -- API --

    def _get_api(self):
        if self._api is None:
            cookie = self._load_cookie()
            if not cookie:
                raise RuntimeError("Overleaf cookie not found. Run 'ols login' first.")
            try:
                import pyoverleaf
            except ImportError:
                raise RuntimeError("pyoverleaf is not installed.")
            self._api = pyoverleaf.Api()
            self._api.login_from_cookies(cookie)
        return self._api

    def _load_cookie(self) -> Optional[Any]:
        """从多个位置搜索 .olauth 文件。"""
        import pickle as _pickle
        # core_path = workspace/<project>/<project>
        # workspace = core_path.parent.parent
        # project root (.bot_data) = workspace.parent
        workspace = self.core_path.parent.parent
        project_root = workspace.parent
        # 同时搜索代码仓库根目录（即包含 cli/main.py 的目录）
        repo_root = Path(__file__).resolve().parent.parent
        search_paths = [
            repo_root / ".olauth",
            project_root / ".bot_data" / ".olauth",
            workspace / ".bot_data" / ".olauth",
            Path.home() / ".olauth",
            self.core_path / ".olauth",
            self.core_path.parent / ".olauth",
        ]
        for p in search_paths:
            if p.exists():
                try:
                    with open(p, "rb") as f:
                        data = _pickle.load(f)
                    return data.get("cookie") if isinstance(data, dict) else data
                except Exception as e:
                    logger.warning(f"Could not load cookie from {p}: {e}")
        return None

    @staticmethod
    def _file_hash(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    # -- Metadata --

    def _load_metadata(self) -> dict:
        if self.metadata_file.exists():
            return json.loads(self.metadata_file.read_text(encoding="utf-8"))
        return {}

    def _save_metadata(self, data: dict):
        self.metadata_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # Files/dirs to exclude from Overleaf sync
    _SYNC_SKIP_DIRS = {"_subagent_results", "_task_workers", "papers"}
    _SYNC_SKIP_EXTS = {
        ".aux", ".log", ".bbl", ".blg", ".fls", ".fdb_latexmk",
        ".out", ".toc", ".synctex.gz", ".pid", ".pdf", ".xdv",
    }

    def _should_sync(self, rel: str) -> bool:
        """Return True if the relative path should be included in Overleaf sync."""
        if rel.startswith("."):
            return False
        parts = rel.split("/")
        if parts[0] in self._SYNC_SKIP_DIRS:
            return False
        _, ext = os.path.splitext(rel)
        if ext in self._SYNC_SKIP_EXTS:
            return False
        return True

    def _rebuild_metadata(self, fetch_ids: bool = False):
        """重建 metadata（扫描 core 目录，更新所有文件 hash）。

        Args:
            fetch_ids: 如果为 True，从 Overleaf API 获取文件树并记录 file ID。
        """
        metadata = self._load_metadata()
        files = metadata.get("files", {})

        # 可选：从 API 获取文件树以记录 file ID
        id_map: dict[str, dict] = {}
        if fetch_ids:
            try:
                api = self._get_api()
                root_folder = api.project_get_files(self.config.project_id)
                metadata["root_folder_id"] = getattr(root_folder, "id", "")
                self._collect_file_ids(root_folder, "", id_map)
            except Exception as e:
                logger.warning(f"Failed to fetch file IDs from Overleaf: {e}")

        for f in self.core_path.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(self.core_path))
            if not self._should_sync(rel):
                continue
            entry = files.get(rel, {})
            entry["hash"] = self._file_hash(f)
            # 合并 API 中的 file ID（如果有）
            if rel in id_map:
                entry["id"] = id_map[rel]["id"]
                entry["type"] = id_map[rel].get("type", "file")
            files[rel] = entry
        metadata["files"] = files
        self._save_metadata(metadata)

    @staticmethod
    def _collect_file_ids(folder, prefix: str, out: dict):
        """递归遍历 Overleaf 文件树，收集 {relative_path: {id, type}}。"""
        for child in getattr(folder, "children", []):
            name = getattr(child, "name", "unknown")
            rel = f"{prefix}{name}" if prefix else name
            if hasattr(child, "children"):
                OverleafSync._collect_file_ids(child, f"{rel}/", out)
            else:
                out[rel] = {
                    "id": getattr(child, "id", ""),
                    "type": getattr(child, "type", "file"),
                }

    def _ensure_folder(self, api, project_id: str, root_folder, folder_path: str):
        """在 Overleaf 上查找或创建子文件夹，返回最终文件夹对象。

        Args:
            api: pyoverleaf API 实例
            project_id: Overleaf 项目 ID
            root_folder: 根文件夹对象 (ProjectFolder)
            folder_path: 相对文件夹路径，如 "neurips_format" 或 "a/b/c"
        Returns:
            目标文件夹对象 (ProjectFolder)
        """
        if not folder_path or folder_path == ".":
            return root_folder

        current_folder = root_folder
        for part in folder_path.split("/"):
            found = None
            for child in getattr(current_folder, "children", []):
                if getattr(child, "name", "") == part and hasattr(child, "children"):
                    found = child
                    break
            if found:
                current_folder = found
            else:
                try:
                    current_folder = api.project_create_folder(
                        project_id, current_folder.id, part
                    )
                except Exception:
                    # 文件夹可能已存在（400），重新获取文件树查找
                    refreshed = api.project_get_files(project_id)
                    target = self._find_folder_in_tree(refreshed, folder_path)
                    if target:
                        return target
                    raise
        return current_folder

    @staticmethod
    def _find_folder_in_tree(root_folder, folder_path: str):
        """在文件树中按路径查找文件夹。"""
        current = root_folder
        for part in folder_path.split("/"):
            found = None
            for child in getattr(current, "children", []):
                if getattr(child, "name", "") == part and hasattr(child, "children"):
                    found = child
                    break
            if not found:
                return None
            current = found
        return current

    # -- Pull --

    def pull(self) -> SyncResult:
        """从 Overleaf 拉取最新文件到 project core。"""
        result = SyncResult(success=True)
        try:
            api = self._get_api()
        except RuntimeError as e:
            return SyncResult(success=False, errors=[str(e)])

        import tempfile
        import zipfile

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = Path(tmpdir) / "project.zip"
                api.download_project(self.config.project_id, zip_path)

                extract_dir = Path(tmpdir) / "extracted"
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_dir)

                metadata = self._load_metadata()

                for f in extract_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = str(f.relative_to(extract_dir))

                    local_file = self.core_path / rel
                    if local_file.exists() and rel in metadata.get("files", {}):
                        recorded_hash = metadata["files"][rel].get("hash", "")
                        local_hash = self._file_hash(local_file)
                        if local_hash != recorded_hash:
                            result.conflicts.append(rel)
                            continue

                    target = self.core_path / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
                    result.pulled.append(rel)

                self._rebuild_metadata(fetch_ids=True)
        except Exception as e:
            result.success = False
            result.errors.append(str(e))

        return result

    # -- Push --

    def push(self, incremental: bool = False) -> SyncResult:
        """将 project core 的变更推送到 Overleaf。

        Args:
            incremental: 如果为 True，仅推送 hash 有变化的文件（增量模式）。
                         默认 False，推送所有可同步文件（全量模式）。
        """
        result = SyncResult(success=True)
        try:
            api = self._get_api()
        except RuntimeError as e:
            return SyncResult(success=False, errors=[str(e)])

        # Pre-check: verify remote project still exists
        try:
            remote_projects = api.get_projects()
            if not any(getattr(p, 'id', None) == self.config.project_id for p in remote_projects):
                return SyncResult(
                    success=False,
                    errors=[
                        "Overleaf project '%s' not found on remote. "
                        "It may have been deleted. "
                        "Use overleaf(action='list') to check, "
                        "or overleaf(action='create_project') to create a new one."
                        % self.config.project_id
                    ],
                )
        except Exception as e:
            logger.warning("Failed to verify remote project existence: %s", e)

        metadata = self._load_metadata()

        try:
            changed_files = []
            for f in self.core_path.rglob("*"):
                if not f.is_file():
                    continue
                rel = str(f.relative_to(self.core_path))
                if not self._should_sync(rel):
                    continue
                if incremental:
                    recorded = metadata.get("files", {}).get(rel, {})
                    if recorded and self._file_hash(f) == recorded.get("hash", ""):
                        continue
                changed_files.append(rel)

            logger.info(f"Overleaf push: {len(changed_files)} files to sync: {changed_files}")

            # 获取根文件夹对象（用于遍历和创建子文件夹）
            root_folder = api.project_get_files(self.config.project_id)
            # 缓存已解析的文件夹，避免重复 API 调用
            folder_cache = {}
            for rel in changed_files:
                local_file = self.core_path / rel
                try:
                    folder_path = os.path.dirname(rel)
                    filename = os.path.basename(rel)
                    # 查找或创建目标文件夹
                    if folder_path in folder_cache:
                        target_folder_id = folder_cache[folder_path]
                    else:
                        target_folder = self._ensure_folder(
                            api, self.config.project_id, root_folder, folder_path
                        )
                        target_folder_id = getattr(target_folder, "id", "")
                        folder_cache[folder_path] = target_folder_id
                    api.project_upload_file(
                        self.config.project_id,
                        target_folder_id,
                        filename,
                        local_file.read_bytes(),
                    )
                    result.pushed.append(rel)
                except Exception as e:
                    result.errors.append(f"{rel}: {e}")
                    logger.warning(f"Overleaf push failed for {rel}: {e}")

            # 检测删除
            for rel, info in metadata.get("files", {}).items():
                if not (self.core_path / rel).exists():
                    try:
                        api.project_delete_entity(self.config.project_id, info.get("id", ""))
                        result.pushed.append(f"(deleted) {rel}")
                    except Exception as e:
                        result.errors.append(f"delete {rel}: {e}")

            self._rebuild_metadata(fetch_ids=True)
        except Exception as e:
            result.success = False
            result.errors.append(str(e))

        return result


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project:
    """项目的一等公民抽象。"""

    def __init__(self, project_id: str, workspace_root: Path):
        # Ensure project_id is a string (handle Typer OptionInfo)
        if hasattr(project_id, "default"):
            project_id = str(project_id.default)
        elif not isinstance(project_id, str):
            project_id = str(project_id)

        self.id = project_id
        self.workspace_root = workspace_root
        self.root = workspace_root / project_id
        self.core = self.root / project_id
        self.memory_dir = self.root / ".project_memory"

        # 确保目录存在
        self.root.mkdir(parents=True, exist_ok=True)
        self.core.mkdir(parents=True, exist_ok=True)

        # 加载配置
        self.config = self._load_config()

        # Git（Default 项目强制 None）
        self._git: Optional[GitRepo] = None
        if not self.is_default:
            if self.config.git.enabled and (
                self.config.git.auto_commit or (self.core / ".git").exists()
            ):
                try:
                    self._git = GitRepo(self.core, self.config.git)
                except Exception as e:
                    logger.warning(f"Git init failed for {project_id}: {e}")

        # 写入追踪
        self._pending_writes: list[str] = []
        self._writes_lock = threading.Lock()

    # -- 属性 --

    @property
    def is_default(self) -> bool:
        return self.id == "Default"

    @property
    def git(self) -> Optional[GitRepo]:
        return self._git

    @property
    def overleaf(self) -> Optional[OverleafSync]:
        if self.is_default or not self.config.overleaf:
            return None
        if not self.config.overleaf.project_id:
            return None
        return OverleafSync(self.core, self.config.overleaf)

    @property
    def main_tex(self) -> Path:
        return self.core / self.config.main_tex

    # -- 配置 --

    def _load_config(self) -> ProjectConfig:
        config_path = self.root / "project.yaml"
        if not config_path.exists():
            return ProjectConfig(name=self.id)
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            return self._parse_config(data)
        except Exception as e:
            logger.warning(f"Failed to load project.yaml: {e}")
            return ProjectConfig(name=self.id)

    @staticmethod
    def _parse_config(data: dict) -> ProjectConfig:
        def _safe_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except Exception:
                return default

        git_data = data.get("git", {})
        git_cfg = GitConfig(
            enabled=git_data.get("enabled", True),
            auto_commit=git_data.get("auto_commit", True),
            auto_pull=git_data.get("auto_pull", True),
            commit_prefix=git_data.get("commit_prefix", "[bot]"),
        )

        overleaf_cfg = None
        if "overleaf" in data and data["overleaf"]:
            ol = data["overleaf"]
            overleaf_cfg = OverleafConfig(
                project_id=ol.get("project_id", ""),
                auto_pull_before_work=ol.get("auto_pull_before_work", True),
                sync_interval_hours=ol.get("sync_interval_hours", 0),
            )

        auto_search_cfg = None
        if "auto_search" in data and data["auto_search"]:
            a = data["auto_search"]
            auto_search_cfg = AutoSearchConfig(
                enabled=a.get("enabled", False),
                interval_hours=a.get("interval_hours", 24),
                keywords=a.get("keywords", []),
            )

        automation_cfg = None
        if "automation" in data and data["automation"]:
            a = data["automation"] or {}
            autoplan_raw = a.get("autoplan", {}) or {}
            automation_cfg = AutomationConfig(
                enabled=a.get("enabled", True),
                timezone=a.get("timezone", "UTC"),
                autoplan=AutomationAutoplanConfig(
                    enabled=autoplan_raw.get("enabled", True),
                    schedule=autoplan_raw.get("schedule", "0 */12 * * *"),
                    run_on_sync_pull=autoplan_raw.get("run_on_sync_pull", True),
                    can_create=autoplan_raw.get("can_create", True),
                    max_system_jobs=max(0, _safe_int(autoplan_raw.get("max_system_jobs", 8), 8)),
                ),
            )

        radar_cfg = None
        if "radar" in data and data["radar"]:
            r = data["radar"] or {}
            channels = r.get("default_channels", [])
            if not isinstance(channels, list):
                channels = []
            radar_cfg = RadarConfig(
                enabled=r.get("enabled", True),
                default_channels=[str(ch).strip() for ch in channels if str(ch).strip()],
            )

        latex_cfg = None
        if "latex" in data and data["latex"]:
            lx = data["latex"]
            latex_cfg = LaTeXConfig(
                engine=lx.get("engine", "pdflatex"),
                use_latexmk=lx.get("use_latexmk", True),
                bibtex=lx.get("bibtex", True),
                compile_passes=lx.get("compile_passes", 2),
                timeout_seconds=lx.get("timeout_seconds", 120),
                output_dir=lx.get("output_dir"),
                extra_args=lx.get("extra_args", []),
            )

        return ProjectConfig(
            name=data.get("name", ""),
            created=data.get("created", ""),
            strategy=data.get("strategy", "interactive"),
            main_tex=data.get("main_tex", "main.tex"),
            overleaf=overleaf_cfg,
            auto_search=auto_search_cfg,
            automation=automation_cfg,
            radar=radar_cfg,
            git=git_cfg,
            latex=latex_cfg,
            tools_blacklist=data.get("tools_blacklist", []),
        )

    def save_config(self) -> None:
        """Serialize current config back to project.yaml."""
        data: dict[str, Any] = {
            "name": self.config.name or self.id,
            "strategy": self.config.strategy,
            "main_tex": self.config.main_tex,
        }
        if self.config.created:
            data["created"] = self.config.created

        # git
        data["git"] = {
            "enabled": self.config.git.enabled,
            "auto_commit": self.config.git.auto_commit,
            "auto_pull": self.config.git.auto_pull,
            "commit_prefix": self.config.git.commit_prefix,
        }

        # overleaf
        if self.config.overleaf:
            data["overleaf"] = {
                "project_id": self.config.overleaf.project_id,
                "auto_pull_before_work": self.config.overleaf.auto_pull_before_work,
                "sync_interval_hours": self.config.overleaf.sync_interval_hours,
            }

        # auto_search
        if self.config.auto_search:
            data["auto_search"] = {
                "enabled": self.config.auto_search.enabled,
                "interval_hours": self.config.auto_search.interval_hours,
                "keywords": self.config.auto_search.keywords,
            }

        # automation
        if self.config.automation:
            data["automation"] = {
                "enabled": self.config.automation.enabled,
                "timezone": self.config.automation.timezone,
                "autoplan": {
                    "enabled": self.config.automation.autoplan.enabled,
                    "schedule": self.config.automation.autoplan.schedule,
                    "run_on_sync_pull": self.config.automation.autoplan.run_on_sync_pull,
                    "can_create": self.config.automation.autoplan.can_create,
                    "max_system_jobs": self.config.automation.autoplan.max_system_jobs,
                },
            }

        # radar
        if self.config.radar:
            data["radar"] = {
                "enabled": self.config.radar.enabled,
                "default_channels": self.config.radar.default_channels,
            }

        # latex
        if self.config.latex:
            data["latex"] = {
                "engine": self.config.latex.engine,
                "use_latexmk": self.config.latex.use_latexmk,
                "bibtex": self.config.latex.bibtex,
                "compile_passes": self.config.latex.compile_passes,
                "timeout_seconds": self.config.latex.timeout_seconds,
            }
            if self.config.latex.output_dir:
                data["latex"]["output_dir"] = self.config.latex.output_dir
            if self.config.latex.extra_args:
                data["latex"]["extra_args"] = self.config.latex.extra_args

        if self.config.tools_blacklist:
            data["tools_blacklist"] = self.config.tools_blacklist

        config_path = self.root / "project.yaml"
        config_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        logger.info(f"Saved project.yaml for {self.id}")

    def link_overleaf(self, overleaf_project_id: str) -> None:
        """Associate an Overleaf project ID and persist to config."""
        from dataclasses import replace
        if self.config.overleaf:
            self.config = replace(self.config, overleaf=replace(self.config.overleaf, project_id=overleaf_project_id))
        else:
            self.config = replace(self.config, overleaf=OverleafConfig(project_id=overleaf_project_id))
        self.save_config()

    # -- 路径 --

    def resolve(self, path: str) -> Path:
        if ".." in Path(path).parts:
            raise PermissionError(f"Path traversal blocked: {path}")
        if Path(path).is_absolute():
            raise PermissionError(f"Absolute path blocked: {path}")
        return self.core / path

    def file_tree(self, max_depth: int = 3) -> list[str]:
        lines: list[str] = []
        self._walk_tree(self.core, "", 0, max_depth, lines)
        return lines

    def _walk_tree(self, directory: Path, prefix: str, depth: int, max_depth: int, lines: list[str]):
        if depth >= max_depth:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return
        hidden = {".git", ".bot", "_subagent_results", "__pycache__", ".DS_Store"}
        entries = [e for e in entries if e.name not in hidden and not e.name.startswith(".")]
        for i, entry in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                self._walk_tree(entry, prefix + extension, depth + 1, max_depth, lines)

    # -- 文件写入 --

    def write_file(self, path: str, content: str) -> str:
        target = self.core / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        with self._writes_lock:
            self._pending_writes.append(path)
        return f"Written: {path}"

    def flush_commits(self, summary: str = None) -> str:
        if not self._pending_writes or not self.git:
            with self._writes_lock:
                self._pending_writes.clear()
            return ""
        if not self.config.git.auto_commit:
            with self._writes_lock:
                self._pending_writes.clear()
            return ""

        with self._writes_lock:
            files = list(set(self._pending_writes))
            self._pending_writes.clear()

        prefix = self.config.git.commit_prefix

        if summary:
            msg = summary
        else:
            msg = f"Edit {', '.join(files[:3])}"
            if len(files) > 3:
                msg += f" and {len(files) - 3} more"

        full_msg = f"{prefix} {msg}" if prefix else msg
        result = self.git.commit(full_msg, files=files)
        return result.output if result.success else result.error

    # -- Session 工厂 --

    def session(self, session_id: str, role_type: str = "Assistant"):
        """创建 Session 实例。延迟导入避免循环依赖。"""
        from core.session import Session
        return Session(self, session_id, role_type=role_type)

    def list_sessions(self) -> list[str]:
        sessions = []
        if self.root.exists():
            for d in self.root.iterdir():
                if d.is_dir() and d.name != self.id and not d.name.startswith("."):
                    if (d / ".bot").exists():
                        sessions.append(d.name)
        return sessions

    # -- Memory --

    def load_memory(self) -> str:
        memory_file = self.memory_dir / "MEMORY.md"
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return ""

    def save_memory(self, key: str, content: str):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if key:
            target = self.memory_dir / f"{key}.md"
        else:
            target = self.memory_dir / "MEMORY.md"
        target.write_text(content, encoding="utf-8")

    # -- Overleaf 便捷方法 --

    def sync_from_overleaf(self) -> SyncResult:
        if not self.config.overleaf or not self.config.overleaf.project_id:
            return SyncResult(success=False, errors=["No Overleaf config"])
        sync = OverleafSync(self.core, self.config.overleaf)
        result = sync.pull()

        if result.pulled and self.git and self.config.git.auto_commit:
            prefix = self.config.git.commit_prefix
            msg = f"Sync from Overleaf ({len(result.pulled)} files)"
            full_msg = f"{prefix} {msg}" if prefix else msg
            self.git.commit(full_msg, files=result.pulled)

        return result

    def sync_to_overleaf(self) -> SyncResult:
        if not self.config.overleaf or not self.config.overleaf.project_id:
            return SyncResult(success=False, errors=["No Overleaf config"])
        sync = OverleafSync(self.core, self.config.overleaf)
        return sync.push()

    # -- LaTeX 编译 --

    def compile_pdf(self) -> CompileResult:
        return self._compile(self.main_tex)

    def compile_pdf_file(self, tex_path: Path, cwd: Path = None) -> CompileResult:
        compile_dir = cwd or self.core
        if not tex_path.is_relative_to(compile_dir):
            return CompileResult(
                success=False, errors=[f"File not in compile directory: {tex_path}"]
            )
        return self._compile(tex_path, cwd=cwd)

    def _compile(self, tex_path: Path, cwd: Path = None) -> CompileResult:
        import time

        compile_dir = cwd or self.core
        start = time.time()
        if not tex_path.exists():
            return CompileResult(success=False, errors=[f"Tex file not found: {tex_path}"])

        latex_config = self.config.latex or LaTeXConfig()

        # Auto-detect engine when using default pdflatex
        if latex_config.engine == "pdflatex":
            detected = self._detect_engine(tex_path)
            if detected != "pdflatex":
                latex_config = LaTeXConfig(
                    engine=detected,
                    use_latexmk=latex_config.use_latexmk,
                    bibtex=latex_config.bibtex,
                    compile_passes=latex_config.compile_passes,
                    timeout_seconds=latex_config.timeout_seconds,
                    output_dir=latex_config.output_dir,
                    extra_args=latex_config.extra_args,
                )
                logger.info(f"Auto-detected LaTeX engine: {detected} (from {tex_path.name})")

        if latex_config.use_latexmk and self._has_latexmk():
            result = self._compile_latexmk(tex_path, latex_config, compile_dir)
        else:
            result = self._compile_manual(tex_path, latex_config, compile_dir)

        result.duration_ms = (time.time() - start) * 1000
        return result

    @staticmethod
    def _detect_engine(tex_path: Path) -> str:
        """Auto-detect LaTeX engine by scanning first 50 lines for key packages."""
        import re
        try:
            head = tex_path.read_text(encoding="utf-8", errors="replace").splitlines()[:50]
        except Exception:
            return "pdflatex"
        for line in head:
            stripped = line.strip()
            if stripped.startswith("%"):
                continue
            if re.search(r"\\usepackage(\[.*?\])?\{(ctex|xeCJK|fontspec|unicode-math)\}", stripped):
                return "xelatex"
            if re.search(r"\\usepackage(\[.*?\])?\{(luacode|luatextra)\}", stripped):
                return "lualatex"
        return "pdflatex"

    @staticmethod
    def _has_latexmk() -> bool:
        return shutil.which("latexmk") is not None

    def _compile_latexmk(self, tex_path: Path, config: LaTeXConfig, compile_dir: Path) -> CompileResult:
        engine_flag = {
            "pdflatex": "-pdf",
            "xelatex": "-xelatex",
            "lualatex": "-lualatex",
        }.get(config.engine, "-pdf")

        cmd = [
            "latexmk", engine_flag,
            "-interaction=nonstopmode", "-file-line-error", "-halt-on-error",
            tex_path.name,
        ]
        if config.extra_args:
            cmd.extend(config.extra_args)

        try:
            proc = subprocess.run(
                cmd, cwd=str(compile_dir),
                capture_output=True, text=True,
                timeout=config.timeout_seconds or 120,
            )
            pdf_path = compile_dir / (tex_path.stem + ".pdf")
            log_file = compile_dir / (tex_path.stem + ".log")

            if proc.returncode == 0 and pdf_path.exists():
                return CompileResult(
                    success=True, pdf_path=pdf_path,
                    warnings=self._parse_warnings(log_file),
                    method="latexmk",
                )
            else:
                return CompileResult(
                    success=False,
                    errors=self._parse_errors(log_file),
                    log_excerpt=proc.stderr[-2000:] if proc.stderr else "",
                    method="latexmk",
                )
        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[f"Compilation timed out after {config.timeout_seconds}s"],
                method="latexmk",
            )

    def _compile_manual(self, tex_path: Path, config: LaTeXConfig, compile_dir: Path) -> CompileResult:
        engine = config.engine or "pdflatex"
        base_cmd = [engine, "-interaction=nonstopmode", "-file-line-error", tex_path.name]
        timeout = config.timeout_seconds or 120

        def run_cmd(cmd):
            return subprocess.run(
                cmd, cwd=str(compile_dir),
                capture_output=True, text=True, timeout=timeout,
            )

        try:
            # Pass 1
            result = run_cmd(base_cmd)
            pdf_path = compile_dir / (tex_path.stem + ".pdf")
            log_file = compile_dir / (tex_path.stem + ".log")

            # nonstopmode may return non-zero but still produce a PDF
            if result.returncode != 0 and not pdf_path.exists():
                return CompileResult(
                    success=False,
                    errors=self._parse_errors(log_file),
                    method=f"{engine} (pass 1 failed)",
                )

            # BibTeX
            if config.bibtex:
                run_cmd(["bibtex", tex_path.stem])

            # Pass 2..N
            passes = config.compile_passes or 2
            for _ in range(2, passes + 1):
                result = run_cmd(base_cmd)

            pdf_path = compile_dir / (tex_path.stem + ".pdf")
            log_file = compile_dir / (tex_path.stem + ".log")

            if pdf_path.exists():
                # nonstopmode: PDF exists = success, even if returncode != 0
                warnings = self._parse_warnings(log_file)
                if result.returncode != 0:
                    # Promote errors to warnings since PDF was still produced
                    warnings = self._parse_errors(log_file) + warnings
                return CompileResult(
                    success=True, pdf_path=pdf_path,
                    warnings=warnings,
                    method=f"{engine} ({passes} passes)",
                )
            else:
                return CompileResult(
                    success=False,
                    errors=self._parse_errors(log_file),
                    log_excerpt=result.stderr[-2000:] if result.stderr else "",
                    method=f"{engine} ({passes} passes)",
                )
        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[f"Compilation timed out after {timeout}s"],
                method=engine,
            )

    # -- LaTeX log 解析 --

    def _parse_errors(self, log_file: Path) -> list[str]:
        if not log_file.exists():
            return ["Log file not found"]
        errors = []
        lines = log_file.read_text(errors="replace").splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("!"):
                error_msg = line
                for j in range(i + 1, min(i + 5, len(lines))):
                    ctx = lines[j]
                    if ctx.startswith("l.") or ctx.startswith("  "):
                        error_msg += "\n" + ctx
                    else:
                        break
                errors.append(error_msg)
            elif ":" in line and any(
                line.endswith(s) for s in [
                    "Undefined control sequence.",
                    "Missing $ inserted.",
                    "Extra alignment tab has been changed to \\cr.",
                ]
            ):
                errors.append(line)
            i += 1

        seen = set()
        unique = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                unique.append(e)
            if len(unique) >= 20:
                break
        # Fallback: if no errors parsed but log exists, include tail
        if not unique:
            tail = lines[-30:] if len(lines) > 30 else lines
            unique = ["[No standard errors parsed. Log tail:]\n" + "\n".join(tail)]
        return unique

    def _parse_warnings(self, log_file: Path) -> list[str]:
        if not log_file.exists():
            return []
        import re
        content = log_file.read_text(errors="replace")
        warnings = []
        patterns = [
            r"LaTeX Warning: (.+)",
            r"Package \w+ Warning: (.+)",
            r"Overfull \\hbox .+ in paragraph",
            r"Underfull \\hbox .+ in paragraph",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                warnings.append(match.group(0))
        return warnings[:20]
