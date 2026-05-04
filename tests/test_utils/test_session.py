"""Tests for safe session persistence."""

import pytest

from code_agent.agent.memory import ConversationMemory
from code_agent.utils.session import SessionManager


def test_session_manager_rejects_path_traversal_names(tmp_path) -> None:
    """Session names must not escape the configured sessions directory."""
    manager = SessionManager(tmp_path / "sessions")
    memory = ConversationMemory()

    with pytest.raises(ValueError):
        manager.save(memory, session_name="../outside")

    assert not (tmp_path / "outside.json").exists()


def test_session_manager_accepts_simple_names(tmp_path) -> None:
    """Normal session names should still save and load."""
    manager = SessionManager(tmp_path / "sessions")
    memory = ConversationMemory()
    memory.add("user", "hello")

    path = manager.save(memory, session_name="work-1")
    loaded, metadata = manager.load("work-1")

    assert path.name == "work-1.json"
    assert loaded.messages[0].content == "hello"
    assert metadata == {}
