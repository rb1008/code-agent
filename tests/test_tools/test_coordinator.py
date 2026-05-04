"""Tests for coordinator task parsing."""

import pytest

from code_agent.config.models import Settings
from code_agent.tools.coordinator import CoordinatorRunTool, ForkAgentTool, _parse_tasks
from code_agent.ui.permission import PermissionManager, PermissionMode
from code_agent.utils.subagents import SubAgentRunner



def test_parse_tasks_accepts_titled_and_untitled_lines() -> None:
    """Coordinator task parsing should be forgiving for slash command input."""
    tasks = _parse_tasks("review: inspect code\n\nrun tests")

    assert tasks == [
        ("review", "inspect code"),
        ("worker-3", "run tests"),
    ]


def test_fork_and_coordinator_require_confirmation() -> None:
    """Sub-agent tools should not be a no-confirmation path to mutating tools."""
    assert ForkAgentTool.permission.require_confirmation is True
    assert CoordinatorRunTool.permission.require_confirmation is True


@pytest.mark.asyncio
async def test_subagent_runner_inherits_permission_manager(monkeypatch) -> None:
    """Child agents should inherit the parent permission mode instead of BYPASS."""
    captured: dict[str, PermissionManager] = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["permission_manager"] = kwargs["permission_manager"]

        async def run(self, prompt: str) -> str:
            return prompt

    monkeypatch.setattr("code_agent.agent.core.CodeAgent", FakeAgent)

    parent_manager = PermissionManager(PermissionMode.AUTO)
    result = await SubAgentRunner(Settings()).run(
        title="child",
        prompt="inspect",
        permission_manager=parent_manager,
    )

    assert result.output == "inspect"
    assert captured["permission_manager"].mode == PermissionMode.AUTO
    assert captured["permission_manager"].mode != PermissionMode.BYPASS
