"""Project-local custom slash command loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional

from code_agent.utils.skills import _split_frontmatter


@dataclass
class CustomCommand:
    """A Markdown-backed slash command."""

    name: str
    prompt: str
    path: Path
    description: str = ""
    argument_hint: str = ""
    user_invocable: bool = True
    disable_model_invocation: bool = False

    def render(self, arguments: str = "") -> str:
        text = self.prompt.replace("$ARGUMENTS", arguments)
        text = text.replace("${ARGUMENTS}", arguments)
        text = text.replace("${CODE_AGENT_COMMAND_DIR}", str(self.path.parent))
        return (
            f"# Custom command: /{self.name}\n\n"
            f"{text.strip()}\n\n"
            f"User arguments:\n{arguments.strip() or '(none)'}"
        )


class CustomCommandLoader:
    """Load `.md` commands from a project-local directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def load(self) -> list[CustomCommand]:
        if not self.root.exists():
            return []
        commands: list[CustomCommand] = []
        for path in sorted(self.root.glob("*.md")):
            command = self._load_file(path)
            if command:
                commands.append(command)
        return commands

    def get(self, name: str) -> Optional[CustomCommand]:
        normalized = name.lstrip("/")
        for command in self.load():
            if command.name == normalized:
                return command
        return None

    def listing(self) -> list[dict[str, str]]:
        return [
            {
                "命令": f"/{command.name}",
                "说明": command.description or "-",
                "参数": command.argument_hint or "-",
                "路径": str(command.path),
            }
            for command in self.load()
            if command.user_invocable
        ]

    def matching(self, query: str, *, limit: int = 5) -> list[CustomCommand]:
        """Return custom commands whose metadata matches the current request."""
        tokens = _query_tokens(query)
        if not tokens:
            return []
        scored: list[tuple[int, CustomCommand]] = []
        for command in self.load():
            if command.disable_model_invocation:
                continue
            haystack = " ".join(
                [command.name, command.description, command.argument_hint]
            ).lower()
            score = 0
            for token in tokens:
                if token == command.name.lower():
                    score += 8
                elif token in command.name.lower():
                    score += 4
                elif token in haystack:
                    score += 2
            if score:
                scored.append((score, command))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [command for _score, command in scored[:limit]]

    def _load_file(self, path: Path) -> Optional[CustomCommand]:
        raw = path.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(raw)
        name = str(meta.get("name") or path.stem).lstrip("/")
        return CustomCommand(
            name=name,
            prompt=body.strip(),
            path=path,
            description=str(meta.get("description") or ""),
            argument_hint=str(meta.get("argument_hint") or meta.get("argument-hint") or ""),
            user_invocable=bool(meta.get("user_invocable", meta.get("user-invocable", True))),
            disable_model_invocation=bool(
                meta.get("disable_model_invocation", meta.get("disable-model-invocation", False))
            ),
        )


def _query_tokens(text: str) -> list[str]:
    """Extract small English/Chinese tokens for command relevance matching."""
    normalized = text.lower()
    tokens: list[str] = []
    tokens.extend(re.findall(r"[a-zA-Z_][\w.-]*", normalized))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        tokens.append(run)
        tokens.extend(run[index : index + 2] for index in range(0, len(run) - 1))

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped
