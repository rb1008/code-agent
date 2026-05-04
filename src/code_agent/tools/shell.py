"""Shell execution tools for Code Agent."""

import asyncio
from pathlib import Path
from typing import Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.config.models import ShellConfig
from code_agent.utils.paths import PathSecurityError, resolve_workspace_path
from code_agent.utils.sandbox import ShellSandbox
from code_agent.utils.security import split_safe_command


class BashTool(BaseTool):
    """Tool for executing bash commands."""

    name = "bash"
    aliases = ["shell", "command", "terminal"]
    search_hint = "运行 shell 命令 测试 构建 包管理"
    description = (
        "在 shell 中执行命令并返回 stdout/stderr。"
        "适合运行测试、构建、安装依赖、查看 git 状态等。"
    )
    parameters = {
        "command": {
            "type": "string",
            "description": "要执行的 bash 命令",
            "required": True,
        },
        "cwd": {
            "type": "string",
            "description": "命令工作目录，默认当前目录",
            "required": False,
        },
        "timeout": {
            "type": "integer",
            "description": "超时时间，单位秒，默认 30",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=False
    )

    def __init__(self, config: Optional[ShellConfig] = None):
        super().__init__(config or ShellConfig())
        self.permission = ToolPermission(
            require_confirmation=self.config.require_confirmation,
            allowed_in_auto_mode=not self.config.require_confirmation,
            destructive=False,
        )

    async def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> ToolResult:
        """Execute a bash command."""
        try:
            command_ok, reason, argv = split_safe_command(
                command,
                allowed_commands=self.config.allowed_commands,
                blocked_commands=self.config.blocked_commands,
            )
            if not command_ok:
                return ToolResult.fail(
                    f"命令因安全规则被拦截：{command}\n"
                    f"原因：{reason}"
                )

            # 设置工作目录
            working_dir = resolve_workspace_path(cwd or ".", self.config)
            if not working_dir.exists():
                return ToolResult.fail(f"工作目录不存在：{working_dir}")
            if not working_dir.is_dir():
                return ToolResult.fail(f"工作目录不是目录：{working_dir}")

            sandbox = ShellSandbox(self.config)
            sandbox_decision = sandbox.check(command, working_dir)
            if not sandbox_decision.allowed:
                return ToolResult.fail(
                    f"沙箱拦截了命令：{sandbox_decision.reason}",
                    sandboxed=False,
                    sandbox_reason=sandbox_decision.reason,
                )
            if not sandbox_decision.sandboxed and self.config.sandbox_fail_if_unavailable:
                return ToolResult.fail(
                    f"Sandbox unavailable for command: {sandbox_decision.reason}",
                    sandboxed=False,
                    sandbox_reason=sandbox_decision.reason,
                )
            
            # 设置超时
            cmd_timeout = timeout or self.config.timeout

            # 使用 argv 形式执行，避免 shell 复合命令绕过白名单。
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=cmd_timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult.fail(
                    f"命令执行超时（{cmd_timeout} 秒）：{command}"
                )

            # 解码输出
            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

            # 构建输出结果
            output_parts = []
            if stdout_str:
                output_parts.append(f"STDOUT:\n{stdout_str}")
            if stderr_str:
                output_parts.append(f"STDERR:\n{stderr_str}")

            output = "\n\n".join(output_parts) if output_parts else "(no output)"

            metadata = {
                "command": command,
                "return_code": process.returncode,
                "cwd": str(working_dir),
                "sandboxed": sandbox_decision.sandboxed,
                "sandbox_reason": sandbox_decision.reason,
            }
            if process.returncode == 0:
                return ToolResult.ok(output, **metadata)

            return ToolResult(
                success=False,
                output=output,
                error=f"命令退出码为 {process.returncode}：\n{output}",
                metadata=metadata,
            )

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except FileNotFoundError as e:
            return ToolResult.fail(f"未找到命令：{e.filename or command}")
        except Exception as e:
            return ToolResult.fail(f"执行命令失败：{str(e)}")


class GlobTool(BaseTool):
    """Tool for finding files matching a glob pattern."""

    name = "glob"
    description = "按 glob 模式查找文件，例如 '*.py' 或 'src/**/*.ts'。"
    aliases = ["find_files", "find"]
    search_hint = "按 glob 模式查找文件"
    parameters = {
        "pattern": {
            "type": "string",
            "description": "用于匹配文件的 glob 模式",
            "required": True,
        },
        "path": {
            "type": "string",
            "description": "搜索目录，默认当前目录",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[ShellConfig] = None):
        super().__init__(config or ShellConfig())

    async def execute(self, pattern: str, path: str = ".") -> ToolResult:
        """Find files matching a glob pattern."""
        try:
            import fnmatch
            import os

            search_path = resolve_workspace_path(path, self.config)
            
            if not search_path.exists():
                return ToolResult.fail(f"路径不存在：{path}")

            matches = []
            
            # 如果模式包含 **，使用递归搜索
            if "**" in pattern:
                for root, dirs, files in os.walk(search_path):
                    # 跳过被忽略的目录
                    dirs[:] = [
                        d for d in dirs 
                        if d not in [".git", "node_modules", "__pycache__"]
                    ]
                    
                    for filename in files:
                        filepath = Path(root) / filename
                        rel_path = filepath.relative_to(search_path)
                        if fnmatch.fnmatch(str(rel_path), pattern):
                            matches.append(str(filepath))
            else:
                # 非递归模式
                for item in search_path.glob(pattern):
                    if item.is_file():
                        matches.append(str(item))

            matches.sort()
            
            if not matches:
                return ToolResult.ok(f"未找到匹配模式的文件：{pattern}")

            output = f"找到 {len(matches)} 个匹配文件：'{pattern}'\n"
            output += "=" * 50 + "\n"
            output += "\n".join(matches)

            return ToolResult.ok(output, matches=len(matches), files=matches)

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"搜索文件失败：{str(e)}")


class GrepTool(BaseTool):
    """Tool for searching file contents using grep."""

    name = "grep"
    aliases = ["rg", "search_content"]
    search_hint = "正则搜索文件内容"
    description = (
        "使用 grep 在文件中搜索文本模式，支持正则表达式。"
    )
    parameters = {
        "pattern": {
            "type": "string",
            "description": "搜索模式，支持正则表达式",
            "required": True,
        },
        "path": {
            "type": "string",
            "description": "要搜索的目录或文件",
            "required": False,
        },
        "include": {
            "type": "string",
            "description": "限定参与搜索的文件 glob，例如 '*.py'",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[ShellConfig] = None):
        super().__init__(config or ShellConfig())

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        include: Optional[str] = None,
    ) -> ToolResult:
        """Search for pattern in files."""
        try:
            search_path = resolve_workspace_path(path, self.config)
            
            if not search_path.exists():
                return ToolResult.fail(f"路径不存在：{path}")

            # 构建 grep 命令
            cmd = ["grep", "-r", "-n", "-I", "--color=never"]
            
            if include:
                cmd.extend(["--include", include])
            
            # 排除常见忽略目录
            for exclude_dir in [".git", "node_modules", "__pycache__", ".venv", "venv"]:
                cmd.extend(["--exclude-dir", exclude_dir])
            
            cmd.extend([pattern, str(search_path)])

            # 执行命令
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30
            )

            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            
            if process.returncode == 0:
                lines = stdout_str.strip().split("\n")
                output = f"找到 {len(lines)} 处匹配：'{pattern}'\n"
                output += "=" * 50 + "\n"
                output += stdout_str
                return ToolResult.ok(output, matches=len(lines))
            elif process.returncode == 1:
                return ToolResult.ok(f"未找到匹配：'{pattern}'")
            else:
                stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""
                return ToolResult.fail(f"搜索失败：{stderr_str}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 grep 命令")
        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except asyncio.TimeoutError:
            return ToolResult.fail("搜索超时（30 秒）")
        except Exception as e:
            return ToolResult.fail(f"搜索失败：{str(e)}")
