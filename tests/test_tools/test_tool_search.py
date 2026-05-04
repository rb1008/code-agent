"""Tests for model-facing tool discovery."""

import pytest

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.tool_search import ToolSearchTool


class SearchableTool(BaseTool):
    """Small lazy tool used by tool_search tests."""

    name = "searchable"
    description = "读取项目里的特殊文件"
    search_hint = "特殊 文件 查找"
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    async def execute(self) -> ToolResult:
        return ToolResult.ok("ok")


@pytest.mark.asyncio
async def test_tool_search_activates_matching_lazy_tools() -> None:
    """tool_search should pin matching tools without executing them."""
    registry = ToolRegistry()
    registry.register_lazy(SearchableTool, SearchableTool)
    tool = ToolSearchTool(registry)

    result = await tool.execute("特殊 文件")

    assert result.success is True
    assert "`searchable`" in result.output
    assert "searchable" in registry.list_active_tools()
