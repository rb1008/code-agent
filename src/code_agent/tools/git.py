"""Git operation tools for Code Agent."""

import asyncio
import shlex
from pathlib import Path
from typing import Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult


async def _run_git(args: list[str], *, timeout: int = 30) -> tuple[int, str, str]:
    """Run git without blocking the event loop used by the window UI."""
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(Path.cwd()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return 124, "", f"git 命令超时（>{timeout}s）：git {' '.join(args)}"

    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _not_git_repo_message(stderr: str) -> Optional[str]:
    """Return a friendly message for the common non-repository failure."""
    if "not a git repository" in stderr.lower():
        return "当前目录不是 git 仓库。请在仓库根目录运行 git 工具，或先初始化 git。"
    return None


class GitStatusTool(BaseTool):
    """Tool for checking git repository status."""

    name = "git_status"
    description = "查看 git 仓库状态，包括修改文件、暂存区变更等。"
    aliases = ["status"]
    search_hint = "git 工作区 状态"
    parameters = {}
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    async def execute(self) -> ToolResult:
        """Get git status."""
        try:
            returncode, stdout, stderr = await _run_git(["status"])

            if returncode == 0:
                return ToolResult.ok(stdout)
            friendly = _not_git_repo_message(stderr.strip())
            if friendly:
                return ToolResult.fail(friendly)
            return ToolResult.fail(f"git status 失败：{stderr.strip()}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 git 命令")
        except Exception as e:
            return ToolResult.fail(f"获取 git 状态失败：{str(e)}")


class GitDiffTool(BaseTool):
    """Tool for viewing git diffs."""

    name = "git_diff"
    description = "查看 commit、分支、暂存区或工作区之间的差异。"
    aliases = ["diff"]
    search_hint = "git diff 变更"
    parameters = {
        "staged": {
            "type": "boolean",
            "description": "是否查看暂存区变更，默认 false",
            "required": False,
        },
        "file": {
            "type": "string",
            "description": "只查看指定文件的 diff",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    async def execute(
        self, staged: bool = False, file: Optional[str] = None
    ) -> ToolResult:
        """Get git diff."""
        try:
            args = ["diff"]
            if staged:
                args.append("--staged")
            if file:
                args.extend(["--", file])

            returncode, stdout, stderr = await _run_git(args)

            if returncode == 0:
                output = stdout if stdout else "未发现差异"
                return ToolResult.ok(output)
            friendly = _not_git_repo_message(stderr.strip())
            if friendly:
                return ToolResult.fail(friendly)
            return ToolResult.fail(f"git diff 失败：{stderr.strip()}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 git 命令")
        except Exception as e:
            return ToolResult.fail(f"获取 git diff 失败：{str(e)}")


class GitLogTool(BaseTool):
    """Tool for viewing git commit history."""

    name = "git_log"
    description = "查看 git 提交历史。"
    aliases = ["log"]
    search_hint = "git 提交历史"
    parameters = {
        "n": {
            "type": "integer",
            "description": "显示的提交数量，默认 10",
            "required": False,
        },
        "file": {
            "type": "string",
            "description": "只查看指定文件的历史",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    async def execute(
        self, n: int = 10, file: Optional[str] = None
    ) -> ToolResult:
        """Get git log."""
        try:
            args = ["log", f"-{n}", "--oneline", "--decorate"]
            if file:
                args.extend(["--", file])

            returncode, stdout, stderr = await _run_git(args)

            if returncode == 0:
                return ToolResult.ok(stdout)
            friendly = _not_git_repo_message(stderr.strip())
            if friendly:
                return ToolResult.fail(friendly)
            return ToolResult.fail(f"git log 失败：{stderr.strip()}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 git 命令")
        except Exception as e:
            return ToolResult.fail(f"获取 git log 失败：{str(e)}")


class GitAddTool(BaseTool):
    """Tool for staging files."""

    name = "git_add"
    description = "将文件加入 git 暂存区。"
    aliases = ["stage"]
    search_hint = "git 暂存 文件"
    parameters = {
        "files": {
            "type": "string",
            "description": "要暂存的文件，空格分隔；'.' 表示全部",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=False
    )

    async def execute(self, files: str) -> ToolResult:
        """Stage files."""
        try:
            parsed_files = shlex.split(files)
            if not parsed_files:
                return ToolResult.fail("缺少要暂存的文件。")
            returncode, _stdout, stderr = await _run_git(["add", "--", *parsed_files])

            if returncode == 0:
                return ToolResult.ok(f"已加入暂存区：{files}")
            friendly = _not_git_repo_message(stderr.strip())
            if friendly:
                return ToolResult.fail(friendly)
            return ToolResult.fail(f"git add 失败：{stderr.strip()}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 git 命令")
        except ValueError as e:
            return ToolResult.fail(f"文件参数解析失败：{str(e)}")
        except Exception as e:
            return ToolResult.fail(f"暂存文件失败：{str(e)}")


class GitCommitTool(BaseTool):
    """Tool for creating commits."""

    name = "git_commit"
    description = "使用给定提交信息创建 git commit。"
    aliases = ["commit"]
    search_hint = "git commit 提交"
    parameters = {
        "message": {
            "type": "string",
            "description": "提交信息",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=False
    )

    async def execute(self, message: str) -> ToolResult:
        """Create a commit."""
        try:
            returncode, _stdout, stderr = await _run_git(["commit", "-m", message])

            if returncode == 0:
                return ToolResult.ok(f"已创建提交：{message}")
            friendly = _not_git_repo_message(stderr.strip())
            if friendly:
                return ToolResult.fail(friendly)
            return ToolResult.fail(f"git commit 失败：{stderr.strip()}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 git 命令")
        except Exception as e:
            return ToolResult.fail(f"创建提交失败：{str(e)}")


class GitBranchTool(BaseTool):
    """Tool for managing branches."""

    name = "git_branch"
    description = "列出、创建、删除或切换 git 分支。"
    aliases = ["branch"]
    search_hint = "git 分支 列出 创建 删除 切换"
    parameters = {
        "action": {
            "type": "string",
            "description": "动作：list、create、delete、switch",
            "required": True,
        },
        "name": {
            "type": "string",
            "description": "分支名，用于 create/delete/switch",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=True
    )

    async def execute(
        self, action: str, name: Optional[str] = None
    ) -> ToolResult:
        """Manage branches."""
        try:
            if action == "list":
                returncode, stdout, stderr = await _run_git(["branch", "-a"])
            elif action == "create" and name:
                returncode, stdout, stderr = await _run_git(["branch", name])
            elif action == "delete" and name:
                returncode, stdout, stderr = await _run_git(["branch", "-d", name])
            elif action == "switch" and name:
                returncode, stdout, stderr = await _run_git(["switch", name])
            else:
                return ToolResult.fail(f"无效操作或缺少分支名：{action}")

            if returncode == 0:
                return ToolResult.ok(stdout if stdout else f"分支操作成功：{action}")
            friendly = _not_git_repo_message(stderr.strip())
            if friendly:
                return ToolResult.fail(friendly)
            return ToolResult.fail(f"git branch 失败：{stderr.strip()}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 git 命令")
        except Exception as e:
            return ToolResult.fail(f"管理分支失败：{str(e)}")
