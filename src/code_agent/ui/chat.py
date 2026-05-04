"""Interactive chat interface for Code Agent.

使用 prompt_toolkit 提供命令行补全、历史记录等交互功能。
"""

from pathlib import Path
from typing import Any, Callable, Iterable, Optional, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style

from code_agent.ui.console import agent_console
from code_agent.ui.commands import COMMAND_SPECS, SLASH_COMMANDS as DEFAULT_SLASH_COMMANDS, command_hint
from code_agent.ui.completion import CodeAgentCompleter


class ChatInterface:
    """Interactive chat interface for Code Agent.

    提供：
    - 命令历史记录
    - Tab 补全
    - 多行输入支持
    - 特殊命令处理（/help, /exit 等）
    """

    # 特殊命令列表
    SLASH_COMMANDS = DEFAULT_SLASH_COMMANDS

    def __init__(
        self,
        history_file: Optional[str] = None,
        *,
        skill_names: Callable[[], Iterable[str]] | None = None,
        workflow_names: Callable[[], Iterable[str]] | None = None,
        custom_commands: Callable[[], Iterable[str]] | None = None,
        tool_names: Callable[[], Iterable[str]] | None = None,
        suggestions_enabled: bool = True,
    ) -> None:
        """Initialize the chat interface.

        Args:
            history_file: Path to history file for persistence
        """
        self.completer = CodeAgentCompleter(
            cwd=Path.cwd(),
            skill_names=skill_names,
            workflow_names=workflow_names,
            custom_commands=custom_commands,
            tool_names=tool_names,
        )

        # 创建 prompt session
        if history_file:
            history: Any = FileHistory(history_file)
        else:
            from prompt_toolkit.history import InMemoryHistory
            history = InMemoryHistory()

        self._suggestions_enabled = suggestions_enabled
        self.session: Any = PromptSession(
            history=history,
            auto_suggest=AutoSuggestFromHistory() if suggestions_enabled else None,
            completer=self.completer,
            complete_while_typing=suggestions_enabled,
            multiline=False,
            enable_history_search=True,
            bottom_toolbar=self._bottom_toolbar,
        )

        # 自定义样式
        self.style = Style.from_dict({
            "prompt": "ansiblue bold",
            "": "ansidefault",
        })

    def get_input(self) -> str:
        """Get user input from the prompt.

        Returns:
            User input string
        """
        try:
            return cast(str, self.session.prompt("› ", style=self.style))
        except KeyboardInterrupt:
            return ""
        except EOFError:
            return "/exit"

    def set_suggestions_enabled(self, enabled: bool) -> None:
        """Toggle typing-time suggestions without disabling manual Tab completion."""
        self._suggestions_enabled = enabled
        self.session.auto_suggest = AutoSuggestFromHistory() if enabled else None
        self.session.default_buffer.complete_while_typing = enabled

    def _bottom_toolbar(self) -> str:
        """显示当前命令的中文参数提示。"""
        session = getattr(self, "session", None)
        text = cast(str, session.default_buffer.text) if session else ""
        hint = command_hint(text)
        if not self._suggestions_enabled:
            return f"{hint} │ 穷鬼模式：已关闭自动建议"
        return hint

    def is_slash_command(self, text: str) -> bool:
        """Check if input is a slash command.

        Args:
            text: Input text

        Returns:
            True if it's a slash command
        """
        return text.startswith("/")

    def parse_command(self, text: str) -> tuple[str, list[str]]:
        """Parse a slash command.

        Args:
            text: Command text

        Returns:
            Tuple of (command_name, args)
        """
        parts = text.split()
        if not parts:
            return "", []
        command = parts[0].lower()
        args = parts[1:]
        return command, args

    def print_help(self) -> None:
        """Print help information."""
        command_lines = "\n".join(
            f"- `{spec.usage}` - {spec.summary}" for spec in COMMAND_SPECS
        )
        help_text = f"""
# Code Agent 帮助

## 常用按键
- `Tab`：补全命令、工具名、技能名、workflow 名称或文件路径
- `Ctrl-R`：搜索历史输入
- `↑/↓`：浏览历史输入

## 命令
{command_lines}

## 示例
- 阅读 README 并总结项目结构。
- 搜索 tools 包里的高风险 shell 命令。
- 运行测试并修复失败用例。
"""
        agent_console.console.print(help_text)

    def print_welcome(
        self,
        version: str,
        model: str,
        cwd: str,
        memory_path: Optional[str] = None,
        transcript_path: Optional[str] = None,
    ) -> None:
        """Print welcome message.

        Args:
            version: Application version
            model: Current model name
            cwd: Current working directory
        """
        agent_console.print_welcome_panel(
            version=version,
            model=model,
            cwd=cwd,
            memory_path=memory_path,
            transcript_path=transcript_path,
        )
