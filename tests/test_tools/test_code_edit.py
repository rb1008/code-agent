"""Tests for code editing tools."""

import tempfile
from pathlib import Path

import pytest

from code_agent.config.models import FileConfig
from code_agent.tools.code_edit import (
    ReplaceCodeTool,
    InsertCodeTool,
    DeleteCodeTool,
    ApplyDiffTool,
)


def make_file_config() -> FileConfig:
    """Allow temp-dir absolute paths in unit tests that create isolated files."""
    return FileConfig(allow_absolute_paths=True)


class TestReplaceCodeTool:
    """Tests for ReplaceCodeTool."""

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def hello():\n    print('hello')\n\ndef world():\n    print('world')\n")
            path = f.name
        yield path
        Path(path).unlink()

    @pytest.mark.asyncio
    async def test_replace_success(self, temp_file):
        """Test successful code replacement."""
        tool = ReplaceCodeTool(make_file_config())
        result = await tool.execute(
            path=temp_file,
            old_string="    print('hello')",
            new_string="    print('hello world')",
        )

        assert result.success is True
        assert "已替换" in result.output

        # Verify file content
        content = Path(temp_file).read_text()
        assert "print('hello world')" in content

    @pytest.mark.asyncio
    async def test_replace_not_found(self, temp_file):
        """Test replacement when old_string not found."""
        tool = ReplaceCodeTool(make_file_config())
        result = await tool.execute(
            path=temp_file,
            old_string="nonexistent code",
            new_string="new code",
        )

        assert result.success is False
        assert "未找到 old_string" in (result.error or "")

    @pytest.mark.asyncio
    async def test_replace_nonexistent_file(self):
        """Test replacement on non-existent file."""
        tool = ReplaceCodeTool(make_file_config())
        result = await tool.execute(
            path="/nonexistent/file.py",
            old_string="old",
            new_string="new",
        )

        assert result.success is False
        assert "文件不存在" in (result.error or "")


class TestInsertCodeTool:
    """Tests for InsertCodeTool."""

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            path = f.name
        yield path
        Path(path).unlink()

    @pytest.mark.asyncio
    async def test_insert_at_line(self, temp_file):
        """Test inserting at a specific line."""
        tool = InsertCodeTool(make_file_config())
        result = await tool.execute(
            path=temp_file,
            new_string="inserted line",
            line_number=2,
        )

        assert result.success is True

        content = Path(temp_file).read_text()
        lines = content.split("\n")
        assert lines[1] == "inserted line"

    @pytest.mark.asyncio
    async def test_insert_after_string(self, temp_file):
        """Test inserting after a specific string."""
        tool = InsertCodeTool(make_file_config())
        result = await tool.execute(
            path=temp_file,
            new_string="\ninserted",
            insert_after="line1",
        )

        assert result.success is True

        content = Path(temp_file).read_text()
        assert "line1\ninserted" in content


class TestDeleteCodeTool:
    """Tests for DeleteCodeTool."""

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("keep this\ndelete this\nkeep this too\n")
            path = f.name
        yield path
        Path(path).unlink()

    @pytest.mark.asyncio
    async def test_delete_success(self, temp_file):
        """Test successful deletion."""
        tool = DeleteCodeTool(make_file_config())
        result = await tool.execute(
            path=temp_file,
            target_string="delete this\n",
        )

        assert result.success is True

        content = Path(temp_file).read_text()
        assert "delete this" not in content
        assert "keep this" in content

    @pytest.mark.asyncio
    async def test_delete_not_found(self, temp_file):
        """Test deletion when target not found."""
        tool = DeleteCodeTool(make_file_config())
        result = await tool.execute(
            path=temp_file,
            target_string="nonexistent",
        )

        assert result.success is False
        assert "未找到 target_string" in (result.error or "")


class TestApplyDiffTool:
    """Tests for ApplyDiffTool."""

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            path = f.name
        yield path
        Path(path).unlink()

    @pytest.mark.asyncio
    async def test_apply_diff(self, temp_file):
        """Test applying a diff."""
        tool = ApplyDiffTool(make_file_config())
        diff = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 line1
-line2
+modified line2
 line3
"""
        result = await tool.execute(
            path=temp_file,
            diff=diff,
        )

        assert result.success is True

        content = Path(temp_file).read_text()
        assert "modified line2" in content
        # 检查原始行已被替换
        lines = content.splitlines()
        assert "modified line2" in lines
        assert "line2" not in lines

    @pytest.mark.asyncio
    async def test_apply_diff_preserves_trailing_content(self, temp_file):
        """Applying a partial hunk must keep lines after the hunk."""
        tool = ApplyDiffTool(make_file_config())
        diff = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 line1
-line2
+changed
"""
        result = await tool.execute(path=temp_file, diff=diff)

        assert result.success is True
        assert Path(temp_file).read_text() == "line1\nchanged\nline3\n"
