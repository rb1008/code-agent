"""Tests for shell tools."""

import pytest

from code_agent.tools.shell import BashTool, GlobTool, GrepTool


class TestBashTool:
    """Tests for BashTool."""

    @pytest.fixture
    def unrestricted_tool(self):
        """Create a BashTool with no command restrictions."""
        from code_agent.config.models import ShellConfig
        config = ShellConfig(allowed_commands=[])
        return BashTool(config=config)

    @pytest.mark.asyncio
    async def test_echo_command(self, unrestricted_tool):
        """Test basic echo command."""
        result = await unrestricted_tool.execute(command="echo hello world")

        assert result.success is True
        assert "hello world" in result.output

    @pytest.mark.asyncio
    async def test_pwd_command(self, unrestricted_tool):
        """Test pwd command."""
        result = await unrestricted_tool.execute(command="pwd")

        assert result.success is True
        assert result.metadata["return_code"] == 0

    @pytest.mark.asyncio
    async def test_blocked_command(self):
        """Test blocked command."""
        tool = BashTool()
        result = await tool.execute(command="sudo apt update")

        assert result.success is False
        assert "被拦截" in (result.error or "")

    @pytest.mark.asyncio
    async def test_allowed_command_requires_exact_executable(self):
        """Allow-list entries should not match arbitrary prefixes."""
        from code_agent.config.models import ShellConfig

        tool = BashTool(config=ShellConfig(allowed_commands=["git"]))
        result = await tool.execute(command="gitmalicious status")

        assert result.success is False
        assert "允许列表" in (result.error or "")

    @pytest.mark.asyncio
    async def test_rejects_shell_compound_allowlist_bypass(self, tmp_path):
        """Allow-list checks must apply to the whole command, not only argv[0]."""
        from code_agent.config.models import ShellConfig

        tool = BashTool(
            config=ShellConfig(
                workspace_root=str(tmp_path),
                allowed_commands=["echo"],
                require_confirmation=False,
            )
        )
        result = await tool.execute(command="echo ok; touch pwned")

        assert result.success is False
        assert "复合语法" in (result.error or "")
        assert not (tmp_path / "pwned").exists()

    @pytest.mark.asyncio
    async def test_quoted_python_semicolon_is_allowed(self, tmp_path):
        """Quoted code arguments may contain semicolons because they are not shell syntax."""
        from code_agent.config.models import ShellConfig

        tool = BashTool(
            config=ShellConfig(
                workspace_root=str(tmp_path),
                allowed_commands=["python"],
                require_confirmation=False,
            )
        )
        result = await tool.execute(command="python -c 'import sys; print(123)'")

        assert result.success is True
        assert "123" in result.output

    @pytest.mark.asyncio
    async def test_nonzero_exit_is_failure(self, unrestricted_tool):
        """A command that exits non-zero should be surfaced as failed."""
        result = await unrestricted_tool.execute(command="python -c 'raise SystemExit(2)'")

        assert result.success is False
        assert result.metadata["return_code"] == 2

    @pytest.mark.asyncio
    async def test_timeout(self, unrestricted_tool):
        """Test command timeout."""
        result = await unrestricted_tool.execute(command="sleep 5", timeout=1)

        assert result.success is False
        assert "超时" in result.error


class TestGlobTool:
    """Tests for GlobTool."""

    @pytest.mark.asyncio
    async def test_glob_py_files(self):
        """Test globbing Python files."""
        tool = GlobTool()
        result = await tool.execute(pattern="src/**/*.py")

        assert result.success is True
        assert result.metadata["matches"] > 0

    @pytest.mark.asyncio
    async def test_glob_no_matches(self):
        """Test glob with no matches."""
        tool = GlobTool()
        result = await tool.execute(pattern="*.nonexistent")

        assert result.success is True
        assert "未找到匹配模式" in result.output

    @pytest.mark.asyncio
    async def test_glob_without_double_star_is_not_recursive(self, tmp_path):
        """Plain glob patterns should not descend into nested directories."""
        from code_agent.config.models import ShellConfig

        (tmp_path / "top.py").write_text("")
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "inner.py").write_text("")

        tool = GlobTool(ShellConfig(workspace_root=str(tmp_path)))
        result = await tool.execute(pattern="*.py")

        assert result.success is True
        assert "top.py" in result.output
        assert "inner.py" not in result.output


class TestGrepTool:
    """Tests for GrepTool."""

    @pytest.mark.asyncio
    async def test_grep_found(self):
        """Test grep finding matches."""
        tool = GrepTool()
        result = await tool.execute(pattern="class.*Tool", path="src", include="*.py")

        assert result.success is True
        assert result.metadata["matches"] > 0

    @pytest.mark.asyncio
    async def test_grep_not_found(self):
        """Test grep with no matches."""
        tool = GrepTool()
        result = await tool.execute(pattern="xyznonexistent123", path="src")

        assert result.success is True
        assert "未找到匹配" in result.output
