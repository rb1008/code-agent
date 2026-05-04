"""Tests for permission handling."""

import pytest

from code_agent.tools.base import ToolPermission
from code_agent.config.models import ShellConfig
from code_agent.tools.shell import BashTool
from code_agent.ui import permission as permission_module
from code_agent.ui.permission import PermissionManager, PermissionMode


@pytest.mark.asyncio
async def test_plan_mode_allows_read_only_but_blocks_mutation() -> None:
    """Plan mode allows inspection but records mutating tool intent."""
    manager = PermissionManager(mode=PermissionMode.INTERACTIVE)
    manager.enter_plan_mode()

    read_allowed = await manager.check_permission(
        tool_name="read_file",
        tool_params={"path": "README.md"},
        permission=ToolPermission(require_confirmation=False),
    )
    write_allowed = await manager.check_permission(
        tool_name="write_file",
        tool_params={"path": "README.md"},
        permission=ToolPermission(require_confirmation=True, destructive=True),
    )

    assert read_allowed is True
    assert write_allowed is False
    assert manager.get_pending_operations()[0]["tool_name"] == "write_file"


@pytest.mark.asyncio
async def test_never_choice_uses_e_prompt(monkeypatch) -> None:
    """The prompt should advertise e(never), matching the implemented branch."""
    prompts = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "e"

    monkeypatch.setattr(permission_module.agent_console, "input", fake_input)
    monkeypatch.setattr(permission_module.agent_console, "print_tool_call", lambda *args: None)

    manager = PermissionManager(mode=PermissionMode.INTERACTIVE)
    allowed = await manager.check_permission(
        tool_name="write_file",
        tool_params={"path": "x.txt"},
        permission=ToolPermission(require_confirmation=True, destructive=True),
    )

    assert allowed is False
    assert "e=本会话总是拒绝" in prompts[0]


@pytest.mark.asyncio
async def test_allow_choice_resets_denial_streak(monkeypatch) -> None:
    """An explicit or default allow should clear previous denial streaks."""
    manager = PermissionManager(mode=PermissionMode.INTERACTIVE)
    manager.denials.record_denial("write_file")

    monkeypatch.setattr(permission_module.agent_console, "input", lambda prompt: "")

    allowed = await manager.check_permission(
        tool_name="write_file",
        tool_params={"path": "x.txt"},
        permission=ToolPermission(require_confirmation=True, destructive=True),
    )

    assert allowed is True
    assert manager.denials.consecutive_by_tool.get("write_file") is None


@pytest.mark.asyncio
async def test_auto_mode_respects_allowed_in_auto_mode() -> None:
    """AUTO mode should still ask for tools that explicitly disallow auto execution."""
    prompts: list[str] = []

    async def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "n"

    manager = PermissionManager(mode=PermissionMode.AUTO, input_callback=fake_input)

    allowed = await manager.check_permission(
        tool_name="create_sub_agent",
        tool_params={"task": "edit files"},
        permission=ToolPermission(
            require_confirmation=True,
            allowed_in_auto_mode=False,
            destructive=False,
        ),
    )

    assert allowed is False
    assert prompts


@pytest.mark.asyncio
async def test_auto_mode_allows_explicit_auto_safe_tools() -> None:
    """Tools marked safe for auto mode should not prompt."""
    prompts: list[str] = []

    async def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "n"

    manager = PermissionManager(mode=PermissionMode.AUTO, input_callback=fake_input)

    allowed = await manager.check_permission(
        tool_name="read_file",
        tool_params={"path": "README.md"},
        permission=ToolPermission(
            require_confirmation=False,
            allowed_in_auto_mode=True,
            destructive=False,
        ),
    )

    assert allowed is True
    assert prompts == []


@pytest.mark.asyncio
async def test_bash_classifier_auto_allows_safe_read_commands() -> None:
    """Safe inspection commands should not require a manual prompt."""
    prompts: list[str] = []
    tool = BashTool(ShellConfig(allowed_commands=["git"]))
    manager = PermissionManager(
        mode=PermissionMode.INTERACTIVE,
        input_callback=lambda prompt: prompts.append(prompt) or "n",
    )

    allowed = await manager.check_permission(
        tool_name="bash",
        tool_params={"command": "git status"},
        permission=tool.permission,
        tool=tool,
    )

    assert allowed is True
    assert prompts == []


@pytest.mark.asyncio
async def test_bash_classifier_rejects_unsafe_command_before_prompt() -> None:
    """Classifier should fail closed for unsupported shell syntax."""
    prompts: list[str] = []
    tool = BashTool(ShellConfig(allowed_commands=["echo"]))
    manager = PermissionManager(
        mode=PermissionMode.INTERACTIVE,
        input_callback=lambda prompt: prompts.append(prompt) or "y",
    )

    allowed = await manager.check_permission(
        tool_name="bash",
        tool_params={"command": "echo ok; touch pwned"},
        permission=tool.permission,
        tool=tool,
    )

    assert allowed is False
    assert prompts == []
