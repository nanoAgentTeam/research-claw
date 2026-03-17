import sys
import asyncio
import time
from typing import Optional, Dict
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.spinner import Spinner
from rich.live import Live
from config.loader import get_config_service
from config.schema import ProviderInstance, ChannelAccount

console = Console()


def _run_async(coro):
    """Run an async function from sync context."""
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _test_llm_interactive(instance: ProviderInstance) -> bool:
    """Run LLM connection test with spinner, return TestResult."""
    from config.diagnostics import test_llm_connection
    with Live(Spinner("dots", text=f" 正在验证 {instance.provider}/{instance.model_name}..."), console=console, transient=True):
        result = _run_async(test_llm_connection(
            api_key=instance.api_key,
            api_base=instance.api_base,
            model_name=instance.model_name,
            provider=instance.provider,
        ))
    if result.ok:
        console.print(f"  [green]✓ {result.message}[/green]")
    else:
        console.print(f"  [red]✗ {result.message}[/red]")
        if result.detail:
            console.print(f"    [dim]{result.detail}[/dim]")
    return result


def _test_im_interactive(platform: str, credentials: dict) -> bool:
    """Run IM connection test with spinner, return TestResult."""
    from config.diagnostics import test_im_connection
    with Live(Spinner("dots", text=f" 正在验证 {platform} 凭据..."), console=console, transient=True):
        result = _run_async(test_im_connection(platform, credentials))
    if result.ok:
        console.print(f"  [green]✓ {result.message}[/green]")
    else:
        console.print(f"  [red]✗ {result.message}[/red]")
        if result.detail:
            console.print(f"    [dim]{result.detail}[/dim]")
    return result

# ── Provider presets ──────────────────────────────────────────
PROVIDER_PRESETS = {
    "Anthropic": {
        "group": "Anthropic",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022"],
        "default_model": "claude-sonnet-4-20250514",
        "api_base": None,
    },
    "OpenAI": {
        "group": "OpenAI",
        "models": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
        "default_model": "gpt-4o",
        "api_base": None,
    },
    "DeepSeek": {
        "group": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "api_base": "https://api.deepseek.com",
    },
    "Gemini": {
        "group": "Google",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
        "default_model": "gemini-2.5-pro",
        "api_base": None,
    },
    "Step (阶跃星辰)": {
        "group": "Step",
        "models": ["step-3.5-flash", "step-2-16k"],
        "default_model": "step-3.5-flash",
        "api_base": "https://api.stepfun.com/v1",
    },
    "OpenRouter": {
        "group": "OpenRouter",
        "models": ["anthropic/claude-sonnet-4", "openai/gpt-4o", "google/gemini-2.5-pro"],
        "default_model": "anthropic/claude-sonnet-4",
        "api_base": "https://openrouter.ai/api/v1",
    },
}


def _ask_or_abort(question):
    """Ask a questionary question and abort if user cancels (Ctrl+C)."""
    result = question.ask()
    if result is None:
        console.print("\n[yellow]已取消。[/yellow]")
        sys.exit(0)
    return result


def _mask_secret(value: str) -> str:
    if not value:
        return "(空)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-2:]


def _status_badge(status: Optional[str]) -> str:
    if status == "ok":
        return "[green]已连通[/green]"
    if status == "auth":
        return "[red]认证失败[/red]"
    if status == "network":
        return "[yellow]网络异常[/yellow]"
    if status == "model":
        return "[magenta]模型错误[/magenta]"
    if status == "fail":
        return "[red]未连通[/red]"
    if status == "skip":
        return "[yellow]未测试[/yellow]"
    return "[dim]-[/dim]"


def _classify_test_failure(message: str) -> str:
    text = (message or "").lower()
    if "401" in text or "403" in text or "无效" in text or "认证失败" in text or "访问被拒绝" in text:
        return "auth"
    if "超时" in text or "无法连接" in text or "连接" in text:
        return "network"
    if "模型" in text and "不存在" in text:
        return "model"
    return "fail"


def _upsert_provider(config, instance: ProviderInstance):
    existing_ids = [item.id for item in config.provider.instances]
    if instance.id in existing_ids:
        config.provider.instances = [instance if item.id == instance.id else item for item in config.provider.instances]
    else:
        config.provider.instances.append(instance)


def _upsert_channel(config, account: ChannelAccount):
    existing_ids = [item.id for item in config.channel.accounts]
    if account.id in existing_ids:
        config.channel.accounts = [account if item.id == account.id else item for item in config.channel.accounts]
    else:
        config.channel.accounts.append(account)


# ── Status display ────────────────────────────────────────────

def _show_current_config(config):
    """Display a compact summary of current configuration."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value")

    # LLM
    active = config.get_active_provider()
    if active and active.api_key:
        key_preview = active.api_key[:8] + "..." if len(active.api_key) > 8 else "***"
        table.add_row("LLM", f"[green]✓[/green] {active.provider}/{active.model_name} ({key_preview})")
    elif active:
        table.add_row("LLM", f"[yellow]![/yellow] {active.provider}/{active.model_name} (未设置 Key)")
    else:
        table.add_row("LLM", "[red]✗ 未配置[/red]")

    # Channel
    if config.channel.accounts:
        for acc in config.channel.accounts:
            status = "[green]✓[/green]" if acc.enabled else "[dim]○[/dim]"
            table.add_row("IM", f"{status} {acc.platform} ({acc.id})")
    else:
        table.add_row("IM", "[dim]○ 未配置 (仅 CLI 可用)[/dim]")

    # Workspace
    table.add_row("工作区", str(config.workspace_path))

    # Overleaf
    try:
        from config.diagnostics import is_overleaf_logged_in
        if is_overleaf_logged_in():
            table.add_row("Overleaf", "[green]✓ 已登录 (.olauth)[/green]")
        else:
            table.add_row("Overleaf", "[dim]○ 未登录 (可选: 运行 ols login)[/dim]")
    except Exception:
        pass

    console.print(Panel(table, title="[bold]当前配置[/bold]", border_style="blue", expand=False))


# ── LLM configuration ────────────────────────────────────────

def _default_provider_instance() -> ProviderInstance:
    now = int(time.time() * 1000)
    return ProviderInstance(
        id=f"new-model-{now}",
        provider="openai",
        model_name="gpt-4o",
        api_key="",
        api_base="",
        enabled=True,
    )


def _choose_provider_instance(config, prompt_text: str) -> Optional[ProviderInstance]:
    if not config.provider.instances:
        console.print("  [yellow]暂无 Provider 实例[/yellow]")
        return None

    choices = [
        f"{item.id} | {item.provider}/{item.model_name} | {'启用' if item.enabled else '禁用'}"
        for item in config.provider.instances
    ]
    selected = _ask_or_abort(questionary.select(prompt_text, choices=choices + ["← 返回"]))
    if selected == "← 返回":
        return None
    selected_id = selected.split(" | ", 1)[0]
    return next((item for item in config.provider.instances if item.id == selected_id), None)


def _edit_provider_fields(existing: Optional[ProviderInstance] = None) -> Optional[ProviderInstance]:
    base = existing or _default_provider_instance()
    provider_type = _ask_or_abort(questionary.select(
        "Provider 类型:",
        choices=["anthropic", "openai", "gemini", "openrouter", "step", "deepseek", "自定义"],
        default=base.provider if base.provider in {"anthropic", "openai", "gemini", "openrouter", "step", "deepseek"} else "自定义",
    ))
    provider_name = base.provider if provider_type == "自定义" else provider_type
    if provider_type == "自定义":
        provider_name = _ask_or_abort(questionary.text("输入 provider 名称:", default=base.provider)).strip()
        if not provider_name:
            console.print("  [red]Provider 名称不能为空[/red]")
            return None

    instance_id = _ask_or_abort(questionary.text("实例 ID:", default=base.id)).strip()
    if not instance_id:
        console.print("  [red]实例 ID 不能为空[/red]")
        return None

    model_name = _ask_or_abort(questionary.text("模型名称:", default=base.model_name)).strip()
    if not model_name:
        console.print("  [red]模型名称不能为空[/red]")
        return None

    api_base_input = _ask_or_abort(questionary.text("API Base (可空):", default=base.api_base or "")).strip()
    api_key_input = _ask_or_abort(questionary.password("API Key (留空则保持原值):"))
    api_key = base.api_key if api_key_input == "" and existing is not None else api_key_input.strip()
    enabled = _ask_or_abort(questionary.confirm("是否启用该实例?", default=base.enabled))

    return ProviderInstance(
        id=instance_id,
        provider=provider_name,
        model_name=model_name,
        api_key=api_key,
        api_base=api_base_input,
        enabled=enabled,
    )


def _show_provider_table(config, test_status: Optional[Dict[str, str]] = None):
    test_status = test_status or {}
    table = Table(title="Provider 实例", expand=False)
    table.add_column("Active", justify="center")
    table.add_column("ID")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Enabled", justify="center")
    table.add_column("API Key")
    table.add_column("最近测试", justify="center")

    if not config.provider.instances:
        table.add_row("-", "(空)", "-", "-", "-", "-")
    else:
        for item in config.provider.instances:
            active = "✓" if config.provider.active_id == item.id else ""
            table.add_row(
                active,
                item.id,
                item.provider,
                item.model_name,
                "✓" if item.enabled else "○",
                _mask_secret(item.api_key),
                _status_badge(test_status.get(item.id)),
            )

    console.print(table)


def _configure_llm(config):
    """Manage provider instances (CRUD + active + test), aligned with Web UI behavior."""
    console.print("\n[bold blue]◆ 管理 Provider 模型实例[/bold blue]")
    provider_test_status: dict[str, str] = {}

    while True:
        console.print("[dim]提示: 建议先添加实例并填写 API Key，再执行“测试连接”后设为活跃。[/dim]")
        _show_provider_table(config, provider_test_status)
        action = _ask_or_abort(questionary.select(
            "Provider 操作:",
            choices=["添加实例", "编辑实例", "设为活跃", "测试连接", "删除实例", "← 返回"],
        ))

        if action == "← 返回":
            return

        if action == "添加实例":
            console.print("[dim]正在添加 Provider：可自定义实例 ID，建议用有语义的名称（如 step-prod）。[/dim]")
            instance = _edit_provider_fields(None)
            if instance is None:
                console.print("  [yellow]⚠ 已取消添加[/yellow]")
                continue
            provider_test_status[instance.id] = "skip"
            _upsert_provider(config, instance)
            if not config.provider.active_id:
                config.provider.active_id = instance.id
            console.print(f"  [green]✓ 已添加 Provider 实例: {instance.id}（状态：未测试）[/green]")
            continue

        target = _choose_provider_instance(config, f"选择要{action}的实例:")
        if target is None:
            continue

        if action == "编辑实例":
            console.print(f"[dim]正在编辑实例 {target.id}：留空 API Key 会保留原值。[/dim]")
            updated = _edit_provider_fields(target)
            if updated is None:
                console.print("  [yellow]⚠ 已取消编辑[/yellow]")
                continue
            old_id = target.id
            _upsert_provider(config, updated)
            if old_id != updated.id:
                config.provider.instances = [item for item in config.provider.instances if item.id != old_id]
                if old_id in provider_test_status:
                    provider_test_status[updated.id] = provider_test_status.pop(old_id)
            if config.provider.active_id == old_id:
                config.provider.active_id = updated.id
            console.print(f"  [green]✓ 已更新实例: {updated.id}[/green]")
        elif action == "设为活跃":
            config.provider.active_id = target.id
            if not target.enabled:
                console.print(f"  [yellow]⚠ {target.id} 当前是禁用状态，设为活跃后仍建议先启用并测试[/yellow]")
            console.print(f"  [green]✓ 已设为活跃: {target.id}[/green]")
        elif action == "测试连接":
            if not target.api_key:
                provider_test_status[target.id] = "fail"
                console.print("  [yellow]⚠ 该实例 API Key 为空，无法测试[/yellow]")
                continue
            result = _test_llm_interactive(target)
            if result.ok:
                provider_test_status[target.id] = "ok"
                console.print(f"  [green]✓ 测试完成：{target.id} 可用[/green]")
            else:
                category = _classify_test_failure(result.message)
                provider_test_status[target.id] = category
                console.print(f"  [red]✗ 测试完成：{target.id} 不可用[/red]")
                console.print(f"  [dim]判定依据: {result.message}[/dim]")
        elif action == "删除实例":
            confirm_delete = _ask_or_abort(questionary.confirm(f"确认删除实例 {target.id}?", default=False))
            if not confirm_delete:
                console.print("  [dim]已取消删除[/dim]")
                continue
            config.provider.instances = [item for item in config.provider.instances if item.id != target.id]
            provider_test_status.pop(target.id, None)
            if config.provider.active_id == target.id:
                config.provider.active_id = config.provider.instances[0].id if config.provider.instances else ""
            console.print(f"  [green]✓ 已删除实例: {target.id}[/green]")


# ── IM configuration ──────────────────────────────────────────

def _default_channel_credentials(platform: str, current: Optional[dict] = None) -> dict:
    current = current or {}
    if platform in ("feishu", "qq"):
        return {
            "app_id": current.get("app_id", ""),
            "app_secret": current.get("app_secret", ""),
        }
    if platform == "dingtalk":
        return {
            "client_id": current.get("client_id", current.get("clientId", "")),
            "client_secret": current.get("client_secret", current.get("clientSecret", "")),
            "robot_code": current.get("robot_code", current.get("robotCode", "")),
            "corp_id": current.get("corp_id", current.get("corpId", "")),
            "agent_id": current.get("agent_id", current.get("agentId", "")),
        }
    if platform == "telegram":
        return {"token": current.get("token", "")}
    if platform == "whatsapp":
        return {"bridge_url": current.get("bridge_url", "")}
    return current


def _default_channel_account(platform: str = "telegram") -> ChannelAccount:
    now = int(time.time() * 1000)
    return ChannelAccount(
        id=f"{platform}-{now}",
        platform=platform,
        enabled=True,
        credentials=_default_channel_credentials(platform),
    )


def _show_channel_table(config, test_status: Optional[Dict[str, str]] = None):
    test_status = test_status or {}
    table = Table(title="Channel 账户", expand=False)
    table.add_column("Active", justify="center")
    table.add_column("ID")
    table.add_column("Platform")
    table.add_column("Enabled", justify="center")
    table.add_column("Credentials")
    table.add_column("最近测试", justify="center")

    if not config.channel.accounts:
        table.add_row("-", "(空)", "-", "-", "-")
    else:
        for account in config.channel.accounts:
            active = "✓" if config.channel.active_id == account.id else ""
            credential_keys = ", ".join(account.credentials.keys()) if account.credentials else "(空)"
            table.add_row(
                active,
                account.id,
                account.platform,
                "✓" if account.enabled else "○",
                credential_keys,
                _status_badge(test_status.get(account.id)),
            )

    console.print(table)


def _choose_channel_account(config, prompt_text: str) -> Optional[ChannelAccount]:
    if not config.channel.accounts:
        console.print("  [yellow]暂无 Channel 账户[/yellow]")
        return None

    choices = [
        f"{item.id} | {item.platform} | {'启用' if item.enabled else '禁用'}"
        for item in config.channel.accounts
    ]
    selected = _ask_or_abort(questionary.select(prompt_text, choices=choices + ["← 返回"]))
    if selected == "← 返回":
        return None
    selected_id = selected.split(" | ", 1)[0]
    return next((item for item in config.channel.accounts if item.id == selected_id), None)


def _edit_channel_fields(existing: Optional[ChannelAccount] = None) -> Optional[ChannelAccount]:
    base = existing or _default_channel_account()
    platform = _ask_or_abort(questionary.select(
        "平台:",
        choices=["feishu", "telegram", "qq", "dingtalk", "whatsapp"],
        default=base.platform,
    ))
    account_id = _ask_or_abort(questionary.text("账户 ID:", default=base.id)).strip()
    if not account_id:
        console.print("  [red]账户 ID 不能为空[/red]")
        return None

    credentials = _default_channel_credentials(platform, base.credentials if base.platform == platform else None)
    updated_credentials = {}
    for key, value in credentials.items():
        if key in {"bridge_url", "client_id", "robot_code", "corp_id", "agent_id"}:
            new_value = _ask_or_abort(questionary.text(f"{key}:", default=value)).strip()
        else:
            prompt = f"{key} (留空则保持原值):" if existing else f"{key}:"
            entered = _ask_or_abort(questionary.password(prompt))
            if entered == "" and existing and base.platform == platform:
                new_value = value
            else:
                new_value = entered.strip()
        updated_credentials[key] = new_value

    enabled = _ask_or_abort(questionary.confirm("是否启用该账户?", default=base.enabled))

    return ChannelAccount(
        id=account_id,
        platform=platform,
        enabled=enabled,
        credentials=updated_credentials,
    )


def _configure_im(config):
    """Manage IM channel accounts (CRUD + active + test), aligned with Web UI behavior."""
    console.print("\n[bold blue]◆ 管理 IM 通讯账户[/bold blue]")
    channel_test_status: dict[str, str] = {}

    while True:
        console.print("[dim]提示: 先添加并填写凭据，再执行“测试凭据”；测试结果会显示在列表中。[/dim]")
        _show_channel_table(config, channel_test_status)
        action = _ask_or_abort(questionary.select(
            "Channel 操作:",
            choices=["添加账户", "编辑账户", "设为活跃", "测试凭据", "删除账户", "← 返回"],
        ))

        if action == "← 返回":
            return

        if action == "添加账户":
            console.print("[dim]正在添加 Channel 账户：ID 建议使用平台+用途（如 telegram-alert）。[/dim]")
            created = _edit_channel_fields(None)
            if created is None:
                console.print("  [yellow]⚠ 已取消添加[/yellow]")
                continue
            channel_test_status[created.id] = "skip"
            _upsert_channel(config, created)
            if not config.channel.active_id:
                config.channel.active_id = created.id
            console.print(f"  [green]✓ 已添加账户: {created.id}（状态：未测试）[/green]")
            continue

        target = _choose_channel_account(config, f"选择要{action}的账户:")
        if target is None:
            continue

        if action == "编辑账户":
            console.print(f"[dim]正在编辑账户 {target.id}：留空敏感字段会保留原值。[/dim]")
            updated = _edit_channel_fields(target)
            if updated is None:
                console.print("  [yellow]⚠ 已取消编辑[/yellow]")
                continue
            old_id = target.id
            _upsert_channel(config, updated)
            if old_id != updated.id:
                config.channel.accounts = [item for item in config.channel.accounts if item.id != old_id]
                if old_id in channel_test_status:
                    channel_test_status[updated.id] = channel_test_status.pop(old_id)
            if config.channel.active_id == old_id:
                config.channel.active_id = updated.id
            console.print(f"  [green]✓ 已更新账户: {updated.id}[/green]")
        elif action == "设为活跃":
            config.channel.active_id = target.id
            if not target.enabled:
                console.print(f"  [yellow]⚠ {target.id} 当前是禁用状态，设为活跃后仍建议先启用[/yellow]")
            console.print(f"  [green]✓ 已设为活跃: {target.id}[/green]")
        elif action == "测试凭据":
            result = _test_im_interactive(target.platform, target.credentials)
            if result.ok:
                channel_test_status[target.id] = "ok"
                console.print(f"  [green]✓ 测试完成：{target.id} 可用[/green]")
            else:
                category = _classify_test_failure(result.message)
                channel_test_status[target.id] = category
                console.print(f"  [red]✗ 测试完成：{target.id} 不可用[/red]")
                console.print(f"  [dim]判定依据: {result.message}[/dim]")
        elif action == "删除账户":
            confirm_delete = _ask_or_abort(questionary.confirm(f"确认删除账户 {target.id}?", default=False))
            if not confirm_delete:
                console.print("  [dim]已取消删除[/dim]")
                continue
            config.channel.accounts = [item for item in config.channel.accounts if item.id != target.id]
            channel_test_status.pop(target.id, None)
            if config.channel.active_id == target.id:
                config.channel.active_id = None
            console.print(f"  [green]✓ 已删除账户: {target.id}[/green]")


# ── Main wizard ───────────────────────────────────────────────

def run_wizard():
    """Run the interactive onboarding wizard with menu-driven flow."""
    console.print()
    console.print("[bold blue]┌  Open Research Claw 配置向导[/bold blue]")
    console.print("[blue]│[/blue]")

    service = get_config_service()
    config = service.config

    # Show current configuration status
    _show_current_config(config)

    # Menu loop — let user choose what to configure
    while True:
        console.print()
        action = _ask_or_abort(questionary.select(
            "您想做什么?",
            choices=[
                "管理 Provider 模型",
                "管理 IM 通讯账号",
                "─────────────────────────",
                "保存并启动网关 (Web UI + IM)",
                "保存并进入 Agent (仅 CLI)",
                "保存并退出",
            ],
        ))

        if action == "─────────────────────────":
            continue
        elif action == "管理 Provider 模型":
            _configure_llm(config)
            service.save()
            console.print("  [dim]配置已自动保存[/dim]")
        elif action == "管理 IM 通讯账号":
            _configure_im(config)
            service.save()
            console.print("  [dim]配置已自动保存[/dim]")
        elif action == "保存并启动网关 (Web UI + IM)":
            service.save()
            console.print("[blue]│[/blue]")
            console.print("[bold blue]└  配置完成！[/bold blue]")
            return "启动网关 (启动 Web UI 和 IM 机器人)"
        elif action == "保存并进入 Agent (仅 CLI)":
            service.save()
            console.print("[blue]│[/blue]")
            console.print("[bold blue]└  配置完成！[/bold blue]")
            return "进入交互式 Agent (仅 CLI 对话)"
        elif action == "保存并退出":
            service.save()
            console.print("[blue]│[/blue]")
            console.print("[bold blue]└  配置已保存，再见！[/bold blue]")
            return "退出"
