"""Project-local poor mode state.

Poor mode is inspired by Claude Code Best's `/poor`: it disables optional
background-like conveniences so interactive sessions spend fewer tokens and
do less work while typing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


@dataclass
class PoorModeState:
    """Persistent state for the lightweight cost-saving mode."""

    path: Path
    active: bool = False

    @classmethod
    def load(cls, path: Path, *, default: bool = False) -> "PoorModeState":
        """Load poor mode from a small project-local YAML file."""
        path = path.expanduser()
        if not path.exists():
            return cls(path=path, active=default)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return cls(path=path, active=default)
        return cls(path=path, active=bool(data.get("poor_mode", default)))

    def set(self, active: bool) -> bool:
        """Set and persist the mode."""
        self.active = active
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "poor_mode": self.active,
            "说明": "开启后会暂停自动持久化记忆，并关闭输入时自动建议以降低开销。",
        }
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return self.active

    def toggle(self) -> bool:
        """Toggle and persist the mode."""
        return self.set(not self.active)

    def render(self) -> str:
        """Return a user-facing Chinese status line."""
        if self.active:
            return (
                "穷鬼模式已开启：暂停自动保存持久化记忆，关闭输入时自动建议，"
                "保留手动 Tab 补全。"
            )
        return "穷鬼模式已关闭：恢复自动持久化记忆和输入建议。"
