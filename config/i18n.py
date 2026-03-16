"""Centralized i18n module for non-LLM auto-reply messages."""
import json
from pathlib import Path
from typing import Any, Dict

_TRANSLATIONS = {}  # type: Dict[str, Dict[str, str]]
_FALLBACK = "en"


def _load():
    # type: () -> None
    global _TRANSLATIONS, _FALLBACK
    p = Path(__file__).parent / "i18n.json"
    if p.exists():
        data = json.loads(p.read_text("utf-8"))
        meta = data.pop("_meta", {})
        _FALLBACK = meta.get("fallback", "en")
        _TRANSLATIONS = data


def _get_lang():
    # type: () -> str
    try:
        from core.infra.config import Config
        return Config.get_reply_language()
    except Exception:
        return "en"


def t(key, **kwargs):
    # type: (str, **Any) -> str
    """Get translated message by key with optional format variables."""
    if not _TRANSLATIONS:
        _load()
    entry = _TRANSLATIONS.get(key)
    if not entry:
        return key  # key itself as fallback
    lang = _get_lang()
    # Lookup chain: exact → language prefix → fallback → first available
    text = (
        entry.get(lang)
        or entry.get(lang.split("-")[0])
        or entry.get(_FALLBACK)
        or next(iter(entry.values()), key)
    )
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


# Pre-load at import time
_load()
