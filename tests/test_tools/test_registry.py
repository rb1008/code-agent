"""Tests for tool registry."""

import pytest

from code_agent.config.models import FileConfig, Settings, ShellConfig
from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.tools import create_default_registry
from code_agent.tools.registry import ToolRegistry


class DummyTool(BaseTool):
    """Dummy tool for testing."""

    name = "dummy_tool"
    description = "A dummy tool for testing"
    parameters = {
        "param1": {
            "type": "string",
            "description": "First parameter",
            "required": True,
        },
    }
    permission = ToolPermission(require_confirmation=False)

    async def execute(self, param1: str) -> ToolResult:
        return ToolResult.ok(f"Executed with {param1}")


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        tool = DummyTool()

        registry.register(tool)

        assert "dummy_tool" in registry
        assert len(registry) == 1

    def test_unregister_tool(self):
        """Test unregistering a tool."""
        registry = ToolRegistry()
        tool = DummyTool()

        registry.register(tool)
        registry.unregister("dummy_tool")

        assert "dummy_tool" not in registry
        assert len(registry) == 0

    def test_get_tool(self):
        """Test getting a tool by name."""
        registry = ToolRegistry()
        tool = DummyTool()
        tool.aliases = ["dummy_alias"]

        registry.register(tool)
        retrieved = registry.get("dummy_tool")
        by_alias = registry.get("dummy_alias")

        assert retrieved is not None
        assert retrieved.name == "dummy_tool"
        assert by_alias is retrieved

    def test_get_nonexistent_tool(self):
        """Test getting a non-existent tool."""
        registry = ToolRegistry()

        retrieved = registry.get("nonexistent")

        assert retrieved is None

    def test_list_tools(self):
        """Test listing all tools."""
        registry = ToolRegistry()
        registry.register(DummyTool())

        tools = registry.list_tools()

        assert "dummy_tool" in tools
        assert len(tools) == 1

    def test_get_schemas(self):
        """Test getting tool schemas."""
        registry = ToolRegistry()
        registry.register(DummyTool())

        schemas = registry.get_schemas()

        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "dummy_tool"

    def test_lazy_tool_is_searchable_before_instantiation(self):
        """Lazy metadata should be usable without creating the tool instance."""
        created = 0

        def factory() -> DummyTool:
            nonlocal created
            created += 1
            return DummyTool()

        registry = ToolRegistry()
        registry.register_lazy(DummyTool, factory)

        assert created == 0
        assert registry.search_metadata("dummy")[0].name == "dummy_tool"
        assert created == 0

        assert registry.get("dummy_tool") is not None
        assert created == 1

    def test_prepare_for_request_limits_active_lazy_tools(self):
        """Each user request should expose only the always-on and relevant tools."""
        registry = ToolRegistry()
        registry.register_lazy(DummyTool, DummyTool)

        active = registry.prepare_for_request(
            "dummy",
            always_include=[],
            limit=4,
        )

        assert [item.name for item in active] == ["dummy_tool"]
        assert registry.list_active_tools() == ["dummy_tool"]

    def test_prepare_for_request_matches_chinese_sentences(self):
        """Chinese full-sentence requests should still activate relevant tools."""
        from code_agent.config.models import Settings
        from code_agent.tools import create_default_registry

        settings = Settings()
        registry = create_default_registry(settings)

        web_tools = registry.prepare_for_request(
            "请帮我搜索一下网页资料",
            always_include=settings.agent.always_active_tools,
            limit=18,
        )
        write_tools = registry.prepare_for_request(
            "创建一个新的 README 文件",
            always_include=settings.agent.always_active_tools,
            limit=18,
        )

        assert "web_search" in [item.name for item in web_tools]
        assert "write_file" in [item.name for item in write_tools]
        assert "discover_skills" in [item.name for item in write_tools]
        assert "use_skill" in [item.name for item in write_tools]
        assert len(write_tools) <= 18

    def test_search_tools(self):
        """Test searching tools by description and alias."""
        registry = ToolRegistry()
        tool = DummyTool()
        tool.aliases = ["demo"]
        tool.search_hint = "sample capability"
        registry.register(tool)

        assert registry.search("sample")[0].name == "dummy_tool"
        assert registry.search("demo")[0].name == "dummy_tool"
        assert registry.search("missing") == []

    @pytest.mark.asyncio
    async def test_execute_tool(self):
        """Test executing a tool through registry."""
        registry = ToolRegistry()
        registry.register(DummyTool())

        result = await registry.execute("dummy_tool", param1="test")

        assert result.success is True
        assert "Executed with test" in result.output

    @pytest.mark.asyncio
    async def test_execute_nonexistent_tool(self):
        """Test executing a non-existent tool."""
        registry = ToolRegistry()

        result = await registry.execute("nonexistent")

        assert result.success is False
        assert "未找到工具" in (result.error or "")


def test_default_registry_passes_settings_to_tools(tmp_path):
    """Configured safety settings should reach the default tool instances."""
    settings = Settings(
        file=FileConfig(workspace_root=str(tmp_path), max_file_size=8),
        shell=ShellConfig(
            workspace_root=str(tmp_path),
            allowed_commands=["echo"],
            require_confirmation=False,
        ),
    )

    registry = create_default_registry(settings)

    assert registry.get("read_file").config.max_file_size == 8
    assert registry.get("bash").config.allowed_commands == ["echo"]
    assert registry.get("bash").permission.require_confirmation is False
    assert "enter_plan_mode" not in registry
