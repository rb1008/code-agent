"""Lightweight semantic code navigation tools.

这个工具不启动外部 LSP 服务，避免在用户机器上引入额外 daemon。
它用 Python AST 和通用文本索引提供 Claude Code 风格的“符号/定义/引用”能力；
如果未来接入 pyright、typescript-language-server，可以把这里作为统一入口。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from code_agent.config.models import ProjectConfig
from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.utils.paths import PathSecurityError, is_blocked_path, resolve_workspace_path


@dataclass(frozen=True)
class SymbolRecord:
    """One semantic symbol discovered in source code."""

    name: str
    kind: str
    path: Path
    line: int
    signature: str

    def render(self, root: Path) -> str:
        rel = self.path.relative_to(root) if self.path.is_relative_to(root) else self.path
        return f"- {self.kind} `{self.name}` {rel}:{self.line}  {self.signature}"


class LSPTool(BaseTool):
    """Tool for semantic symbol, definition, and reference lookup."""

    name = "lsp_tool"
    description = "轻量级语义代码导航：列出符号、查定义、查引用、做基础诊断。"
    aliases = ["symbols", "definition", "references", "semantic_search"]
    search_hint = "LSP 语义 符号 定义 引用 类 函数 诊断 代码理解"
    parameters = {
        "action": {
            "type": "string",
            "description": "操作：symbols、definition、references、diagnostics",
            "required": True,
        },
        "query": {
            "type": "string",
            "description": "符号名或引用关键词；symbols/diagnostics 可省略",
            "required": False,
        },
        "path": {
            "type": "string",
            "description": "搜索范围，默认当前工作区",
            "required": False,
        },
        "limit": {
            "type": "integer",
            "description": "最大返回条数，默认 40",
            "required": False,
        },
    }
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True, destructive=False)

    CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".h"}

    def __init__(self, config: Optional[ProjectConfig] = None):
        super().__init__(config or ProjectConfig())

    async def execute(
        self,
        action: str,
        query: Optional[str] = None,
        path: str = ".",
        limit: int = 40,
    ) -> ToolResult:
        """Run one semantic navigation action."""
        try:
            root = resolve_workspace_path(path, self.config)
            safe_limit = max(1, min(int(limit or 40), 200))
            action_name = action.strip().lower()

            if action_name == "symbols":
                return self._symbols(root, safe_limit)
            if action_name == "definition":
                return self._definition(root, query or "", safe_limit)
            if action_name == "references":
                return self._references(root, query or "", safe_limit)
            if action_name == "diagnostics":
                return self._diagnostics(root, safe_limit)

            return ToolResult.fail("未知 action。可用：symbols、definition、references、diagnostics")
        except PathSecurityError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"LSP 工具执行失败：{type(e).__name__}: {e}")

    def _symbols(self, root: Path, limit: int) -> ToolResult:
        symbols = self._collect_symbols(root)[:limit]
        if not symbols:
            return ToolResult.ok("没有发现可展示的代码符号。")
        workspace = self._workspace_root()
        lines = [f"语义符号（最多 {limit} 条）："]
        lines.extend(symbol.render(workspace) for symbol in symbols)
        return ToolResult.ok("\n".join(lines), count=len(symbols))

    def _definition(self, root: Path, query: str, limit: int) -> ToolResult:
        if not query.strip():
            return ToolResult.fail("definition 需要 query，例如类名或函数名。")
        q = query.strip().lower()
        matches = [
            symbol
            for symbol in self._collect_symbols(root)
            if symbol.name.lower() == q or q in symbol.name.lower()
        ][:limit]
        if not matches:
            return ToolResult.ok(f"没有找到定义：{query}")
        workspace = self._workspace_root()
        lines = [f"定义匹配：{query}"]
        lines.extend(symbol.render(workspace) for symbol in matches)
        return ToolResult.ok("\n".join(lines), count=len(matches))

    def _references(self, root: Path, query: str, limit: int) -> ToolResult:
        if not query.strip():
            return ToolResult.fail("references 需要 query，例如变量、函数或类名。")
        workspace = self._workspace_root()
        pattern = re.compile(rf"\b{re.escape(query.strip())}\b")
        lines = [f"引用匹配：{query}"]
        count = 0
        for file_path in self._iter_code_files(root):
            try:
                for index, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
                    if pattern.search(line):
                        rel = file_path.relative_to(workspace) if file_path.is_relative_to(workspace) else file_path
                        lines.append(f"- {rel}:{index}: {line.strip()[:180]}")
                        count += 1
                        if count >= limit:
                            return ToolResult.ok("\n".join(lines), count=count)
            except UnicodeDecodeError:
                continue
        if count == 0:
            return ToolResult.ok(f"没有找到引用：{query}")
        return ToolResult.ok("\n".join(lines), count=count)

    def _diagnostics(self, root: Path, limit: int) -> ToolResult:
        diagnostics: list[str] = []
        workspace = self._workspace_root()
        for file_path in self._iter_code_files(root):
            if file_path.suffix != ".py":
                continue
            try:
                ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
            except SyntaxError as e:
                rel = file_path.relative_to(workspace) if file_path.is_relative_to(workspace) else file_path
                diagnostics.append(f"- {rel}:{e.lineno or 1}: Python 语法错误：{e.msg}")
                if len(diagnostics) >= limit:
                    break
            except UnicodeDecodeError:
                continue
        if not diagnostics:
            return ToolResult.ok("基础诊断未发现 Python 语法错误。")
        return ToolResult.ok("基础诊断：\n" + "\n".join(diagnostics), count=len(diagnostics))

    def _collect_symbols(self, root: Path) -> list[SymbolRecord]:
        records: list[SymbolRecord] = []
        for file_path in self._iter_code_files(root):
            if file_path.suffix == ".py":
                records.extend(self._python_symbols(file_path))
            else:
                records.extend(self._text_symbols(file_path))
        return sorted(records, key=lambda item: (str(item.path), item.line, item.name))

    def _python_symbols(self, path: Path) -> list[SymbolRecord]:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            return []

        records: list[SymbolRecord] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                records.append(SymbolRecord(node.name, "class", path, node.lineno, f"class {node.name}"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                args = ", ".join(arg.arg for arg in node.args.args[:8])
                records.append(SymbolRecord(node.name, "function", path, node.lineno, f"{prefix} {node.name}({args})"))
        return records

    def _text_symbols(self, path: Path) -> list[SymbolRecord]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return []

        patterns = [
            ("class", re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")),
            ("function", re.compile(r"\b(?:function\s+|async\s+function\s+|const\s+|let\s+|var\s+)([A-Za-z_$][\w$]*)\s*(?:=|\()")),
            ("function", re.compile(r"\bfunc\s+([A-Za-z_]\w*)\s*\(")),
            ("struct", re.compile(r"\bstruct\s+([A-Za-z_]\w*)")),
        ]
        records: list[SymbolRecord] = []
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            for kind, pattern in patterns:
                match = pattern.search(stripped)
                if match:
                    records.append(SymbolRecord(match.group(1), kind, path, index, stripped[:140]))
                    break
        return records

    def _iter_code_files(self, root: Path) -> list[Path]:
        if root.is_file():
            return [root] if root.suffix in self.CODE_SUFFIXES else []
        files: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in self.CODE_SUFFIXES:
                continue
            if is_blocked_path(path, self.config) or self._ignored(path):
                continue
            files.append(path)
            if len(files) >= max(self.config.max_context_files * 20, 200):
                break
        return files

    def _ignored(self, path: Path) -> bool:
        workspace = self._workspace_root()
        rel = str(path.relative_to(workspace)) if path.is_relative_to(workspace) else path.name
        return any(fnmatch(path.name, pattern) or fnmatch(rel, pattern) for pattern in self.config.ignore_patterns)

    def _workspace_root(self) -> Path:
        return Path(self.config.workspace_root).expanduser().resolve()
