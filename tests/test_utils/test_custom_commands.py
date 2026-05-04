"""Tests for Markdown-backed custom slash commands."""

from code_agent.utils.custom_commands import CustomCommandLoader


def test_custom_command_loader_reads_metadata_and_renders_arguments(tmp_path) -> None:
    """Custom commands should load frontmatter and render argument placeholders."""
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "fix.md").write_text(
        "---\n"
        "description: Fix a failing test\n"
        "argument_hint: test name\n"
        "---\n"
        "Please fix $ARGUMENTS using files near ${CODE_AGENT_COMMAND_DIR}.",
        encoding="utf-8",
    )

    command = CustomCommandLoader(commands).get("/fix")

    assert command is not None
    assert command.name == "fix"
    assert command.description == "Fix a failing test"
    rendered = command.render("test_cli")
    assert "# Custom command: /fix" in rendered
    assert "Please fix test_cli" in rendered
    assert "User arguments:\ntest_cli" in rendered


def test_custom_command_listing_filters_non_invocable(tmp_path) -> None:
    """Commands marked non-user-invocable should not appear in slash listings."""
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "public.md").write_text("Public command", encoding="utf-8")
    (commands / "private.md").write_text(
        "---\nuser_invocable: false\n---\nPrivate command",
        encoding="utf-8",
    )

    rows = CustomCommandLoader(commands).listing()

    assert [row["命令"] for row in rows] == ["/public"]


def test_custom_command_matching_returns_only_relevant_commands(tmp_path) -> None:
    """Per-turn command hints should be request-relevant."""
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "fix.md").write_text(
        "---\ndescription: 修复测试失败\n---\nFix $ARGUMENTS.",
        encoding="utf-8",
    )
    (commands / "release.md").write_text(
        "---\ndescription: 发布版本\n---\nRelease $ARGUMENTS.",
        encoding="utf-8",
    )

    matches = CustomCommandLoader(commands).matching("帮我修复这个测试", limit=1)

    assert [command.name for command in matches] == ["fix"]
