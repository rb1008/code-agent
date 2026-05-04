"""Tests for default registration of P1 tools."""

from code_agent.config.models import AgentConfig, Settings
from code_agent.tools import create_default_registry


def test_default_registry_includes_project_extension_tools(tmp_path) -> None:
    """P1 tools should be usable without manual registry wiring."""
    settings = Settings(
        agent=AgentConfig(
            skills_dir=str(tmp_path / "skills"),
            commands_dir=str(tmp_path / "commands"),
            workflows_dir=str(tmp_path / "workflows"),
        )
    )

    registry = create_default_registry(settings)

    for name in [
        "tool_search",
        "lsp_tool",
        "discover_skills",
        "list_skills",
        "use_skill",
        "workflow_list",
        "workflow_run",
        "monitor_start",
        "monitor_list",
        "monitor_read",
        "monitor_stop",
        "fork_agent",
        "coordinator_run",
    ]:
        assert registry.get(name) is not None
