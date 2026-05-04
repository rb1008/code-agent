"""Tests for rule-based permissions."""

from code_agent.tools.base import ToolPermission
from code_agent.utils.permissions import PermissionRuleStore


def test_permission_store_last_matching_rule_wins(tmp_path) -> None:
    """Session rules should override earlier project rules."""
    store = PermissionRuleStore(tmp_path / "settings.yaml")
    store.add_rule(tool="bash", behavior="deny", content="git *", source="project")
    store.add_rule(tool="bash", behavior="allow", content="git status", source="session")

    decision = store.decide(
        tool_name="bash",
        tool_params={"command": "git status"},
        permission=ToolPermission(require_confirmation=True),
    )

    assert decision.behavior == "allow"
    assert decision.rule is not None
    assert decision.rule.source == "session"


def test_permission_store_persists_project_rules(tmp_path) -> None:
    """Project rules should survive reload."""
    path = tmp_path / "settings.yaml"
    store = PermissionRuleStore(path)
    store.add_rule(tool="write_file", behavior="deny", content="secrets/*", source="project")

    reloaded = PermissionRuleStore(path)
    decision = reloaded.decide(
        tool_name="write_file",
        tool_params={"path": "secrets/key.txt"},
        permission=ToolPermission(require_confirmation=True, destructive=True),
    )

    assert decision.behavior == "deny"
    assert "permissions:" in path.read_text(encoding="utf-8")
