"""Tests for tool lifecycle hooks."""

import pytest

from code_agent.utils.tool_hooks import ToolHookManager


@pytest.mark.asyncio
async def test_tool_hook_manager_runs_environment_aware_hooks(tmp_path) -> None:
    """Hook commands should receive tool context through environment variables."""
    hook_file = tmp_path / "hooks.yaml"
    hook_file.write_text(
        "hooks:\n"
        "  pre_tool_use:\n"
        '    - command: "python -c \'import os; print(os.environ[\\"CODE_AGENT_TOOL_NAME\\"])\'"\n',
        encoding="utf-8",
    )
    manager = ToolHookManager(hook_file, cwd=tmp_path)

    results = await manager.run("pre_tool_use", tool_name="bash", tool_params={})

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].output == "bash"
