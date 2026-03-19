"""Lightweight GitAgent for interactive version management sub-sessions."""

from __future__ import annotations

import json
import asyncio
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger
from core.prompts import render as render_prompt

if TYPE_CHECKING:
    from core.project import Project

_GIT_AGENT_FALLBACK = """\
你是一个 Git 版本管理助手，帮助用户查看项目历史、撤销修改、恢复文件。

关键概念：
- git_history 输出的提交列表中，第一条是最新的提交，越往下越旧。
- "重置到某个提交" 意味着回到那个提交完成后的状态，该提交之后的所有提交会被丢弃。
- git_undo(steps=N) 会丢弃最近 N 个提交。如果用户想回到第 2 条提交的状态，steps=1（丢弃第 1 条）。
- 如果用户只是想丢弃未提交的修改（工作区改动），应该用 git_discard，而不是 git_undo。git_undo 是回退已提交的 commit。
- 恢复单个文件用 git_restore_file，不需要回退整个项目。

规则：
- 用中文回复，简洁明了。
- 执行破坏性操作（undo、discard、restore_file）前，必须先用 git_status 或 git_diff 展示影响范围，等用户明确确认后再执行。
- 不要编造信息，所有数据必须来自工具调用结果。
- 如果用户的请求不明确，主动询问澄清。"""

SYSTEM_PROMPT = render_prompt("agent_git.txt", _GIT_AGENT_FALLBACK)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "git_history",
            "description": "查看最近的提交历史。",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "显示最近 n 条提交，默认 10。"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "查看当前工作区状态（未提交的修改）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "查看与指定版本的差异。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "对比的 git 引用，默认 HEAD。"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_undo",
            "description": "撤销最近的提交（硬回退）。必须先展示影响再执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {"type": "integer", "description": "回退的提交数，默认 1。"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_restore_file",
            "description": "将指定文件恢复到某个版本。必须先展示差异再执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对于项目根目录）。"},
                    "ref": {"type": "string", "description": "恢复到的 git 引用，如 HEAD~1。"},
                },
                "required": ["path", "ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_discard",
            "description": "丢弃工作区中未提交的修改。支持已跟踪文件（恢复到最近提交）和未跟踪文件/目录（永久删除）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "指定文件路径（可选，不传则丢弃所有未提交修改）。"}
                },
            },
        },
    },
]


class GitAgent:
    """Lightweight agent for interactive git sub-sessions."""

    label = "Git"
    color = "magenta"
    exit_commands = ("/done", "/back", "/quit")

    def __init__(self, project: "Project", provider: Any, model: str | None = None):
        self.project = project
        self.provider = provider
        self.model = model
        self._history: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._actions: list[str] = []  # 记录执行过的操作，用于 summary

    async def process_message(
        self, user_input: str, on_token: Any | None = None
    ) -> str:
        """Process one user message, handle tool calls, return final text."""
        self._history.append({"role": "user", "content": user_input})

        max_rounds = 10
        for _ in range(max_rounds):
            try:
                response = await self.provider.chat(
                    messages=self._history,
                    tools=TOOL_DEFINITIONS,
                    model=self.model,
                    temperature=0.3,
                    on_token=on_token,
                )
            except Exception as e:
                logger.error(f"GitAgent LLM call failed: {e}")
                return f"LLM 调用失败：{e}"

            # LLM 返回错误
            if response.finish_reason == "error":
                logger.warning(f"GitAgent LLM error response: {response.content}")
                return response.content or "LLM 返回了错误，请重试。"

            if response.has_tool_calls:
                # Append assistant message with tool calls
                assistant_msg = {"role": "assistant", "content": response.content or ""}
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                self._history.append(assistant_msg)

                # Execute each tool call
                for tc in response.tool_calls:
                    logger.info(f"GitAgent executing tool: {tc.name}({tc.arguments})")
                    result = await self._execute_tool(tc.name, tc.arguments)
                    logger.info(f"GitAgent tool result: {result[:200]}")
                    self._history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                continue
            else:
                # Final text response
                text = response.content or ""
                if text:
                    self._history.append({"role": "assistant", "content": text})
                return text

        logger.warning(f"GitAgent hit max_rounds ({max_rounds})")
        return "达到最大轮次，请重新描述你的需求。"

    async def _execute_tool(self, name: str, args: dict) -> str:
        """Dispatch tool call to GitRepo methods."""
        git = self.project.git
        if not git:
            return "[ERROR] Git not available."

        try:
            if name == "git_history":
                n = args.get("n", 10)
                result = await asyncio.to_thread(git.log, n)
                return result or "没有提交记录。"

            elif name == "git_status":
                result = await asyncio.to_thread(git.status)
                return result

            elif name == "git_diff":
                ref = args.get("ref", "HEAD")
                result = await asyncio.to_thread(git.diff, ref)
                return result or "没有差异。"

            elif name == "git_undo":
                steps = args.get("steps", 1)
                result = await asyncio.to_thread(git.reset, f"HEAD~{steps}")
                if result.success:
                    self._actions.append(f"回退了 {steps} 个提交")
                    return f"已回退 {steps} 个提交。\n{result.output}"
                return f"回退失败：{result.error}"

            elif name == "git_restore_file":
                path = args.get("path", "")
                ref = args.get("ref", "HEAD~1")
                if not path:
                    return "[ERROR] path is required."
                result = await asyncio.to_thread(git.checkout_file, ref, path)
                if result.success:
                    self._actions.append(f"恢复了 {path}")
                    return f"已将 {path} 恢复到 {ref}。"
                return f"恢复失败：{result.error}"

            elif name == "git_discard":
                path = args.get("path", "")
                if path:
                    # Check if the path is untracked
                    check = await asyncio.to_thread(
                        git._run, "git", "ls-files", "--error-unmatch", path
                    )
                    if not check.success:
                        # Untracked file/dir — use git clean
                        result = await asyncio.to_thread(
                            git._run, "git", "clean", "-fd", path
                        )
                        if result.success:
                            self._actions.append(f"删除了未跟踪的 {path}")
                            return f"已删除未跟踪的 {path}。"
                        return f"删除失败：{result.error}"
                    else:
                        # Tracked file — use checkout
                        result = await asyncio.to_thread(git.checkout_file, "HEAD", path)
                        if result.success:
                            self._actions.append(f"丢弃了 {path} 的修改")
                            return f"已丢弃 {path} 的未提交修改。"
                        return f"丢弃失败：{result.error}"
                else:
                    # Discard all: checkout tracked + clean untracked
                    r1 = await asyncio.to_thread(git._run, "git", "checkout", "--", ".")
                    r2 = await asyncio.to_thread(git._run, "git", "clean", "-fd")
                    msgs = []
                    if r1.success:
                        msgs.append("已丢弃所有已跟踪文件的修改")
                    if r2.success and r2.output:
                        msgs.append(f"已删除未跟踪文件:\n{r2.output}")
                    elif r2.success:
                        msgs.append("无未跟踪文件需要清理")
                    if msgs:
                        self._actions.append("丢弃了所有未提交修改")
                        return "\n".join(msgs)
                    return f"丢弃失败：{r1.error} {r2.error}"

            else:
                return f"[ERROR] Unknown tool: {name}"

        except Exception as e:
            logger.error(f"GitAgent tool error ({name}): {e}")
            return f"[ERROR] {e}"

    def get_summary(self) -> str:
        """Return a summary of actions taken during this session."""
        if not self._actions:
            return ""
        return "；".join(self._actions)
