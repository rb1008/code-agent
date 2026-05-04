"""Rule-based permission model for tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal, Optional

import yaml  # type: ignore[import-untyped]

from code_agent.tools.base import ToolPermission

PermissionBehavior = Literal["allow", "ask", "deny"]
PermissionRuleSource = Literal["user", "project", "session", "command"]


@dataclass
class PermissionRule:
    """A permission rule matching a tool and optional command/path content."""

    tool: str
    behavior: PermissionBehavior
    content: Optional[str] = None
    source: PermissionRuleSource = "project"

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: PermissionRuleSource) -> "PermissionRule":
        return cls(
            tool=str(data.get("tool", "")),
            behavior=str(data.get("behavior", "ask")),  # type: ignore[arg-type]
            content=data.get("content"),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"tool": self.tool, "behavior": self.behavior}
        if self.content:
            data["content"] = self.content
        return data


@dataclass
class PermissionDecision:
    """The result of permission rule evaluation."""

    behavior: PermissionBehavior
    reason: str
    rule: Optional[PermissionRule] = None


@dataclass
class DenialTracker:
    """Track repeated denials to avoid useless retry loops."""

    max_consecutive: int = 3
    max_total: int = 20
    consecutive_by_tool: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def record_denial(self, tool_name: str) -> None:
        self.total += 1
        self.consecutive_by_tool[tool_name] = self.consecutive_by_tool.get(tool_name, 0) + 1

    def record_success(self, tool_name: str) -> None:
        self.consecutive_by_tool.pop(tool_name, None)

    def should_fallback(self, tool_name: str) -> bool:
        return (
            self.total >= self.max_total
            or self.consecutive_by_tool.get(tool_name, 0) >= self.max_consecutive
        )

    def message(self, tool_name: str) -> str:
        return (
            f"`{tool_name}` 连续多次被拒绝。"
            "请调整方案、询问用户更安全的替代方案，或改用只读工具。"
        )


class PermissionRuleStore:
    """Load and persist project/session permission rules."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.project_rules: list[PermissionRule] = []
        self.session_rules: list[PermissionRule] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.project_rules = []
            return
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw_rules = data.get("permissions", {}).get("rules", [])
        self.project_rules = [
            PermissionRule.from_dict(rule, "project")
            for rule in raw_rules
            if isinstance(rule, dict) and rule.get("tool")
        ]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "permissions": {
                "rules": [rule.to_dict() for rule in self.project_rules],
            }
        }
        self.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def add_rule(
        self,
        *,
        tool: str,
        behavior: PermissionBehavior,
        content: Optional[str] = None,
        source: PermissionRuleSource = "session",
    ) -> PermissionRule:
        rule = PermissionRule(tool=tool, behavior=behavior, content=content, source=source)
        if source == "session":
            self.session_rules.append(rule)
        elif source == "project":
            self.project_rules.append(rule)
            self.save()
        else:
            self.project_rules.append(rule)
        return rule

    def clear_session(self) -> None:
        self.session_rules.clear()

    def rules(self) -> list[PermissionRule]:
        return [*self.project_rules, *self.session_rules]

    def decide(
        self,
        *,
        tool_name: str,
        tool_params: dict[str, Any],
        permission: ToolPermission,
        is_read_only: bool = False,
    ) -> PermissionDecision:
        """Evaluate allow/ask/deny rules with last-match-wins precedence."""
        matches = [
            rule
            for rule in self.rules()
            if _matches_tool(rule.tool, tool_name) and _matches_content(rule, tool_params)
        ]
        if matches:
            rule = matches[-1]
            return PermissionDecision(rule.behavior, f"{rule.source} rule matched", rule)

        if is_read_only or not permission.require_confirmation:
            return PermissionDecision("allow", "read-only tool")
        if permission.destructive:
            return PermissionDecision("ask", "destructive tool")
        return PermissionDecision("ask", "confirmation required")


def _matches_tool(pattern: str, tool_name: str) -> bool:
    if not pattern:
        return False
    if pattern == tool_name:
        return True
    return fnmatch(tool_name, pattern)


def _matches_content(rule: PermissionRule, params: dict[str, Any]) -> bool:
    if not rule.content:
        return True

    candidates: list[str] = []
    for key in ("command", "cmd", "path", "file_path", "cwd"):
        value = params.get(key)
        if value is not None:
            candidates.append(str(value))

    if not candidates:
        candidates = [str(value) for value in params.values() if value is not None]

    return any(fnmatch(candidate, rule.content) for candidate in candidates)
