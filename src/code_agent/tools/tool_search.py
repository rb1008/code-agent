"""Model-facing tool search and lazy activation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult

if TYPE_CHECKING:
    from code_agent.tools.registry import ToolRegistry


class ToolSearchTool(BaseTool):
    """Search and activate tools without exposing every schema to the model."""

    name = "tool_search"
    description = (
        "按能力搜索可用工具，并把匹配工具激活到后续请求中。"
        "当不确定应该使用哪个工具时，先调用它。"
    )
    aliases = ["tools_search", "find_tool"]
    search_hint = "工具 搜索 发现 激活 lazy loading capability"
    parameters = {
        "query": {
            "type": "string",
            "description": "能力关键词，例如：读取文件、搜索代码、git diff、网页搜索、语义符号",
            "required": True,
        },
        "activate": {
            "type": "boolean",
            "description": "是否将匹配工具激活到后续请求，默认 true",
            "required": False,
        },
        "limit": {
            "type": "integer",
            "description": "最多返回/激活多少个工具，默认 8",
            "required": False,
        },
    }
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True, destructive=False)

    def __init__(self, registry: "ToolRegistry") -> None:
        super().__init__(None)
        self.registry = registry

    async def execute(self, query: str, activate: bool = True, limit: int = 8) -> ToolResult:
        """Return matching tools and optionally pin them for later turns."""
        safe_limit = max(1, min(int(limit or 8), 20))
        matches = self.registry.search_metadata(query)[:safe_limit]
        if activate:
            matches = self.registry.activate_matching(query, limit=safe_limit, pinned=True)
            matches = [item for item in matches if item.matches(query)][:safe_limit]

        if not matches:
            return ToolResult.ok(f"没有找到匹配工具：{query}")

        lines = [
            f"工具搜索：{query}",
            f"已激活：{'是' if activate else '否'}",
            "",
        ]
        for item in matches:
            state = "已激活" if item.active else "未激活"
            risk = "破坏性" if item.destructive else ("只读" if item.read_only else "需确认")
            aliases = f"；别名：{', '.join(item.aliases)}" if item.aliases else ""
            lines.append(f"- `{item.name}` [{state}/{risk}]：{item.description}{aliases}")
            if item.search_hint:
                lines.append(f"  提示：{item.search_hint}")
        lines.append("")
        lines.append("说明：本轮 LangGraph 已绑定的工具不会动态变化；已激活工具会在下一轮请求中暴露。")
        return ToolResult.ok("\n".join(lines), activated=[item.name for item in matches if item.active])
