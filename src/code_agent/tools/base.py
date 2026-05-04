"""Base tool definitions for Code Agent."""

import abc
from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Result of a tool execution."""

    success: bool = Field(default=True, description="Whether the execution succeeded")
    output: str = Field(default="", description="Output of the tool execution")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    @classmethod
    def ok(cls, output: str, **metadata: Any) -> "ToolResult":
        """Create a successful result."""
        return cls(success=True, output=output, metadata=metadata)

    @classmethod
    def fail(cls, error: str, **metadata: Any) -> "ToolResult":
        """Create a failed result."""
        return cls(success=False, error=error, metadata=metadata)


class ToolPermission(BaseModel):
    """Permission configuration for a tool."""

    require_confirmation: bool = Field(default=True, description="Require user confirmation")
    allowed_in_auto_mode: bool = Field(default=False, description="Allowed in auto mode")
    destructive: bool = Field(default=False, description="Whether the tool is destructive")


class BaseTool(abc.ABC):
    """Base class for all tools."""

    name: str = ""
    description: str = ""
    aliases: list[str] = []
    search_hint: str = ""
    max_result_size_chars: int = 12000
    parameters: dict[str, Any] = {}
    permission: ToolPermission = ToolPermission()

    def __init__(self, config: Optional[Any] = None) -> None:
        """Initialize the tool with configuration."""
        self.config: Any = config

    @abc.abstractmethod
    async def execute(self, *args: Any, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given parameters."""
        raise NotImplementedError

    def get_schema(self) -> dict[str, Any]:
        """Get the JSON schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": [
                        k for k, v in self.parameters.items() if v.get("required", False)
                    ],
                },
            },
        }

    def matches(self, query: str) -> bool:
        """Return whether this tool matches a search query."""
        haystack = " ".join(
            [
                self.name,
                self.description,
                self.search_hint,
                " ".join(self.aliases),
            ]
        ).lower()
        return all(part in haystack for part in query.lower().split())

    def is_read_only(self) -> bool:
        """Return whether the tool should be treated as read-only."""
        return not self.permission.require_confirmation and not self.permission.destructive

    def is_destructive(self) -> bool:
        """Return whether the tool can make hard-to-reverse changes."""
        return self.permission.destructive

    def get_tool_use_summary(self, params: dict[str, Any] | None = None) -> str:
        """Return a compact user-facing description of a tool call."""
        if not params:
            return self.name
        interesting = []
        for key in ("file_path", "path", "pattern", "command", "query", "message"):
            if key in params and params[key] is not None:
                interesting.append(str(params[key]))
        suffix = f": {', '.join(interesting[:2])}" if interesting else ""
        return f"{self.name}{suffix}"

    def __str__(self) -> str:
        return f"{self.name}: {self.description}"
