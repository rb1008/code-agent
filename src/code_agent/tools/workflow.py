"""Project workflow script tools."""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from typing import Any, Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.utils.sandbox import ShellSandbox


class WorkflowListTool(BaseTool):
    """List workflow scripts."""

    name = "workflow_list"
    aliases = ["workflows"]
    search_hint = "列出 项目 workflow 脚本"
    description = "列出 .code-agent/workflows 中的项目本地 workflow 脚本。"
    parameters: dict[str, Any] = {}
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    def __init__(self, workflows_dir: Path | str | None = None) -> None:
        super().__init__()
        self.workflows_dir = Path(workflows_dir) if workflows_dir else Path(".code-agent/workflows")

    async def execute(self) -> ToolResult:
        workflows = _list_workflows(self.workflows_dir)
        if not workflows:
            return ToolResult.ok("未找到可执行的 workflow。")
        return ToolResult.ok(
            "可用 workflow：\n" + "\n".join(f"- {path.name}" for path in workflows),
            workflows=[path.name for path in workflows],
        )


class WorkflowRunTool(BaseTool):
    """Run a workflow script."""

    name = "workflow_run"
    aliases = ["run_workflow"]
    search_hint = "运行 项目 workflow 脚本"
    description = "运行 .code-agent/workflows 中的项目本地 workflow 脚本。"
    parameters = {
        "name": {"type": "string", "description": "workflow 文件名", "required": True},
        "arguments": {
            "type": "string",
            "description": "传给 workflow 脚本的参数",
            "required": False,
        },
        "timeout": {
            "type": "integer",
            "description": "超时时间，单位秒",
            "required": False,
        },
    }
    permission = ToolPermission(require_confirmation=True, allowed_in_auto_mode=False)

    def __init__(self, workflows_dir: Path | str | None = None, shell_config: object | None = None) -> None:
        super().__init__()
        self.workflows_dir = Path(workflows_dir) if workflows_dir else Path(".code-agent/workflows")
        self.shell_config = shell_config

    async def execute(
        self,
        name: str,
        arguments: str = "",
        timeout: int = 120,
    ) -> ToolResult:
        workflow = _resolve_workflow(self.workflows_dir, name)
        if not workflow:
            return ToolResult.fail(f"未找到 workflow：{name}")
        if self.shell_config:
            decision = ShellSandbox(self.shell_config).check(str(workflow), workflow.parent)
            if not decision.allowed:
                return ToolResult.fail(f"workflow 被沙箱拦截：{decision.reason}")

        try:
            parsed_arguments = shlex.split(arguments)
        except ValueError as e:
            return ToolResult.fail(f"workflow 参数无效：{e}")

        cmd = [str(workflow), *parsed_arguments]
        env = os.environ.copy()
        env["CODE_AGENT_WORKFLOW"] = workflow.name
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workflow.parent),
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult.fail(f"workflow 执行超时（{timeout}s）：{name}")
        output = _decode(stdout)
        err = _decode(stderr)
        combined = "\n".join(part for part in [output, err] if part).strip() or "(no output)"
        if process.returncode == 0:
            return ToolResult.ok(combined, workflow=workflow.name, return_code=process.returncode)
        return ToolResult(
            success=False,
            output=combined,
            error=f"workflow 退出码为 {process.returncode}：\n{combined}",
            metadata={"workflow": workflow.name, "return_code": process.returncode},
        )


def _list_workflows(workflows_dir: Path) -> list[Path]:
    if not workflows_dir.exists():
        return []
    return sorted(path for path in workflows_dir.iterdir() if path.is_file() and os.access(path, os.X_OK))


def _resolve_workflow(workflows_dir: Path, name: str) -> Optional[Path]:
    candidate = (workflows_dir / name).resolve()
    root = workflows_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.exists() and candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def _decode(data: bytes | None) -> str:
    return data.decode("utf-8", errors="replace") if data else ""
