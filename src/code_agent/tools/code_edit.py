"""Code editing tools for Code Agent."""

import difflib
from pathlib import Path
from typing import Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.config.models import FileConfig
from code_agent.utils.paths import (
    PathSecurityError,
    ensure_allowed_extension,
    ensure_file_size,
    resolve_workspace_path,
)


class ReplaceCodeTool(BaseTool):
    """Tool for replacing code snippets in files."""

    name = "replace_code"
    aliases = ["edit", "replace", "file_edit"]
    search_hint = "替换 精确 代码片段"
    description = (
        "把文件中的精确代码片段替换为新内容。"
        "old_string 必须和文件内容完全一致，包括空格和换行。"
    )
    parameters = {
        "path": {
            "type": "string",
            "description": "要编辑的文件路径",
            "required": True,
        },
        "old_string": {
            "type": "string",
            "description": "要替换的精确字符串，必须完全匹配文件内容",
            "required": True,
        },
        "new_string": {
            "type": "string",
            "description": "用于替换 old_string 的新字符串",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=True
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(self, path: str, old_string: str, new_string: str) -> ToolResult:
        """Replace a code snippet in a file."""
        try:
            file_path = self._prepare_file(path)

            if not file_path.exists():
                return ToolResult.fail(f"文件不存在：{path}")

            if not file_path.is_file():
                return ToolResult.fail(f"路径不是文件：{path}")

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if old_string not in content:
                # 尝试查找相似内容，提供有用的错误信息
                lines = content.split("\n")
                old_lines = old_string.split("\n")
                
                # 尝试逐行匹配，找出最接近的行
                best_match = None
                best_score = 0.0
                for i, line in enumerate(lines):
                    if old_lines[0] in line:
                        score = self._similarity("\n".join(lines[i:i + len(old_lines)]), old_string)
                        if score > best_score:
                            best_score = score
                            best_match = (i + 1, "\n".join(lines[i:i + len(old_lines)]))
                
                error_msg = f"文件中未找到 old_string：{path}\n"
                if best_match and best_score > 0.5:
                    error_msg += (
                        f"\n你是否想匹配这一段（第 {best_match[0]} 行，"
                        f"相似度 {best_score:.0%}）：\n{best_match[1]}"
                    )
                return ToolResult.fail(error_msg)

            new_content = content.replace(old_string, new_string, 1)
            self._ensure_content_size(new_content)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            # 生成 diff 用于展示
            diff = self._generate_diff(content, new_content, path)

            return ToolResult.ok(
                f"已替换 {path} 中的代码\n\nDiff:\n{diff}",
                path=str(file_path),
                lines_changed=abs(content.count("\n") - new_content.count("\n")),
            )

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"替换代码失败：{str(e)}")

    def _prepare_file(self, path: str) -> Path:
        """Resolve and validate an editable file path."""
        file_path = resolve_workspace_path(path, self.config)
        ensure_allowed_extension(file_path, self.config)
        if file_path.exists():
            ensure_file_size(file_path, self.config.max_file_size)
        return file_path

    def _ensure_content_size(self, content: str) -> None:
        """Ensure edited content stays within the configured file size limit."""
        size = len(content.encode("utf-8"))
        if size > self.config.max_file_size:
            raise PathSecurityError(
                f"编辑后的内容过大（{size} bytes）。"
                f"允许的最大值：{self.config.max_file_size} bytes"
            )

    def _similarity(self, a: str, b: str) -> float:
        """计算两个字符串的相似度。"""
        return difflib.SequenceMatcher(None, a, b).ratio()

    def _generate_diff(self, old: str, new: str, path: str) -> str:
        """生成统一格式的 diff。"""
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines, new_lines, fromfile=path, tofile=path, lineterm=""
        )
        return "".join(diff)


class InsertCodeTool(BaseTool):
    """Tool for inserting code at specific positions."""

    name = "insert_code"
    description = "在文件指定位置插入代码，可按精确字符串后方或行号插入。"
    aliases = ["insert", "append_code"]
    search_hint = "插入 代码 行号"
    parameters = {
        "path": {
            "type": "string",
            "description": "要编辑的文件路径",
            "required": True,
        },
        "new_string": {
            "type": "string",
            "description": "要插入的代码",
            "required": True,
        },
        "insert_after": {
            "type": "string",
            "description": "在这个精确字符串之后插入，可选；与 line_number 二选一",
            "required": False,
        },
        "line_number": {
            "type": "integer",
            "description": "插入位置行号，从 1 开始，可选",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=True
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(
        self,
        path: str,
        new_string: str,
        insert_after: Optional[str] = None,
        line_number: Optional[int] = None,
    ) -> ToolResult:
        """Insert code at a specific position."""
        try:
            file_path = resolve_workspace_path(path, self.config)
            ensure_allowed_extension(file_path, self.config)

            if not file_path.exists():
                return ToolResult.fail(f"文件不存在：{path}")

            if not file_path.is_file():
                return ToolResult.fail(f"路径不是文件：{path}")

            ensure_file_size(file_path, self.config.max_file_size)

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if insert_after:
                if insert_after not in content:
                    return ToolResult.fail(f"未找到 insert_after 字符串：{insert_after}")
                new_content = content.replace(insert_after, insert_after + new_string, 1)
            elif line_number:
                lines = content.split("\n")
                if line_number < 1 or line_number > len(lines) + 1:
                    return ToolResult.fail(
                        f"行号 {line_number} 超出范围（1-{len(lines) + 1}）"
                    )
                lines.insert(line_number - 1, new_string)
                new_content = "\n".join(lines)
            else:
                return ToolResult.fail("必须提供 insert_after 或 line_number")

            size = len(new_content.encode("utf-8"))
            if size > self.config.max_file_size:
                return ToolResult.fail(
                    f"编辑后的内容过大（{size} bytes）。"
                    f"允许的最大值：{self.config.max_file_size} bytes"
                )

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return ToolResult.ok(
                f"已向 {path} 插入代码（位置：{line_number or '匹配内容之后'}）",
                path=str(file_path),
            )

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"插入代码失败：{str(e)}")


class DeleteCodeTool(BaseTool):
    """Tool for deleting code snippets."""

    name = "delete_code"
    description = "从文件中删除一个精确匹配的代码片段。"
    aliases = ["delete", "remove_code"]
    search_hint = "删除 精确 代码片段"
    parameters = {
        "path": {
            "type": "string",
            "description": "要编辑的文件路径",
            "required": True,
        },
        "target_string": {
            "type": "string",
            "description": "要删除的精确字符串",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=True
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(self, path: str, target_string: str) -> ToolResult:
        """Delete a code snippet from a file."""
        try:
            file_path = resolve_workspace_path(path, self.config)
            ensure_allowed_extension(file_path, self.config)

            if not file_path.exists():
                return ToolResult.fail(f"文件不存在：{path}")

            if not file_path.is_file():
                return ToolResult.fail(f"路径不是文件：{path}")

            ensure_file_size(file_path, self.config.max_file_size)

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if target_string not in content:
                return ToolResult.fail(f"文件中未找到 target_string：{path}")

            new_content = content.replace(target_string, "", 1)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return ToolResult.ok(
                f"已从 {path} 删除代码",
                path=str(file_path),
            )

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"删除代码失败：{str(e)}")


class ApplyDiffTool(BaseTool):
    """Tool for applying unified diff patches."""

    name = "apply_diff"
    description = "将 unified diff 补丁应用到文件。"
    aliases = ["patch", "diff"]
    search_hint = "应用 unified diff 补丁"
    parameters = {
        "path": {
            "type": "string",
            "description": "要打补丁的文件路径",
            "required": True,
        },
        "diff": {
            "type": "string",
            "description": "要应用的 unified diff 内容",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=True
    )

    def __init__(self, config: Optional[FileConfig] = None):
        super().__init__(config or FileConfig())

    async def execute(self, path: str, diff: str) -> ToolResult:
        """Apply a diff to a file."""
        try:
            file_path = resolve_workspace_path(path, self.config)
            ensure_allowed_extension(file_path, self.config)

            if not file_path.exists():
                return ToolResult.fail(f"文件不存在：{path}")

            if not file_path.is_file():
                return ToolResult.fail(f"路径不是文件：{path}")

            ensure_file_size(file_path, self.config.max_file_size)

            with open(file_path, "r", encoding="utf-8") as f:
                original = f.read()

            # 解析 diff 并应用
            new_content = self._apply_unified_diff(original, diff)

            if new_content is None:
                return ToolResult.fail("应用 diff 失败：补丁与文件内容不匹配")

            size = len(new_content.encode("utf-8"))
            if size > self.config.max_file_size:
                return ToolResult.fail(
                    f"打补丁后的内容过大（{size} bytes）。"
                    f"允许的最大值：{self.config.max_file_size} bytes"
                )

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return ToolResult.ok(
                f"已将 diff 应用到 {path}",
                path=str(file_path),
            )

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"应用 diff 失败：{str(e)}")

    def _apply_unified_diff(self, original: str, diff: str) -> Optional[str]:
        """解析并应用统一格式的 diff。"""
        original_had_trailing_newline = original.endswith("\n")
        lines = original.splitlines()
        diff_lines = diff.split("\n")

        result = []
        source_index = 0
        diff_index = 0
        saw_hunk = False

        while diff_index < len(diff_lines):
            diff_line = diff_lines[diff_index]
            if diff_line.startswith("@@"):
                saw_hunk = True
                # 解析 hunk 头
                parts = diff_line.split(" ")
                old_range = parts[1][1:]  # 去掉 '-'
                if "," in old_range:
                    hunk_start = int(old_range.split(",")[0]) - 1
                else:
                    hunk_start = int(old_range) - 1

                if hunk_start < source_index:
                    return None

                result.extend(lines[source_index:hunk_start])
                source_index = hunk_start
                diff_index += 1

                while diff_index < len(diff_lines) and not diff_lines[diff_index].startswith("@@"):
                    hunk_line = diff_lines[diff_index]

                    if hunk_line == "\\ No newline at end of file":
                        diff_index += 1
                        continue

                    if hunk_line.startswith("-"):
                        # 删除行
                        if source_index < len(lines) and lines[source_index] == hunk_line[1:]:
                            source_index += 1
                        else:
                            return None
                    elif hunk_line.startswith("+"):
                        # 新增行
                        result.append(hunk_line[1:])
                    elif hunk_line.startswith(" "):
                        # 上下文行
                        if source_index < len(lines) and lines[source_index] == hunk_line[1:]:
                            result.append(lines[source_index])
                            source_index += 1
                        else:
                            return None
                    elif hunk_line == "":
                        # Ignore blank separators outside actual hunk lines.
                        pass
                    else:
                        return None

                    diff_index += 1
                continue

            diff_index += 1

        if not saw_hunk:
            return None

        result.extend(lines[source_index:])
        patched = "\n".join(result)
        if original_had_trailing_newline:
            patched += "\n"

        return patched
