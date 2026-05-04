"""Minimal MCP stdio bridge tools.

This module intentionally keeps the bridge small and dependency-free. It talks
JSON-RPC over line-delimited stdio, which is enough for local adapters and tests.
Servers that require full MCP header framing can be wrapped by a small adapter
script without changing the agent tool surface.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from code_agent.config.models import MCPConfig, MCPServerConfig
from code_agent.tools.base import BaseTool, ToolPermission, ToolResult


@dataclass
class MCPResponse:
    """JSON-RPC response from an MCP server."""

    result: Any = None
    error: Optional[str] = None


class MCPStdioClient:
    """Small one-shot JSON-RPC stdio client."""

    def __init__(self, server: MCPServerConfig, timeout: int = 20) -> None:
        self.server = server
        self.timeout = timeout

    async def request(self, method: str, params: Optional[dict[str, Any]] = None) -> MCPResponse:
        env = os.environ.copy()
        env.update(self.server.env)
        process = await asyncio.create_subprocess_exec(
            self.server.command,
            *self.server.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        assert process.stdin is not None
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        process.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
        await process.stdin.drain()
        process.stdin.close()

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return MCPResponse(error=f"MCP request timed out: {method}")

        if process.returncode not in (0, None) and not stdout:
            return MCPResponse(error=_decode(stderr) or f"MCP server exited {process.returncode}")

        for line in _decode(stdout).splitlines():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 1:
                if "error" in message:
                    return MCPResponse(error=str(message["error"]))
                return MCPResponse(result=message.get("result"))
        return MCPResponse(error=_decode(stderr) or "MCP server returned no JSON-RPC response")


class MCPBaseTool(BaseTool):
    """Base class for MCP bridge tools."""

    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    def __init__(self, config: Optional[MCPConfig] = None) -> None:
        super().__init__(config or MCPConfig())

    def _server(self, server: str) -> MCPServerConfig | None:
        cfg: MCPServerConfig | None = self.config.servers.get(server)
        if not cfg or not cfg.enabled:
            return None
        return cfg

    async def _request(
        self,
        server: str,
        method: str,
        params: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        cfg = self._server(server)
        if not cfg:
            return ToolResult.fail(f"MCP server is not configured or disabled: {server}")
        response = await MCPStdioClient(cfg).request(method, params)
        if response.error:
            return ToolResult.fail(response.error)
        return ToolResult.ok(
            json.dumps(response.result, ensure_ascii=False, indent=2),
            server=server,
            method=method,
        )


class ListMCPResourcesTool(MCPBaseTool):
    """List resources advertised by an MCP server."""

    name = "list_mcp_resources"
    aliases = ["mcp_resources"]
    search_hint = "列出 MCP 资源"
    description = "列出已配置 MCP 服务器暴露的资源。"
    parameters = {
        "server": {
            "type": "string",
            "description": "已配置的 MCP 服务器名称",
            "required": True,
        }
    }

    async def execute(self, server: str) -> ToolResult:
        return await self._request(server, "resources/list")


class ReadMCPResourceTool(MCPBaseTool):
    """Read one MCP resource."""

    name = "read_mcp_resource"
    aliases = ["mcp_read"]
    search_hint = "读取 MCP 资源 URI"
    description = "从已配置 MCP 服务器读取指定资源 URI。"
    parameters = {
        "server": {"type": "string", "description": "已配置的 MCP 服务器名称", "required": True},
        "uri": {"type": "string", "description": "资源 URI", "required": True},
    }

    async def execute(self, server: str, uri: str) -> ToolResult:
        return await self._request(server, "resources/read", {"uri": uri})


class CallMCPTool(BaseTool):
    """Call a named tool on a configured MCP server."""

    name = "mcp_call_tool"
    aliases = ["mcp_tool"]
    search_hint = "调用 MCP 服务器工具"
    description = "调用已配置 MCP 服务器暴露的工具。"
    permission = ToolPermission(require_confirmation=True, allowed_in_auto_mode=False)
    parameters = {
        "server": {"type": "string", "description": "已配置的 MCP 服务器名称", "required": True},
        "tool": {"type": "string", "description": "MCP 工具名称", "required": True},
        "arguments": {
            "type": "object",
            "description": "传给 MCP 工具的参数",
            "required": False,
        },
    }

    def __init__(self, config: Optional[MCPConfig] = None) -> None:
        super().__init__(config or MCPConfig())

    async def execute(
        self,
        server: str,
        tool: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        cfg = self.config.servers.get(server)
        if not cfg or not cfg.enabled:
            return ToolResult.fail(f"MCP server is not configured or disabled: {server}")
        response = await MCPStdioClient(cfg).request(
            "tools/call",
            {"name": tool, "arguments": arguments or {}},
        )
        if response.error:
            return ToolResult.fail(response.error)
        return ToolResult.ok(
            json.dumps(response.result, ensure_ascii=False, indent=2),
            server=server,
            tool=tool,
        )


def _decode(data: bytes | None) -> str:
    return data.decode("utf-8", errors="replace") if data else ""
