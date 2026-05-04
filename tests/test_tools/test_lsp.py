"""Tests for lightweight semantic code navigation."""

import pytest

from code_agent.config.models import ProjectConfig
from code_agent.tools.lsp import LSPTool


@pytest.mark.asyncio
async def test_lsp_tool_finds_python_symbols_and_references(tmp_path) -> None:
    """The semantic tool should locate definitions and references in Python files."""
    source = tmp_path / "sample.py"
    source.write_text(
        "class Runner:\n"
        "    def run(self):\n"
        "        return helper()\n\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    tool = LSPTool(ProjectConfig(workspace_root=str(tmp_path)))

    symbols = await tool.execute("symbols")
    definition = await tool.execute("definition", query="helper")
    references = await tool.execute("references", query="helper")

    assert symbols.success is True
    assert "class `Runner`" in symbols.output
    assert "function `helper`" in definition.output
    assert "sample.py:3" in references.output


@pytest.mark.asyncio
async def test_lsp_tool_reports_python_syntax_diagnostics(tmp_path) -> None:
    """Diagnostics should catch simple Python syntax errors without external LSP servers."""
    (tmp_path / "broken.py").write_text("def nope(:\n    pass\n", encoding="utf-8")
    tool = LSPTool(ProjectConfig(workspace_root=str(tmp_path)))

    result = await tool.execute("diagnostics")

    assert result.success is True
    assert "Python 语法错误" in result.output
