"""Workspace-scoped shell sandbox policy."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from code_agent.utils.paths import get_workspace_root, is_relative_to

WRITE_OPERATORS = {">", ">>", "1>", "1>>", "2>", "2>>", "&>", "&>>"}
WRITE_COMMANDS = {
    "cp",
    "mv",
    "rm",
    "touch",
    "mkdir",
    "rmdir",
    "tee",
    "sed",
    "python",
    "python3",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "pip",
    "git",
}


@dataclass
class SandboxDecision:
    """Decision for shell sandbox enforcement."""

    allowed: bool
    reason: str = ""
    sandboxed: bool = False


class ShellSandbox:
    """A conservative app-level sandbox for bash commands.

    This is not an OS-level sandbox. It is a second policy layer that restricts
    cwd, obvious write targets, deny paths, and network commands before the
    subprocess is launched.
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self.root = get_workspace_root(config)

    def check(self, command: str, cwd: Path) -> SandboxDecision:
        if not getattr(self.config, "sandbox_enabled", True):
            return SandboxDecision(True, "sandbox disabled", sandboxed=False)
        if self._is_excluded(command):
            return SandboxDecision(True, "命令匹配沙箱排除规则", sandboxed=False)
        if not is_relative_to(cwd.resolve(), self.root):
            return SandboxDecision(False, f"工作目录超出沙箱根目录：{cwd}")
        if not getattr(self.config, "sandbox_allow_network", True) and self._uses_network(command):
            return SandboxDecision(False, "沙箱已阻止网络命令")

        for path in self._extract_candidate_paths(command):
            decision = self._check_path(path, cwd)
            if not decision.allowed:
                return decision
        return SandboxDecision(True, "已通过工作区沙箱策略", sandboxed=True)

    def _is_excluded(self, command: str) -> bool:
        return any(
            fnmatch(command, pattern) or command.startswith(pattern.rstrip("*"))
            for pattern in getattr(self.config, "sandbox_excluded_commands", [])
        )

    def _uses_network(self, command: str) -> bool:
        try:
            parts = shlex.split(command)
        except ValueError:
            return True
        if not parts:
            return False
        network_commands = {"curl", "wget", "ssh", "scp", "rsync", "nc", "telnet"}
        if Path(parts[0]).name.lower() in network_commands:
            return True
        return bool(re.search(r"https?://|git@|ssh://", command))

    def _extract_candidate_paths(self, command: str) -> list[str]:
        try:
            parts = shlex.split(command)
        except ValueError:
            return []
        candidates: list[str] = []
        write_context = False
        previous = ""
        for part in parts:
            if part in WRITE_OPERATORS:
                write_context = True
                previous = part
                continue
            if previous in WRITE_OPERATORS:
                candidates.append(part)
                write_context = False
            elif _looks_like_path(part):
                candidates.append(part)
            previous = part
        if write_context:
            candidates.append("")
        return candidates

    def _check_path(self, raw_path: str, cwd: Path) -> SandboxDecision:
        if not raw_path:
            return SandboxDecision(False, "沙箱已阻止空的重定向目标")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (cwd / path).resolve()
        else:
            path = path.resolve()

        if not is_relative_to(path, self.root):
            return SandboxDecision(False, f"沙箱已阻止工作区外路径：{raw_path}")

        rel = path.relative_to(self.root).as_posix()
        for pattern in getattr(self.config, "sandbox_deny_write_paths", []):
            if fnmatch(rel, pattern) or rel.startswith(pattern.rstrip("/") + "/"):
                return SandboxDecision(False, f"沙箱已阻止受保护路径：{raw_path}")

        writable = getattr(self.config, "sandbox_writable_paths", ["."])
        if not any(_matches_workspace_pattern(rel, pattern) for pattern in writable):
            return SandboxDecision(False, f"路径不在沙箱可写范围内：{raw_path}")
        return SandboxDecision(True, sandboxed=True)


def _looks_like_path(value: str) -> bool:
    if value.startswith("-"):
        return False
    return (
        "/" in value or value.startswith(".") or value.startswith("~") or bool(Path(value).suffix)
    )


def _matches_workspace_pattern(rel: str, pattern: str) -> bool:
    normalized = pattern.strip() or "."
    if normalized == ".":
        return True
    normalized = normalized.rstrip("/")
    return fnmatch(rel, normalized) or rel.startswith(normalized + "/")
