"""Setup configuration for Research Claw."""

import json
from pathlib import Path
import typer
from rich.console import Console
from rich.prompt import Prompt

app = typer.Typer()
console = Console()

@app.command()
def main():
    """Interactive setup for Research Claw."""
    console.print("[bold blue]Research Claw Setup[/bold blue]\n")

    config_dir = Path.home() / ".open_research_claw"
    config_path = config_dir / "config.json"

    # Ensure directory exists
    config_dir.mkdir(parents=True, exist_ok=True)

    # Load existing config if available
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            console.print(f"Found existing config at {config_path}")
            if not typer.confirm("Do you want to update it?", default=True):
                return
        except:
            pass

    # Configure Qwen (via OpenAI compatible interface)
    console.print("\n[bold green]1. LLM Configuration (Qwen/DashScope)[/bold green]")
    console.print("We will configure Qwen-Max using DashScope's OpenAI-compatible API.")

    api_key = Prompt.ask("Enter your DashScope API Key (sk-...)")

    # Default Qwen config structure
    new_config = {
        "agents": {
            "defaults": {
                "model": "qwen-max",
                "workspace": str(Path.home() / "open_research_claw_workspace"),
                "max_tool_iterations": 15
            }
        },
        "providers": {
            "openai": {
                "apiKey": api_key,
                "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1"
            },
            # Keep other providers empty/default
            "anthropic": {"apiKey": ""},
            "openrouter": {"apiKey": ""}
        },
        "channels": {
            "telegram": {
                "enabled": False,
                "token": "",
                "allowFrom": []
            }
        },
        "tools": {
            "web": {
                "search": {
                    "apiKey": ""
                }
            }
        }
    }

    # Merge with existing telegram config if present
    if "channels" in config and "telegram" in config["channels"]:
        if config["channels"]["telegram"].get("token"):
             console.print("\n[bold green]2. Telegram Configuration[/bold green]")
             if typer.confirm("Keep existing Telegram config?", default=True):
                 new_config["channels"]["telegram"] = config["channels"]["telegram"]

    if not new_config["channels"]["telegram"]["token"]:
        console.print("\n[bold green]2. Telegram Configuration (Optional)[/bold green]")
        if typer.confirm("Do you want to configure Telegram now?", default=False):
            token = Prompt.ask("Enter Telegram Bot Token")
            user_id = Prompt.ask("Enter your Telegram User ID")
            new_config["channels"]["telegram"]["enabled"] = True
            new_config["channels"]["telegram"]["token"] = token
            new_config["channels"]["telegram"]["allowFrom"] = [user_id]

    # Save
    config_path.write_text(json.dumps(new_config, indent=2))
    console.print(f"\n[bold green]✓ Configuration saved to {config_path}[/bold green]")
    console.print("\nYou can now run Research Claw with:")
    console.print("[cyan]python3 -m open_research_claw.cli.main agent -m 'Hello Qwen!'[/cyan]")

if __name__ == "__main__":
    app()
