"""Append-only project-local session transcripts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from code_agent.utils.token_budget import truncate_for_budget

SECRET_KEY_NAMES = {"api_key", "apikey", "authorization", "password", "secret", "token"}
SECRET_VALUE_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b")


@dataclass
class TranscriptEvent:
    """A normalized transcript event."""

    timestamp: str
    kind: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionTranscript:
    """Write a durable JSONL trace of an agent session.

    The transcript is intended for debugging and later review. It is not used as
    prompt memory directly, so it can retain full session chronology while the
    agent memory stays compact.
    """

    def __init__(self, path: Path, max_event_chars: int = 20000) -> None:
        self.path = path
        self.max_event_chars = max_event_chars
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def create_in_dir(
        cls,
        directory: Path,
        *,
        max_event_chars: int = 20000,
        now: Optional[datetime] = None,
    ) -> "SessionTranscript":
        """Create a timestamped transcript in a directory."""
        timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
        return cls(directory / f"{timestamp}.jsonl", max_event_chars=max_event_chars)

    def append_event(
        self,
        kind: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append one JSONL event with bounded and redacted content."""
        redacted_content = _redact(content)
        bounded_content, truncated = truncate_for_budget(redacted_content, self.max_event_chars)
        safe_metadata = _redact(metadata or {})
        if truncated and isinstance(safe_metadata, dict):
            safe_metadata["truncated"] = True

        event = TranscriptEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            content=bounded_content,
            metadata=safe_metadata if isinstance(safe_metadata, dict) else {},
        )
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.__dict__, ensure_ascii=False) + "\n")

    def append_system(self, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """Append a system/session event."""
        self.append_event("system", content, metadata)

    def append_user(self, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """Append a user message."""
        self.append_event("user", content, metadata)

    def append_assistant(
        self,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append an assistant message."""
        self.append_event("assistant", content, metadata)

    def append_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        summary: Optional[str] = None,
    ) -> None:
        """Append a tool call event."""
        self.append_event(
            "tool_call",
            summary or tool_name,
            {"tool": tool_name, "params": params},
        )

    def append_tool_result(self, tool_name: str, success: bool, output: str) -> None:
        """Append a tool result event."""
        self.append_event(
            "tool_result",
            output,
            {"tool": tool_name, "success": success},
        )

    def tail(self, limit: int = 20) -> list[TranscriptEvent]:
        """Read the last N transcript events."""
        if not self.path.exists() or limit <= 0:
            return []

        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        events: list[TranscriptEvent] = []
        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(
                TranscriptEvent(
                    timestamp=str(data.get("timestamp", "")),
                    kind=str(data.get("kind", "event")),
                    content=str(data.get("content", "")),
                    metadata=(
                        data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
                    ),
                )
            )
        return events

    def export_markdown(self, output_path: Optional[Path] = None) -> Path:
        """Export the JSONL transcript to a readable Markdown file."""
        if output_path is None:
            output_path = self.path.with_suffix(".md")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        lines = ["# Code Agent 会话记录", "", f"来源：`{self.path}`", ""]
        for event in self.tail(limit=10_000):
            title = _event_title(event.kind)
            lines.append(f"## {title}")
            if event.timestamp:
                lines.append(f"- 时间：`{event.timestamp}`")
            tool = event.metadata.get("tool")
            if tool:
                lines.append(f"- 工具：`{tool}`")
            if "success" in event.metadata:
                lines.append(f"- 成功：`{event.metadata['success']}`")
            lines.append("")
            lines.append(event.content.strip() or "_空_")
            lines.append("")

        output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return output_path


def _redact(value: Any) -> Any:
    """Redact likely secrets from transcript content and metadata."""
    if isinstance(value, str):
        return SECRET_VALUE_RE.sub(_mask_secret_match, value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SECRET_KEY_NAMES:
                redacted[key_text] = _mask_secret(str(item))
            else:
                redacted[key_text] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def _event_title(kind: str) -> str:
    """Return a Chinese event title for transcript export."""
    titles = {
        "system": "系统",
        "user": "用户",
        "assistant": "助手",
        "tool_call": "工具调用",
        "tool_result": "工具结果",
        "tool_hook": "工具 Hook",
    }
    return titles.get(kind, kind.replace("_", " "))


def _mask_secret_match(match: re.Match[str]) -> str:
    return _mask_secret(match.group(1))


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
