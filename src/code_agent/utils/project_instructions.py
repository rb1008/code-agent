"""Load bounded project-local instruction files for the agent prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectInstructions:
    """Project instruction payload and source files."""

    content: str
    sources: list[Path]
    truncated: bool = False


def load_project_instructions(
    project_root: Path,
    file_names: list[str],
    max_chars: int,
) -> ProjectInstructions:
    """Load project-local instruction files with a hard prompt budget."""
    root = project_root.expanduser().resolve()
    parts: list[str] = []
    sources: list[Path] = []
    remaining = max_chars
    truncated = False

    for file_name in file_names:
        candidate = (root / file_name).expanduser().resolve()
        if not _is_within(candidate, root) or not candidate.is_file():
            continue

        label = candidate.relative_to(root).as_posix()
        text = candidate.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue

        section_prefix = f"### {label}\n\n"
        available = remaining - len(section_prefix)
        if available <= 0:
            truncated = True
            break

        if len(text) > available:
            text = text[:available].rstrip()
            truncated = True

        section = f"{section_prefix}{text}"
        parts.append(section)
        sources.append(candidate)
        remaining -= len(section) + 2

        if truncated or remaining <= 0:
            break

    content = "\n\n".join(parts)
    if truncated and content:
        content = f"{content}\n\n[Project instructions truncated to {max_chars} chars]"

    return ProjectInstructions(content=content, sources=sources, truncated=truncated)


def _is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is inside ``root``."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
