"""Tests for file operation tools."""

import tempfile
from pathlib import Path

import pytest

from code_agent.config.models import FileConfig
from code_agent.tools.file import (
    ReadFileTool,
    WriteFileTool,
    ListDirectoryTool,
    FileExistsTool,
    SearchFilesTool,
)


def make_file_config() -> FileConfig:
    """Allow temp-dir absolute paths in unit tests that create isolated files."""
    return FileConfig(allow_absolute_paths=True)


class TestReadFileTool:
    """Tests for ReadFileTool."""
    
    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Line 1\nLine 2\nLine 3\n")
            path = f.name
        yield path
        Path(path).unlink()  # Cleanup
    
    @pytest.mark.asyncio
    async def test_read_existing_file(self, temp_file):
        """Test reading an existing file."""
        tool = ReadFileTool(make_file_config())
        result = await tool.execute(path=temp_file)
        
        assert result.success is True
        assert "Line 1" in result.output
        assert "Line 2" in result.output
        assert result.metadata["total_lines"] == 3
    
    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self):
        """Test reading a non-existent file."""
        tool = ReadFileTool(make_file_config())
        result = await tool.execute(path="/nonexistent/file.txt")
        
        assert result.success is False
        assert "文件不存在" in (result.error or "")
    
    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, temp_file):
        """Test reading with offset and limit."""
        tool = ReadFileTool(make_file_config())
        result = await tool.execute(path=temp_file, offset=2, limit=1)
        
        assert result.success is True
        assert "Line 2" in result.output
        assert "Line 1" not in result.output
        assert result.metadata["lines_read"] == 1


class TestWriteFileTool:
    """Tests for WriteFileTool."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        with tempfile.TemporaryDirectory() as d:
            yield d
    
    @pytest.mark.asyncio
    async def test_write_new_file(self, temp_dir):
        """Test writing a new file."""
        tool = WriteFileTool(make_file_config())
        file_path = Path(temp_dir) / "test.txt"
        
        result = await tool.execute(path=str(file_path), content="Hello, World!")
        
        assert result.success is True
        assert file_path.exists()
        assert file_path.read_text() == "Hello, World!"
    
    @pytest.mark.asyncio
    async def test_overwrite_existing_file(self, temp_dir):
        """Test overwriting an existing file."""
        tool = WriteFileTool(make_file_config())
        file_path = Path(temp_dir) / "test.txt"
        file_path.write_text("Old content")
        
        result = await tool.execute(path=str(file_path), content="New content")
        
        assert result.success is True
        assert file_path.read_text() == "New content"

    @pytest.mark.asyncio
    async def test_write_project_file_without_allowed_extension_after_approval(self, tmp_path):
        """Approved writes should support common project files without suffixes."""
        tool = WriteFileTool(
            FileConfig(workspace_root=str(tmp_path), allowed_extensions=[".py"])
        )

        result = await tool.execute(path="Dockerfile", content="FROM python:3.13\n")

        assert result.success is True
        assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == "FROM python:3.13\n"

    @pytest.mark.asyncio
    async def test_write_still_respects_blocked_paths(self, tmp_path):
        """Relaxing extensions must not bypass workspace blocked-path rules."""
        tool = WriteFileTool(
            FileConfig(workspace_root=str(tmp_path), blocked_paths=["secrets"])
        )

        result = await tool.execute(path="secrets/token", content="secret")

        assert result.success is False
        assert "禁止访问规则" in (result.error or "")


class TestListDirectoryTool:
    """Tests for ListDirectoryTool."""
    
    @pytest.fixture
    def temp_dir_with_files(self):
        """Create a temporary directory with files."""
        with tempfile.TemporaryDirectory() as d:
            # Create some files and directories
            (Path(d) / "file1.txt").write_text("content1")
            (Path(d) / "file2.py").write_text("content2")
            (Path(d) / "subdir").mkdir()
            (Path(d) / "subdir" / "nested.txt").write_text("nested")
            yield d
    
    @pytest.mark.asyncio
    async def test_list_directory(self, temp_dir_with_files):
        """Test listing directory contents."""
        tool = ListDirectoryTool(make_file_config())
        result = await tool.execute(path=temp_dir_with_files)
        
        assert result.success is True
        assert "file1.txt" in result.output
        assert "file2.py" in result.output
        assert "subdir" in result.output
    
    @pytest.mark.asyncio
    async def test_list_recursive(self, temp_dir_with_files):
        """Test recursive directory listing."""
        tool = ListDirectoryTool(make_file_config())
        result = await tool.execute(path=temp_dir_with_files, recursive=True)
        
        assert result.success is True
        assert "nested.txt" in result.output


class TestFileExistsTool:
    """Tests for FileExistsTool."""
    
    @pytest.mark.asyncio
    async def test_existing_file(self):
        """Test checking an existing file."""
        tool = FileExistsTool()
        result = await tool.execute(path="pyproject.toml")
        
        assert result.success is True
        assert result.metadata["exists"] is True
        assert result.metadata["type"] == "file"
    
    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        """Test checking a non-existent file."""
        tool = FileExistsTool(make_file_config())
        result = await tool.execute(path="/nonexistent/file.txt")

        assert result.success is True
        assert result.metadata["exists"] is False

    @pytest.mark.asyncio
    async def test_default_rejects_outside_workspace(self, tmp_path):
        """Default file tools must not read arbitrary absolute paths."""
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("secret")

        tool = ReadFileTool()
        result = await tool.execute(path=str(outside_file))

        assert result.success is False
        assert "超出工作区根目录" in (result.error or "")

    @pytest.mark.asyncio
    async def test_rejects_disallowed_extension(self, tmp_path):
        """Configured extension allow-list should be enforced."""
        config = FileConfig(workspace_root=str(tmp_path), allowed_extensions=[".py"])
        file_path = tmp_path / "notes.txt"
        file_path.write_text("hello")

        tool = ReadFileTool(config)
        result = await tool.execute(path="notes.txt")

        assert result.success is False
        assert "不在允许列表中" in (result.error or "")


class TestSearchFilesTool:
    """Tests for async content search."""

    @pytest.mark.asyncio
    async def test_search_files_uses_async_subprocess_and_finds_matches(self, tmp_path):
        """SearchFilesTool should find content without blocking via subprocess.run."""
        (tmp_path / "a.py").write_text("class Demo:\n    pass\n", encoding="utf-8")
        tool = SearchFilesTool(FileConfig(workspace_root=str(tmp_path)))

        result = await tool.execute(pattern="class Demo", file_pattern="*.py")

        assert result.success is True
        assert "a.py" in result.output
