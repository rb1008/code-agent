"""Tests for sub-agent tool wiring."""

import pytest

from code_agent.config.models import FileConfig, Settings
from code_agent.tools.agent_tool import AgentTool
from code_agent.ui.permission import PermissionManager, PermissionMode


def test_agent_tool_requires_confirmation() -> None:
    """Creating sub-agents must not be a no-confirmation path to tool execution."""
    assert AgentTool.permission.require_confirmation is True
    assert AgentTool.permission.allowed_in_auto_mode is False


@pytest.mark.asyncio
async def test_agent_tool_passes_settings_to_child_registry(monkeypatch, tmp_path) -> None:
    """Sub agents should inherit workspace/tool settings from the parent agent."""
    captured: dict[str, Settings] = {}

    def fake_create_default_registry(settings: Settings) -> object:
        captured["settings"] = settings
        from code_agent.tools.registry import ToolRegistry

        return ToolRegistry()

    async def fake_run(self: object, prompt: str) -> str:
        assert "上下文" in prompt
        return "ok"

    import code_agent.agent.core as core_module
    import code_agent.tools as tools_module

    monkeypatch.setattr(tools_module, "create_default_registry", fake_create_default_registry)
    monkeypatch.setattr(core_module.CodeAgent, "run", fake_run)

    settings = Settings(file=FileConfig(workspace_root=str(tmp_path)))
    result = await AgentTool(settings).execute("执行子任务", context="项目上下文")

    assert result.success is True
    assert captured["settings"] is settings
    assert "子 Agent 已完成任务" in result.output


@pytest.mark.asyncio
async def test_agent_tool_inherits_parent_permission_manager(monkeypatch) -> None:
    """Child agents spawned through create_sub_agent should keep parent permission behavior."""
    captured: dict[str, PermissionManager] = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["permission_manager"] = kwargs["permission_manager"]

        async def run(self, prompt: str) -> str:
            return "ok"

    import code_agent.agent.core as core_module

    monkeypatch.setattr(core_module, "CodeAgent", FakeAgent)

    tool = AgentTool(Settings())
    parent_manager = PermissionManager(PermissionMode.AUTO, input_callback=lambda _prompt: "y")
    tool.permission_manager = parent_manager

    result = await tool.execute("执行子任务")

    assert result.success is True
    assert captured["permission_manager"].mode == PermissionMode.AUTO
    assert captured["permission_manager"].mode != PermissionMode.BYPASS
    assert captured["permission_manager"].input_callback is parent_manager.input_callback
