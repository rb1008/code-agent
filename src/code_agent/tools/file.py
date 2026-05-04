"""File operation tools for Code Agent."""

import asyncio
from pathlib import Path
from typing import Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.config.models import FileConfig
from code_agent.utils.paths import (
    PathSecurityError,
    ensure_allowed_extension,
    ensure_file_size,
    is_blocked_path,
    resolve_workspace_path,
)


class ReadFileTool(BaseTool):
    """Tool for reading file contents."""

    name = "read_file"
    description = "读取文件内容，支持按行号范围读取，返回文本内容。"
    aliases = ["read", "cat", "head", "tail"]
    search_hint = "读取 查看 文件 内容"
    parameters = {
        "path": {
            "type": "string",
            "description": "要读取的文件路径，通常相对于当前工作区",
            "required": True,
        },
        "offset": {
            "type": "integer",
            "description": "开始读取的行号，从 1 开始",
            "required": False,
        },
        "limit": {
            "type": "integer",
            "description": "最多读取的行数",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(
        self, path: str, offset: Optional[int] = None, limit: Optional[int] = None
    ) -> ToolResult:
        """Read a file and return its contents."""
        try:
            file_path = resolve_workspace_path(path, self.config)
            ensure_allowed_extension(file_path, self.config)

            if not file_path.exists():
                return ToolResult.fail(f"文件不存在：{path}")

            if not file_path.is_file():
                return ToolResult.fail(f"路径不是文件：{path}")

            ensure_file_size(file_path, self.config.max_file_size)

            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            total_lines = len(lines)

            if offset is not None:
                start = max(0, offset - 1)
                lines = lines[start:]

            if limit is not None:
                lines = lines[:limit]

            content = "".join(lines)

            result = f"文件：{path}\n"
            result += f"行数：{offset or 1}-{min((offset or 1) + len(lines) - 1, total_lines)} / 共 {total_lines} 行\n"
            result += "=" * 50 + "\n"
            result += content

            return ToolResult.ok(
                result, path=str(file_path), total_lines=total_lines, lines_read=len(lines)
            )

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except UnicodeDecodeError:
            return ToolResult.fail(f"文件不是可读取的文本文件：{path}")
        except Exception as e:
            return ToolResult.fail(f"读取文件失败：{str(e)}")


class WriteFileTool(BaseTool):
    """Tool for writing/creating files."""

    name = "write_file"
    description = "创建新文件或用给定内容覆盖已有文件。"
    aliases = ["write", "create_file"]
    search_hint = "创建 覆盖 文件"
    parameters = {
        "path": {
            "type": "string",
            "description": "要写入的文件路径，通常相对于当前工作区",
            "required": True,
        },
        "content": {
            "type": "string",
            "description": "要写入文件的内容",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=True
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(self, path: str, content: str) -> ToolResult:
        """Write content to a file."""
        try:
            file_path = resolve_workspace_path(path, self.config)
            # 写入工具已经是破坏性工具，执行前会经过用户确认；这里继续保留
            # 工作区边界、blocked_paths 和大小限制，但不再用扩展名白名单拦住
            # Dockerfile、Makefile、.gitignore、HTML/CSS 等真实项目文件创建。

            content_size = len(content.encode("utf-8"))
            if content_size > self.config.max_file_size:
                return ToolResult.fail(
                    f"内容过大（{content_size} bytes）。"
                    f"允许的最大值：{self.config.max_file_size} bytes"
                )

            if file_path.exists() and not file_path.is_file():
                return ToolResult.fail(f"路径已存在但不是文件：{path}")

            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            return ToolResult.ok(
                f"已写入 {len(content)} 个字符到 {path}",
                path=str(file_path),
                size=len(content),
            )

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"写入文件失败：{str(e)}")


class ListDirectoryTool(BaseTool):
    """Tool for listing directory contents."""

    name = "list_directory"
    description = "列出目录中的文件和子目录，可选择递归展示。"
    aliases = ["ls", "dir"]
    search_hint = "列出 目录 文件"
    parameters = {
        "path": {
            "type": "string",
            "description": "要列出的目录路径，默认当前目录",
            "required": False,
        },
        "recursive": {
            "type": "boolean",
            "description": "是否递归列出子目录",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(
        self, path: str = ".", recursive: bool = False
    ) -> ToolResult:
        """List directory contents."""
        try:
            dir_path = resolve_workspace_path(path, self.config)

            if not dir_path.exists():
                return ToolResult.fail(f"目录不存在：{path}")

            if not dir_path.is_dir():
                return ToolResult.fail(f"路径不是目录：{path}")

            def should_ignore(p: Path) -> bool:
                return is_blocked_path(p, self.config)

            lines = [f"目录：{dir_path}", "=" * 50]

            if recursive:
                for item in sorted(dir_path.rglob("*")):
                    if should_ignore(item):
                        continue
                    rel_path = item.relative_to(dir_path)
                    prefix = "  " * len(rel_path.parts)
                    if item.is_dir():
                        lines.append(f"{prefix}📁 {item.name}/")
                    else:
                        size = item.stat().st_size
                        lines.append(f"{prefix}📄 {item.name} ({self._format_size(size)})")
            else:
                items = sorted(dir_path.iterdir())
                dirs = [item for item in items if item.is_dir()]
                files = [item for item in items if item.is_file()]

                for item in dirs:
                    if should_ignore(item):
                        continue
                    lines.append(f"📁 {item.name}/")

                for item in files:
                    if should_ignore(item):
                        continue
                    size = item.stat().st_size
                    lines.append(f"📄 {item.name} ({self._format_size(size)})")

            return ToolResult.ok("\n".join(lines), path=str(dir_path))

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"列出目录失败：{str(e)}")

    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        size_value = float(size)
        for unit in ["B", "KB", "MB", "GB"]:
            if size_value < 1024:
                return f"{size_value:.1f} {unit}"
            size_value /= 1024
        return f"{size_value:.1f} TB"


class SearchFilesTool(BaseTool):
    """Tool for searching files by content pattern."""

    name = "search_files"
    description = "在文件内容中搜索文本或正则模式。"
    aliases = ["search", "grep_files"]
    search_hint = "搜索 文件 内容"
    parameters = {
        "pattern": {
            "type": "string",
            "description": "搜索模式，支持正则表达式",
            "required": True,
        },
        "path": {
            "type": "string",
            "description": "搜索目录，默认当前目录",
            "required": False,
        },
        "file_pattern": {
            "type": "string",
            "description": "用于过滤文件的 glob 模式，例如 '*.py'",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: Optional[str] = None,
    ) -> ToolResult:
        """Search for files containing the pattern."""
        try:
            search_path = resolve_workspace_path(path, self.config)

            if not search_path.exists():
                return ToolResult.fail(f"路径不存在：{path}")

            cmd = ["grep", "-r", "-n", "-I", pattern]

            if file_pattern:
                cmd.extend(["--include", file_pattern])

            for blocked in self.config.blocked_paths:
                cmd.extend(["--exclude-dir", blocked])

            cmd.append(str(search_path))

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult.fail("搜索超时")

            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

            if process.returncode == 0:
                lines = stdout_text.strip().split("\n")
                output = f"找到 {len(lines)} 处匹配：'{pattern}'\n"
                output += "=" * 50 + "\n"
                output += stdout_text
                return ToolResult.ok(output, matches=len(lines))
            elif process.returncode == 1:
                return ToolResult.ok(f"未找到匹配：'{pattern}'")
            else:
                return ToolResult.fail(f"搜索失败：{stderr_text}")

        except FileNotFoundError:
            return ToolResult.fail("未找到 grep 命令，请先安装 grep。")
        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"搜索文件失败：{str(e)}")


class FileExistsTool(BaseTool):
    """Tool for checking if a file exists."""

    name = "file_exists"
    description = "检查文件或目录是否存在。"
    aliases = ["exists", "stat"]
    search_hint = "检查 路径 是否存在"
    parameters = {
        "path": {
            "type": "string",
            "description": "要检查的路径",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(self, path: str) -> ToolResult:
        """Check if a path exists."""
        try:
            file_path = resolve_workspace_path(path, self.config)
            exists = file_path.exists()
            is_file = file_path.is_file() if exists else False
            is_dir = file_path.is_dir() if exists else False

            if exists:
                if is_file:
                    size = file_path.stat().st_size
                    return ToolResult.ok(
                        f"文件存在：{path}（{size} bytes）",
                        exists=True,
                        type="file",
                        size=size,
                    )
                elif is_dir:
                    return ToolResult.ok(
                        f"目录存在：{path}",
                        exists=True,
                        type="directory",
                    )

            return ToolResult.ok(f"路径不存在：{path}", exists=False)

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"检查路径失败：{str(e)}")
