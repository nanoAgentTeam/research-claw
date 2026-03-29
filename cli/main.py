"""CLI commands for Research Claw."""

import asyncio
import os
import sys
import signal
import subprocess
import time
import re
from pathlib import Path
from typing import Optional

# Add project root to sys.path
root_path = Path(__file__).resolve().parent.parent
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))

# Disable Langfuse globally to prevent initialization warnings and network issues
os.environ["DISABLE_LANGFUSE"] = "true"
os.environ["LANGFUSE_PUBLIC_KEY"] = "disabled"
os.environ["LANGFUSE_SECRET_KEY"] = "disabled"

import typer
from rich.console import Console

app = typer.Typer(
    name="open_research_claw",
    help="Research Claw - AI Research Assistant",
    no_args_is_help=True,
)

console = Console()
GATEWAY_PID_DIR = root_path / ".open_research_claw" / "gateway_pids"


def _pid_exists(pid: int) -> bool:
    """Return True if process exists and is signalable by current user."""
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Process exists but current context cannot signal it.
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _gateway_pid_file(pid: int) -> Path:
    return GATEWAY_PID_DIR / f"{pid}.pid"


def _register_gateway_pid(pid: int) -> None:
    try:
        GATEWAY_PID_DIR.mkdir(parents=True, exist_ok=True)
        _gateway_pid_file(pid).write_text(str(pid), encoding="utf-8")
    except Exception:
        pass


def _unregister_gateway_pid(pid: int) -> None:
    try:
        _gateway_pid_file(pid).unlink(missing_ok=True)
    except Exception:
        pass


def _collect_gateway_pids_from_pid_files() -> list[int]:
    if not GATEWAY_PID_DIR.exists():
        return []
    found: list[int] = []
    for pid_file in GATEWAY_PID_DIR.glob("*.pid"):
        try:
            text = pid_file.read_text(encoding="utf-8").strip()
            pid = int(text)
        except Exception:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass
            continue
        if _pid_exists(pid):
            found.append(pid)
        else:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass
    return found


def _is_gateway_process_cmd(command: str) -> bool:
    """
    Identify gateway processes started from this project.
    Supports both:
    - python cli/main.py gateway ...
    - open_research_claw gateway ...
    """
    normalized = command.strip()
    if re.search(r"\blegacy[-_]gateway\b", normalized):
        return False
    return (
        bool(re.search(r"\bcli/main\.py\b.*(?:^|\s)gateway(?:\s|$)", normalized))
        or bool(re.search(r"\bopen_research_claw\b.*(?:^|\s)gateway(?:\s|$)", normalized))
    )


def _find_gateway_processes() -> list[tuple[int, str]]:
    """Find running gateway process ids and command lines."""
    try:
        res = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    found: list[tuple[int, str]] = []
    current_pid = os.getpid()
    for raw in res.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        pid_s, cmd = parts
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == current_pid:
            continue
        if _is_gateway_process_cmd(cmd):
            found.append((pid, cmd))
    return found


def _stop_pid(pid: int, wait_seconds: float = 5.0) -> tuple[bool, str]:
    """
    Try to stop a process gracefully, then force if needed.
    Returns (stopped, detail_message).
    """
    if not _pid_exists(pid):
        return True, "already exited"

    stages = [
        (signal.SIGINT, wait_seconds, "SIGINT"),
        (signal.SIGTERM, 2.0, "SIGTERM"),
        (signal.SIGKILL, 1.0, "SIGKILL"),
    ]

    for sig, timeout, sig_name in stages:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True, f"stopped ({sig_name})"
        except PermissionError:
            return False, f"permission denied on {sig_name}"
        except Exception as e:
            return False, f"send {sig_name} failed: {e}"

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _pid_exists(pid):
                return True, f"stopped ({sig_name})"
            time.sleep(0.1)

    return (not _pid_exists(pid), "not responding after SIGKILL")

@app.callback()
def main():
    """Research Claw - AI Research Assistant."""
    pass

_OVERLEAF_PRESETS = {
    "1": {
        "name": "Overleaf",
        "base_url": "https://www.overleaf.com",
        "login_path": "/login",
        "cookie_names": ["overleaf_session2"],
        "login_cmd": ["ols", "login"],
        "login_module": "olsync",
        "login_pkg": "overleaf-sync",
    },
    "2": {
        "name": "CSTCloud",
        "base_url": "https://latex.cstcloud.cn",
        "login_path": "/oidc/login",
        "cookie_names": ["overleaf.sid", "overleaf_session2", "GCLB"],
        "login_cmd": [sys.executable, "-m", "core.olsync_cstcloud.olsync", "login"],
        "login_module": "core.olsync_cstcloud.olsync",
        "login_pkg": "PySide6 (pip install PySide6)",
    },
}


def _save_overleaf_settings(preset: dict) -> None:
    """Write overleaf base_url/login_path/cookie_names back to settings.json."""
    from config.loader import get_config_service, get_config_path
    import json

    config_path = get_config_path()
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    data["overleaf"] = {
        "baseUrl": preset["base_url"],
        "loginPath": preset["login_path"],
        "cookieNames": preset["cookie_names"],
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.command()
def login():
    """Login to an Overleaf instance (overleaf.com or CSTCloud)."""
    console.print("[bold]Select Overleaf instance:[/bold]\n")
    for key, preset in _OVERLEAF_PRESETS.items():
        console.print(f"  [{key}] {preset['name']} ({preset['base_url']})")
    console.print()

    choice = typer.prompt("Enter choice", default="1")
    preset = _OVERLEAF_PRESETS.get(choice)
    if not preset:
        console.print(f"[red]Invalid choice: {choice}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[blue]Logging in to {preset['name']} ({preset['base_url']})...[/blue]\n")

    # Check if required dependencies are available
    try:
        __import__(preset["login_module"])
    except ImportError as e:
        console.print(
            f"[red]Required dependency missing: {e}[/red]\n"
            f"  pip install {preset['login_pkg']}"
        )
        raise typer.Exit(1)

    # Run the external login command
    result = subprocess.run(preset["login_cmd"])
    if result.returncode != 0:
        console.print("[red]Login failed.[/red]")
        raise typer.Exit(1)

    # Save overleaf settings
    _save_overleaf_settings(preset)
    console.print(f"\n[green]Login successful. Overleaf instance set to {preset['name']}.[/green]")


@app.command()
def reset():
    """Reset workspace and Overleaf data. Keeps LLM provider and IM channel config."""
    import json
    import shutil
    from config.loader import get_config_path

    console.print("[bold red]This will delete:[/bold red]")
    console.print("  - All projects (workspace/)")
    console.print("  - Overleaf credentials (.olauth)")
    console.print("  - Overleaf settings")
    console.print("  - Logs")
    console.print()
    console.print("[bold green]Preserved:[/bold green]")
    console.print("  - LLM provider configuration")
    console.print("  - IM channel accounts")
    console.print()

    if not typer.confirm("Proceed with reset?", default=False):
        console.print("Cancelled.")
        raise typer.Exit(0)

    # 1. Delete workspace
    config_path = get_config_path()
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        workspace_dir = Path(data.get("agents", {}).get("defaults", {}).get("workspace", "./workspace"))
        if not workspace_dir.is_absolute():
            workspace_dir = (root_path / workspace_dir).resolve()
    else:
        workspace_dir = root_path / "workspace"

    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
        console.print(f"  [dim]Deleted {workspace_dir}[/dim]")

    # 2. Delete .olauth
    for olauth in [root_path / ".olauth", Path.home() / ".olauth"]:
        if olauth.exists():
            olauth.unlink()
            console.print(f"  [dim]Deleted {olauth}[/dim]")

    # 3. Clear overleaf settings from settings.json (keep everything else)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop("overleaf", None)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        console.print("  [dim]Cleared overleaf settings[/dim]")

    # 4. Delete logs
    logs_dir = root_path / "logs"
    if logs_dir.exists():
        shutil.rmtree(logs_dir)
        console.print(f"  [dim]Deleted {logs_dir}[/dim]")

    # 5. Delete runtime state
    runtime_dir = root_path / ".open_research_claw"
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
        console.print(f"  [dim]Deleted {runtime_dir}[/dim]")

    console.print("\n[green]Reset complete. Run 'python cli/main.py login' to reconfigure Overleaf.[/green]")


def _find_latest_session_for(workspace: Path, project_id: str) -> Optional[str]:
    """Find the latest session for today in the given project. Returns session_id or None."""
    from datetime import datetime
    project_root = workspace / project_id
    if not project_root.exists():
        return None
    today = datetime.now().strftime("%m%d")
    prefix = f"{today}_"
    max_seq = 0
    found = False
    for d in project_root.iterdir():
        if d.is_dir() and d.name.startswith(prefix):
            try:
                seq = int(d.name[len(prefix):])
                if seq > max_seq:
                    max_seq = seq
                    found = True
            except ValueError:
                pass
    return f"{prefix}{max_seq:02d}" if found else None


@app.command(hidden=True)
def onboard():
    """Initialize configuration (disabled — configure via Web UI or settings.default.json)."""
    console.print("[yellow]onboard 已禁用。请通过 Web UI 或直接编辑 settings.json 进行配置。[/yellow]")
    raise typer.Exit()

@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    project_id: str = typer.Option("Default", "--project", "-p", help="Project ID"),
    session_id: str = typer.Option(None, "--session", "-s", help="Session ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
    new_session: bool = typer.Option(False, "--new-session", help="Force a new session"),
    e2e: bool = typer.Option(False, "--e2e", help="E2E auto mode: new session, skip confirmations, auto done, exit after completion"),
):
    """Interact with the agent directly."""
    from loguru import logger
    # Configure logging level
    logger.remove()
    log_level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=log_level,
        format="<dim>{time:HH:mm:ss}</dim> | <level>{level: <8}</level> | <dim>{name}:{function}:{line}</dim> - {message}",
        colorize=True,
    )

    from config.loader import load_config, get_config_service
    from bus.queue import MessageBus
    from providers.proxy import DynamicProviderProxy
    from agent.loop import AgentLoop

    config = load_config()

    if not config.get_api_key():
        console.print("[red]Error: No API key configured.[/red]")
        console.print("[dim]Run 'python cli/main.py onboard' to set up your configuration.[/dim]")
        console.print("[dim]Or Run 'python cli/main.py gateway' to visit the web UI and set up your configuration.[/dim]")
        raise typer.Exit(1)
    # --- Auto-derive profile and mode from project_id ---
    if project_id == "Default":
        profile = "chat_mode_agent"
        mode = "CHAT"
    else:
        profile = "project_mode_agent"
        mode = "NORMAL"

    # --- Auto-derive session_id ---
    if e2e:
        new_session = True  # --e2e implies new session

    from core.session import generate_session_id

    if session_id is None:
        if new_session:
            session_id = generate_session_id(config.workspace_path / project_id)
        else:
            # Interactive: reuse today's latest session, or generate new
            _find = _find_latest_session_for(config.workspace_path, project_id)
            session_id = _find or generate_session_id(config.workspace_path / project_id)

    bus = MessageBus()
    provider = DynamicProviderProxy()

    # Initialize the current loop state
    current_params = {
        "project_id": project_id,
        "session_id": session_id,
        "mode": mode,
        "profile": profile,
    }

    # Create Project + Session (role_type derived from profile)
    from core.project import Project
    from agent.tools.loader import ToolLoader
    profile_data = ToolLoader._load_profile(profile)
    role_type = profile_data.get("role_type", "Assistant")
    proj = Project(project_id, config.workspace_path)
    sess = proj.session(session_id, role_type=role_type)

    def create_agent(params, proj=None, sess=None):
        return AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=None,  # Handled by proxy
            brave_api_key=config.tools.web.search.api_key or None,
            s2_api_key=config.tools.academic.semanticscholar_api_key or None,
            project_id=params["project_id"],
            session_id=params["session_id"],
            mode=params["mode"],
            profile=params["profile"],
            config=config,
            project=proj,
            session=sess,
        )

    agent_loop = create_agent(current_params, proj, sess)

    # [NEW] Rich terminal renderer for structured event display
    from cli.renderer import TerminalRenderer
    renderer = TerminalRenderer(console=console, verbose=verbose)
    on_token = renderer.on_token
    on_event = renderer.on_event
    
    # --e2e: auto-inject --e2e into /task command so user doesn't need to write it twice
    if e2e and message and message.lstrip().startswith("/task") and "--e2e" not in message:
        # Insert --e2e after "/task"
        message = message.lstrip().replace("/task", "/task --e2e", 1)

    if message:
        # Single message mode — all commands handled by CommandRouter in AgentLoop

        async def run_once():
            # Start a background task to print outbound messages
            async def print_outbound():
                while True:
                    try:
                        msg = await bus.outbound.get()
                        console.print(f"[dim]🤖 [Async] {msg.content.strip()}[/dim]")
                        bus.outbound.task_done()
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass

            printer = asyncio.create_task(print_outbound())

            console.print("\n🤖 ", end="")
            await agent_loop.process_direct(message, session_id, on_token=on_token, on_event=on_event)

            if e2e:
                # Auto-done: if still in task session, dispatch /done
                ctx_mgr = agent_loop.context
                if ctx_mgr and ctx_mgr._task_session:
                    from bus.events import InboundMessage
                    _msg = InboundMessage(content="/done", channel="cli", sender_id="cli", chat_id=session_id)
                    _ctx = agent_loop._build_command_context(_msg)
                    await agent_loop._command_router.dispatch("/done", _ctx)
                printer.cancel()
                return

            # Keep process alive to receive background events (e.g. from deep research)
            console.print("\n[dim]⏳ Waiting for background tasks to complete... (Ctrl+C to exit)[/dim]")
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            finally:
                printer.cancel()
        
        try:
            asyncio.run(run_once())
        except KeyboardInterrupt:
            console.print("\nStopped.")
    else:
        # Interactive mode
        console.print("🤖 Interactive mode (Ctrl+C to exit)")
        console.print("[dim]输入 /help 查看可用命令[/dim]\n")
        
        async def run_interactive():
            nonlocal agent_loop
            # Start a background task to print outbound messages from the bus
            async def print_outbound():
                while True:
                    try:
                        msg = await bus.outbound.get()
                        content = msg.content.strip()
                        if content:
                            console.print(f"\n[dim]🤖 [Background] {content}[/dim]")
                        bus.outbound.task_done()
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass

            printer = asyncio.create_task(print_outbound())

            while True:
                try:
                    user_input = await asyncio.to_thread(console.input, f"[bold blue][{current_params['project_id']}:{current_params['session_id']}] You:[/bold blue] ")
                    
                    if not user_input.strip():
                        continue
                        
                    # [HOT RELOAD] Handle Management Commands
                    if user_input.lower().startswith("/switch "):
                        new_project = user_input.split(" ", 1)[1].strip()
                        current_params["project_id"] = new_project
                        # Auto-derive profile and mode from project
                        if new_project == "Default":
                            current_params["profile"] = "chat_mode_agent"
                            current_params["mode"] = "CHAT"
                        else:
                            current_params["profile"] = "project_mode_agent"
                            current_params["mode"] = "NORMAL"
                        p_data = ToolLoader._load_profile(current_params["profile"])
                        p_role_type = p_data.get("role_type", "Assistant")
                        new_proj = Project(new_project, config.workspace_path)
                        new_sess = new_proj.session(current_params["session_id"], role_type=p_role_type)
                        agent_loop = create_agent(current_params, new_proj, new_sess)
                        console.print(f"[green]Switched to project: {new_project}[/green]")
                        continue

                    if user_input.lower().startswith("/new "):
                        new_session = user_input.split(" ", 1)[1].strip()
                        current_params["session_id"] = new_session
                        p_data = ToolLoader._load_profile(current_params["profile"])
                        p_role_type = p_data.get("role_type", "Assistant")
                        new_proj = Project(current_params["project_id"], config.workspace_path)
                        new_sess = new_proj.session(new_session, role_type=p_role_type)
                        agent_loop = create_agent(current_params, new_proj, new_sess)
                        console.print(f"[green]Switched to new session: {new_session}[/green]")
                        continue
                    
                    console.print("\n🤖 ", end="")
                    await agent_loop.process_direct(user_input, current_params["session_id"], on_token=on_token, on_event=on_event)
                    # Sync CLI state after agent may have changed project/session (e.g. /reset, /switch)
                    current_params["project_id"] = agent_loop.project_id
                    current_params["session_id"] = agent_loop.session_id
                    console.print("\n")

                    # Subagent sub-loop (e.g. /git → GitAgent, /task → TaskAgent)
                    if agent_loop._pending_subagent:
                        sub = agent_loop._pending_subagent
                        agent_loop._pending_subagent = None

                        sub_label = getattr(sub, "label", "Sub")
                        sub_color = getattr(sub, "color", "cyan")
                        sub_exit_cmds = getattr(sub, "exit_commands", ("/done", "/back", "/quit"))

                        while True:
                            try:
                                sub_input = await asyncio.to_thread(
                                    console.input,
                                    f"[bold {sub_color}][{sub_label}] You:[/bold {sub_color}] "
                                )
                                if not sub_input.strip():
                                    continue
                                if sub_input.strip().lower() in sub_exit_cmds:
                                    # Build rich summary for main session context
                                    summary_parts = []
                                    state = getattr(sub, 'state', None)
                                    if state:
                                        if getattr(state, 'goal', ''):
                                            summary_parts.append(f"目标: {state.goal}")
                                        if getattr(state, 'proposal', ''):
                                            prop = state.proposal
                                            if len(prop) > 500:
                                                prop = prop[:500] + "..."
                                            summary_parts.append(f"方案摘要:\n{prop}")
                                        tg = getattr(state, 'task_graph', None)
                                        if tg and hasattr(tg, 'tasks'):
                                            task_lines = []
                                            for tid, t in tg.tasks.items():
                                                task_lines.append(f"  [{tid}] {t.title} — {t.status.value}")
                                            summary_parts.append("任务状态:\n" + "\n".join(task_lines))
                                        artifacts = getattr(state, 'artifacts', [])
                                        if artifacts:
                                            summary_parts.append(f"产出文件: {', '.join(artifacts[:10])}")

                                    brief = sub.get_summary()
                                    if brief:
                                        summary_parts.append(brief)

                                    full_summary = "\n".join(summary_parts) if summary_parts else ""

                                    if full_summary:
                                        console.print(f"\n🔧 退出 {sub_label} 模式。[{brief or '无操作'}]\n")
                                        await agent_loop.history_logger.log_outbound(
                                            f"[{sub_label} 操作记录]\n{full_summary}"
                                        )
                                    else:
                                        console.print(f"\n🔧 退出 {sub_label} 模式。\n")
                                    break

                                console.print(f"\n[{sub_label}] 🤖 ", end="")
                                response = await sub.process_message(sub_input, on_token=on_token)
                                if not response or not response.strip():
                                    console.print("（无响应，请重新描述你的需求）")
                                console.print("\n")

                                # Nested subagent detection (e.g. TaskAgent -> MergeAgent)
                                if hasattr(sub, '_pending_subagent') and sub._pending_subagent:
                                    nested = sub._pending_subagent
                                    sub._pending_subagent = None
                                    nested_label = getattr(nested, "label", "Sub")
                                    nested_color = getattr(nested, "color", "cyan")
                                    nested_exit_cmds = getattr(nested, "exit_commands", ("/done",))

                                    while True:
                                        try:
                                            nested_input = await asyncio.to_thread(
                                                console.input,
                                                f"[bold {nested_color}][{nested_label}] You:[/bold {nested_color}] "
                                            )
                                            if not nested_input.strip():
                                                continue
                                            if nested_input.strip().lower() in nested_exit_cmds:
                                                summary = nested.get_summary()
                                                console.print(f"\n🔀 退出 {nested_label} 模式。[{summary or '无操作'}]\n")
                                                break
                                            console.print(f"\n[{nested_label}] 🤖 ", end="")
                                            nested_resp = await nested.process_message(nested_input, on_token=on_token)
                                            if not nested_resp or not nested_resp.strip():
                                                console.print("（无响应）")
                                            console.print("\n")
                                        except KeyboardInterrupt:
                                            console.print(f"\n🔀 退出 {nested_label} 模式。\n")
                                            break
                                        except Exception as e:
                                            console.print(f"\n[red]{nested_label} 模式错误: {e}[/red]\n")
                                            continue

                                    # Callback to parent agent to sync state
                                    if hasattr(sub, '_on_subagent_exit'):
                                        sub._on_subagent_exit(nested)
                            except KeyboardInterrupt:
                                console.print(f"\n🔧 退出 {sub_label} 模式。\n")
                                break
                            except Exception as e:
                                console.print(f"\n[red]{sub_label} 模式错误: {e}[/red]\n")
                                continue
                except KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    printer.cancel()
                    break
        
        asyncio.run(run_interactive())

@app.command()
def subagent(
    role: str = typer.Option(..., "--role", "-r", help="Role name of the agent"),
    profile: str = typer.Option("project_mode_subagent", "--profile", help="Agent profile"),
    project_id: str = typer.Option("Default", "--project-id", help="Project ID"),
    max_loops: int = typer.Option(60, "--max-loops", help="Max execution loops"),
    session_id: str = typer.Option("cli:subagent", "--session", help="Session ID"),
    research_id: str = typer.Option(None, "--research-id", help="Research ID"),
    mode: str = typer.Option("CHAT", "--mode", help="Running mode (CHAT, NORMAL, TASK)"),
):
    """Run a specialized sub-agent in a standalone process."""
    # Validate mode
    VALID_MODES = {"CHAT", "NORMAL", "TASK"}
    if mode.upper() not in VALID_MODES:
        raise typer.BadParameter(f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}")

    from config.loader import load_config
    from bus.queue import MessageBus
    from providers.openai_provider import OpenAIProvider
    from agent.loop import AgentLoop
    from agent.tools.loader import ToolLoader
    import os

    config = load_config()
    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model_name = config.get_api_model() or config.agents.defaults.model
    
    bus = MessageBus()
    provider = OpenAIProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=model_name
    )

    # Derive role_type from profile
    profile_data = ToolLoader._load_profile(profile)
    role_type = profile_data.get("role_type", "Worker")

    # 1. Determine Agent Directory
    workspace_root = config.workspace_path
    session_root = workspace_root / project_id / session_id

    if research_id:
        agent_dir = session_root / research_id / "subagents" / role
    else:
        agent_dir = session_root / "subagents" / role

    agent_dir.mkdir(parents=True, exist_ok=True)

    # Load dynamic system prompt if it exists (for spawned agents)
    system_prompt = "You are a helpful research assistant."
    prompt_file = agent_dir / "system_prompt.txt"
    if prompt_file.exists():
        try:
            system_prompt = prompt_file.read_text(encoding="utf-8").strip()
            console.print(f"[dim]Loaded dynamic system prompt for [{role}][/dim]")
        except Exception as e:
            console.print(f"[yellow]Warning: Failed to load system_prompt.txt: {e}[/yellow]")

    bus = MessageBus()
    provider = OpenAIProvider(api_key=api_key, api_base=api_base, default_model=model_name)

    # Create Project + Session for subagent
    from core.project import Project
    proj = Project(project_id, config.workspace_path)
    sess = proj.session(session_id, role_type=role_type)

    inner_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=model_name,
        system_prompt=system_prompt,
        profile=profile,
        role_name=role,
        project_id=project_id,
        session_id=session_id,
        research_id=research_id,
        mode=mode,
        allow_recursion=False,
        project=proj,
        session=sess,
    )

    console.print(f"[bold green]🚀 Sub-agent [{role}] started in project [{project_id}] session [{session_id}] PID {os.getpid()}[/bold green]")

    try:
        asyncio.run(inner_loop.run())
    except KeyboardInterrupt:
        console.print(f"\n[yellow]Sub-agent [{role}] interrupted.[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Sub-agent [{role}] crashed: {e}[/red]")
        import traceback
        traceback.print_exc()

@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
):
    """Start the gateway server and UI."""
    import shutil
    from loguru import logger
    import sys

    # Ensure settings.json exists in project root (copy from default if missing)
    project_root = Path(__file__).resolve().parent.parent
    settings_path = project_root / "settings.json"
    if not settings_path.exists():
        default_path = project_root / "settings.default.json"
        if default_path.exists():
            shutil.copy2(default_path, settings_path)
            console.print("[green]Created settings.json from settings.default.json[/green]")
        else:
            # Write minimal default config
            import json
            settings_path.write_text(json.dumps({
                "provider": {"activeId": "", "instances": []},
                "channel": {"accounts": []},
                "userInfo": {"language": "zh", "llmLanguage": "auto"}
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            console.print("[green]Created default settings.json[/green]")

    # Configure gateway logging: stderr + rotating log file
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<dim>{time:HH:mm:ss}</dim> | <level>{level: <8}</level> | <dim>{name}:{function}:{line}</dim> - {message}",
        colorize=True,
    )
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        str(log_dir / "gateway.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )
    logger.info(f"Log file: {log_dir / 'gateway.log'}")

    host = "127.0.0.1"
    console.print(f"[bold green]Starting Gateway on http://{host}:{port}[/bold green]")
    console.print(f"[blue]Web UI available at http://{host}:{port}/ui[/blue]")

    # Supervisor loop: restart when child exits with code 42 (triggered by /api/restart)
    import subprocess, sys as _sys, time as _time

    project_root = str(Path(__file__).resolve().parent.parent)
    log_dir_str = str(log_dir / "gateway.log")

    # Build the child startup script with logging configuration included
    child_script = f"""
import sys
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="INFO",
    format="<dim>{{time:HH:mm:ss}}</dim> | <level>{{level: <8}}</level> | <dim>{{name}}:{{function}}:{{line}}</dim> - {{message}}",
    colorize=True)
logger.add({log_dir_str!r}, level="DEBUG",
    format="{{time:YYYY-MM-DD HH:mm:ss.SSS}} | {{level: <8}} | {{name}}:{{function}}:{{line}} - {{message}}",
    rotation="10 MB", retention="7 days", encoding="utf-8")
from agent.services.gateway_server import start_gateway_server
start_gateway_server(host={host!r}, port={port})
"""

    pid = os.getpid()
    _register_gateway_pid(pid)
    try:
        while True:
            proc = subprocess.run(
                [_sys.executable, "-c", child_script],
                cwd=project_root,
            )
            if proc.returncode != 42:
                break
            console.print("[yellow]Restarting gateway server...[/yellow]")
            _time.sleep(1)  # wait for port release
    except KeyboardInterrupt:
        console.print("Stopping...")
    finally:
        _unregister_gateway_pid(pid)


@app.command()
def stop():
    """Stop all running gateway processes started by this project."""
    targets: list[tuple[int, str]] = []

    # Primary source: pid files written by gateway command.
    for pid in _collect_gateway_pids_from_pid_files():
        targets.append((pid, "pid-file registry"))

    # Fallback for older processes started before pid-file support.
    if not targets:
        targets = _find_gateway_processes()

    if not targets:
        console.print("[yellow]No running gateway process found.[/yellow]")
        return

    console.print(f"[bold]Found {len(targets)} gateway process(es). Stopping...[/bold]")

    failed = 0
    for pid, cmd in targets:
        ok, detail = _stop_pid(pid)
        status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        if not ok:
            failed += 1
        _unregister_gateway_pid(pid)
        console.print(f"{status} pid={pid} | {detail}")
        console.print(f"[dim]{cmd}[/dim]")

    if failed:
        raise typer.Exit(1)

@app.command()
def doctor():
    """Run system diagnostics to check all configurations."""
    from config.loader import load_config
    from config.diagnostics import run_diagnostics_sync
    from rich.table import Table

    config = load_config()
    console.print("[bold]正在运行系统诊断...[/bold]\n")

    results = run_diagnostics_sync(config)

    table = Table(title="系统诊断结果", show_lines=True)
    table.add_column("检查项", style="bold")
    table.add_column("状态")
    table.add_column("详情")

    status_map = {
        "success": "[green]✓ 通过[/green]",
        "error": "[red]✗ 失败[/red]",
        "warning": "[yellow]⚠ 警告[/yellow]",
        "skip": "[dim]○ 跳过[/dim]",
    }

    all_ok = True
    for r in results:
        status_icon = status_map.get(r["status"], r["status"])
        table.add_row(r["name"], status_icon, r.get("message", ""))
        if r["status"] == "error":
            all_ok = False

    console.print(table)
    if all_ok:
        console.print("\n[bold green]所有检查通过 ✓[/bold green]")
    else:
        console.print("\n[bold red]存在失败项，请检查配置后重试[/bold red]")
        console.print("[dim]提示: 运行 'python cli/main.py onboard' 重新配置[/dim]")


@app.command()
def legacy_gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
):
    """Start the gateway (Legacy mode)."""
    from config.loader import load_config
    from bus.queue import MessageBus
    from agent.loop import AgentLoop
    from channels.feishu import FeishuChannel
    from channels.im_telegram import ImTelegramChannel
    from channels.im_qq import ImQQChannel
    from channels.im_dingtalk import ImDingTalkChannel
    from core.automation.runtime import AutomationRuntime
    
    console.print(f"Starting Research Claw gateway...")

    config = load_config()
    bus = MessageBus()

    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model_name = config.get_api_model() or config.agents.defaults.model

    if not api_key:
        console.print("[red]Error: No API key configured.[/red]")
        console.print("[dim]Run 'python cli/main.py onboard' to set up your configuration.[/dim]")
        raise typer.Exit(1)

    from providers.openai_provider import OpenAIProvider
    provider = OpenAIProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=model_name
    )

    # Create Project + Session for gateway (role_type derived from profile)
    from core.project import Project
    from agent.tools.loader import ToolLoader as _GWLoader
    _gw_profile = _GWLoader._load_profile("chat_mode_agent")
    proj = Project("gateway_hub", config.workspace_path)
    sess = proj.session("gateway:default", role_type=_gw_profile.get("role_type", "Assistant"))

    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        project_id="gateway_hub",
        model=model_name,
        s2_api_key=config.tools.academic.semanticscholar_api_key or None,
        profile="chat_mode_agent",
        project=proj,
        session=sess,
    )

    # Show enabled IM accounts
    enabled_accounts = [acc for acc in config.channel.accounts if acc.enabled]
    if enabled_accounts:
        platforms = [f"{acc.platform} ({acc.id})" for acc in enabled_accounts]
        console.print(f"Enabled channels: {', '.join(platforms)}")
    else:
        console.print("[yellow]Warning: No enabled channel accounts configured[/yellow]")

    automation_runtime = AutomationRuntime(
        workspace=config.workspace_path,
        provider=provider,
        model=model_name,
        config=config,
        s2_api_key=config.tools.academic.semanticscholar_api_key or None,
    )

    # Initialize channels (config.channels already populated by sync_from_unified_config)
    im_telegram = ImTelegramChannel(config, bus) if config.channels.telegram.token else None
    feishu = FeishuChannel(config.channels.feishu, bus) if config.channels.feishu.app_id else None
    im_qq = ImQQChannel(config, bus) if config.channels.qq.app_id else None
    im_dingtalk = ImDingTalkChannel(config, bus) if config.channels.dingtalk.client_id else None

    # Debug: print channel initialization status
    if im_qq:
        console.print("[dim]QQ channel initialized successfully[/dim]")

    async def run():
        await automation_runtime.start()

        tasks = [agent.run()]

        # Start all initialized channels
        if im_telegram:
            console.print("Starting IM Telegram channel...")
            tasks.append(im_telegram.start())
        if feishu:
            console.print("Starting Feishu channel...")
            tasks.append(feishu.start())
        if im_qq:
            console.print("Starting IM QQ channel...")
            tasks.append(im_qq.start())
        if im_dingtalk:
            console.print("Starting IM DingTalk channel...")
            tasks.append(im_dingtalk.start())

        # Dispatcher loop
        tasks.append(bus.dispatch_outbound())
        
        # Subscribe channels to outbound
        if im_telegram:
            bus.subscribe_outbound("im_telegram", im_telegram.send)
            bus.subscribe_outbound("telegram", im_telegram.send)
        if feishu:
            bus.subscribe_outbound("im_feishu", feishu.send)
            bus.subscribe_outbound("feishu", feishu.send)
        if im_qq:
            bus.subscribe_outbound("im_qq", im_qq.send)
        if im_dingtalk:
            bus.subscribe_outbound("im_dingtalk", im_dingtalk.send)

        try:
            await asyncio.gather(*tasks)
        finally:
            await automation_runtime.stop()
        
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("Stopping...")


if __name__ == "__main__":
    app()
