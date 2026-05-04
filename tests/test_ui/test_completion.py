"""Tests for CLI completion helpers."""

from prompt_toolkit.document import Document

from code_agent.ui.commands import command_hint
from code_agent.ui.completion import CodeAgentCompleter


def _texts(completer: CodeAgentCompleter, text: str) -> list[str]:
    return [
        completion.text
        for completion in completer.get_completions(Document(text, len(text)), object())
    ]


def test_slash_command_completion_matches_prefix() -> None:
    """Slash command completion should work while typing."""
    texts = _texts(CodeAgentCompleter(), "/mo")
    poor_texts = _texts(CodeAgentCompleter(), "/po")

    assert "/model" in texts
    assert "/monitor" in texts
    assert "/poor" in poor_texts


def test_dynamic_skill_and_workflow_completion() -> None:
    """Project skills and workflow names should be completed as command arguments."""
    completer = CodeAgentCompleter(
        skill_names=lambda: ["review"],
        workflow_names=lambda: ["test"],
    )

    assert "review" in _texts(completer, "/skill r")
    assert "test" in _texts(completer, "/workflow t")


def test_path_completion_preserves_typed_directory(tmp_path) -> None:
    """Completing nested paths should not drop the already typed parent directory."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "code_agent").mkdir()
    completer = CodeAgentCompleter(cwd=tmp_path)

    assert "src/code_agent/" in _texts(completer, "src/co")


def test_command_hint_is_chinese() -> None:
    """The bottom toolbar should explain command arguments in Chinese."""
    hint = command_hint("/workflow ")

    assert "用法" not in hint
    assert "workflow" in hint
    assert "脚本名" in hint
