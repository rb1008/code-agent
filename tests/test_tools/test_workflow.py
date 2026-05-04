"""Tests for project workflow tools."""

import os

import pytest

from code_agent.config.models import ShellConfig
from code_agent.tools.workflow import WorkflowListTool, WorkflowRunTool


@pytest.mark.asyncio
async def test_workflow_tools_list_and_run_executable_script(tmp_path) -> None:
    """Workflow tools should only list executable scripts and run them in place."""
    workflows = tmp_path / ".code-agent" / "workflows"
    workflows.mkdir(parents=True)
    script = workflows / "hello"
    script.write_text("#!/bin/sh\necho workflow:$CODE_AGENT_WORKFLOW:$1\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | 0o111)
    (workflows / "notes.md").write_text("not executable", encoding="utf-8")
    shell_config = ShellConfig(workspace_root=str(tmp_path))

    listed = await WorkflowListTool(workflows).execute()
    result = await WorkflowRunTool(workflows, shell_config).execute("hello", "ok")

    assert listed.success is True
    assert "- hello" in listed.output
    assert "notes.md" not in listed.output
    assert result.success is True
    assert result.output == "workflow:hello:ok"


@pytest.mark.asyncio
async def test_workflow_run_preserves_quoted_arguments(tmp_path) -> None:
    """Quoted workflow arguments should arrive as one argv item."""
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    script = workflows / "echoarg"
    script.write_text("#!/bin/sh\necho \"$1\"\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | 0o111)

    result = await WorkflowRunTool(workflows, ShellConfig(workspace_root=str(tmp_path))).execute(
        "echoarg",
        '"hello world"',
    )

    assert result.success is True
    assert result.output == "hello world"


@pytest.mark.asyncio
async def test_workflow_run_blocks_path_escape(tmp_path) -> None:
    """Workflow resolution should not allow escaping the workflow directory."""
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("#!/bin/sh\necho outside\n", encoding="utf-8")
    outside.chmod(outside.stat().st_mode | 0o111)

    result = await WorkflowRunTool(workflows, ShellConfig(workspace_root=str(tmp_path))).execute(
        os.path.join("..", "outside")
    )

    assert result.success is False
    assert "未找到 workflow" in (result.error or "")
