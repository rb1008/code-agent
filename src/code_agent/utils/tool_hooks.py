"""Tool lifecycle hook execution."""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml  # type: ignore[import-untyped]


@dataclass
class HookCommand:
    """One external command attached to a tool lifecycle event."""

    event: str
    command: str
    timeout: int = 10
    continue_on_error: bool = True


@dataclass
class HookResult:
    """Result from one hook command."""

    event: str
    command: str
    success: bool
    output: str


class ToolHookManager:
    """Load and execute project-local tool hooks."""

    def __init__(self, path: Path, cwd: Optional[Path] = None) -> None:
        self.path = path
        self.cwd = cwd or Path.cwd()
        self.hooks: dict[str, list[HookCommand]] = {}
        self.load()

    def load(self) -> None:
        self.hooks = {}
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw_hooks = data.get("hooks", {})
        if not isinstance(raw_hooks, dict):
            return
        for event, commands in raw_hooks.items():
            if not isinstance(commands, list):
                continue
            for entry in commands:
                hook = self._parse_hook(str(event), entry)
                if hook:
                    self.hooks.setdefault(hook.event, []).append(hook)

    async def run(
        self,
        event: str,
        *,
        tool_name: str,
        tool_params: dict[str, Any],
        output: str = "",
        success: Optional[bool] = None,
    ) -> list[HookResult]:
        """Run hooks for one event."""
        results: list[HookResult] = []
        for hook in self.hooks.get(event, []):
            env = os.environ.copy()
            env.update(
                {
                    "CODE_AGENT_HOOK_EVENT": event,
                    "CODE_AGENT_TOOL_NAME": tool_name,
                    "CODE_AGENT_TOOL_SUCCESS": "" if success is None else str(success).lower(),
                    "CODE_AGENT_TOOL_OUTPUT": output[:8000],
                }
            )
            for key, value in tool_params.items():
                env[f"CODE_AGENT_TOOL_PARAM_{key.upper()}"] = str(value)

            try:
                process = await asyncio.create_subprocess_shell(
                    hook.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.cwd),
                    env=env,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=hook.timeout,
                )
                text = _decode(stdout) + _decode(stderr)
                result = HookResult(
                    event=event,
                    command=hook.command,
                    success=process.returncode == 0,
                    output=text.strip(),
                )
            except Exception as e:
                result = HookResult(event=event, command=hook.command, success=False, output=str(e))

            results.append(result)
            if not result.success and not hook.continue_on_error:
                break
        return results

    def describe(self) -> list[dict[str, Any]]:
        """Return hook metadata for diagnostics."""
        rows: list[dict[str, Any]] = []
        for event, hooks in self.hooks.items():
            for hook in hooks:
                rows.append(
                    {
                        "event": event,
                        "command": hook.command,
                        "timeout": hook.timeout,
                        "continue_on_error": hook.continue_on_error,
                    }
                )
        return rows

    def _parse_hook(self, event: str, entry: Any) -> Optional[HookCommand]:
        if isinstance(entry, str):
            return HookCommand(event=event, command=entry)
        if not isinstance(entry, dict):
            return None
        command = entry.get("command")
        if isinstance(command, list):
            command = " ".join(shlex.quote(str(part)) for part in command)
        if not command:
            return None
        return HookCommand(
            event=event,
            command=str(command),
            timeout=int(entry.get("timeout", 10)),
            continue_on_error=bool(entry.get("continue_on_error", True)),
        )


def _decode(data: bytes | None) -> str:
    return data.decode("utf-8", errors="replace") if data else ""
