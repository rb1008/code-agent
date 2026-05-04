"""Tests for git tools."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from code_agent.tools.git import (
    GitStatusTool,
    GitDiffTool,
    GitLogTool,
    GitBranchTool,
)


@pytest.fixture
def git_repo():
    """Create a temporary git repository."""
    with tempfile.TemporaryDirectory() as d:
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True)

        # Create a file and commit
        (Path(d) / "test.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=d, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=d, capture_output=True)

        # Save current dir and change to repo
        old_cwd = os.getcwd()
        os.chdir(d)
        yield d
        os.chdir(old_cwd)


class TestGitStatusTool:
    """Tests for GitStatusTool."""

    @pytest.mark.asyncio
    async def test_status(self, git_repo):
        """Test git status."""
        tool = GitStatusTool()
        result = await tool.execute()

        assert result.success is True
        # Should show on branch main/master
        assert "On branch" in result.output or "nothing to commit" in result.output


class TestGitDiffTool:
    """Tests for GitDiffTool."""

    @pytest.mark.asyncio
    async def test_diff_no_changes(self, git_repo):
        """Test diff with no changes."""
        tool = GitDiffTool()
        result = await tool.execute()

        assert result.success is True
        assert "未发现差异" in result.output or result.output == ""


class TestGitLogTool:
    """Tests for GitLogTool."""

    @pytest.mark.asyncio
    async def test_log(self, git_repo):
        """Test git log."""
        tool = GitLogTool()
        result = await tool.execute(n=5)

        assert result.success is True
        assert "initial" in result.output


class TestGitBranchTool:
    """Tests for GitBranchTool."""

    @pytest.mark.asyncio
    async def test_list_branches(self, git_repo):
        """Test listing branches."""
        tool = GitBranchTool()
        result = await tool.execute(action="list")

        assert result.success is True
        # Should show at least main or master
        assert "main" in result.output or "master" in result.output
