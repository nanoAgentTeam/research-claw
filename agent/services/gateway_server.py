import asyncio
import json
import shutil
from pathlib import Path
from typing import Optional, List, Any, Dict
import re
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from loguru import logger

from config.loader import get_config_service, convert_keys, convert_to_camel, get_config_path
from config.schema import ProviderInstance, ChannelAccount, Config, PushSubscription, SmtpProfile
from agent.services.im_runtime import get_im_runtime
from bus.queue import MessageBus
from providers.proxy import DynamicProviderProxy
from agent.loop import AgentLoop
from core.project import Project
from core.automation import AutomationRuntime
from core.automation.chat_registry import ChatContactRegistry
from core.automation.models import AutomationJob, JobSchedule, OutputPolicy, SUPPORTED_JOB_TYPES
from core.automation.push_targets import build_apprise_url, send_apprise_notification
from core.memory import ProjectMemoryStore

app = FastAPI(title="ContextBot Gateway")


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/ui")


@app.middleware("http")
async def disable_ui_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path.rstrip("/")
    if path in {"/ui", "/ui/index.html"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Global bus and runtime
bus = MessageBus()
im_runtime = get_im_runtime(bus)
automation_runtime: Optional[AutomationRuntime] = None

# Global chat contact registry
_chat_registry: Optional[ChatContactRegistry] = None


async def _record_chat_contact(msg) -> None:
    """Inbound hook: record IM contact for push subscription auto-population."""
    if _chat_registry is None:
        return
    if not msg.channel or not msg.chat_id or msg.channel == "cli":
        return
    meta = {}
    if hasattr(msg, "metadata") and isinstance(msg.metadata, dict):
        chat_type = msg.metadata.get("chat_type")
        if chat_type:
            meta["chat_type"] = chat_type
    _chat_registry.record_contact(
        channel=msg.channel,
        chat_id=msg.chat_id,
        sender_id=getattr(msg, "sender_id", ""),
        metadata=meta or None,
    )

# Initialize Agent Loop with Dynamic Proxy
proxy_provider = DynamicProviderProxy()
_gw_config_service = get_config_service()
_gw_active = _gw_config_service.config.get_active_provider()
_gw_model = _gw_active.model_name if _gw_active else "gpt-3.5-turbo"
_gw_workspace = _gw_config_service.config.workspace_path
agent_loop = AgentLoop(
    bus=bus,
    provider=proxy_provider,
    workspace=_gw_workspace,
    model=_gw_model,
)


class JobSchedulePayload(BaseModel):
    cron: str
    timezone: str = "UTC"


class JobCreatePayload(BaseModel):
    id: str
    name: str
    type: str = "normal"
    schedule: JobSchedulePayload
    prompt: str
    enabled: bool = True


class JobUpdatePayload(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    enabled: Optional[bool] = None
    frozen: Optional[bool] = None
    prompt: Optional[str] = None
    schedule: Optional[JobSchedulePayload] = None


class SubscriptionPayload(BaseModel):
    channel: str
    chat_id: str


class ConfigSubscriptionCreatePayload(BaseModel):
    channel: str
    chat_id: str = ""
    params: Dict[str, str] = Field(default_factory=dict)
    apprise_url: str = ""
    enabled: bool = True
    remark: str = ""


class ConfigSubscriptionUpdatePayload(BaseModel):
    channel: Optional[str] = None
    chat_id: Optional[str] = None
    params: Optional[Dict[str, str]] = None
    apprise_url: Optional[str] = None
    enabled: Optional[bool] = None
    remark: Optional[str] = None


class SubscriptionTestPayload(BaseModel):
    title: str = "ContextBot 推送测试"
    body: str = "这是一条测试消息，用于确认订阅推送链路是否可用。"


class SmtpProfileCreatePayload(BaseModel):
    id: str = ""
    name: str = ""
    provider: str = "custom"
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = ""
    use_tls: bool = True
    enabled: bool = True
    is_default: bool = False


class SmtpProfileUpdatePayload(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    use_tls: Optional[bool] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None


class SmtpProfileTestPayload(BaseModel):
    recipient: str
    title: str = "ContextBot SMTP 测试"
    body: str = "这是一条 SMTP 测试邮件，用于确认邮件推送配置可用。"


class ConfigRestorePayload(BaseModel):
    filename: str


class AutomationRestorePayload(BaseModel):
    filename: str
    mode: str = "replace"

@app.on_event("startup")
async def startup_event():
    global automation_runtime, _chat_registry
    service = get_config_service()
    _chat_registry = ChatContactRegistry(service.config.workspace_path)
    active = service.config.get_active_provider()
    model_name = active.model_name if active else "gpt-3.5-turbo"
    try:
        automation_runtime = AutomationRuntime(
            workspace=service.config.workspace_path,
            provider=proxy_provider,
            model=model_name,
            config=service.config,
            bus=bus,
            s2_api_key=service.config.tools.academic.semanticscholar_api_key or None,
        )
        await automation_runtime.start()
    except Exception as e:
        automation_runtime = None
        logger.warning(f"AutomationRuntime startup failed, continue without scheduler: {e}")

    # Register bus hooks for unified history logging
    # Use lambda to always resolve the current history_logger (it changes on project switch)
    bus.add_inbound_hook(lambda msg: agent_loop.history_logger.log_inbound(msg))
    bus.add_inbound_hook(_record_chat_contact)
    bus.add_outbound_hook(lambda msg: agent_loop.history_logger.log_outbound(msg))

    # Start IM channels and Agent loop in background
    asyncio.create_task(im_runtime.start_all())
    asyncio.create_task(agent_loop.run())
    logger.info("Gateway services (AutomationRuntime/IM/AgentLoop) started")


@app.on_event("shutdown")
async def shutdown_event():
    global automation_runtime
    if automation_runtime:
        await automation_runtime.stop()
        automation_runtime = None

# Storage for active websocket connections for logs
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

# Loguru sink to broadcast logs via WebSocket
def websocket_log_sink(message):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(manager.broadcast(str(message)))
    except RuntimeError:
        # No running event loop (e.g., during startup or from a non-async thread)
        pass

logger.add(websocket_log_sink, level="INFO")

# --- API Routes ---

@app.get("/api/health")
async def health_check():
    """Lightweight health check endpoint."""
    return {"status": "ok"}


@app.get("/api/language")
async def get_language():
    """Return the current UI language from settings.json."""
    config_path = get_config_path()
    lang = "zh"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            # settings.json uses camelCase; fall back to snake_case for compat
            lang = data.get("userInfo", data.get("user_info", {})).get("language", "zh")
    except Exception:
        pass
    return {"language": lang}


@app.put("/api/language")
async def set_language(payload: dict):
    """Persist UI language to settings.json and update Config at runtime."""
    lang = str(payload.get("language", "zh")).strip().lower()
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="language must be 'zh' or 'en'")
    config_path = get_config_path()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        # Use camelCase to match save_config convention
        user_info = data.setdefault("userInfo", {})
        user_info["language"] = lang
        # Remove legacy snake_case key to prevent duplication
        data.pop("user_info", None)
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # Update runtime Config so _t() picks up the change immediately
        try:
            from core.infra.config import Config
            Config.LANGUAGE = lang
        except Exception:
            pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to save language: {e}")
    return {"ok": True, "language": lang}


@app.get("/api/llm-language")
async def get_llm_language():
    """Return the current LLM reply language from settings.json."""
    config_path = get_config_path()
    llm_lang = "auto"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            llm_lang = data.get("userInfo", data.get("user_info", {})).get("llmLanguage",
                       data.get("userInfo", data.get("user_info", {})).get("llm_language", "auto"))
    except Exception:
        pass
    return {"llmLanguage": llm_lang}


@app.put("/api/llm-language")
async def set_llm_language(payload: dict):
    """Persist LLM reply language to settings.json and update Config at runtime."""
    llm_lang = str(payload.get("llmLanguage", "auto")).strip().lower()
    valid_langs = ("auto", "zh", "en", "ja", "ko", "fr", "de", "es", "ru", "pt", "ar")
    if llm_lang not in valid_langs:
        raise HTTPException(status_code=400, detail=f"llmLanguage must be one of {valid_langs}")
    config_path = get_config_path()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        user_info = data.setdefault("userInfo", {})
        user_info["llmLanguage"] = llm_lang
        data.pop("user_info", None)
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            from core.infra.config import Config
            Config.LLM_LANGUAGE = llm_lang
        except Exception:
            pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to save llm language: {e}")
    return {"ok": True, "llmLanguage": llm_lang}

@app.get("/api/config")
async def get_config():
    from config.loader import convert_to_camel
    service = get_config_service()
    # Convert to camelCase for frontend and exclude legacy fields
    config_dict = service.config.model_dump(exclude={"providers", "channels"})
    smtp_cfg = config_dict.get("smtp")
    smtp_profiles = smtp_cfg.get("profiles") if isinstance(smtp_cfg, dict) else None
    if isinstance(smtp_profiles, list):
        for row in smtp_profiles:
            if not isinstance(row, dict):
                continue
            secret = str(row.get("password") or "")
            row["password_set"] = bool(secret)
    config_dict = convert_to_camel(config_dict)
    return config_dict

@app.post("/api/config")
async def update_config(new_config: dict):
    service = get_config_service()
    try:
        # Convert camelCase to snake_case for Pydantic
        converted_config = convert_keys(new_config)
        # Preserve push subscriptions when frontend payload does not include it
        if "push_subscriptions" not in converted_config:
            existing = getattr(service.config, "push_subscriptions", None)
            if existing is not None:
                converted_config["push_subscriptions"] = existing.model_dump()
        # Preserve smtp profiles when frontend payload does not include it
        if "smtp" not in converted_config:
            existing_smtp = getattr(service.config, "smtp", None)
            if existing_smtp is not None:
                converted_config["smtp"] = existing_smtp.model_dump()
        else:
            existing_profiles = {
                str(p.id): str(p.password or "")
                for p in _list_smtp_profiles(service.config)
            }
            smtp_cfg = converted_config.get("smtp")
            incoming = smtp_cfg.get("profiles") if isinstance(smtp_cfg, dict) else None
            if isinstance(incoming, list):
                for row in incoming:
                    if not isinstance(row, dict):
                        continue
                    row.pop("password_set", None)
                    pid = str(row.get("id") or "").strip()
                    if not pid:
                        continue
                    current = existing_profiles.get(pid, "")
                    if not current:
                        continue
                    if "password" not in row or str(row.get("password") or "") == "":
                        row["password"] = current
        # Validate with Pydantic
        config = Config.model_validate(converted_config)
        service.save(config)

        # Trigger hot-reload for IM channels
        await im_runtime.sync_with_config()

        return {"status": "success", "message": "Configuration updated and hot-reloaded"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _list_config_subscriptions(config: Config) -> list[PushSubscription]:
    current = getattr(config, "push_subscriptions", None)
    if current is None:
        config.push_subscriptions.items = []
        return []
    if isinstance(current, list):
        items: list[PushSubscription] = []
        for idx, item in enumerate(current):
            if isinstance(item, PushSubscription):
                entry = item
            elif isinstance(item, dict):
                entry = PushSubscription.model_validate(item)
            else:
                continue
            if not entry.id:
                entry.id = f"sub-{idx+1}"
            entry.channel = str(entry.channel or "").strip()
            entry.chat_id = str(entry.chat_id or "").strip()
            entry.apprise_url = str(entry.apprise_url or "").strip()
            entry.params = {str(k): str(v) for k, v in (entry.params or {}).items()}
            items.append(entry)
        config.push_subscriptions.items = items
        return items
    items = getattr(current, "items", None)
    if not isinstance(items, list):
        config.push_subscriptions.items = []
        return []
    normalized: list[PushSubscription] = []
    for idx, item in enumerate(items):
        if isinstance(item, PushSubscription):
            entry = item
        elif isinstance(item, dict):
            entry = PushSubscription.model_validate(item)
        else:
            continue
        if not entry.id:
            entry.id = f"sub-{idx+1}"
        entry.channel = str(entry.channel or "").strip()
        entry.chat_id = str(entry.chat_id or "").strip()
        entry.apprise_url = str(entry.apprise_url or "").strip()
        entry.params = {str(k): str(v) for k, v in (entry.params or {}).items()}
        normalized.append(entry)
    config.push_subscriptions.items = normalized
    return normalized


def _list_smtp_profiles(config: Config) -> list[SmtpProfile]:
    current = getattr(config, "smtp", None)
    profiles = getattr(current, "profiles", None) if current is not None else None
    if not isinstance(profiles, list):
        config.smtp.profiles = []
        return []

    normalized: list[SmtpProfile] = []
    for idx, item in enumerate(profiles):
        if isinstance(item, SmtpProfile):
            entry = item
        elif isinstance(item, dict):
            entry = SmtpProfile.model_validate(item)
        else:
            continue
        entry.id = str(entry.id or "").strip() or f"smtp-{idx+1}"
        entry.name = str(entry.name or "").strip()
        entry.provider = str(entry.provider or "custom").strip().lower() or "custom"
        entry.host = str(entry.host or "").strip()
        entry.user = str(entry.user or "").strip()
        entry.password = str(entry.password or "")
        entry.from_email = str(entry.from_email or "").strip()
        entry.from_name = str(entry.from_name or "").strip()
        entry.port = int(entry.port or 587)
        entry.use_tls = bool(entry.use_tls)
        entry.enabled = bool(entry.enabled)
        entry.is_default = bool(entry.is_default)
        normalized.append(entry)

    _ensure_single_default_smtp_profile(normalized)

    config.smtp.profiles = normalized
    return normalized


def _ensure_single_default_smtp_profile(items: list[SmtpProfile], preferred_id: str = "") -> None:
    """Keep exactly one enabled default profile when possible."""
    preferred = str(preferred_id or "").strip()
    if len(items) == 1:
        items[0].is_default = True
        return

    for item in items:
        if not item.enabled:
            item.is_default = False

    enabled_items = [x for x in items if x.enabled]
    if not enabled_items:
        for item in items:
            item.is_default = False
        return

    if preferred:
        target = next((x for x in enabled_items if x.id == preferred), None)
        if target is not None:
            for item in items:
                item.is_default = (item.id == target.id)
            return

    existing_default = next((x for x in enabled_items if x.is_default), None)
    chosen = existing_default or enabled_items[0]
    for item in items:
        item.is_default = (item.id == chosen.id)


def _smtp_presets() -> list[dict[str, Any]]:
    return [
        {"id": "custom", "label": "自定义", "host": "", "port": 587, "use_tls": True},
        {"id": "qq", "label": "QQ邮箱", "host": "smtp.qq.com", "port": 587, "use_tls": True},
        {"id": "163", "label": "163邮箱", "host": "smtp.163.com", "port": 465, "use_tls": True},
        {"id": "gmail", "label": "Gmail", "host": "smtp.gmail.com", "port": 587, "use_tls": True},
        {"id": "outlook", "label": "Outlook/Hotmail", "host": "smtp-mail.outlook.com", "port": 587, "use_tls": True},
    ]


def _smtp_profile_public(profile: SmtpProfile) -> dict[str, Any]:
    row = profile.model_dump()
    secret = str(row.get("password") or "")
    row["password_set"] = bool(secret)
    return row


def _config_backup_dir() -> Path:
    return get_config_path().parent / "backups"


BACKUP_KEEP_LATEST = 15


def _prune_backups(glob_pattern: str, keep_latest: int = BACKUP_KEEP_LATEST) -> None:
    backup_dir = _config_backup_dir()
    if keep_latest <= 0 or not backup_dir.exists():
        return
    files = sorted(backup_dir.glob(glob_pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[keep_latest:]:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue


def _safe_backup_path(filename: str) -> Path:
    name = str(filename or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="filename is required")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="invalid filename")
    target = (_config_backup_dir() / name).resolve()
    if target.parent != _config_backup_dir().resolve():
        raise HTTPException(status_code=400, detail="invalid backup path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"backup not found: {name}")
    return target


def _default_config_path() -> Path:
    return get_config_path().parent / "settings.default.json"


def _load_default_config_payload() -> dict[str, Any]:
    path = _default_config_path()
    if path.exists() and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    default_payload = convert_to_camel(Config().model_dump(exclude={"providers", "channels"}))
    path.write_text(json.dumps(default_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return default_payload


def _create_config_backup() -> Path:
    src = get_config_path()
    if not src.exists():
        raise FileNotFoundError(f"settings file not found: {src}")
    backup_dir = _config_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"settings_{stamp}.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    payload = {
        "backup_time": now.isoformat(),
        "source": str(src),
        "config": raw,
    }
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_backups("settings_*.json")
    return dst


def _workspace_projects() -> list[Project]:
    root = _workspace_root()
    if not root.exists():
        return []
    rows: list[Project] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith(".") or path.name == "Default":
            continue
        try:
            rows.append(Project(path.name, root))
        except Exception:
            continue
    return rows


@app.get("/api/config/backups")
async def list_config_backups():
    backup_dir = _config_backup_dir()
    if not backup_dir.exists():
        return {"items": []}
    rows: list[dict[str, Any]] = []
    for path in sorted(backup_dir.glob("settings_*.json"), reverse=True):
        try:
            stat = path.stat()
            backup_time = datetime.fromtimestamp(stat.st_mtime).isoformat()
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and str(raw.get("backup_time", "")).strip():
                    backup_time = str(raw.get("backup_time"))
            except Exception:
                pass
            rows.append(
                {
                    "filename": path.name,
                    "timestamp": backup_time,
                    "size_bytes": int(stat.st_size),
                }
            )
        except Exception:
            continue
    return {"items": rows}


@app.get("/api/config/backups/{filename}")
async def view_config_backup(filename: str):
    path = _safe_backup_path(filename)
    if not path.name.startswith("settings_"):
        raise HTTPException(status_code=400, detail="not a config backup")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"filename": path.name, "content": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"read backup failed: {e}")


@app.delete("/api/config/backups/{filename}")
async def delete_config_backup(filename: str):
    path = _safe_backup_path(filename)
    if not path.name.startswith("settings_"):
        raise HTTPException(status_code=400, detail="not a config backup")
    path.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/config/backup")
async def create_config_backup_api():
    try:
        dst = _create_config_backup()
        return {"ok": True, "filename": dst.name, "keep_latest": BACKUP_KEEP_LATEST}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/config/restore")
async def restore_config_backup(payload: ConfigRestorePayload):
    service = get_config_service()
    backup_path = _safe_backup_path(payload.filename)

    try:
        raw = json.loads(backup_path.read_text(encoding="utf-8"))
        source_payload = raw.get("config") if isinstance(raw, dict) and isinstance(raw.get("config"), dict) else raw
        converted = convert_keys(source_payload)
        config = Config.model_validate(converted)
        service.save(config)
        await im_runtime.sync_with_config()
        return {"ok": True, "restored": backup_path.name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"restore failed: {e}")


@app.post("/api/config/reset")
async def reset_config_default():
    # TODO: 需要增加凭证保留逻辑后再启用
    # service = get_config_service()
    # try:
    #     payload = _load_default_config_payload()
    #     config = Config.model_validate(convert_keys(payload))
    #     service.save(config)
    #     await im_runtime.sync_with_config()
    #     return {"ok": True, "message": "configuration reset to defaults", "default_file": str(_default_config_path())}
    # except Exception as e:
    #     raise HTTPException(status_code=400, detail=f"reset failed: {e}")
    return {"ok": False, "message": "Config reset is temporarily disabled to prevent accidental credential loss."}


@app.get("/api/config/default")
async def get_default_config_info():
    path = _default_config_path()
    if not path.exists() or not path.is_file():
        _load_default_config_payload()
    content: Optional[Dict[str, Any]] = None
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        content = None
    return {"file": str(path), "exists": path.exists(), "content": content}


@app.get("/api/automation/backups")
async def list_automation_backups():
    backup_dir = _config_backup_dir()
    if not backup_dir.exists():
        return {"items": []}
    rows: list[dict[str, Any]] = []
    for path in sorted(backup_dir.glob("automation_*.json"), reverse=True):
        try:
            stat = path.stat()
            backup_time = datetime.fromtimestamp(stat.st_mtime).isoformat()
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and str(raw.get("backup_time", "")).strip():
                    backup_time = str(raw.get("backup_time"))
            except Exception:
                pass
            rows.append(
                {
                    "filename": path.name,
                    "timestamp": backup_time,
                    "size_bytes": int(stat.st_size),
                }
            )
        except Exception:
            continue
    return {"items": rows}


@app.get("/api/automation/backups/{filename}")
async def view_automation_backup(filename: str):
    path = _safe_backup_path(filename)
    if not path.name.startswith("automation_"):
        raise HTTPException(status_code=400, detail="not an automation backup")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"filename": path.name, "content": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"read backup failed: {e}")


@app.delete("/api/automation/backups/{filename}")
async def delete_automation_backup(filename: str):
    path = _safe_backup_path(filename)
    if not path.name.startswith("automation_"):
        raise HTTPException(status_code=400, detail="not an automation backup")
    path.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/automation/backup")
async def create_automation_backup():
    from core.automation.store_fs import FSAutomationStore

    projects = _workspace_projects()
    payload_projects: list[dict[str, Any]] = []
    for project in projects:
        store = FSAutomationStore(project)
        jobs = [job.to_dict() for job in store.list_jobs()]
        states: dict[str, Any] = {}
        for job in store.list_jobs():
            states[job.id] = store.get_job_state(job.id)
        payload_projects.append(
            {
                "project_id": project.id,
                "jobs": jobs,
                "states": states,
                "subscriptions": store.get_subscriptions(),
                "linked_subscription_ids": store.get_linked_subscription_ids(),
            }
        )

    backup_dir = _config_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"automation_{stamp}.json"
    data = {
        "backup_time": now.isoformat(),
        "projects": payload_projects,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_backups("automation_*.json")
    return {"ok": True, "filename": path.name, "projects": len(payload_projects), "keep_latest": BACKUP_KEEP_LATEST}


@app.post("/api/automation/restore")
async def restore_automation_backup(payload: AutomationRestorePayload):
    from core.automation.store_fs import FSAutomationStore

    filename = str(payload.filename or "").strip()
    mode = str(payload.mode or "replace").strip().lower()
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    if mode not in {"replace", "merge"}:
        raise HTTPException(status_code=400, detail="mode must be replace|merge")
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    backup_path = (_config_backup_dir() / filename).resolve()
    if backup_path.parent != _config_backup_dir().resolve():
        raise HTTPException(status_code=400, detail="invalid backup path")
    if not backup_path.exists() or not backup_path.is_file():
        raise HTTPException(status_code=404, detail=f"backup not found: {filename}")

    try:
        raw = json.loads(backup_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid backup file: {e}")

    projects_data = raw.get("projects", []) if isinstance(raw, dict) else []
    if not isinstance(projects_data, list):
        raise HTTPException(status_code=400, detail="invalid backup payload")

    changed_projects: list[str] = []
    skipped: list[str] = []
    for row in projects_data:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("project_id", "")).strip()
        if not pid:
            continue
        try:
            project = _get_project_or_404(pid)
        except HTTPException:
            skipped.append(pid)
            continue

        store = FSAutomationStore(project)
        jobs = row.get("jobs", []) if isinstance(row.get("jobs", []), list) else []
        states = row.get("states", {}) if isinstance(row.get("states", {}), dict) else {}
        subscriptions = row.get("subscriptions", {}) if isinstance(row.get("subscriptions", {}), dict) else {}

        if mode == "replace":
            for p in list(store.jobs_dir.glob("*.json")):
                p.unlink(missing_ok=True)
            for p in list(store.states_dir.glob("*.json")):
                p.unlink(missing_ok=True)
            store.subscriptions_file.unlink(missing_ok=True)

        for job_data in jobs:
            if not isinstance(job_data, dict):
                continue
            try:
                job = AutomationJob.from_dict(job_data)
                if job.id:
                    store.upsert_job(job)
            except Exception:
                continue

        for job_id, state in states.items():
            if not isinstance(state, dict):
                continue
            try:
                store.update_job_state(str(job_id), state)
            except Exception:
                continue

        if mode == "replace":
            current_subs = store.get_subscriptions()
            for ch, ids in current_subs.items():
                for cid in ids:
                    store.remove_subscription(ch, cid)

        for ch, ids in subscriptions.items():
            if not isinstance(ids, list):
                continue
            for cid in ids:
                try:
                    store.add_subscription(str(ch), str(cid))
                except Exception:
                    continue

        linked_ids = row.get("linked_subscription_ids", [])
        if isinstance(linked_ids, list) and linked_ids:
            store.set_linked_subscription_ids([str(i) for i in linked_ids])
        elif mode == "replace":
            store.set_linked_subscription_ids([])

        changed_projects.append(pid)
        await _maybe_reschedule(project)

    return {
        "ok": True,
        "mode": mode,
        "restored_projects": changed_projects,
        "skipped_projects": skipped,
    }


@app.get("/api/chat-contacts")
async def list_chat_contacts():
    if _chat_registry is None:
        return {"items": []}
    contacts = _chat_registry.get_contacts()
    _im_normalize = {"feishu": "im_feishu", "telegram": "im_telegram", "dingtalk": "im_dingtalk", "qq": "im_qq"}
    seen = {}
    for c in contacts.values():
        c = dict(c)
        ch = str(c.get("channel") or "").strip()
        if ch in _im_normalize:
            c["channel"] = _im_normalize[ch]
        key = c["channel"] + ":" + c.get("chat_id", "")
        if key not in seen:
            seen[key] = c
    return {"items": list(seen.values())}


@app.get("/api/config/subscriptions")
async def list_config_subscriptions():
    service = get_config_service()
    items = _list_config_subscriptions(service.config)
    return {"items": [item.model_dump() for item in items]}


@app.get("/api/config/smtp-profiles")
async def list_smtp_profiles():
    service = get_config_service()
    items = _list_smtp_profiles(service.config)
    return {"items": [_smtp_profile_public(item) for item in items], "presets": _smtp_presets()}


@app.post("/api/config/smtp-profiles")
async def create_smtp_profile(payload: SmtpProfileCreatePayload):
    service = get_config_service()
    config = service.config
    items = _list_smtp_profiles(config)
    pid = str(payload.id or "").strip() or f"smtp-{int(datetime.now().timestamp() * 1000)}"
    if any(x.id == pid for x in items):
        raise HTTPException(status_code=409, detail=f"smtp profile already exists: {pid}")

    profile = SmtpProfile(
        id=pid,
        name=str(payload.name or "").strip(),
        provider=str(payload.provider or "custom").strip().lower() or "custom",
        host=str(payload.host or "").strip(),
        port=int(payload.port or 587),
        user=str(payload.user or "").strip(),
        password=str(payload.password or ""),
        from_email=str(payload.from_email or "").strip(),
        from_name=str(payload.from_name or "").strip(),
        use_tls=bool(payload.use_tls),
        enabled=bool(payload.enabled),
        is_default=bool(payload.is_default),
    )
    if profile.port <= 0:
        raise HTTPException(status_code=400, detail="smtp port must be > 0")
    if not profile.from_email:
        raise HTTPException(status_code=400, detail="from_email is required")
    if not profile.host:
        raise HTTPException(status_code=400, detail="smtp host is required")

    items.append(profile)
    _ensure_single_default_smtp_profile(items, preferred_id=profile.id if payload.is_default else "")
    config.smtp.profiles = items
    service.save(config)
    return {"ok": True, "item": _smtp_profile_public(profile)}


@app.put("/api/config/smtp-profiles/{profile_id}")
async def update_smtp_profile(profile_id: str, payload: SmtpProfileUpdatePayload):
    service = get_config_service()
    config = service.config
    items = _list_smtp_profiles(config)
    pid = str(profile_id or "").strip()
    target = next((x for x in items if x.id == pid), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"smtp profile not found: {pid}")

    if payload.name is not None:
        target.name = str(payload.name).strip()
    if payload.provider is not None:
        target.provider = str(payload.provider).strip().lower() or "custom"
    if payload.host is not None:
        target.host = str(payload.host).strip()
    if payload.port is not None:
        target.port = int(payload.port)
    if payload.user is not None:
        target.user = str(payload.user).strip()
    if payload.password is not None:
        target.password = str(payload.password or "")
    if payload.from_email is not None:
        target.from_email = str(payload.from_email).strip()
    if payload.from_name is not None:
        target.from_name = str(payload.from_name).strip()
    if payload.use_tls is not None:
        target.use_tls = bool(payload.use_tls)
    if payload.enabled is not None:
        target.enabled = bool(payload.enabled)
    if payload.is_default is not None:
        target.is_default = bool(payload.is_default)

    if target.port <= 0:
        raise HTTPException(status_code=400, detail="smtp port must be > 0")
    if not target.from_email:
        raise HTTPException(status_code=400, detail="from_email is required")
    if not target.host:
        raise HTTPException(status_code=400, detail="smtp host is required")

    _ensure_single_default_smtp_profile(items, preferred_id=target.id if payload.is_default is True else "")
    config.smtp.profiles = items
    service.save(config)
    return {"ok": True, "item": _smtp_profile_public(target)}


@app.delete("/api/config/smtp-profiles/{profile_id}")
async def delete_smtp_profile(profile_id: str):
    service = get_config_service()
    config = service.config
    items = _list_smtp_profiles(config)
    pid = str(profile_id or "").strip()
    filtered = [x for x in items if x.id != pid]
    if len(filtered) == len(items):
        raise HTTPException(status_code=404, detail=f"smtp profile not found: {pid}")

    _ensure_single_default_smtp_profile(filtered)
    config.smtp.profiles = filtered
    service.save(config)
    return {"ok": True}


@app.post("/api/config/smtp-profiles/{profile_id}/test")
async def test_smtp_profile(profile_id: str, payload: SmtpProfileTestPayload):
    service = get_config_service()
    profiles = _list_smtp_profiles(service.config)
    pid = str(profile_id or "").strip()
    target = next((x for x in profiles if x.id == pid), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"smtp profile not found: {pid}")

    test_url = build_apprise_url("email", params={"email": payload.recipient, "profile_id": target.id})
    ok, info = await asyncio.to_thread(send_apprise_notification, test_url, payload.title, payload.body)
    if not ok:
        raise HTTPException(status_code=400, detail=f"smtp test failed: {info}")
    return {"ok": True, "message": "smtp test sent"}


@app.get("/api/config/subscriptions/capabilities")
async def get_config_subscription_capabilities():
    return {
        "channels": [
            {"id": "telegram", "label": "Telegram", "fields": ["params.bot_token", "params.chat_id"]},
            {"id": "feishu", "label": "Feishu", "fields": ["params.token"]},
            {"id": "qq", "label": "QQ Push(qmsg)", "fields": ["params.token", "params.mode", "params.qq", "params.bot"]},
            {"id": "wecombot", "label": "微信(企业微信群机器人)", "fields": ["params.key"]},
            {"id": "serverchan", "label": "Server酱", "fields": ["params.sendkey"]},
            {"id": "email", "label": "Email", "fields": ["params.email", "params.profile_id"]},
            {"id": "custom", "label": "自定义Apprise URL", "fields": ["apprise_url"]},
        ]
    }


@app.post("/api/config/subscriptions")
async def create_config_subscription(payload: ConfigSubscriptionCreatePayload):
    service = get_config_service()
    config = service.config
    items = _list_config_subscriptions(config)

    channel = str(payload.channel or "").strip()
    chat_id = str(payload.chat_id or "").strip()
    params = {str(k): str(v) for k, v in (payload.params or {}).items()}
    apprise_url = str(payload.apprise_url or "").strip()
    remark = str(payload.remark or "").strip()
    if not channel:
        raise HTTPException(status_code=400, detail="channel is required")
    url = build_apprise_url(channel, chat_id=chat_id, params=params, apprise_url=apprise_url)
    if not url:
        raise HTTPException(status_code=400, detail="subscription fields are incomplete for selected channel")

    exists = any(
        x.channel == channel
        and x.chat_id == chat_id
        and x.apprise_url == apprise_url
        and (x.params or {}) == params
        for x in items
    )
    if exists:
        raise HTTPException(status_code=409, detail="subscription already exists")

    sid = f"sub-{int(datetime.now().timestamp() * 1000)}"
    item = PushSubscription(
        id=sid,
        channel=channel,
        chat_id=chat_id,
        params=params,
        apprise_url=apprise_url,
        enabled=bool(payload.enabled),
        remark=remark,
    )
    items.append(item)
    config.push_subscriptions.items = items
    service.save(config)
    return {"ok": True, "item": item.model_dump()}


@app.put("/api/config/subscriptions/{subscription_id}")
async def update_config_subscription(subscription_id: str, payload: ConfigSubscriptionUpdatePayload):
    service = get_config_service()
    config = service.config
    items = _list_config_subscriptions(config)

    sid = str(subscription_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="subscription_id is required")

    target = next((x for x in items if x.id == sid), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"subscription not found: {sid}")

    if payload.channel is not None:
        channel = str(payload.channel).strip()
        if not channel:
            raise HTTPException(status_code=400, detail="channel cannot be empty")
        target.channel = channel
    if payload.chat_id is not None:
        target.chat_id = str(payload.chat_id).strip()
    if payload.enabled is not None:
        target.enabled = bool(payload.enabled)
    if payload.params is not None:
        target.params = {str(k): str(v) for k, v in payload.params.items()}
    if payload.apprise_url is not None:
        target.apprise_url = str(payload.apprise_url).strip()
    if payload.remark is not None:
        target.remark = str(payload.remark).strip()

    if not build_apprise_url(
        target.channel,
        chat_id=target.chat_id,
        params=target.params,
        apprise_url=target.apprise_url,
    ):
        raise HTTPException(status_code=400, detail="subscription fields are incomplete for selected channel")

    config.push_subscriptions.items = items
    service.save(config)
    return {"ok": True, "item": target.model_dump()}


@app.delete("/api/config/subscriptions/{subscription_id}")
async def delete_config_subscription(subscription_id: str):
    service = get_config_service()
    config = service.config
    items = _list_config_subscriptions(config)
    sid = str(subscription_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="subscription_id is required")

    filtered = [x for x in items if x.id != sid]
    if len(filtered) == len(items):
        raise HTTPException(status_code=404, detail=f"subscription not found: {sid}")

    config.push_subscriptions.items = filtered
    service.save(config)
    return {"ok": True}


@app.post("/api/config/subscriptions/{subscription_id}/test")
async def test_config_subscription(subscription_id: str, payload: SubscriptionTestPayload):
    service = get_config_service()
    items = _list_config_subscriptions(service.config)
    sid = str(subscription_id or "").strip()
    target = next((x for x in items if x.id == sid), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"subscription not found: {sid}")

    url = build_apprise_url(target.channel, chat_id=target.chat_id, params=target.params, apprise_url=target.apprise_url)
    if not url:
        raise HTTPException(status_code=400, detail="invalid subscription target fields")

    if url.startswith("im-direct://"):
        from bus.events import OutboundMessage
        content = f"{payload.title}\n{payload.body}".strip()
        await bus.publish_outbound(OutboundMessage(
            channel=target.channel, chat_id=target.chat_id,
            content=content or "ContextBot Test / ContextBot 测试通知",
            metadata={"source": "subscription_test"},
        ))
        return {"ok": True, "message": "test sent via IM runtime", "target": target.model_dump()}

    ok, info = await asyncio.to_thread(send_apprise_notification, url, payload.title, payload.body)
    if not ok:
        raise HTTPException(status_code=502, detail=str(info or "upstream notification failed"))
    return {"ok": True, "message": "test sent", "target": target.model_dump()}


@app.post("/api/config/subscriptions/test-enabled")
async def test_enabled_config_subscriptions(payload: SubscriptionTestPayload):
    service = get_config_service()
    items = _list_config_subscriptions(service.config)
    enabled_items = [x for x in items if x.enabled]
    if not enabled_items:
        raise HTTPException(status_code=400, detail="no enabled subscriptions")

    success = 0
    failed: list[dict[str, str]] = []
    for item in enabled_items:
        url = build_apprise_url(item.channel, chat_id=item.chat_id, params=item.params, apprise_url=item.apprise_url)
        if not url:
            failed.append({"id": item.id, "reason": "invalid target fields"})
            continue
        if url.startswith("im-direct://"):
            from bus.events import OutboundMessage
            content = f"{payload.title}\n{payload.body}".strip()
            try:
                await bus.publish_outbound(OutboundMessage(
                    channel=item.channel, chat_id=item.chat_id,
                    content=content or "ContextBot Test / ContextBot 测试通知",
                    metadata={"source": "subscription_test"},
                ))
                success += 1
            except Exception as e:
                failed.append({"id": item.id, "reason": f"IM runtime error: {e}"})
            continue
        ok, info = await asyncio.to_thread(send_apprise_notification, url, payload.title, payload.body)
        if ok:
            success += 1
        else:
            failed.append({"id": item.id, "reason": info})

    return {
        "ok": success > 0,
        "success": success,
        "failed": failed,
        "total": len(enabled_items),
    }

@app.get("/api/diagnostics")
async def run_diagnostics():
    from config.diagnostics import run_all_diagnostics
    service = get_config_service()
    results = await run_all_diagnostics(service.config)
    return results

@app.post("/api/config/test-llm")
async def test_llm(instance: ProviderInstance):
    from config.diagnostics import test_llm_connection
    result = await test_llm_connection(
        api_key=instance.api_key,
        api_base=instance.api_base,
        model_name=instance.model_name,
        provider=instance.provider,
    )
    return result.to_dict()


def _workspace_root() -> Path:
    return get_config_service().config.workspace_path


def _get_project_or_404(project_id: str) -> Project:
    pid = str(project_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="project_id is required")

    root = _workspace_root()
    project_path = root / pid
    if not project_path.exists() or not project_path.is_dir():
        raise HTTPException(status_code=404, detail=f"project not found: {pid}")
    return Project(pid, root)


def _validate_cron(cron_expr: str, timezone: str) -> None:
    cron_expr = str(cron_expr or "").strip()
    timezone = str(timezone or "UTC").strip() or "UTC"
    if not cron_expr:
        raise HTTPException(status_code=400, detail="schedule.cron is required")
    parts = [p for p in cron_expr.split(" ") if p]
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail="invalid cron: expected 5 fields")
    if not timezone:
        raise HTTPException(status_code=400, detail="schedule.timezone is required")


async def _maybe_reschedule(project: Project) -> Optional[Dict[str, Any]]:
    global automation_runtime
    if not automation_runtime:
        return {"warning": "runtime_not_ready"}
    try:
        await automation_runtime.reschedule_project(project)
    except Exception as e:
        logger.warning(f"reschedule failed for {project.id}: {e}")
        return {"warning": f"reschedule_failed: {e}"}
    return None


def _parse_iso_time(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _extract_line_value(content: str, key: str) -> str:
    prefix = f"{key}:"
    for line in (content or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _parse_run_entry(entry: dict[str, Any], store: ProjectMemoryStore) -> dict[str, Any]:
    mem_id = str(entry.get("id", "")).strip()
    scope = str(entry.get("scope", "")).strip()
    job_id = scope.split(":", 1)[1] if scope.startswith("job:") else ""
    payload = store.get(mem_id) if mem_id else None
    content = str((payload or {}).get("content", ""))

    started_at = _extract_line_value(content, "Started") or str(entry.get("created_at", "")).strip()
    ended_at = _extract_line_value(content, "Ended") or str(entry.get("updated_at", "")).strip()
    status_raw = _extract_line_value(content, "Status")
    if not status_raw:
        # Fallback: check tags for status:xxx
        tags = entry.get("tags", []) if isinstance(entry.get("tags"), list) else []
        for tag in tags:
            if str(tag).startswith("status:"):
                status_raw = str(tag).split(":", 1)[1]
                break
    trigger = _extract_line_value(content, "Trigger")
    run_id = _extract_line_value(content, "Run ID")

    started_dt = _parse_iso_time(started_at)
    ended_dt = _parse_iso_time(ended_at)
    if started_dt and ended_dt and ended_dt >= started_dt:
        duration_seconds = int((ended_dt - started_dt).total_seconds())
    else:
        duration_seconds = 0

    # Build a short preview from summary (truncated)
    raw_summary = str(entry.get("summary", "")).strip()
    preview = raw_summary[:120] + ("..." if len(raw_summary) > 120 else "") if raw_summary else ""

    # Extract token usage and model info from content
    tokens_raw = _extract_line_value(content, "Tokens")
    model_raw = _extract_line_value(content, "Model")

    return {
        "id": mem_id,
        "job_id": job_id,
        "run_id": run_id,
        "trigger": trigger,
        "status": status_raw or "unknown",
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "title": entry.get("title", ""),
        "summary": raw_summary,
        "preview": preview,
        "tokens": tokens_raw or "",
        "model": model_raw or "",
    }


def _merge_job_with_state(job: AutomationJob, state: dict[str, Any]) -> dict[str, Any]:
    data = job.to_dict()
    run_count = _safe_int(state.get("run_count"), 0)
    total_duration_seconds = _safe_int(state.get("total_duration_seconds"), 0)
    last_duration_seconds = _safe_int(state.get("last_duration_seconds"), 0)

    avg_duration_seconds = int(total_duration_seconds / run_count) if run_count > 0 else 0
    data["runtime"] = {
        "last_started_at": str(state.get("last_started_at", "") or ""),
        "last_ended_at": str(state.get("last_ended_at", "") or ""),
        "last_run_at": str(state.get("last_run_at", "") or ""),
        "last_status": str(state.get("last_status", "") or ""),
        "run_count": run_count,
        "last_duration_seconds": last_duration_seconds,
        "total_duration_seconds": total_duration_seconds,
        "avg_duration_seconds": avg_duration_seconds,
        "consecutive_failures": _safe_int(state.get("consecutive_failures"), 0),
        "last_entry_id": str(state.get("last_entry_id", "") or ""),
        "last_total_tokens": _safe_int(state.get("last_total_tokens"), 0),
        "total_tokens": _safe_int(state.get("total_tokens"), 0),
        "last_model_name": str(state.get("last_model_name", "") or ""),
        "last_provider_id": str(state.get("last_provider_id", "") or ""),
    }
    return data


@app.get("/api/projects")
async def list_projects():
    service = get_config_service()
    root = service.config.workspace_path
    rows: list[dict[str, Any]] = []

    if not root.exists():
        return rows

    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        if path.name == "Default":
            continue
        try:
            project = Project(path.name, root)
            has_automation = bool(project.config.automation and project.config.automation.enabled)
            rows.append(
                {
                    "id": project.id,
                    "name": project.id,
                    "hasAutomation": has_automation,
                }
            )
        except Exception:
            continue

    return rows


@app.post("/api/projects/refresh")
async def refresh_projects():
    global automation_runtime
    if not automation_runtime:
        raise HTTPException(status_code=503, detail="runtime_not_ready")
    try:
        summary = await automation_runtime.refresh_workspace_projects()
        return {"ok": True, **summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"refresh projects failed: {e}")


@app.get("/api/debug/scheduler")
async def debug_scheduler():
    global automation_runtime
    if not automation_runtime:
        return {"error": "runtime_not_ready"}
    diag = automation_runtime.scheduler.get_diagnostics()
    diag["project_job_keys"] = {
        k: sorted(v) for k, v in automation_runtime._project_job_keys.items()
    }
    return diag


@app.get("/api/projects/{pid}/jobs")
async def list_project_jobs(pid: str):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    rows: list[dict[str, Any]] = []
    for job in store.list_jobs():
        state = store.get_job_state(job.id)
        rows.append(_merge_job_with_state(job, state))
    return rows


@app.get("/api/projects/{pid}/runs")
async def list_project_runs(pid: str, job_id: Optional[str] = None, limit: int = 50):
    project = _get_project_or_404(pid)
    memory_store = ProjectMemoryStore(project)
    cap = max(1, min(int(limit), 200))

    if job_id:
        payload = memory_store.list_by_scope(scope=f"job:{job_id}", kind="job_run", limit=cap)
        entries = payload.get("items", [])
    else:
        entries = memory_store.list_recent_entries(kind="job_run", limit=cap)

    rows = [_parse_run_entry(entry, memory_store) for entry in entries if isinstance(entry, dict)]
    rows.sort(key=lambda x: str(x.get("started_at", "")), reverse=True)
    return rows


@app.get("/api/projects/{pid}/runs/{run_id}")
async def get_project_run_detail(pid: str, run_id: str):
    project = _get_project_or_404(pid)
    memory_store = ProjectMemoryStore(project)
    item = memory_store.get(run_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    if str(item.get("kind", "")).strip() != "job_run":
        raise HTTPException(status_code=400, detail=f"memory entry is not a job run: {run_id}")

    parsed = _parse_run_entry(item, memory_store)
    parsed["content"] = item.get("content", "")
    parsed["scope"] = item.get("scope", "")
    parsed["source"] = item.get("source", "")
    return parsed


@app.delete("/api/projects/{pid}/runs/{run_id}")
async def delete_project_run(pid: str, run_id: str):
    project = _get_project_or_404(pid)
    memory_store = ProjectMemoryStore(project)
    item = memory_store.get(run_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    if str(item.get("kind", "")).strip() != "job_run":
        raise HTTPException(status_code=400, detail=f"memory entry is not a job run: {run_id}")
    if not memory_store.delete(run_id):
        raise HTTPException(status_code=400, detail=f"delete run failed: {run_id}")
    return {"ok": True, "deleted": run_id}


@app.post("/api/projects/{pid}/jobs")
async def create_project_job(pid: str, payload: JobCreatePayload):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)

    job_id = str(payload.id or "").strip()
    name = str(payload.name or "").strip()
    job_type = str(payload.type or "normal").strip().lower()
    prompt = str(payload.prompt or "").strip()
    cron = str(payload.schedule.cron or "").strip()
    timezone = str(payload.schedule.timezone or "UTC").strip() or "UTC"

    if not job_id:
        raise HTTPException(status_code=400, detail="id is required")
    if store.get_job(job_id):
        raise HTTPException(status_code=409, detail=f"job already exists: {job_id}")
    if job_type not in SUPPORTED_JOB_TYPES:
        raise HTTPException(status_code=400, detail=f"invalid type: {job_type}")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    _validate_cron(cron, timezone)

    job = AutomationJob(
        id=job_id,
        name=name or job_id,
        type=job_type,
        schedule=JobSchedule(cron=cron, timezone=timezone),
        prompt=prompt,
        enabled=bool(payload.enabled),
        managed_by="user",
        frozen=False,
        output_policy=OutputPolicy(mode="default"),
        metadata={"origin": "user"},
    )
    store.upsert_job(job)
    warning = await _maybe_reschedule(project)
    return {"ok": True, "job": job.to_dict(), **(warning or {})}


@app.put("/api/projects/{pid}/jobs/{job_id}")
async def update_project_job(pid: str, job_id: str, payload: JobUpdatePayload):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")

    if payload.name is not None:
        name = str(payload.name).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        job.name = name
    if payload.type is not None:
        new_type = str(payload.type).strip().lower()
        if new_type not in SUPPORTED_JOB_TYPES:
            raise HTTPException(status_code=400, detail=f"invalid type: {new_type}")
        job.type = new_type
    if payload.enabled is not None:
        job.enabled = bool(payload.enabled)
    if payload.frozen is not None:
        job.frozen = bool(payload.frozen)
    if payload.prompt is not None:
        prompt = str(payload.prompt).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt cannot be empty")
        job.prompt = prompt
    if payload.schedule is not None:
        cron = str(payload.schedule.cron or "").strip()
        timezone = str(payload.schedule.timezone or "UTC").strip() or "UTC"
        _validate_cron(cron, timezone)
        job.schedule = JobSchedule(cron=cron, timezone=timezone)

    store.upsert_job(job)
    warning = await _maybe_reschedule(project)
    return {"ok": True, "job": job.to_dict(), **(warning or {})}


@app.delete("/api/projects/{pid}/jobs/{job_id}")
async def delete_project_job(pid: str, job_id: str):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    if str(job.id).strip() == "radar.autoplan":
        raise HTTPException(status_code=403, detail="core job radar.autoplan cannot be deleted")

    ok = store.delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to delete job")
    warning = await _maybe_reschedule(project)
    return {"ok": True, **(warning or {})}


@app.post("/api/projects/{pid}/jobs/{job_id}/run")
async def run_project_job_now(pid: str, job_id: str):
    global automation_runtime
    project = _get_project_or_404(pid)

    if not automation_runtime:
        raise HTTPException(status_code=503, detail="runtime_not_ready")

    result = await automation_runtime.run_job_now(project.id, job_id, trigger="manual_web")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "run_failed"))
    return {"ok": True, "run": result.get("run", {})}


@app.post("/api/projects/{pid}/bootstrap")
async def bootstrap_project_jobs(pid: str, mode: str = "merge"):
    from core.automation.store_fs import FSAutomationStore
    from core.automation.radar_defaults import apply_default_radar_jobs

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    replace_mode = str(mode or "merge").strip().lower() in {"replace", "reset"}
    applied = apply_default_radar_jobs(
        store,
        overwrite_existing=True,
        disable_other_system_radar_jobs=replace_mode,
    )
    warning = await _maybe_reschedule(project)
    return {"ok": True, "mode": "replace" if replace_mode else "merge", "applied": applied, **(warning or {})}


@app.post("/api/projects/{pid}/freeze-all-autoplan")
async def freeze_all_autoplan_jobs(pid: str):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)

    frozen_ids: list[str] = []
    for job in store.list_jobs():
        origin = str((job.metadata or {}).get("origin", "")).strip().lower()
        if origin != "autoplan":
            continue
        if job.frozen:
            continue
        job.frozen = True
        store.upsert_job(job)
        frozen_ids.append(job.id)

    warning = await _maybe_reschedule(project)
    return {"ok": True, "count": len(frozen_ids), "job_ids": frozen_ids, **(warning or {})}


@app.get("/api/projects/{pid}/subscriptions")
async def list_project_subscriptions(pid: str):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    raw = store.get_subscriptions()
    rows: list[dict[str, str]] = []
    for channel, chat_ids in sorted(raw.items()):
        for chat_id in chat_ids:
            rows.append({"channel": channel, "chat_id": chat_id})
    return {"items": rows, "raw": raw}


@app.post("/api/projects/{pid}/subscriptions")
async def add_project_subscription(pid: str, payload: SubscriptionPayload):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    channel = str(payload.channel or "").strip()
    chat_id = str(payload.chat_id or "").strip()
    if not channel or not chat_id:
        raise HTTPException(status_code=400, detail="channel and chat_id are required")
    store.add_subscription(channel, chat_id)
    return {"ok": True}


@app.delete("/api/projects/{pid}/subscriptions")
async def remove_project_subscription(pid: str, channel: str, chat_id: str):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    channel = str(channel or "").strip()
    chat_id = str(chat_id or "").strip()
    if not channel or not chat_id:
        raise HTTPException(status_code=400, detail="channel and chat_id are required")
    store.remove_subscription(channel, chat_id)
    return {"ok": True}


class LinkedSubscriptionIdsPayload(BaseModel):
    ids: list[str] = Field(default_factory=list)


@app.get("/api/projects/{pid}/subscriptions/linked")
async def get_linked_subscriptions(pid: str):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    return {"ids": store.get_linked_subscription_ids()}


@app.put("/api/projects/{pid}/subscriptions/linked")
async def set_linked_subscriptions(pid: str, payload: LinkedSubscriptionIdsPayload):
    from core.automation.store_fs import FSAutomationStore

    project = _get_project_or_404(pid)
    store = FSAutomationStore(project)
    cleaned = [str(i).strip() for i in (payload.ids or []) if str(i).strip()]
    store.set_linked_subscription_ids(cleaned)
    return {"ok": True, "ids": store.get_linked_subscription_ids()}


@app.post("/api/config/test-im")
async def test_im(account: ChannelAccount):
    from config.diagnostics import test_im_connection
    # Convert credential keys from camelCase to snake_case for test function
    converted_credentials = convert_keys(account.credentials)
    result = await test_im_connection(account.platform, converted_credentials)
    return result.to_dict()

# --- Token Usage Analytics ---

def _iter_session_traj_dirs(project_root: Path):
    """Yield trajectory directories for all sessions in a project."""
    for session_dir in project_root.iterdir():
        if not session_dir.is_dir() or session_dir.name.startswith("."):
            continue
        if session_dir.name == project_root.name or session_dir.name == "project.yaml":
            continue
        traj_dir = session_dir / ".bot" / "memory" / "trajectories"
        if traj_dir.exists():
            yield traj_dir


def _scan_trajectory_files(project_root: Path, days: int = 30) -> list:
    """Read token usage from index files (fast), fallback to full JSON scan for old data."""
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    indexed_dirs = set()

    # Pass 1: read token_usage.jsonl index files (fast path)
    for traj_dir in _iter_session_traj_dirs(project_root):
        index_file = traj_dir / "token_usage.jsonl"
        if not index_file.exists():
            continue
        indexed_dirs.add(str(traj_dir))
        try:
            for line in index_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    ts_str = str(r.get("timestamp", ""))[:19]
                    ts = datetime.fromisoformat(ts_str)
                    if ts < cutoff:
                        continue
                    r["date"] = ts.strftime("%Y-%m-%d")
                    r["timestamp"] = ts.isoformat()
                    records.append(r)
                except Exception:
                    continue
        except Exception:
            continue

    # Pass 2: fallback full JSON scan for dirs without index
    for traj_dir in _iter_session_traj_dirs(project_root):
        if str(traj_dir) in indexed_dirs:
            continue
        for traj_file in traj_dir.glob("turn_*.json"):
            try:
                data = json.loads(traj_file.read_text(encoding="utf-8"))
                token_usage = data.get("token_usage")
                if not token_usage:
                    continue
                ts_str = str(data.get("timestamp", ""))[:19]
                ts = datetime.fromisoformat(ts_str)
                if ts < cutoff:
                    continue
                records.append({
                    "timestamp": ts.isoformat(),
                    "date": ts.strftime("%Y-%m-%d"),
                    "session_id": data.get("session_id", ""),
                    "role": data.get("role", ""),
                    "mode": data.get("mode", ""),
                    "prompt_tokens": int(token_usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(token_usage.get("completion_tokens", 0)),
                    "total_tokens": int(token_usage.get("total_tokens", 0)),
                    "inbound": (str(data.get("inbound", "") or ""))[:80],
                    "duration_ms": data.get("duration_ms", 0),
                })
            except Exception:
                continue

    records.sort(key=lambda r: r["timestamp"], reverse=True)
    return records


@app.get("/api/projects/{pid}/token-usage")
async def get_token_usage(pid: str, days: int = 30):
    """Return per-turn token usage records and daily/session aggregations."""
    project = _get_project_or_404(pid)
    root = _workspace_root()
    project_root = root / pid

    cap_days = max(1, min(int(days), 365))
    records = _scan_trajectory_files(project_root, cap_days)

    # Aggregate by date / session
    by_date = {}
    by_session = {}
    grand_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "turns": 0}

    for r in records:
        for bucket_key, bucket in [(r["date"], by_date), (r.get("session_id") or "unknown", by_session)]:
            if bucket_key not in bucket:
                bucket[bucket_key] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "turns": 0}
            bucket[bucket_key]["prompt_tokens"] += r.get("prompt_tokens", 0)
            bucket[bucket_key]["completion_tokens"] += r.get("completion_tokens", 0)
            bucket[bucket_key]["total_tokens"] += r.get("total_tokens", 0)
            bucket[bucket_key]["turns"] += 1

        grand_total["prompt_tokens"] += r.get("prompt_tokens", 0)
        grand_total["completion_tokens"] += r.get("completion_tokens", 0)
        grand_total["total_tokens"] += r.get("total_tokens", 0)
        grand_total["turns"] += 1

    return {
        "project_id": pid,
        "days": cap_days,
        "total": grand_total,
        "by_date": [{"date": k, **v} for k, v in sorted(by_date.items(), reverse=True)],
        "by_session": [{"session_id": k, **v} for k, v in sorted(by_session.items(), reverse=True)],
        "records": records[:200],
    }


# --- WebSocket for Logs ---

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- Static UI ---

from pathlib import Path
static_dir = Path(__file__).parent.parent.parent / "static" / "ui"
if static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")
    logger.info(f"Mounted Web UI from {static_dir}")
else:
    logger.warning(f"Web UI directory not found at {static_dir}")

def start_gateway_server(host: str = "127.0.0.1", port: int = 18790):
    import uvicorn
    logger.info(f"Starting Gateway Server on http://{host}:{port}/ui")
    uvicorn.run(app, host=host, port=port)
