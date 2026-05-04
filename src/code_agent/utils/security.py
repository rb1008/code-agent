"""Security utilities for Code Agent.

提供安全检查功能，防止危险操作。
参考 Claude Code 的权限系统设计。
"""

import shlex
from pathlib import Path
from typing import Optional


class SecurityChecker:
    """Security checker for tool executions.
    
    检查内容：
    1. 路径安全检查（防止目录遍历）
    2. 命令安全检查（防止危险命令）
    3. 文件大小限制
    """
    
    # 危险命令模式
    DANGEROUS_PATTERNS = [
        "rm -rf /",
        "rm -rf /*",
        "> /dev/sda",
        "dd if=/dev/zero",
        "mkfs",
        "format",
        "sudo",
        "su ",
        "chmod -R 777 /",
        "chown -R",
        "shutdown",
        "reboot",
    ]
    
    # 敏感路径
    SENSITIVE_PATHS = [
        "/etc/passwd",
        "/etc/shadow",
        "/etc/hosts",
        "/.ssh",
        "~/.ssh",
    ]
    
    @classmethod
    def is_path_safe(cls, path: str, base_dir: Optional[str] = None) -> tuple[bool, str]:
        """Check if a path is safe to access.
        
        Args:
            path: Path to check
            base_dir: Base directory that paths should be within
            
        Returns:
            Tuple of (is_safe, reason)
        """
        try:
            resolved = Path(path).resolve()
            
            # 检查是否为绝对路径且超出基础目录
            if base_dir:
                base = Path(base_dir).resolve()
                try:
                    resolved.relative_to(base)
                except ValueError:
                    return False, f"路径超出允许目录：{path} 不在 {base_dir} 内"
            
            # 检查敏感路径
            for sensitive in cls.SENSITIVE_PATHS:
                sensitive_path = Path(sensitive).expanduser().resolve()
                if resolved == sensitive_path or str(resolved).startswith(str(sensitive_path)):
                    return False, f"禁止访问敏感路径：{path}"
            
            return True, ""
            
        except Exception as e:
            return False, f"路径校验失败：{str(e)}"
    
    @classmethod
    def is_command_safe(cls, command: str) -> tuple[bool, str]:
        """Check if a shell command is safe to execute.
        
        Args:
            command: Command to check
            
        Returns:
            Tuple of (is_safe, reason)
        """
        command_lower = command.lower().strip()
        
        # 检查危险模式
        for pattern in cls.DANGEROUS_PATTERNS:
            if pattern.lower() in command_lower:
                return False, f"命令包含危险模式：{pattern}"

        try:
            parts = shlex.split(command)
        except ValueError as e:
            return False, f"命令解析失败：{str(e)}"

        if not parts:
            return True, ""

        executable = Path(parts[0]).name.lower()
        if executable in {"sudo", "su", "shutdown", "reboot", "poweroff", "halt", "mkfs"}:
            return False, f"命令包含危险可执行程序：{executable}"

        if executable == "dd" and any(part.startswith("of=/dev/") for part in parts[1:]):
            return False, "禁止使用 dd 直接写入设备文件"

        if executable == "rm":
            flags = "".join(part for part in parts[1:] if part.startswith("-"))
            targets = [part for part in parts[1:] if not part.startswith("-")]
            recursive = "r" in flags or "R" in flags
            force = "f" in flags
            dangerous_targets = {"/", "/*", "~", "~/", ".", "./", "..", "../"}
            if recursive and force and any(target in dangerous_targets for target in targets):
                return False, "禁止对宽泛路径执行递归强制删除"
        
        return True, ""
    
    @classmethod
    def check_file_size(cls, path: str, max_size: int = 1024 * 1024) -> tuple[bool, str]:
        """Check if a file size is within limits.
        
        Args:
            path: File path
            max_size: Maximum allowed size in bytes
            
        Returns:
            Tuple of (is_safe, reason)
        """
        try:
            size = Path(path).stat().st_size
            if size > max_size:
                return False, f"文件大小 {size} bytes 超过上限 {max_size} bytes"
            return True, ""
        except Exception as e:
            return False, f"无法检查文件大小：{str(e)}"


SHELL_CONTROL_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "<<", "2>", "2>>", "&>", "&>>"}


def split_safe_command(
    command: str,
    *,
    allowed_commands: list[str] | None = None,
    blocked_commands: list[str] | None = None,
) -> tuple[bool, str, list[str]]:
    """Parse one safe command into argv.

    The agent should run one executable at a time. We intentionally reject shell
    control syntax instead of trying to partially emulate shell parsing; that
    prevents allow-list bypasses such as ``echo ok; touch pwned`` while still
    allowing quoted code arguments like ``python -c 'import os; print(1)'``.
    """
    is_safe, reason = SecurityChecker.is_command_safe(command)
    if not is_safe:
        return False, reason, []

    blocked_commands = blocked_commands or []
    command_lower = command.lower().strip()
    for blocked in blocked_commands:
        if blocked.lower() in command_lower:
            return False, f"命令包含被禁止的模式：{blocked}", []

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return False, f"命令解析失败：{e}", []

    if not parts:
        return False, "命令为空", []

    if _has_shell_control_syntax(command, parts):
        return (
            False,
            "不支持 shell 复合语法。请一次执行一个命令，不要使用 ;、&&、||、|、重定向或命令替换。",
            [],
        )

    executable_index = _executable_index(parts)
    if executable_index is None:
        return False, "未找到可执行命令", []

    if allowed_commands:
        main_cmd = Path(parts[executable_index]).name.lower()
        allowed = {Path(cmd).name.lower() for cmd in allowed_commands}
        if main_cmd not in allowed:
            return False, f"命令不在允许列表中：{main_cmd}", []

    return True, "", parts


def _executable_index(parts: list[str]) -> int | None:
    """Return the argv index of the executable, skipping leading env assignments."""
    index = 0
    while index < len(parts) and "=" in parts[index] and not parts[index].startswith("="):
        index += 1
    return index if index < len(parts) else None


def _has_shell_control_syntax(command: str, parts: list[str]) -> bool:
    """Detect shell-only syntax outside the supported single-command argv model."""
    if "\n" in command or "\r" in command:
        return True
    if any(part in SHELL_CONTROL_TOKENS for part in parts):
        return True
    if any(_has_unquoted_command_substitution(part) for part in parts):
        return True
    # shlex keeps semicolons glued to adjacent words. Treat those as shell
    # control unless the whole token came from a quoted argument.
    quoted_tokens = set(_quoted_tokens(command))
    return any(";" in part and part not in quoted_tokens for part in parts)


def _has_unquoted_command_substitution(part: str) -> bool:
    return "$(" in part or "`" in part


def _quoted_tokens(command: str) -> list[str]:
    """Best-effort extraction of quoted tokens for semicolon allow-listing."""
    tokens: list[str] = []
    quote: str | None = None
    current: list[str] = []
    for char in command:
        if quote:
            if char == quote:
                tokens.append("".join(current))
                current = []
                quote = None
            else:
                current.append(char)
        elif char in {"'", '"'}:
            quote = char
    return tokens
