"""Project-local Markdown persistence for bounded long-term memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from code_agent.agent.memory import ConversationMemory, Message


START_MARKER = "<!-- code-agent-memory:v1 -->"
END_MARKER = "<!-- /code-agent-memory -->"


@dataclass(frozen=True)
class DreamResult:
    """主动整理记忆后的结果摘要。"""

    path: Path
    before_chars: int
    after_chars: int
    topics: dict[str, int]
    core_facts: list[str]

    def render(self) -> str:
        topic_text = "，".join(f"{name} {count} 条" for name, count in self.topics.items())
        facts = "\n".join(f"- {fact}" for fact in self.core_facts[:6]) or "- 暂无核心事实"
        return (
            f"记忆整理完成：{self.path}\n"
            f"大小：{self.before_chars} -> {self.after_chars} 字符\n"
            f"主题：{topic_text or '暂无'}\n\n"
            f"核心事实：\n{facts}"
        )


TOPIC_FILES = {
    "核心事实": "core.md",
    "用户偏好": "preferences.md",
    "项目约束": "constraints.md",
    "当前任务": "tasks.md",
    "文件与工具": "files-tools.md",
    "错误与修正": "errors-fixes.md",
    "工作日志": "worklog.md",
}


class PersistentMemory:
    """Load and save compact memory summaries in a Markdown file."""

    def __init__(
        self,
        path: Path,
        max_chars: int = 12000,
        memory_dir: Path | None = None,
        auto_dream_enabled: bool = True,
        auto_dream_min_messages: int = 14,
    ) -> None:
        self.path = path
        self.max_chars = max_chars
        self.memory_dir = memory_dir
        self.auto_dream_enabled = auto_dream_enabled
        self.auto_dream_min_messages = auto_dream_min_messages
        self._last_auto_dream_signature: tuple[int, int] | None = None

    def load_into(self, memory: ConversationMemory) -> bool:
        """Load persisted summary into memory if the Markdown file exists."""
        if not self.path.exists() and not self._topic_files():
            return False

        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        body = self._extract_body(text) if text else ""
        summary_title = "摘要" if "## 摘要" in body else "Summary"
        summary = self._extract_section(body, summary_title) if body else ""
        topic_summary = self._load_topic_summary()
        if topic_summary:
            summary = f"{summary}\n\n{topic_summary}".strip()
        if not summary or summary in {"- 暂无持久上下文。", "- No durable context yet."}:
            return False

        if memory.summary:
            memory.summary = f"{summary}\n\n{memory.summary}"
        else:
            memory.summary = summary
        memory.summary = self._trim(memory.summary, self.max_chars)
        return True

    def save(self, memory: ConversationMemory) -> Path:
        """Write a bounded Markdown memory file, replacing old content."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        summary = self._build_summary(memory)
        recent = self._build_recent_context(memory.messages)
        structured = self._build_structured_sections(memory)

        content = (
            "# Code Agent 持久化记忆\n\n"
            f"{START_MARKER}\n"
            f"更新时间：{datetime.now().isoformat(timespec='seconds')}\n\n"
            "## 摘要\n\n"
            f"{summary or '- 暂无持久上下文。'}\n\n"
            "## 最近重要上下文\n\n"
            f"{recent or '- 暂无最近重要上下文。'}\n"
            f"{structured}"
            f"{END_MARKER}\n"
        )

        self.path.write_text(self._trim_document(content, self.max_chars), encoding="utf-8")
        return self.path

    def dream(self, memory: ConversationMemory) -> DreamResult:
        """主动整理长期记忆：提取事实、去重、按主题归档并压缩旧条目。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.memory_dir:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
        previous = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        before_chars = len(previous)
        candidate_lines = self._collect_memory_lines(previous, memory)
        topics = self._classify_lines(candidate_lines)
        core_facts = self._core_facts(topics)
        summary = "\n".join(f"- {fact}" for fact in core_facts) or "- 暂无核心事实。"
        archive = self._render_topic_archive(topics, exclude=set(core_facts))
        recent = self._build_recent_context(memory.messages)

        content = (
            "# Code Agent 持久化记忆\n\n"
            f"{START_MARKER}\n"
            f"更新时间：{datetime.now().isoformat(timespec='seconds')}\n"
            "整理方式：/dream 主动整理\n\n"
            "## 摘要\n\n"
            f"{summary}\n\n"
            "## 主题归档\n\n"
            f"{archive or '- 暂无可归档内容。'}\n\n"
            "## 最近重要上下文\n\n"
            f"{recent or '- 暂无最近重要上下文。'}\n"
            f"{END_MARKER}\n"
        )
        content = self._trim_document(content, self.max_chars)
        self.path.write_text(content, encoding="utf-8")
        self._write_topic_files(topics, core_facts)
        memory.summary = self._trim(summary, self.max_chars // 2)
        self._last_auto_dream_signature = self._auto_dream_signature(memory)
        return DreamResult(
            path=self.path,
            before_chars=before_chars,
            after_chars=len(content),
            topics={topic: len(items) for topic, items in topics.items() if items},
            core_facts=core_facts,
        )

    def maybe_auto_dream(self, memory: ConversationMemory) -> DreamResult | None:
        """Auto-organize memory when it is useful, but avoid running every turn."""
        if not self.auto_dream_enabled:
            return None
        durable_count = sum(
            1
            for message in memory.messages
            if self._is_durable_message(message.role, " ".join(message.content.split()))
        )
        if durable_count < self.auto_dream_min_messages:
            return None
        signature = self._auto_dream_signature(memory)
        if signature == self._last_auto_dream_signature:
            return None
        if self.path.exists() and len(self.path.read_text(encoding="utf-8")) < int(self.max_chars * 0.75):
            return None
        return self.dream(memory)

    def clear(self) -> None:
        """Remove persisted memory."""
        if self.path.exists():
            self.path.unlink()
        for path in self._topic_files():
            path.unlink(missing_ok=True)

    def _build_summary(self, memory: ConversationMemory) -> str:
        parts: list[str] = []
        if memory.summary:
            parts.append(memory.summary.strip())

        durable_lines = self._summarize_messages(memory.messages)
        if durable_lines:
            parts.append("\n".join(durable_lines))

        return self._trim("\n\n".join(part for part in parts if part), self.max_chars // 2)

    def _summarize_messages(self, messages: list[Message]) -> list[str]:
        lines: list[str] = []
        for msg in messages[-12:]:
            content = " ".join(msg.content.split())
            if not content:
                continue
            if not self._is_durable_message(msg.role, content):
                continue

            if msg.role == "user":
                lines.append(f"- 用户提出：{self._clip(content, 220)}")
            elif msg.role == "assistant":
                lines.append(f"- Agent 回复：{self._clip(content, 260)}")
            elif msg.role == "tool":
                tool_name = msg.metadata.get("tool", "tool")
                lines.append(f"- 工具 `{tool_name}` 观察到：{self._clip(content, 260)}")

        return lines[-12:]

    def _collect_memory_lines(self, previous: str, memory: ConversationMemory) -> list[str]:
        """Collect old Markdown bullets plus current durable messages."""
        lines: list[str] = []
        if previous:
            body = self._extract_body(previous)
            for raw in body.splitlines():
                line = raw.strip()
                if self._is_memory_bullet(line):
                    lines.append(self._clean_memory_line(line))

        for topic_file in self._topic_files():
            for raw in topic_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if self._is_memory_bullet(line):
                    lines.append(self._clean_memory_line(line))

        if memory.summary:
            for raw in memory.summary.splitlines():
                line = raw.strip()
                if line:
                    lines.append(self._clean_memory_line(line))

        for msg in memory.messages:
            content = " ".join(msg.content.split())
            if not content or not self._is_durable_message(msg.role, content):
                continue
            if msg.role == "tool":
                tool_name = msg.metadata.get("tool", "tool")
                lines.append(f"工具 `{tool_name}`：{self._clip(content, 220)}")
            elif msg.role == "user":
                lines.append(f"用户需求：{self._clip(content, 240)}")
            elif msg.role == "assistant":
                lines.append(f"实现记录：{self._clip(content, 240)}")

        return self._dedupe(lines)

    def _write_topic_files(self, topics: dict[str, list[str]], core_facts: list[str]) -> None:
        """Write directory-based topic memory for human review and startup loading."""
        if not self.memory_dir:
            return
        for topic, filename in TOPIC_FILES.items():
            items = topics.get(topic, [])
            if topic == "核心事实":
                items = core_facts or items
            path = self.memory_dir / filename
            body = "\n".join(f"- {self._clip(item, 240)}" for item in self._dedupe(items)[-20:])
            content = (
                f"# {topic}\n\n"
                f"更新时间：{datetime.now().isoformat(timespec='seconds')}\n\n"
                f"{body or '- 暂无。'}\n"
            )
            path.write_text(self._trim_document(content, max(1200, self.max_chars // 2)), encoding="utf-8")

    def _load_topic_summary(self) -> str:
        """Load compact summaries from topic Markdown files."""
        sections = []
        for path in self._topic_files():
            title = path.stem
            text = path.read_text(encoding="utf-8")
            bullets = [
                self._clean_memory_line(line.strip())
                for line in text.splitlines()
                if self._is_memory_bullet(line.strip())
            ][:8]
            if bullets:
                sections.append(f"### {title}\n" + "\n".join(f"- {item}" for item in bullets))
        if not sections:
            return ""
        return "## 目录记忆\n\n" + "\n\n".join(sections)

    def _topic_files(self) -> list[Path]:
        if not self.memory_dir or not self.memory_dir.exists():
            return []
        return sorted(path for path in self.memory_dir.glob("*.md") if path.is_file())

    def _auto_dream_signature(self, memory: ConversationMemory) -> tuple[int, int]:
        durable_text = "|".join(
            message.content
            for message in memory.messages
            if self._is_durable_message(message.role, " ".join(message.content.split()))
        )
        return (len(memory.messages), hash(durable_text))

    def _classify_lines(self, lines: list[str]) -> dict[str, list[str]]:
        topics: dict[str, list[str]] = {
            "核心事实": [],
            "用户偏好": [],
            "项目约束": [],
            "当前任务": [],
            "文件与工具": [],
            "错误与修正": [],
            "工作日志": [],
        }
        for line in lines:
            target = self._topic_for(line)
            topics[target].append(line)
        return {topic: items[-8:] for topic, items in topics.items()}

    def _topic_for(self, line: str) -> str:
        lowered = line.lower()
        if any(key in lowered for key in ("不要", "必须", "do not", "must", "保留", "硬编码", "api key")):
            return "用户偏好"
        if any(key in lowered for key in ("config", "workspace", "sandbox", "权限", "路径", "项目内", "配置")):
            return "项目约束"
        if any(key in lowered for key in ("用户需求", "需要", "实现", "升级", "修复", "检查")):
            return "当前任务"
        if any(key in lowered for key in ("工具 `", "文件", ".py", ".md", "pytest", "ruff", "mypy")):
            return "文件与工具"
        if any(key in lowered for key in ("error", "failed", "traceback", "错误", "失败", "blocked", "503")):
            return "错误与修正"
        if any(key in lowered for key in ("完成", "通过", "测试", "变更", "新增", "agent 回复", "实现记录")):
            return "工作日志"
        return "核心事实"

    def _core_facts(self, topics: dict[str, list[str]]) -> list[str]:
        selected: list[str] = []
        for topic in ("用户偏好", "项目约束", "当前任务", "错误与修正", "核心事实"):
            selected.extend(topics.get(topic, [])[-3:])
        return [self._clip(item, 180) for item in self._dedupe(selected)[:10]]

    def _render_topic_archive(self, topics: dict[str, list[str]], exclude: set[str] | None = None) -> str:
        exclude = exclude or set()
        sections = []
        for topic, items in topics.items():
            if not items:
                continue
            visible_items = [item for item in items[-8:] if self._clip(item, 180) not in exclude]
            if not visible_items:
                body = "- 已提升为核心事实。"
            else:
                body = "\n".join(f"- {self._clip(item, 220)}" for item in visible_items)
            sections.append(f"### {topic}\n\n{body}")
        return "\n\n".join(sections)

    def _is_memory_bullet(self, line: str) -> bool:
        if not line.startswith("-"):
            return False
        ignored = {
            "-",
            "- 暂无持久上下文。",
            "- 暂无最近重要上下文。",
            "- No durable context yet.",
            "- No recent important context.",
        }
        return line not in ignored

    def _clean_memory_line(self, line: str) -> str:
        line = line.strip()
        line = re.sub(r"^-+\s*", "", line)
        line = re.sub(r"^`?(user|assistant|tool)`?\s*[:：]\s*", "", line, flags=re.I)
        line = re.sub(r"^(用户提出|Agent 回复|工具 `[^`]+` 观察到)\s*[:：]\s*", "", line)
        return " ".join(line.split())

    def _dedupe(self, lines: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            clean = self._clean_memory_line(line)
            if not clean:
                continue
            key = re.sub(r"\W+", "", clean.lower())[:160]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(clean)
        return deduped

    def _is_durable_message(self, role: str, content: str) -> bool:
        """Return whether a message is important enough for long-term memory."""
        if role == "tool":
            return True

        lowered = content.lower()
        durable_keywords = {
            "config",
            "api",
            "base_url",
            "model",
            "memory",
            "persistent",
            "test",
            "pytest",
            "mypy",
            "ruff",
            "error",
            "bug",
            "fix",
            "remember",
            "workspace",
            "配置",
            "模型",
            "记忆",
            "持久化",
            "错误",
            "修复",
            "测试",
            "通过",
            "项目",
            "需求",
            "记住",
        }
        if any(keyword in lowered for keyword in durable_keywords):
            return True

        ephemeral_markers = ("只回复", "reply ok", "hello", "你好", "ok")
        if any(marker in lowered for marker in ephemeral_markers):
            return False

        return len(content) >= 120

    def _build_recent_context(self, messages: list[Message]) -> str:
        lines: list[str] = []
        for msg in messages[-6:]:
            content = " ".join(msg.content.split())
            if content and self._is_durable_message(msg.role, content):
                lines.append(f"- `{msg.role}`: {self._clip(content, 180)}")
        return "\n".join(lines)

    def _build_structured_sections(self, memory: ConversationMemory) -> str:
        """Build Claude-style structured notes without unbounded growth."""
        sections = {
            "当前状态": self._current_state(memory.messages),
            "任务规格": self._task_specification(memory.messages),
            "文件和工具": self._files_and_tools(memory.messages),
            "工作流": self._workflow(memory.messages),
            "错误与修正": self._errors(memory.messages),
            "经验记录": self._learnings(memory.messages),
            "工作日志": self._worklog(memory.messages),
        }
        rendered = []
        for title, body in sections.items():
            rendered.append(f"\n\n## {title}\n\n{body or '-'}")
        return "".join(rendered) + "\n"

    def _current_state(self, messages: list[Message]) -> str:
        for msg in reversed(messages):
            if msg.role in {"assistant", "user"} and self._is_durable_message(msg.role, msg.content):
                return f"- {self._clip(' '.join(msg.content.split()), 260)}"
        return ""

    def _task_specification(self, messages: list[Message]) -> str:
        items = []
        for msg in messages:
            if msg.role == "user" and self._is_durable_message(msg.role, msg.content):
                items.append(f"- {self._clip(' '.join(msg.content.split()), 220)}")
        return "\n".join(items[-5:])

    def _files_and_tools(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            if msg.role != "tool":
                continue
            tool_name = msg.metadata.get("tool", "tool")
            summary = self._clip(" ".join(msg.content.split()), 180)
            lines.append(f"- `{tool_name}`: {summary}")
        return "\n".join(lines[-8:])

    def _workflow(self, messages: list[Message]) -> str:
        commands = []
        for msg in messages:
            if msg.role == "tool" and msg.metadata.get("tool") in {"bash", "git_status", "git_diff"}:
                commands.append(f"- `{msg.metadata.get('tool')}`: {self._clip(' '.join(msg.content.split()), 180)}")
        return "\n".join(commands[-5:])

    def _errors(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            content = " ".join(msg.content.split())
            lowered = content.lower()
            if any(marker in lowered for marker in ("error", "failed", "traceback", "错误", "失败")):
                lines.append(f"- {self._clip(content, 240)}")
        return "\n".join(lines[-6:])

    def _learnings(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            content = " ".join(msg.content.split())
            lowered = content.lower()
            if any(marker in lowered for marker in ("remember", "记住", "不要", "do not", "must")):
                lines.append(f"- {self._clip(content, 220)}")
        return "\n".join(lines[-6:])

    def _worklog(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages[-10:]:
            if msg.role in {"assistant", "tool"} and self._is_durable_message(msg.role, msg.content):
                lines.append(f"- `{msg.role}`: {self._clip(' '.join(msg.content.split()), 180)}")
        return "\n".join(lines[-8:])

    def _extract_body(self, text: str) -> str:
        if START_MARKER in text and END_MARKER in text:
            return text.split(START_MARKER, 1)[1].split(END_MARKER, 1)[0]
        return text

    def _extract_section(self, text: str, title: str) -> str:
        marker = f"## {title}"
        if marker not in text:
            return text.strip()
        after = text.split(marker, 1)[1]
        next_section = after.find("\n## ")
        section = after if next_section == -1 else after[:next_section]
        lines = [
            line.strip()
            for line in section.strip().splitlines()
            if line.strip() and not line.startswith(("Updated:", "更新时间："))
        ]
        return "\n".join(lines).strip()

    def _trim(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[-max_chars:].lstrip()

    def _trim_document(self, text: str, max_chars: int) -> str:
        """Trim a memory document while preserving its title and newest details."""
        if len(text) <= max_chars:
            return text
        marker = "\n\n[... 记忆内容因长度限制被截断 ...]\n\n"
        head_chars = min(500, max_chars // 2)
        tail_chars = max_chars - head_chars - len(marker)
        if tail_chars <= 0:
            return text[:max_chars]
        return f"{text[:head_chars].rstrip()}{marker}{text[-tail_chars:].lstrip()}"

    def _clip(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."
