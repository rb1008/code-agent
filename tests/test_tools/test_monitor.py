"""Tests for session-local monitor tools."""

import asyncio

import pytest

from code_agent.config.models import ShellConfig
from code_agent.tools.monitor import (
    MonitorListTool,
    MonitorManager,
    MonitorReadTool,
    MonitorStartTool,
    MonitorStopTool,
)


@pytest.mark.asyncio
async def test_monitor_tools_start_read_list_and_stop(tmp_path) -> None:
    """Monitor tools should retain recent output for the current process."""
    manager = MonitorManager()
    await manager.clear()
    shell_config = ShellConfig(workspace_root=str(tmp_path))

    started = await MonitorStartTool(shell_config).execute(
        "python -c 'print(\"hello-monitor\")'",
        ".",
    )
    monitor_id = started.metadata["monitor_id"]
    for _ in range(20):
        read = await MonitorReadTool().execute(monitor_id)
        if "hello-monitor" in read.output:
            break
        await asyncio.sleep(0.05)

    listed = await MonitorListTool().execute()
    stopped = await MonitorStopTool().execute(monitor_id)

    assert started.success is True
    assert "hello-monitor" in read.output
    assert listed.success is True
    assert monitor_id in listed.output
    assert stopped.success is False
    assert "未运行或不存在" in (stopped.error or "")
    await manager.clear()


@pytest.mark.asyncio
async def test_monitor_start_respects_workspace(tmp_path) -> None:
    """Monitor cwd must stay inside the configured workspace."""
    manager = MonitorManager()
    await manager.clear()
    result = await MonitorStartTool(ShellConfig(workspace_root=str(tmp_path))).execute(
        "python -c 'print(1)'",
        str(tmp_path.parent),
    )

    assert result.success is False
    assert "超出" in (result.error or "")


@pytest.mark.asyncio
async def test_monitor_start_uses_shell_allowlist_and_rejects_compound_commands(tmp_path) -> None:
    """Background monitors should share BashTool command safety semantics."""
    manager = MonitorManager()
    await manager.clear()
    shell_config = ShellConfig(
        workspace_root=str(tmp_path),
        allowed_commands=["echo"],
        require_confirmation=False,
    )

    result = await MonitorStartTool(shell_config).execute("echo ok; touch pwned", ".")

    assert result.success is False
    assert "安全规则" in (result.error or "")
    assert not (tmp_path / "pwned").exists()
