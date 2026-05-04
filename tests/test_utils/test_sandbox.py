"""Tests for shell sandbox policy."""

from code_agent.config.models import ShellConfig
from code_agent.utils.sandbox import ShellSandbox


def test_shell_sandbox_blocks_outside_workspace(tmp_path) -> None:
    """Shell commands should not write outside the workspace boundary."""
    sandbox = ShellSandbox(ShellConfig(workspace_root=str(tmp_path)))

    decision = sandbox.check("echo hi > /tmp/outside.txt", tmp_path)

    assert decision.allowed is False
    assert "工作区外路径" in decision.reason


def test_shell_sandbox_blocks_protected_workspace_path(tmp_path) -> None:
    """Sandbox deny paths protect agent configuration."""
    sandbox = ShellSandbox(ShellConfig(workspace_root=str(tmp_path)))

    decision = sandbox.check("echo nope > .code-agent/settings.yaml", tmp_path)

    assert decision.allowed is False
    assert "受保护路径" in decision.reason


def test_shell_sandbox_can_block_network(tmp_path) -> None:
    """Network can be disabled independently from shell permissions."""
    sandbox = ShellSandbox(ShellConfig(workspace_root=str(tmp_path), sandbox_allow_network=False))

    decision = sandbox.check("curl https://example.com", tmp_path)

    assert decision.allowed is False
    assert "网络命令" in decision.reason
