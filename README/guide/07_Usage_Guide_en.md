# Usage Guide

## Creating a Paper Project from Scratch

```
# 1. Start the CLI
python cli/main.py agent

# 2. Create a project in Default
You: Create a paper project about MoE, also create and link it on Overleaf

# The Bot will automatically complete: create project → create Overleaf project → link → switch into it

# 3. After entering the project, start writing
You: Use multiple agents to help me write a complete paper

# The Bot will create subagents (researcher, writer, etc.) to collaborate

# 4. Compile (you can also ask the Agent to compile directly, the command is not required)
You: /compile

# 5. Sync to Overleaf
You: /sync push
```

## Modifying an Existing Paper Project

```
# 1. View the project list
You: What projects do I have

# 2. Switch to the target project (automatically pulls the latest version from Overleaf)
You: Switch to NSR_Parallel_Reasoning latest session

# 3. Modify directly
You: Help me rewrite the Introduction section

# 4. Or use subagents to modify
You: Create a reviewer agent to review the paper, then create a writer agent to revise based on the review comments

# 5. Compile to verify
You: /compile

# 6. View git history, roll back if necessary
You: /git
```

## Interacting with the Bot

### CLI (Local)

```
python cli/main.py agent
```

Interactive conversation, supports all features. The prompt shows the current project and session:

```
[Default:cli:default] You: ...
[MoE_Research:0217_01] You: ...
```

### Lark / Telegram / QQ (Remote)

```
python cli/main.py gateway --port 18790
```

Configure IM account information (Lark / Telegram / QQ) through `settings.json`. Messages are passed bidirectionally through the MessageBus.

Configure IM accounts through `channel.accounts` in `settings.json`, or manage them via the Web UI.

### Common Commands

| Command | Description |
|------|------|
| `/reset` | Reset the current session (clear conversation history) |
| `/compile` | Compile the current project's LaTeX |
| `/sync pull` | Pull from Overleaf |
| `/sync push` | Push to Overleaf |
| `/git` | Enter Git management mode |
| `/task <topic>` | Start deep research (DAG sequential execution) |
| `/back` | Return to Default |

## Scheduled Tasks and Push Notifications (Scheduling Feature Not Yet Verified)

### Push to IM

In Gateway mode, the Agent's output is automatically pushed to connected IM channels (Lark / Telegram / QQ) through the MessageBus.

### Periodic Automatic Execution

If you need to execute tasks periodically (e.g., automatic daily research), you can call the CLI's single-message mode through an external cron job:

```
# crontab example: automatic research daily at 9:00
0 9 * * * cd /path/to/open_research_claw && python cli/main.py agent -m "Research the latest papers in the MoE field" -p MyPaper
```

Output will be written to project files and automatically git committed. If the gateway is running simultaneously, results will also be pushed to IM.
