"""Bash command classifier used before interactive permission prompts.

这个分类器是一个本地、可测试的轻量版本：先用确定性规则给出
allow / ask / deny，再把理由展示给用户。它不会替代 BashTool 自身的
安全执行校验，只负责让权限提示更聪明、更可解释。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex

from code_agent.utils.security import split_safe_command


@dataclass(frozen=True)
class BashClassification:
    """Classifier decision for one bash command."""

    behavior: str
    reason: str
    risk: str
    command: str

    @property
    def should_allow(self) -> bool:
        return self.behavior == "allow"

    @property
    def should_deny(self) -> bool:
        return self.behavior == "deny"

    def render(self) -> str:
        """Render a concise Chinese explanation for permission UI."""
        label = {
            "allow": "自动允许",
            "ask": "需要确认",
            "deny": "自动拒绝",
        }.get(self.behavior, self.behavior)
        return f"Bash 分类器：{label}（风险：{self.risk}）\n原因：{self.reason}"


SAFE_READ_COMMANDS = {
    "pwd",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "git",
}

SAFE_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "rev-parse",
    "ls-files",
}

MUTATING_COMMANDS = {
    "cp",
    "mv",
    "touch",
    "mkdir",
    "rm",
    "rmdir",
    "chmod",
    "chown",
    "git",
    "npm",
    "pnpm",
    "yarn",
    "pip",
    "python",
    "python3",
    "pytest",
    "make",
    "go",
    "cargo",
}

DENY_EXECUTABLES = {
    "sudo",
    "su",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "mkfs",
    "dd",
}

HIGH_RISK_ARGS = {
    "--force",
    "-f",
    "--hard",
    "--delete",
    "--prune",
}


def classify_bash_command(
    command: str,
    *,
    allowed_commands: list[str] | None = None,
    blocked_commands: list[str] | None = None,
) -> BashClassification:
    """Classify a bash command before asking the user for permission.

    The classifier is deliberately conservative:
    - clearly safe inspection commands can run without a prompt;
    - dangerous commands are denied before the prompt;
    - anything mutating, network/install-like, or ambiguous still asks.
    """
    command = command.strip()
    if not command:
        return BashClassification("deny", "命令为空。", "high", command)

    command_ok, reason, argv = split_safe_command(
        command,
        allowed_commands=allowed_commands,
        blocked_commands=blocked_commands,
    )
    if not command_ok:
        return BashClassification("deny", reason, "high", command)

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return BashClassification("deny", f"命令解析失败：{e}", "high", command)

    executable = _main_executable(parts)
    if not executable:
        return BashClassification("deny", "未找到可执行命令。", "high", command)

    if executable in DENY_EXECUTABLES:
        return BashClassification("deny", f"命令 `{executable}` 风险过高。", "high", command)

    if executable == "git":
        return _classify_git(command, argv)

    if executable in SAFE_READ_COMMANDS:
        if _contains_high_risk_arg(argv):
            return BashClassification("ask", "命令包含高风险参数，需要你确认。", "medium", command)
        return BashClassification("allow", "这是只读查询命令。", "low", command)

    if executable in MUTATING_COMMANDS:
        return BashClassification(
            "ask",
            f"`{executable}` 可能修改文件、安装依赖或运行项目代码，需要你确认。",
            "medium",
            command,
        )

    return BashClassification("ask", f"未知命令 `{executable}`，需要你确认。", "medium", command)


def _main_executable(parts: list[str]) -> str:
    """Return executable name, skipping leading VAR=value assignments."""
    index = 0
    while index < len(parts) and "=" in parts[index] and not parts[index].startswith("="):
        index += 1
    if index >= len(parts):
        return ""
    return Path(parts[index]).name.lower()


def _classify_git(command: str, argv: list[str]) -> BashClassification:
    """Classify git commands by subcommand."""
    subcommand = ""
    for part in argv[1:]:
        if part.startswith("-"):
            continue
        subcommand = part.lower()
        break

    if not subcommand:
        return BashClassification("ask", "git 命令缺少明确子命令。", "medium", command)

    if subcommand in SAFE_GIT_SUBCOMMANDS and not _contains_high_risk_arg(argv):
        return BashClassification("allow", f"`git {subcommand}` 是只读或低风险查询。", "low", command)

    if subcommand in {"reset", "clean", "checkout", "restore", "push", "commit", "add", "merge", "rebase"}:
        return BashClassification(
            "ask",
            f"`git {subcommand}` 会改变工作区、历史或远端状态，需要你确认。",
            "medium",
            command,
        )

    return BashClassification("ask", f"`git {subcommand}` 风险不明确，需要你确认。", "medium", command)


def _contains_high_risk_arg(argv: list[str]) -> bool:
    lowered = {part.lower() for part in argv}
    return any(arg in lowered for arg in HIGH_RISK_ARGS)
