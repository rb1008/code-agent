"""Context pressure reporting helpers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from code_agent.agent.memory import ConversationMemory
from code_agent.utils.token_budget import estimate_text_tokens


@dataclass(frozen=True)
class ContextSection:
    """Estimated token usage for one context category."""

    name: str
    tokens: int


@dataclass(frozen=True)
class ContextReport:
    """Structured context budget report."""

    model: str
    limit: int
    threshold_ratio: float
    sections: list[ContextSection]
    messages: int
    summary_chars: int

    @property
    def total_tokens(self) -> int:
        return sum(section.tokens for section in self.sections)

    @property
    def usage_ratio(self) -> float:
        return self.total_tokens / self.limit if self.limit else 0

    @property
    def threshold_tokens(self) -> int:
        return int(self.limit * self.threshold_ratio)

    def rows(self) -> list[tuple[str, str]]:
        """Return compact key/value rows for Rich panels."""
        rows = [
            ("模型", self.model),
            ("估算 token", str(self.total_tokens)),
            ("Token 上限", str(self.limit)),
            ("使用率", f"{self.usage_ratio:.1%}"),
            ("自动压缩阈值", f"{self.threshold_tokens} ({self.threshold_ratio:.0%})"),
            ("保留消息数", str(self.messages)),
            ("摘要字符数", str(self.summary_chars)),
        ]
        rows.extend((section.name, f"{section.tokens} token") for section in self.sections)
        return rows

    def render_text(self, width: int = 24) -> str:
        """Render a readable plain-text report for the window UI."""
        lines = [
            f"模型：{self.model}",
            f"上下文：{self.usage_ratio:.1%} ({self.total_tokens}/{self.limit})",
            f"自动压缩：{self.threshold_tokens} token ({self.threshold_ratio:.0%})",
            f"消息数：{self.messages}",
            f"摘要字符数：{self.summary_chars}",
            "",
            "明细：",
        ]
        max_tokens = max([section.tokens for section in self.sections] + [1])
        for section in self.sections:
            fill = int((section.tokens / max_tokens) * width) if max_tokens else 0
            bar = "#" * fill or "."
            lines.append(f"{section.name:<10} {bar:<{width}} {section.tokens}")
        return "\n".join(lines)


def build_context_report(
    *,
    memory: ConversationMemory,
    model: str,
    system_prompt: str,
    limit: int,
    threshold_ratio: float,
) -> ContextReport:
    """Build a role-aware context report."""
    role_tokens: Counter[str] = Counter()
    for message in memory.messages:
        role_tokens[message.role] += estimate_text_tokens(message.content, model) + 4

    sections = [
        ContextSection("系统提示", estimate_text_tokens(system_prompt, model)),
        ContextSection(
            "摘要", estimate_text_tokens(memory.summary, model) if memory.summary else 0
        ),
    ]
    sections.extend(
        [
            ContextSection("用户", role_tokens.get("user", 0)),
            ContextSection("助手", role_tokens.get("assistant", 0)),
            ContextSection("工具", role_tokens.get("tool", 0)),
            ContextSection("系统消息", role_tokens.get("system", 0)),
        ]
    )
    return ContextReport(
        model=model,
        limit=limit,
        threshold_ratio=threshold_ratio,
        sections=sections,
        messages=len(memory.messages),
        summary_chars=len(memory.summary),
    )
