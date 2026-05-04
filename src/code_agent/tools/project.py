"""Project analysis tools for Code Agent."""

import ast
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.config.models import ProjectConfig
from code_agent.utils.paths import PathSecurityError, is_blocked_path, resolve_workspace_path


class GetProjectStructureTool(BaseTool):
    """Tool for getting project directory structure."""

    name = "get_project_structure"
    description = "以树形结构展示项目目录。"
    aliases = ["tree", "project_tree"]
    search_hint = "项目 目录 树"
    parameters = {
        "path": {
            "type": "string",
            "description": "根路径，默认当前目录",
            "required": False,
        },
        "max_depth": {
            "type": "integer",
            "description": "最大展示深度，默认 3",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[ProjectConfig] = None):
        super().__init__(config or ProjectConfig())

    async def execute(self, path: str = ".", max_depth: int = 3) -> ToolResult:
        """Get project structure as a tree."""
        try:
            root = resolve_workspace_path(path, self.config)

            if not root.exists():
                return ToolResult.fail(f"路径不存在：{path}")

            ignore_patterns = set(self.config.ignore_patterns)

            def should_ignore(p: Path) -> bool:
                """Check if path should be ignored."""
                if is_blocked_path(p, self.config):
                    return True
                rel = str(p.relative_to(root)) if p != root and p.is_relative_to(root) else p.name
                for pattern in ignore_patterns:
                    if fnmatch(p.name, pattern) or fnmatch(rel, pattern):
                        return True
                return False

            def build_tree(directory: Path, prefix: str = "", depth: int = 0) -> list[str]:
                """Recursively build tree structure."""
                if depth > max_depth:
                    return [f"{prefix}...（已达到最大深度）"]

                lines = []
                try:
                    items = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name))
                except PermissionError:
                    return [f"{prefix}[无权限访问]"]

                visible_items = [item for item in items if not should_ignore(item)]

                for i, item in enumerate(visible_items):
                    is_last = i == len(visible_items) - 1
                    connector = "└── " if is_last else "├── "
                    child_prefix = "    " if is_last else "│   "

                    if item.is_dir():
                        lines.append(f"{prefix}{connector}📁 {item.name}/")
                        lines.extend(build_tree(item, prefix + child_prefix, depth + 1))
                    else:
                        size = item.stat().st_size
                        lines.append(f"{prefix}{connector}📄 {item.name} ({self._format_size(size)})")

                return lines

            tree_lines = [f"📦 {root.name}/"]
            tree_lines.extend(build_tree(root))

            return ToolResult.ok("\n".join(tree_lines), root=str(root))

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"获取项目结构失败：{str(e)}")

    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        size_value = float(size)
        for unit in ["B", "KB", "MB", "GB"]:
            if size_value < 1024:
                return f"{size_value:.1f} {unit}"
            size_value /= 1024
        return f"{size_value:.1f} TB"


class SummarizeFileTool(BaseTool):
    """Tool for generating a summary of a file."""

    name = "summarize_file"
    description = "生成文件摘要，包括行数、导入、函数、类等结构信息。"
    aliases = ["file_summary"]
    search_hint = "总结 代码 文件 结构"
    parameters = {
        "path": {
            "type": "string",
            "description": "要摘要的文件路径",
            "required": True,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[ProjectConfig] = None):
        super().__init__(config or ProjectConfig())

    async def execute(self, path: str) -> ToolResult:
        """Summarize a file."""
        try:
            file_path = resolve_workspace_path(path, self.config)

            if not file_path.exists():
                return ToolResult.fail(f"文件不存在：{path}")

            if not file_path.is_file():
                return ToolResult.fail(f"路径不是文件：{path}")

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            lines = content.split("\n")
            total_lines = len(lines)

            # 分析 Python 文件
            if file_path.suffix == ".py":
                imports, functions, classes = self._summarize_python(content)

                summary = f"文件：{path}\n"
                summary += "类型：Python\n"
                summary += f"总行数：{total_lines}\n"
                summary += f"导入：{len(imports)}\n"
                summary += f"函数：{len(functions)}\n"
                summary += f"类：{len(classes)}\n"

                if imports:
                    summary += "\n导入列表：\n"
                    for imp in imports[:10]:
                        summary += f"  {imp}\n"
                    if len(imports) > 10:
                        summary += f"  ... 还有 {len(imports) - 10} 项\n"

                if functions:
                    summary += "\n函数列表：\n"
                    for func in functions[:10]:
                        summary += f"  {func}\n"
                    if len(functions) > 10:
                        summary += f"  ... 还有 {len(functions) - 10} 项\n"

                if classes:
                    summary += "\n类列表：\n"
                    for cls in classes[:10]:
                        summary += f"  {cls}\n"
                    if len(classes) > 10:
                        summary += f"  ... 还有 {len(classes) - 10} 项\n"

            else:
                # 通用文件摘要
                summary = f"文件：{path}\n"
                summary += f"类型：{file_path.suffix or '未知'}\n"
                summary += f"总行数：{total_lines}\n"
                summary += f"大小：{len(content)} bytes\n"

            return ToolResult.ok(summary, path=str(file_path), lines=total_lines)

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except UnicodeDecodeError:
            return ToolResult.fail(f"文件不是可读取的文本文件：{path}")
        except Exception as e:
            return ToolResult.fail(f"生成文件摘要失败：{str(e)}")

    def _summarize_python(self, content: str) -> tuple[list[str], list[str], list[str]]:
        """Return imports, top-level functions, and classes from Python source."""
        tree = ast.parse(content)
        imports: list[str] = []
        functions: list[str] = []
        classes: list[str] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                names = ", ".join(alias.name for alias in node.names)
                imports.append(f"import {names}")
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                names = ", ".join(alias.name for alias in node.names)
                imports.append(f"from {module} import {names}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)

        return imports, functions, classes


class GetDependenciesTool(BaseTool):
    """Tool for analyzing project dependencies."""

    name = "get_dependencies"
    description = "分析项目依赖文件并列出依赖信息。"
    aliases = ["deps", "dependencies"]
    search_hint = "分析 依赖 包 文件"
    parameters = {
        "path": {
            "type": "string",
            "description": "项目根路径，默认当前目录",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )

    def __init__(self, config: Optional[ProjectConfig] = None):
        super().__init__(config or ProjectConfig())

    async def execute(self, path: str = ".") -> ToolResult:
        """Get project dependencies."""
        try:
            root = resolve_workspace_path(path, self.config)

            deps = []

            # Python 依赖
            req_file = root / "requirements.txt"
            if req_file.exists():
                with open(req_file, "r", encoding="utf-8") as f:
                    content = f.read()
                deps.append(("requirements.txt", content.strip().split("\n")))

            pyproject = root / "pyproject.toml"
            if pyproject.exists():
                deps.append(("pyproject.toml", ["[详见 pyproject.toml]"]))

            setup_py = root / "setup.py"
            if setup_py.exists():
                deps.append(("setup.py", ["[详见 setup.py]"]))

            # Node.js 依赖
            package_json = root / "package.json"
            if package_json.exists():
                deps.append(("package.json", ["[详见 package.json]"]))

            # 其他依赖文件
            cargo_toml = root / "Cargo.toml"
            if cargo_toml.exists():
                deps.append(("Cargo.toml", ["[详见 Cargo.toml]"]))

            go_mod = root / "go.mod"
            if go_mod.exists():
                with open(go_mod, "r", encoding="utf-8") as f:
                    content = f.read()
                deps.append(("go.mod", content.strip().split("\n")))

            if not deps:
                return ToolResult.ok("未找到项目依赖文件。")

            output = "项目依赖：\n"
            output += "=" * 50 + "\n"

            for filename, items in deps:
                output += f"\n📦 {filename}:\n"
                for item in items:
                    output += f"  {item}\n"

            return ToolResult.ok(output, files=[f for f, _ in deps])

        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"获取依赖信息失败：{str(e)}")
