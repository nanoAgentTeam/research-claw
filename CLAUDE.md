# CLAUDE.md

这是一个为 Claude Code (claude.ai/code) 在此存储库中工作时提供指导的文件。

**重要提示：请始终使用简体中文回答用户的问题和进行交流。**

代码要兼容 python 3.9

## 开发指令

### 核心执行

- **交互式 Agent**: `python cli/main.py agent`
- **单次消息 Agent**: `python cli/main.py agent -m "你的指令" -p ProjectID`
- **网关模式 (飞书/Telegram)**: `python cli/main.py gateway --port 18790`
- **启动子 Agent**: `python cli/main.py subagent --role RoleName --project-id PID --session SID`
- **初始配置**: `python cli/main.py onboard` (创建配置文件与工作区)

### 环境配置

    python 3.9+

- **安装依赖**: `pip install -r requirements.txt`
- **认证**: Overleaf 同步需要 `.olauth` 文件 (通过 `ols login` 生成)。

## 代码架构

### 概览

Research Claw 是一个自托管的 AI 科研助手，管理论文项目、检索文献、追踪学术进展与截稿日期，并通过多渠道随时响应。它基于分层 AI Agent 架构，管理一个 `workspace/` 目录，分为 **Default** (全局管理) 与 **Project** (特定论文) 空间。

### 核心组件 (`core/`)

- **`Project` (`core/project.py`)**: 核心抽象。管理 `core` 目录 (LaTeX 文件)、Git 版本控制、Overleaf 同步以及 LaTeX 编译。
- **`Session` (`core/session.py`)**: 在项目内管理独立的对话历史、元数据以及子 Agent 的工作区。
- **`FS` (`core/fs/`)**: 提供路径安全的文件操作以及为子 Agent 提供 Overlay 虚拟文件系统。
- **`Tools` (`core/tools/`)**: 模块化的工具系统 (搜索、编译、Git 等)，通过 `config/tools.json` 配置。

### Agent 系统 (`agent/`)

- **`AgentLoop` (`agent/loop.py`)**: `Assistant` 角色的主执行循环。处理工具调用与指令路由。
- **`WideAgentLoop` (`agent/wide_loop.py`)**: 编排复杂的多 Agent 任务 (Swarm/DAG 模式)。
- **`GitAgent` (`agent/git_agent.py`)**: 专门用于版本控制操作的 Agent。

### 分层与隔离

- **Assistant (主 Agent)**: 对项目的 `core/` 目录拥有完整的读写权限。
- **Worker (子 Agent)**: 在 Session 特定的 `subagents/` 覆盖层中工作。变更仅在 `Assistant` 核准后或通过 `MergeAgent` 合并至 `core/`。

### 工作区结构

- `workspace/{项目名}/{项目名}/`: "core" 目录 (实际的 LaTeX 文件 + Git)。
- `workspace/{项目名}/{MMDD_NN}/`: Session 目录，用于历史记录与隔离。
- `workspace/{项目名}/project.yaml`: 项目特定设定 (Overleaf ID, Git 配置, LaTeX 引擎)。

## 开发指南

- **路径安全**: 务必使用 `project.resolve(path)` 或 `session.fs` 以防止路径穿越 (Path Traversal)。
- **日志**: 使用 `loguru` 进行结构化日志记录。
- **配置**: 全局设置位于 `settings.json`；功能开关位于 `config/*.json`。
