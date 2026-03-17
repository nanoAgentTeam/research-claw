# Open Research Claw Project Overview

## What Is This

Open Research Claw is an AI Agent system designed for academic paper writing. It enables researchers to manage and write academic papers through natural language conversations.

## What It Can Do

- Create a paper project from scratch, or import an existing Overleaf project
- Use single-Agent or multi-Agent collaboration to write and revise papers
- Automatically sync to Overleaf, with Git tracking every revision
- Interact locally via CLI, or remotely via Lark / Telegram / QQ
- Set up scheduled tasks for automatic research, with results pushed to IM (scheduling feature not yet verified)

## Design Philosophy

The system is built around several core ideas:

1. **Two-Space Separation**: Default (lobby) handles project management and chatting, while Project (workspace) handles actual paper work. Responsibilities are clear and do not interfere with each other.

2. **Session Isolation**: Each time you enter a project, an independent working session is created. Conversation histories and SubAgent workspaces are isolated from each other, preventing cross-contamination.

3. **Agent Permission Layering**: The main Agent has full read/write access to the project, while SubAgents can only work in isolated overlays. Their output must be reviewed or merged before entering the project.

4. **Automatic Version Control**: Every file modification is tracked by Git and automatically committed. You can roll back at any time without worrying about breaking things.

5. **Overleaf Bidirectional Sync**: Local changes can be pushed to Overleaf, and changes on Overleaf can be pulled to local.

## Overall Architecture

The system is divided into four layers:

- **Channel Layer**: CLI, Telegram, Lark, QQ (with Gateway Web UI), responsible for receiving and displaying messages
- **Message Bus**: Asynchronous message queue, decoupling Channels and Agents, supporting multiple channels working simultaneously
- **Agent Core**: AgentLoop, tool system, context management, scheduler — the core of the system
- **Reasoning Layer**: LLM Provider abstraction, supporting OpenAI-compatible APIs
