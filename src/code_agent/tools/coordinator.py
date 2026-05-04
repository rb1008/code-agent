"""Coordinator and fork sub-agent tools."""

from __future__ import annotations

from typing import Optional

from code_agent.agent.memory import ConversationMemory
from code_agent.config.models import Settings
from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.ui.permission import PermissionManager
from code_agent.utils.subagents import SubAgentRunner


class ForkAgentTool(BaseTool):
    """Run one isolated forked sub-agent."""

    name = "fork_agent"
    aliases = ["fork"]
    search_hint = "派生 隔离 子 agent"
    description = "基于当前上下文派生一个隔离子 agent 来执行单个任务。"
    parameters = {
        "task": {"type": "string", "description": "交给子 agent 的任务", "required": True},
        "title": {"type": "string", "description": "任务短标题", "required": False},
    }
    permission = ToolPermission(require_confirmation=True, allowed_in_auto_mode=False)

    def __init__(
        self,
        settings: Settings,
        memory: Optional[ConversationMemory] = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.memory = memory
        self.permission_manager: Optional[PermissionManager] = None

    async def execute(self, task: str, title: str = "fork") -> ToolResult:
        result = await SubAgentRunner(self.settings).run(
            title=title or "fork",
            prompt=task,
            inherited_memory=self.memory,
            permission_manager=self.permission_manager,
        )
        return ToolResult.ok(
            f"子 Agent 已完成：{result.title}\n\n{result.output}",
            title=result.title,
        )


class CoordinatorRunTool(BaseTool):
    """Run several worker prompts concurrently and synthesize their outputs."""

    name = "coordinator_run"
    aliases = ["coordinate_workers"]
    search_hint = "并行 运行 worker 子 agent"
    description = (
        "并发运行多个 worker 子 agent。"
        "tasks 使用多行 '标题: 任务' 格式。"
    )
    parameters = {
        "tasks": {
            "type": "string",
            "description": "多行 worker 任务，每行格式为 '标题: 任务'",
            "required": True,
        }
    }
    permission = ToolPermission(require_confirmation=True, allowed_in_auto_mode=False)

    def __init__(
        self,
        settings: Settings,
        memory: Optional[ConversationMemory] = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.memory = memory
        self.permission_manager: Optional[PermissionManager] = None

    async def execute(self, tasks: str) -> ToolResult:
        parsed = _parse_tasks(tasks)
        if not parsed:
            return ToolResult.fail("没有解析到 worker 任务。请使用类似 'research: inspect tests' 的多行格式。")
        results = await SubAgentRunner(self.settings).run_many(
            parsed,
            inherited_memory=self.memory,
            permission_manager=self.permission_manager,
        )
        output = ["Coordinator worker 结果："]
        for result in results:
            output.append(f"\n## {result.title}\n{result.output}")
        return ToolResult.ok("\n".join(output), workers=len(results))


def _parse_tasks(tasks: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for index, line in enumerate(tasks.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped:
            title, prompt = stripped.split(":", 1)
        else:
            title, prompt = f"worker-{index}", stripped
        if prompt.strip():
            parsed.append((title.strip() or f"worker-{index}", prompt.strip()))
    return parsed
