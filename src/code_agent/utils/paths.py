"""Workspace path helpers for tools.

Tool implementations should resolve paths through this module instead of calling
``Path(...).resolve()`` directly. That keeps file, shell, and project tools on the
same workspace boundary.
"""

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from code_agent.utils.security import SecurityChecker


class PathSecurityError(ValueError):
    """Raised when a requested path is outside the configured safety boundary."""


def get_workspace_root(config: Any) -> Path:
    """Return the configured workspace root as an absolute path."""
    root = getattr(config, "workspace_root", ".") or "."
    return Path(root).expanduser().resolve()


def resolve_workspace_path(path: str, config: Any) -> Path:
    """Resolve ``path`` within the configured workspace.

    Relative paths are resolved against ``config.workspace_root``. Absolute paths
    are allowed only when they still point inside the workspace, unless
    ``config.allow_absolute_paths`` is explicitly enabled.
    """
    root = get_workspace_root(config)
    raw_path = Path(path).expanduser()
    resolved = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()

    allow_absolute_paths = bool(getattr(config, "allow_absolute_paths", False))
    if not allow_absolute_paths and not is_relative_to(resolved, root):
        raise PathSecurityError(f"路径超出工作区根目录：{path}")

    is_safe, reason = SecurityChecker.is_path_safe(str(resolved))
    if not is_safe:
        raise PathSecurityError(reason)

    ensure_not_blocked(resolved, config)
    return resolved


def is_relative_to(path: Path, root: Path) -> bool:
    """Compatibility wrapper for Path.is_relative_to."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def ensure_not_blocked(path: Path, config: Any) -> None:
    """Reject paths matching configured blocked path patterns."""
    blocked_patterns = getattr(config, "blocked_paths", None)
    if blocked_patterns is None:
        blocked_patterns = getattr(config, "ignore_patterns", [])

    if not blocked_patterns:
        return

    root = get_workspace_root(config)
    rel_text = str(path)
    if is_relative_to(path, root):
        rel_text = str(path.relative_to(root))

    rel_parts = Path(rel_text).parts
    for pattern in blocked_patterns:
        if any(fnmatch(part, pattern) for part in rel_parts) or fnmatch(rel_text, pattern):
            raise PathSecurityError(f"路径命中禁止访问规则 '{pattern}'：{path}")


def is_blocked_path(path: Path, config: Any) -> bool:
    """Return True when a path matches configured blocked path patterns."""
    try:
        ensure_not_blocked(path, config)
        return False
    except PathSecurityError:
        return True


def ensure_allowed_extension(path: Path, config: Any) -> None:
    """Reject files whose extension is not in the configured allow-list."""
    allowed_extensions = getattr(config, "allowed_extensions", [])
    if not allowed_extensions:
        return

    allowed = {ext.lower() for ext in allowed_extensions}
    if path.suffix.lower() not in allowed:
        raise PathSecurityError(
            f"文件扩展名 '{path.suffix or '<无扩展名>'}' 不在允许列表中：{path}"
        )


def ensure_file_size(path: Path, max_size: int) -> None:
    """Reject files larger than ``max_size`` bytes."""
    size = path.stat().st_size
    if size > max_size:
        raise PathSecurityError(f"文件大小 {size} bytes 超过上限 {max_size} bytes")
