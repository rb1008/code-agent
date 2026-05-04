"""命令、项目对象和路径补全。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from code_agent.ui.commands import COMMAND_MAP, COMMAND_SPECS


class CodeAgentCompleter(Completer):
    """为普通 CLI 和窗口模式共用的补全器。"""

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        skill_names: Callable[[], Iterable[str]] | None = None,
        workflow_names: Callable[[], Iterable[str]] | None = None,
        custom_commands: Callable[[], Iterable[str]] | None = None,
        tool_names: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.cwd = cwd or Path.cwd()
        self.skill_names = skill_names or (lambda: [])
        self.workflow_names = workflow_names or (lambda: [])
        self.custom_commands = custom_commands or (lambda: [])
        self.tool_names = tool_names or (lambda: [])

    def get_completions(self, document: Document, complete_event: object) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/"):
            yield from self._slash_completions(text)
            return
        yield from self._path_completions(_current_word(text))

    def _slash_completions(self, text: str) -> Iterable[Completion]:
        parts = text.split()
        ends_with_space = text.endswith(" ")
        command_fragment = parts[0] if parts else text

        if len(parts) <= 1 and not ends_with_space:
            all_commands = [spec.name for spec in COMMAND_SPECS]
            all_commands.extend(f"/{name.lstrip('/')}" for name in self.custom_commands())
            seen: set[str] = set()
            for command in all_commands:
                if command in seen:
                    continue
                seen.add(command)
                if _matches(command, command_fragment):
                    spec = COMMAND_MAP.get(command)
                    yield Completion(
                        command,
                        start_position=-len(command_fragment),
                        display=command,
                        display_meta=spec.summary if spec else "项目自定义命令",
                    )
            return

        command = command_fragment.lower()
        arg_fragment = "" if ends_with_space else parts[-1]
        start_position = -len(arg_fragment)
        if command == "/skill":
            yield from _named_completions(self.skill_names(), arg_fragment, start_position, "项目技能")
        elif command == "/workflow":
            yield from _named_completions(
                self.workflow_names(), arg_fragment, start_position, "workflow 脚本"
            )
        elif command in {"/allow", "/deny", "/allow-project", "/deny-project", "/tool-search"}:
            yield from _named_completions(self.tool_names(), arg_fragment, start_position, "工具")
        elif COMMAND_MAP.get(command) and COMMAND_MAP[command].path_args:
            yield from self._path_completions(arg_fragment, start_position=start_position)
        else:
            yield from self._path_completions(arg_fragment, start_position=start_position)

    def _path_completions(
        self,
        word: str,
        *,
        start_position: int | None = None,
    ) -> Iterable[Completion]:
        if not _should_complete_path(word):
            return
        expanded = Path(word).expanduser()
        typed_parent = word if word.endswith("/") else ""
        if not typed_parent and "/" in word:
            typed_parent = word.rsplit("/", 1)[0] + "/"
        base_dir: Path
        prefix: str
        if word.endswith("/"):
            base_dir = expanded
            prefix = ""
        else:
            base_dir = expanded.parent if str(expanded.parent) != "." else self.cwd
            prefix = expanded.name

        if not base_dir.is_absolute():
            base_dir = (self.cwd / base_dir).resolve()
        if not base_dir.exists() or not base_dir.is_dir():
            return

        start = start_position if start_position is not None else -len(word)
        for path in sorted(base_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:80]:
            if prefix and not path.name.startswith(prefix):
                continue
            suffix = "/" if path.is_dir() else ""
            yield Completion(
                typed_parent + path.name + suffix,
                start_position=start,
                display_meta="目录" if path.is_dir() else "文件",
            )


def _named_completions(
    names: Iterable[str],
    fragment: str,
    start_position: int,
    meta: str,
) -> Iterable[Completion]:
    for name in sorted({item for item in names if item}):
        if _matches(name, fragment):
            yield Completion(name, start_position=start_position, display_meta=meta)


def _matches(value: str, fragment: str) -> bool:
    return not fragment or value.startswith(fragment) or fragment.lower() in value.lower()


def _current_word(text: str) -> str:
    if not text:
        return ""
    return text.split()[-1] if not text.endswith(" ") else ""


def _should_complete_path(word: str) -> bool:
    return bool(word) and (
        word.startswith(".")
        or word.startswith("/")
        or word.startswith("~")
        or "/" in word
    )
