"""Real connectivity tests for LLM providers and IM platforms."""

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger


@dataclass
class TestResult:
    """Result of a connectivity test."""
    ok: bool
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {"status": "success" if self.ok else "error", "message": self.message, "detail": self.detail}


# ── LLM Tests ────────────────────────────────────────────────

async def test_llm_connection(
    api_key: str,
    api_base: Optional[str] = None,
    model_name: str = "gpt-4o",
    provider: str = "openai",
    timeout: float = 15.0,
) -> TestResult:
    """
    Test LLM connectivity by sending a minimal chat completion request.
    Works with any OpenAI-compatible API (OpenAI, DeepSeek, Step, OpenRouter, vLLM, etc.)
    and Anthropic's native API.
    """
    if not api_key or api_key.strip() == "":
        return TestResult(ok=False, message="API Key 为空")

    provider_name = (provider or "openai").lower().strip()
    resolved_api_base = (api_base or "").strip() or None

    provider_default_bases = {
        "openrouter": "https://openrouter.ai/api/v1",
        "step": "https://api.stepfun.com/v1",
        "deepseek": "https://api.deepseek.com",
    }

    if not resolved_api_base:
        resolved_api_base = provider_default_bases.get(provider_name)

    try:
        if provider_name == "anthropic" and not resolved_api_base:
            return await _test_anthropic_native(api_key, model_name, timeout)
        else:
            return await _test_openai_compatible(api_key, resolved_api_base, model_name, timeout)
    except httpx.TimeoutException:
        return TestResult(ok=False, message="连接超时", detail=f"超过 {timeout}s 未响应")
    except httpx.ConnectError as e:
        return TestResult(ok=False, message="无法连接到服务器", detail=str(e))
    except Exception as e:
        return TestResult(ok=False, message=f"测试失败: {type(e).__name__}", detail=str(e))


async def _test_openai_compatible(
    api_key: str, api_base: Optional[str], model_name: str, timeout: float
) -> TestResult:
    """Test OpenAI-compatible endpoints (covers DeepSeek, Step, OpenRouter, vLLM, etc.)."""
    base = (api_base or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
            },
        )

    if resp.status_code == 200:
        data = resp.json()
        actual_model = data.get("model", model_name)
        return TestResult(ok=True, message=f"连接成功 ({actual_model})")
    elif resp.status_code == 401:
        return TestResult(ok=False, message="API Key 无效 (401 Unauthorized)")
    elif resp.status_code == 403:
        return TestResult(ok=False, message="访问被拒绝 (403 Forbidden)")
    elif resp.status_code == 404:
        return TestResult(ok=False, message=f"模型 {model_name} 不存在 (404)", detail=resp.text[:200])
    elif resp.status_code == 429:
        # Rate limited but key is valid
        return TestResult(ok=True, message=f"Key 有效 (429 限流中，但认证通过)")
    else:
        return TestResult(ok=False, message=f"HTTP {resp.status_code}", detail=resp.text[:300])


async def _test_anthropic_native(api_key: str, model_name: str, timeout: float) -> TestResult:
    """Test Anthropic's native API."""
    url = "https://api.anthropic.com/v1/messages"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

    if resp.status_code == 200:
        return TestResult(ok=True, message=f"连接成功 ({model_name})")
    elif resp.status_code == 401:
        return TestResult(ok=False, message="API Key 无效 (401)")
    elif resp.status_code == 429:
        return TestResult(ok=True, message="Key 有效 (429 限流中，但认证通过)")
    else:
        return TestResult(ok=False, message=f"HTTP {resp.status_code}", detail=resp.text[:300])


# ── IM Tests ─────────────────────────────────────────────────

async def test_feishu_connection(app_id: str, app_secret: str, timeout: float = 10.0) -> TestResult:
    """Test Feishu credentials by fetching a tenant_access_token."""
    if not app_id or not app_secret:
        return TestResult(ok=False, message="App ID 或 App Secret 为空")

    try:
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json={"app_id": app_id, "app_secret": app_secret},
            )
        data = resp.json()
        if data.get("code") == 0:
            return TestResult(ok=True, message="飞书认证成功")
        else:
            return TestResult(ok=False, message=f"飞书认证失败: {data.get('msg', 'unknown')}")
    except httpx.TimeoutException:
        return TestResult(ok=False, message="飞书 API 连接超时")
    except Exception as e:
        return TestResult(ok=False, message=f"飞书测试出错: {e}")


async def test_telegram_connection(token: str, timeout: float = 10.0) -> TestResult:
    """Test Telegram bot token by calling getMe."""
    if not token:
        return TestResult(ok=False, message="Bot Token 为空")

    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        data = resp.json()
        if data.get("ok"):
            bot_name = data["result"].get("username", "unknown")
            return TestResult(ok=True, message=f"Telegram Bot @{bot_name} 验证成功")
        else:
            return TestResult(ok=False, message=f"Token 无效: {data.get('description', 'unknown')}")
    except httpx.TimeoutException:
        return TestResult(ok=False, message="Telegram API 连接超时 (可能需要代理)")
    except Exception as e:
        return TestResult(ok=False, message=f"Telegram 测试出错: {e}")


async def test_qq_connection(app_id: str, app_secret: str, timeout: float = 10.0) -> TestResult:
    """Test QQ bot credentials (NapCat/LLOneBot etc.)."""
    if not app_id or not app_secret:
        return TestResult(ok=False, message="App ID 或 App Secret 为空")

    try:
        # NapCat/LLOneBot typically use webhook or API endpoint
        # This is a simple format validation
        if len(app_id) < 3 or len(app_secret) < 8:
            return TestResult(ok=False, message="凭据格式无效 (过短)")
        return TestResult(ok=True, message="QQ 凭据格式有效 (需实际消息测试)")
    except Exception as e:
        return TestResult(ok=False, message=f"QQ 测试出错: {e}")


async def test_dingtalk_connection(client_id: str, client_secret: str, timeout: float = 10.0) -> TestResult:
    """Test DingTalk credentials by fetching access token."""
    if not client_id or not client_secret:
        return TestResult(ok=False, message="Client ID 或 Client Secret 为空")

    try:
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json={"appKey": client_id, "appSecret": client_secret},
            )
        if resp.status_code != 200:
            return TestResult(ok=False, message=f"DingTalk HTTP {resp.status_code}", detail=resp.text[:300])
        data = resp.json()
        if data.get("accessToken"):
            return TestResult(ok=True, message="钉钉认证成功")
        return TestResult(ok=False, message=f"钉钉认证失败: {data}")
    except httpx.TimeoutException:
        return TestResult(ok=False, message="钉钉 API 连接超时")
    except Exception as e:
        return TestResult(ok=False, message=f"钉钉测试出错: {e}")


async def test_im_connection(platform: str, credentials: dict, timeout: float = 10.0) -> TestResult:
    """Dispatch IM test based on platform."""
    if platform == "feishu":
        return await test_feishu_connection(
            credentials.get("app_id", ""),
            credentials.get("app_secret", ""),
            timeout,
        )
    elif platform == "telegram":
        return await test_telegram_connection(credentials.get("token", ""), timeout)
    elif platform == "qq":
        return await test_qq_connection(
            credentials.get("app_id", ""),
            credentials.get("app_secret", ""),
            timeout,
        )
    elif platform == "dingtalk":
        return await test_dingtalk_connection(
            credentials.get("client_id", credentials.get("clientId", "")),
            credentials.get("client_secret", credentials.get("clientSecret", "")),
            timeout,
        )
    else:
        return TestResult(ok=False, message=f"不支持的平台: {platform}")


def _get_overleaf_instance_name() -> str:
    """Return a human-readable name for the configured Overleaf instance."""
    try:
        from config.loader import get_config_service
        base_url = get_config_service().config.overleaf.base_url
        if not base_url:
            return "Overleaf (not configured)"
        if "cstcloud" in base_url:
            return "CSTCloud"
        if "overleaf.com" in base_url:
            return "Overleaf"
        return base_url
    except Exception:
        return "Overleaf"


# ── Full diagnostics ─────────────────────────────────────────

async def run_all_diagnostics(config) -> list[dict]:
    """Run a comprehensive system check."""
    results = []

    # 1. LLM check
    active = config.get_active_provider()
    if active and active.api_key:
        r = await test_llm_connection(
            api_key=active.api_key,
            api_base=active.api_base,
            model_name=active.model_name,
            provider=active.provider,
        )
        results.append({"name": f"LLM ({active.provider}/{active.model_name})", **r.to_dict()})
    else:
        results.append({"name": "LLM", "status": "error", "message": "未配置活跃 LLM 实例"})

    # 2. Channel checks
    if config.channel.accounts:
        for acc in config.channel.accounts:
            if acc.enabled:
                r = await test_im_connection(acc.platform, acc.credentials)
                results.append({"name": f"Channel ({acc.platform}/{acc.id})", **r.to_dict()})
    else:
        results.append({"name": "Channel", "status": "skip", "message": "未配置 Channel 账户 (仅 CLI 模式)"})

    # 3. Workspace
    import os
    ws = config.workspace_path
    if ws.exists() and os.access(ws, os.W_OK):
        results.append({"name": "工作区", "status": "success", "message": f"{ws} 可写"})
    elif ws.exists():
        results.append({"name": "工作区", "status": "error", "message": f"{ws} 不可写"})
    else:
        results.append({"name": "工作区", "status": "warning", "message": f"{ws} 不存在 (将自动创建)"})

    # 4. Overleaf
    try:
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        olauth_candidates = [
            repo_root / ".olauth",
            Path.home() / ".olauth",
        ]
        olauth_found = next((p for p in olauth_candidates if p.exists()), None)
        instance_name = _get_overleaf_instance_name()
        if olauth_found:
            results.append({"name": "Overleaf", "status": "success", "message": f"已登录 — {instance_name} ({olauth_found})"})
        else:
            results.append({"name": "Overleaf", "status": "skip", "message": "未登录 (运行 python cli/main.py login)"})
    except Exception:
        results.append({"name": "Overleaf", "status": "skip", "message": "检测跳过"})

    return results


def is_overleaf_logged_in() -> bool:
    """Check whether a valid .olauth cookie file exists."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [repo_root / ".olauth", Path.home() / ".olauth"]
    return any(p.exists() for p in candidates)


def get_overleaf_status_snippet(project=None) -> str:
    """Build a short Overleaf status text for system prompt injection.

    Returns an empty string when there is nothing useful to report.
    """
    from pathlib import Path
    logged_in = is_overleaf_logged_in()

    lines = []
    if logged_in:
        lines.append("Overleaf login: active (.olauth found)")
    else:
        lines.append(
            "Overleaf login: not found. "
            "To enable Overleaf sync, run "
            "'python cli/main.py login' on the server."
        )

    if project and not getattr(project, "is_default", True):
        cfg = getattr(project, "config", None)
        ol_cfg = getattr(cfg, "overleaf", None) if cfg else None
        has_link = bool(ol_cfg and getattr(ol_cfg, "project_id", None))
        if has_link:
            if logged_in:
                lines.append(
                    "This project is linked to Overleaf. "
                    "Use /sync pull to get the latest changes."
                )
            else:
                lines.append(
                    "This project has an Overleaf link but .olauth is missing. "
                    "Sync will fail until login."
                )
        else:
            lines.append("This project is not linked to Overleaf.")

    return "\n".join(lines)


def run_diagnostics_sync(config) -> list[dict]:
    """Synchronous wrapper for diagnostics."""
    return asyncio.run(run_all_diagnostics(config))
