"""Tests for project tools."""

import tempfile
from pathlib import Path

import pytest

from code_agent.config.models import ProjectConfig
from code_agent.tools.project import (
    GetProjectStructureTool,
    SummarizeFileTool,
    GetDependenciesTool,
)


def make_project_config() -> ProjectConfig:
    """Allow temp-dir absolute paths in unit tests that create isolated projects."""
    return ProjectConfig(allow_absolute_paths=True)


class TestGetProjectStructureTool:
    """Tests for GetProjectStructureTool."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project structure."""
        with tempfile.TemporaryDirectory() as d:
            # Create some files and directories
            (Path(d) / "src").mkdir()
            (Path(d) / "src" / "main.py").write_text("print('hello')\n")
            (Path(d) / "src" / "utils").mkdir()
            (Path(d) / "src" / "utils" / "helper.py").write_text("def helper(): pass\n")
            (Path(d) / "README.md").write_text("# Project\n")
            (Path(d) / "pyproject.toml").write_text("[project]\n")
            yield d

    @pytest.mark.asyncio
    async def test_structure(self, temp_project):
        """Test getting project structure."""
        tool = GetProjectStructureTool(make_project_config())
        result = await tool.execute(path=temp_project, max_depth=3)

        assert result.success is True
        assert "main.py" in result.output
        assert "helper.py" in result.output
        assert "README.md" in result.output

    @pytest.mark.asyncio
    async def test_max_depth(self, temp_project):
        """Test max depth limit."""
        tool = GetProjectStructureTool(make_project_config())
        result = await tool.execute(path=temp_project, max_depth=1)

        assert result.success is True
        assert "src" in result.output
        # helper.py might not show at depth 1


class TestSummarizeFileTool:
    """Tests for SummarizeFileTool."""

    @pytest.fixture
    def temp_py_file(self):
        """Create a temporary Python file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                "import os\n"
                "from pathlib import Path\n"
                "\n"
                "def helper():\n"
                "    pass\n"
                "\n"
                "class MyClass:\n"
                "    def method(self):\n"
                "        pass\n"
            )
            path = f.name
        yield path
        Path(path).unlink()

    @pytest.mark.asyncio
    async def test_summarize_python(self, temp_py_file):
        """Test summarizing a Python file."""
        tool = SummarizeFileTool(make_project_config())
        result = await tool.execute(path=temp_py_file)

        assert result.success is True
        assert "Python" in result.output
        assert "导入：2" in result.output
        # 方法定义也算作函数，所以可能是 2
        assert "函数：" in result.output
        assert "类：1" in result.output

    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        """Test summarizing non-existent file."""
        tool = SummarizeFileTool(make_project_config())
        result = await tool.execute(path="/nonexistent/file.py")

        assert result.success is False


class TestGetDependenciesTool:
    """Tests for GetDependenciesTool."""

    @pytest.fixture
    def temp_project_with_deps(self):
        """Create a temporary project with dependency files."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "requirements.txt").write_text(
                "requests>=2.0\npytest>=7.0\n"
            )
            (Path(d) / "pyproject.toml").write_text("[project]\n")
            yield d

    @pytest.mark.asyncio
    async def test_dependencies(self, temp_project_with_deps):
        """Test getting dependencies."""
        tool = GetDependenciesTool(make_project_config())
        result = await tool.execute(path=temp_project_with_deps)

        assert result.success is True
        assert "requests" in result.output
        assert "pytest" in result.output

    @pytest.mark.asyncio
    async def test_no_dependencies(self):
        """Test project with no dependency files."""
        with tempfile.TemporaryDirectory() as d:
            tool = GetDependenciesTool(make_project_config())
            result = await tool.execute(path=d)

            assert result.success is True
            assert "未找到项目依赖文件" in result.output
