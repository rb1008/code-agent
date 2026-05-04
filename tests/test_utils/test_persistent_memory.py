"""Tests for Markdown persistent memory."""

from code_agent.agent.memory import ConversationMemory
from code_agent.utils.persistent_memory import PersistentMemory


def test_persistent_memory_saves_bounded_markdown_and_loads_summary(tmp_path) -> None:
    """Persistent memory should be project-local Markdown and bounded."""
    memory = ConversationMemory()
    memory.add("user", "Remember that the project uses gpt-4.1-mini.")
    memory.add("assistant", "Noted.")

    path = tmp_path / ".code_agent_memory.md"
    store = PersistentMemory(path, max_chars=1000)
    store.save(memory)

    text = path.read_text(encoding="utf-8")
    assert "# Code Agent 持久化记忆" in text
    assert "gpt-4.1-mini" in text
    assert "## 当前状态" in text
    assert len(text) <= 1000

    loaded = ConversationMemory()
    assert store.load_into(loaded) is True
    assert "gpt-4.1-mini" in loaded.summary


def test_persistent_memory_overwrites_instead_of_appending_unbounded(tmp_path) -> None:
    """Saving repeatedly should replace the file and respect max_chars."""
    path = tmp_path / ".code_agent_memory.md"
    store = PersistentMemory(path, max_chars=1200)
    memory = ConversationMemory()

    for i in range(50):
        memory.add("user", f"Important item {i} " + "x" * 100)
        store.save(memory)

    text = path.read_text(encoding="utf-8")
    assert len(text) <= 1200
    assert text.count("# Code Agent 持久化记忆") <= 1


def test_persistent_memory_ignores_ephemeral_chat(tmp_path) -> None:
    """Trivial replies should not pollute long-term memory."""
    memory = ConversationMemory()
    memory.add("user", "请只回复 OK")
    memory.add("assistant", "OK")

    path = tmp_path / ".code_agent_memory.md"
    PersistentMemory(path).save(memory)

    text = path.read_text(encoding="utf-8")
    assert "请只回复 OK" not in text
    assert "- 暂无持久上下文。" in text


def test_persistent_memory_dream_dedupes_and_archives_by_topic(tmp_path) -> None:
    """Dream should rewrite memory into bounded themed archive."""
    memory = ConversationMemory()
    memory.summary = "- 用户要求保留 config.yaml 中硬编码 API key。\n"
    memory.add("user", "请记住：不要移除 config.yaml 中硬编码 API key。")
    memory.add("assistant", "已完成 /dream 记忆整理功能，并运行 pytest 通过。")
    memory.add("tool", "pytest -q: 125 passed", tool="bash")
    path = tmp_path / ".code_agent_memory.md"
    path.write_text(
        "# Code Agent 持久化记忆\n\n"
        "<!-- code-agent-memory:v1 -->\n"
        "## 摘要\n\n"
        "- 用户要求保留 config.yaml 中硬编码 API key。\n"
        "- 用户要求保留 config.yaml 中硬编码 API key。\n"
        "<!-- /code-agent-memory -->\n",
        encoding="utf-8",
    )

    memory_dir = tmp_path / ".code-agent" / "memory"
    result = PersistentMemory(path, max_chars=1800, memory_dir=memory_dir).dream(memory)

    text = path.read_text(encoding="utf-8")
    assert result.after_chars <= 1800
    assert "整理方式：/dream 主动整理" in text
    assert "## 主题归档" in text
    assert "### 用户偏好" in text
    assert "### 文件与工具" in text
    assert text.count("用户要求保留 config.yaml 中硬编码 API key") == 1
    assert "config.yaml" in memory.summary
    assert (memory_dir / "preferences.md").exists()


def test_persistent_memory_loads_directory_topic_memory(tmp_path) -> None:
    """Startup loading should include topic Markdown files under memory_dir."""
    memory_dir = tmp_path / ".code-agent" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "constraints.md").write_text(
        "# 项目约束\n\n- 用户要求 config.yaml 中的 API key 暂时保留。\n",
        encoding="utf-8",
    )
    store = PersistentMemory(tmp_path / ".code_agent_memory.md", memory_dir=memory_dir)
    memory = ConversationMemory()

    assert store.load_into(memory) is True
    assert "API key" in memory.summary


def test_persistent_memory_auto_dream_runs_when_memory_grows(tmp_path) -> None:
    """Auto dream should organize oversized durable memory once per growth signature."""
    memory = ConversationMemory(max_messages=50, compact_threshold=99)
    for i in range(8):
        memory.add("user", f"请记住第 {i} 条项目需求：需要保留测试、配置和错误修复记录。")
        memory.add("assistant", f"已完成第 {i} 条需求，并运行 pytest 通过。")

    path = tmp_path / ".code_agent_memory.md"
    store = PersistentMemory(
        path,
        max_chars=1000,
        memory_dir=tmp_path / ".code-agent" / "memory",
        auto_dream_enabled=True,
        auto_dream_min_messages=4,
    )
    store.save(memory)

    result = store.maybe_auto_dream(memory)

    assert result is not None
    assert "整理方式：/dream 主动整理" in path.read_text(encoding="utf-8")
    assert store.maybe_auto_dream(memory) is None
