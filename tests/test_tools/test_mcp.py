"""Tests for minimal MCP bridge tools."""

import json

import pytest

from code_agent.config.models import MCPConfig, MCPServerConfig
from code_agent.tools.mcp import CallMCPTool, ListMCPResourcesTool, ReadMCPResourceTool


@pytest.fixture
def mcp_config(tmp_path) -> MCPConfig:
    """Create a line-delimited JSON-RPC server fixture."""
    server = tmp_path / "server.py"
    server.write_text(
        "import json, sys\n"
        "msg=json.loads(sys.stdin.readline())\n"
        "method=msg['method']\n"
        "if method == 'resources/list':\n"
        "    result={'resources':[{'uri':'memo://one','name':'One'}]}\n"
        "elif method == 'resources/read':\n"
        "    result={'contents':[{'uri':msg['params']['uri'],'text':'hello'}]}\n"
        "elif method == 'tools/call':\n"
        "    result={'content':[{'type':'text','text':msg['params']['name']}]}\n"
        "else:\n"
        "    result={}\n"
        "print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}))\n",
        encoding="utf-8",
    )
    return MCPConfig(
        enabled=True,
        servers={"local": MCPServerConfig(command="python", args=[str(server)])},
    )


@pytest.mark.asyncio
async def test_mcp_resource_tools(mcp_config: MCPConfig) -> None:
    """MCP resource listing and reading should call the configured server."""
    listed = await ListMCPResourcesTool(mcp_config).execute("local")
    read = await ReadMCPResourceTool(mcp_config).execute("local", "memo://one")

    assert listed.success is True
    assert "memo://one" in listed.output
    assert read.success is True
    assert "hello" in read.output


@pytest.mark.asyncio
async def test_mcp_call_tool(mcp_config: MCPConfig) -> None:
    """Generic MCP tool calls should pass tool name and arguments."""
    result = await CallMCPTool(mcp_config).execute("local", "echo", {"text": "hello"})

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["content"][0]["text"] == "echo"
