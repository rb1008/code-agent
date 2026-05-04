"""Tests for CLI helpers."""

from pathlib import Path

from code_agent.cli import (
    _create_persistent_memory,
    _create_session_transcript,
    _find_config_path,
    _load_project_instructions,
    _load_poor_mode,
    _mask_secret,
    _normalize_project_feature_paths,
    _normalize_workspace_roots,
    _build_project_extension_context,
    _bind_tool_runtime_context,
)
from code_agent.config.models import AgentConfig, Settings
from code_agent.tools.agent_tool import AgentTool
from code_agent.ui.window import format_window_message, indent_block


def test_config_example_loads() -> None:
    """The shipped example config should stay in sync with settings models."""
    settings = Settings.from_yaml(Path("config.example.yaml"))

    assert settings.agent.skills_dir == ".code-agent/skills"
    assert settings.buddy.enabled is False
    assert settings.mcp.enabled is False


def test_find_config_path_walks_up_from_cwd(tmp_path, monkeypatch) -> None:
    """Config discovery should work from nested project directories."""
    (tmp_path / "config.yaml").write_text("llm:\n  model_name: test\n", encoding="utf-8")
    nested = tmp_path / "src" / "code_agent"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert _find_config_path(None) == tmp_path / "config.yaml"


def test_persistent_memory_path_is_relative_to_config_file(tmp_path) -> None:
    """Project-local memory should resolve beside config.yaml, not arbitrary cwd."""
    config_path = tmp_path / "config.yaml"
    settings = Settings(
        agent=AgentConfig(
            persistent_memory_enabled=True,
            persistent_memory_path=".code_agent_memory.md",
        )
    )

    store = _create_persistent_memory(settings, config_path)

    assert store is not None
    assert store.path == tmp_path / ".code_agent_memory.md"
    assert store.memory_dir == tmp_path / ".code-agent" / "memory"


def test_poor_mode_state_path_is_relative_to_config_file(tmp_path) -> None:
    """Poor mode runtime state should be project-local and separate from config."""
    config_path = tmp_path / "config.yaml"
    settings = Settings(
        agent=AgentConfig(
            poor_mode_path=".code-agent/runtime.yaml",
        )
    )

    state = _load_poor_mode(settings, config_path)
    state.set(True)

    assert state.path == tmp_path / ".code-agent" / "runtime.yaml"
    assert not config_path.exists()


def test_session_transcript_dir_is_relative_to_config_file(tmp_path) -> None:
    """Project-local transcripts should resolve beside config.yaml."""
    config_path = tmp_path / "config.yaml"
    settings = Settings(
        agent=AgentConfig(
            transcript_enabled=True,
            transcript_dir=".code-agent/transcripts",
        )
    )

    transcript = _create_session_transcript(settings, config_path)

    assert transcript is not None
    assert transcript.path.parent == tmp_path / ".code-agent" / "transcripts"


def test_workspace_roots_are_relative_to_config_file(tmp_path) -> None:
    """Relative workspace roots should not drift with the process cwd."""
    settings = Settings()
    config_path = tmp_path / "config.yaml"

    _normalize_workspace_roots(settings, config_path)

    assert settings.shell.workspace_root == str(tmp_path.resolve())
    assert settings.file.workspace_root == str(tmp_path.resolve())
    assert settings.project.workspace_root == str(tmp_path.resolve())


def test_project_feature_paths_are_relative_to_config_file(tmp_path) -> None:
    """Skill, command, workflow, and Buddy paths should be project-local."""
    settings = Settings()
    config_path = tmp_path / "config.yaml"

    _normalize_project_feature_paths(settings, config_path)

    assert settings.agent.skills_dir == str((tmp_path / ".code-agent" / "skills").resolve())
    assert settings.agent.commands_dir == str((tmp_path / ".code-agent" / "commands").resolve())
    assert settings.agent.workflows_dir == str((tmp_path / ".code-agent" / "workflows").resolve())
    assert settings.agent.buddy_settings_path == str((tmp_path / ".code-agent" / "buddy.yaml").resolve())


def test_buddy_model_config_falls_back_to_main_llm() -> None:
    """Buddy's independent model channel should be configurable without duplicating secrets."""
    settings = Settings()
    settings.llm.base_url = "https://example.test/v1"
    settings.llm.api_key = "main-key"
    settings.llm.model_name = "main-model"
    settings.buddy.enabled = True

    assert settings.get_buddy_base_url() == "https://example.test/v1"
    assert settings.get_buddy_api_key() == "main-key"
    assert settings.get_buddy_model_name() == "main-model"


def test_project_extension_context_does_not_inline_all_skills_and_commands(tmp_path) -> None:
    """Startup context should avoid loading every extension summary into the prompt."""
    skills = tmp_path / ".code-agent" / "skills" / "review"
    commands = tmp_path / ".code-agent" / "commands"
    skills.mkdir(parents=True)
    commands.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\ndescription: Review changes\n---\nReview $ARGUMENTS.",
        encoding="utf-8",
    )
    (commands / "fix.md").write_text(
        "---\ndescription: Fix failure\n---\nFix $ARGUMENTS.",
        encoding="utf-8",
    )
    settings = Settings(
        agent=AgentConfig(
            skills_dir=str(tmp_path / ".code-agent" / "skills"),
            commands_dir=str(commands),
        )
    )

    context = _build_project_extension_context(settings)

    assert "Project Skills" in context
    assert "Project Commands" in context
    assert "1 project skills" in context
    assert "1 project slash commands" in context
    assert "Review changes" not in context
    assert "Fix failure" not in context


def test_project_instructions_are_loaded_from_config_root(tmp_path) -> None:
    """Project instructions should resolve beside config.yaml and stay bounded."""
    config_path = tmp_path / "config.yaml"
    (tmp_path / "CLAUDE.md").write_text("Use the local test rules.", encoding="utf-8")
    settings = Settings(
        agent=AgentConfig(
            project_instruction_files=["CLAUDE.md"],
            project_instruction_max_chars=1000,
        )
    )

    instructions = _load_project_instructions(settings, config_path)

    assert instructions.sources == [tmp_path / "CLAUDE.md"]
    assert "Use the local test rules." in instructions.content


def test_mask_secret_never_prints_full_value() -> None:
    """Doctor output should not leak full API keys."""
    assert _mask_secret("sk-1234567890") == "sk-1...7890"


def test_window_message_formatting() -> None:
    """Window mode should format transcript entries with stable markers."""
    assert indent_block("a\nb") == "  a\n  b"
    assert format_window_message("user", "hello").startswith("┏━ 你")
    assert "┃ hello" in format_window_message("user", "hello")
    assert format_window_message("assistant", "done").startswith("╔═ Agent")
    assert "║ done" in format_window_message("assistant", "done")


def test_cli_direct_tool_binding_includes_permission_manager() -> None:
    """Slash-command tool execution should pass parent permission state to tools."""
    from code_agent.agent.core import CodeAgent

    agent = CodeAgent(Settings())
    tool = AgentTool(Settings())

    _bind_tool_runtime_context(agent, tool)

    assert tool.permission_manager is agent.permission_manager
