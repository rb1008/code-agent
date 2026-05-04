"""Tests for context reports."""

from code_agent.agent.memory import ConversationMemory
from code_agent.utils.context_report import build_context_report


def test_context_report_breaks_down_roles() -> None:
    """Context reports should show where prompt pressure comes from."""
    memory = ConversationMemory(max_messages=10, compact_threshold=99)
    memory.summary = "Earlier work summary"
    memory.add("user", "please inspect the project")
    memory.add("assistant", "I will inspect it")
    memory.add("tool", "README contents", tool="read_file")

    report = build_context_report(
        memory=memory,
        model="test",
        system_prompt="system prompt",
        limit=1000,
        threshold_ratio=0.8,
    )

    names = [section.name for section in report.sections]
    assert names == ["系统提示", "摘要", "用户", "助手", "工具", "系统消息"]
    assert report.total_tokens > 0
    assert report.threshold_tokens == 800
    assert "明细：" in report.render_text()
