"""Session-local monitor tools for long-running commands."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.utils.paths import PathSecurityError, resolve_workspace_path
from code_agent.utils.sandbox import ShellSandbox
from code_agent.utils.security import split_safe_command


@dataclass
class MonitorTask:
    """One background monitor task."""

    id: str
    command: str
    cwd: str
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    process: Any = None
    output: str = ""
    max_output_chars: int = 12000

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.returncode is None)

    def append(self, text: str) -> None:
        self.output += text
        if len(self.output) > self.max_output_chars:
            self.output = self.output[-self.max_output_chars :]


class MonitorManager:
    """Singleton monitor manager for the current Python process."""

    _instance: ClassVar[Optional["MonitorManager"]] = None

    def __new__(cls) -> "MonitorManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.tasks = {}
        return cls._instance

    tasks: dict[str, MonitorTask]

    async def start(
        self,
        argv: list[str],
        command: str,
        cwd: Path,
        max_output_chars: int,
    ) -> MonitorTask:
        task_id = str(uuid.uuid4())[:8]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
        )
        task = MonitorTask(
            id=task_id,
            command=command,
            cwd=str(cwd),
            process=process,
            max_output_chars=max_output_chars,
        )
        self.tasks[task_id] = task
        asyncio.create_task(self._pump(task))
        return task

    async def _pump(self, task: MonitorTask) -> None:
        if not task.process or not task.process.stdout:
            return
        while True:
            line = await task.process.stdout.readline()
            if not line:
                break
            task.append(line.decode("utf-8", errors="replace"))
        await task.process.wait()

    def list(self) -> list[MonitorTask]:
        return list(self.tasks.values())

    def get(self, task_id: str) -> Optional[MonitorTask]:
        return self.tasks.get(task_id)

    async def stop(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or not task.process or task.process.returncode is not None:
            return False
        task.process.terminate()
        try:
            await asyncio.wait_for(task.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            task.process.kill()
            await task.process.wait()
        return True

    async def stop_all(self) -> None:
        for task_id in list(self.tasks):
            await self.stop(task_id)

    async def clear(self) -> None:
        """Stop and forget all monitor tasks."""
        await self.stop_all()
        self.tasks.clear()


class MonitorStartTool(BaseTool):
    """Start a background monitor command."""

    name = "monitor_start"
    aliases = ["monitor"]
    search_hint = "启动 后台 监控 命令"
    description = "在当前会话中启动后台命令，并保留最近输出。"
    parameters = {
        "command": {"type": "string", "description": "要监控的命令", "required": True},
        "cwd": {"type": "string", "description": "工作目录", "required": False},
    }
    permission = ToolPermission(require_confirmation=True, allowed_in_auto_mode=False)

    def __init__(self, shell_config: object, max_output_chars: int = 12000) -> None:
        super().__init__()
        self.shell_config = shell_config
        self.max_output_chars = max_output_chars
        self.manager = MonitorManager()

    async def execute(self, command: str, cwd: str = ".") -> ToolResult:
        try:
            command_ok, reason, argv = split_safe_command(
                command,
                allowed_commands=getattr(self.shell_config, "allowed_commands", []),
                blocked_commands=getattr(self.shell_config, "blocked_commands", []),
            )
            if not command_ok:
                return ToolResult.fail(f"后台监控命令被安全规则拦截：{reason}")
            working_dir = resolve_workspace_path(cwd, self.shell_config)
            decision = ShellSandbox(self.shell_config).check(command, working_dir)
            if not decision.allowed:
                return ToolResult.fail(f"后台监控被沙箱拦截：{decision.reason}")
            task = await self.manager.start(argv, command, working_dir, self.max_output_chars)
            return ToolResult.ok(
                f"已启动后台监控：[{task.id}] {command}",
                monitor_id=task.id,
                command=command,
            )
        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except FileNotFoundError as e:
            return ToolResult.fail(f"未找到命令：{e.filename or command}")


class MonitorListTool(BaseTool):
    """List monitor tasks."""

    name = "monitor_list"
    aliases = ["monitors"]
    search_hint = "列出 后台 监控 任务"
    description = "列出当前会话中的后台监控任务。"
    parameters: dict[str, Any] = {}
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    def __init__(self) -> None:
        super().__init__()
        self.manager = MonitorManager()

    async def execute(self) -> ToolResult:
        tasks = self.manager.list()
        if not tasks:
            return ToolResult.ok("当前没有后台监控任务。")
        lines = ["后台监控任务："]
        for task in tasks:
            state = "运行中" if task.running else "已退出"
            lines.append(f"- [{task.id}] {state}: {task.command}")
        return ToolResult.ok("\n".join(lines), count=len(tasks))


class MonitorReadTool(BaseTool):
    """Read monitor output."""

    name = "monitor_read"
    aliases = ["monitor_output"]
    search_hint = "读取 监控 输出"
    description = "读取某个监控任务保留的最近输出。"
    parameters = {
        "monitor_id": {"type": "string", "description": "监控任务 ID", "required": True}
    }
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    def __init__(self) -> None:
        super().__init__()
        self.manager = MonitorManager()

    async def execute(self, monitor_id: str) -> ToolResult:
        task = self.manager.get(monitor_id)
        if not task:
            return ToolResult.fail(f"未找到后台监控任务：{monitor_id}")
        return ToolResult.ok(task.output or "（暂时没有输出）", running=task.running)


class MonitorStopTool(BaseTool):
    """Stop monitor task."""

    name = "monitor_stop"
    aliases = ["stop_monitor"]
    search_hint = "停止 监控 任务"
    description = "停止一个后台监控任务。"
    parameters = {
        "monitor_id": {"type": "string", "description": "监控任务 ID", "required": True}
    }
    permission = ToolPermission(require_confirmation=True, allowed_in_auto_mode=False)

    def __init__(self) -> None:
        super().__init__()
        self.manager = MonitorManager()

    async def execute(self, monitor_id: str) -> ToolResult:
        stopped = await self.manager.stop(monitor_id)
        if not stopped:
            return ToolResult.fail(f"后台监控任务未运行或不存在：{monitor_id}")
        return ToolResult.ok(f"已停止后台监控：{monitor_id}", monitor_id=monitor_id)
