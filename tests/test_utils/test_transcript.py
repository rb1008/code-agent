"""Tests for session transcripts."""

import json
from datetime import datetime

from code_agent.utils.transcript import SessionTranscript


def test_transcript_appends_redacted_jsonl_and_exports_markdown(tmp_path) -> None:
    """Transcript files should be durable, bounded, and safe to inspect."""
    transcript = SessionTranscript.create_in_dir(
        tmp_path,
        now=datetime(2026, 5, 3, 12, 0, 0),
        max_event_chars=1000,
    )

    transcript.append_user("hello sk-1234567890abcdef")
    transcript.append_tool_call("bash", {"api_key": "sk-secretsecret", "cmd": "pwd"})
    transcript.append_tool_result("bash", True, "/tmp/project")

    lines = transcript.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    payload = json.loads(lines[0])
    assert payload["kind"] == "user"
    assert "sk-1...cdef" in payload["content"]
    assert "sk-1234567890abcdef" not in transcript.path.read_text(encoding="utf-8")

    events = transcript.tail(2)
    assert [event.kind for event in events] == ["tool_call", "tool_result"]

    exported = transcript.export_markdown()
    text = exported.read_text(encoding="utf-8")
    assert "Code Agent 会话记录" in text
    assert "工具结果" in text


def test_transcript_tail_ignores_invalid_jsonl(tmp_path) -> None:
    """A partially written line should not break transcript inspection."""
    transcript = SessionTranscript(tmp_path / "session.jsonl")
    transcript.path.write_text("not-json\n", encoding="utf-8")
    transcript.append_assistant("done")

    events = transcript.tail(10)

    assert len(events) == 1
    assert events[0].kind == "assistant"
