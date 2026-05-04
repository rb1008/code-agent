"""Console UI components using Rich.

提供美观的终端输出，包括：
- 代码高亮
- Markdown 渲染
- 进度指示
- 状态面板
"""

from typing import Any, Optional, cast

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.table import Table
from rich.status import Status
from rich.theme import Theme

# 自定义主题
custom_theme = Theme({
    "brand": "bold cyan",
    "muted": "dim",
    "info": "cyan",
    "warning": "yellow",
    "error": "red bold",
    "success": "green bold",
    "tool": "magenta",
    "user": "blue bold",
    "assistant": "green",
    "branch": "dim",
    "user_box": "bright_blue",
    "assistant_box": "bright_green",
    "tool_box": "bright_magenta",
})

# 全局控制台实例
console = Console(theme=custom_theme)


class AgentConsole:
    """Rich console wrapper for Code Agent UI.
    
    封装 Rich 库的功能，提供统一的输出接口。
    """
    
    def __init__(self) -> None:
        """Initialize the console."""
        self.console = Console(theme=custom_theme)
        self._tool_counter = 0
        
    def print_header(self, title: str, subtitle: str = "") -> None:
        """Print a styled header.
        
        Args:
            title: Main title text
            subtitle: Optional subtitle
        """
        text = f"[bold cyan]{title}[/bold cyan]"
        if subtitle:
            text += f"\n[dim]{subtitle}[/dim]"
        self.console.print(Panel(text, border_style="cyan", box=box.ROUNDED))

    def print_welcome_panel(
        self,
        version: str,
        model: str,
        cwd: str,
        memory_path: Optional[str] = None,
        transcript_path: Optional[str] = None,
    ) -> None:
        """Print the interactive CLI welcome panel."""
        body = Table.grid(padding=(0, 2))
        body.add_column(style="brand", no_wrap=True)
        body.add_column()
        body.add_row("Code Agent", f"v{version}")
        body.add_row("模型", model)
        body.add_row("工作区", cwd)
        if memory_path:
            body.add_row("记忆", memory_path)
        if transcript_path:
            body.add_row("会话记录", transcript_path)
        body.add_row("试试", "/help  /context  /doctor  /tool-search")

        self.console.print(
            Panel(
                body,
                border_style="bright_black",
                box=box.SQUARE,
                padding=(1, 2),
            )
        )

    def print_status_line(
        self,
        *,
        model: str,
        used_tokens: int,
        token_limit: int,
        tools: int,
        plan_mode: bool,
        memory: str,
    ) -> None:
        """Print a compact Claude-style status line."""
        pct = int((used_tokens / token_limit) * 100) if token_limit else 0
        mode = "计划" if plan_mode else "默认"
        self.console.print(
            "[dim]"
            f"{model} │ 上下文 {pct}% ({used_tokens}/{token_limit}) │ "
            f"工具 {tools} │ 记忆 {memory} │ 模式 {mode}"
            "[/dim]"
        )

    def print_message(self, role: str, content: str) -> None:
        """Print a chat message with styling.
        
        Args:
            role: Message role (user/assistant/tool/system)
            content: Message content
        """
        if role == "user":
            self._print_message_panel(
                title="用户",
                content=content,
                border_style="user_box",
                title_style="blue bold",
            )
            return
        elif role == "assistant":
            self._print_message_panel(
                title="助手",
                content=content,
                border_style="assistant_box",
                title_style="green bold",
            )
            return
        elif role == "tool":
            self._print_message_panel(
                title="工具",
                content=content,
                border_style="tool_box",
                title_style="magenta bold",
            )
            return
        else:
            self._print_message_panel(
                title=role.upper(),
                content=content,
                border_style="bright_black",
                title_style="bold",
            )
        
    def print_code(self, code: str, language: str = "python", title: Optional[str] = None) -> None:
        """Print syntax-highlighted code.
        
        Args:
            code: Code content
            language: Programming language for highlighting
            title: Optional panel title
        """
        syntax = Syntax(code, language, theme="monokai", line_numbers=True)
        if title:
            self.console.print(Panel(syntax, title=title, border_style="blue"))
        else:
            self.console.print(syntax)
            
    def print_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        summary: Optional[str] = None,
    ) -> None:
        """Print a tool call notification.
        
        Args:
            tool_name: Name of the tool being called
            params: Tool parameters
        """
        label = summary or tool_name
        self._tool_counter += 1
        self.console.print()
        self.console.print(
            f"[magenta bold]操作 {self._tool_counter}[/magenta bold] "
            f"[bright_black]┄[/bright_black] [bold]{label}[/bold]"
        )
        self.console.print(f"[branch]  │ 工具: {tool_name}[/branch]")
        for key, value in list(params.items())[:4]:
            rendered = self._clip_inline(str(value), 160)
            self.console.print(f"[branch]  │ {key}: {rendered}[/branch]")
        
    def print_tool_result(self, success: bool, output: str) -> None:
        """Print a tool execution result.
        
        Args:
            success: Whether the execution succeeded
            output: Tool output or error message
        """
        style = "green" if success else "red"
        label = "完成" if success else "错误"
        preview = self._clip_block(output.strip(), 1200)
        self.console.print(f"[{style}]  └─ {label}[/{style}]")
        if preview:
            for line in preview.splitlines()[:18]:
                self.console.print(f"[branch]     {line}[/branch]")
        
    def print_error(self, message: str) -> None:
        """Print an error message.
        
        Args:
            message: Error message
        """
        self.console.print(
            Panel(message, title="[bold red]错误[/bold red]", border_style="red", box=box.ROUNDED)
        )
        
    def print_warning(self, message: str) -> None:
        """Print a warning message.
        
        Args:
            message: Warning message
        """
        self.console.print(f"[yellow]警告[/yellow] {message}")
        
    def print_info(self, message: str) -> None:
        """Print an info message.
        
        Args:
            message: Info message
        """
        self.console.print(f"[cyan]信息[/cyan] {message}")
        
    def print_success(self, message: str) -> None:
        """Print a success message.
        
        Args:
            message: Success message
        """
        self.console.print(f"[bold green]完成[/bold green] {message}")
        
    def status(self, message: str) -> Status:
        """Create a status spinner.
        
        Args:
            message: Status message
            
        Returns:
            Rich Status context manager
        """
        return cast(Status, self.console.status(f"[cyan]✻ {message}[/cyan]", spinner="dots"))
        
    def print_table(self, data: list[dict[str, Any]], title: Optional[str] = None) -> None:
        """Print data as a table.
        
        Args:
            data: List of dictionaries
            title: Optional table title
        """
        if not data:
            self.console.print("[dim](empty)[/dim]")
            return
            
        table = Table(title=title, box=box.SIMPLE_HEAVY, show_lines=False)
        
        # 从第一个字典获取列名
        for key in data[0].keys():
            table.add_column(key, style="cyan")
            
        # 添加数据行
        for row in data:
            table.add_row(*[str(v) for v in row.values()])
            
        self.console.print(table)

    def print_key_values(self, rows: list[tuple[str, Any]], title: str) -> None:
        """Print key/value rows in a compact panel."""
        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan", no_wrap=True)
        table.add_column()
        for key, value in rows:
            table.add_row(key, str(value))
        self.console.print(Panel(table, title=title, border_style="cyan", box=box.ROUNDED))
        
    def print_divider(self) -> None:
        """Print a horizontal divider."""
        self.console.print("─" * self.console.width, style="dim")
        
    def clear(self) -> None:
        """Clear the console."""
        self.console.clear()
        
    def input(self, prompt: str = "") -> str:
        """Get user input.
        
        Args:
            prompt: Input prompt
            
        Returns:
            User input string
        """
        return cast(str, self.console.input(f"[blue bold]{prompt}[/blue bold]"))

    def _print_message_panel(
        self,
        *,
        title: str,
        content: str,
        border_style: str,
        title_style: str,
    ) -> None:
        self.console.print()
        self.console.print(
            Panel(
                Markdown(content or " "),
                title=f"[{title_style}]{title}[/{title_style}]",
                title_align="left",
                border_style=border_style,
                box=box.SQUARE,
                padding=(0, 1),
            )
        )

    def _clip_inline(self, text: str, max_chars: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 1].rstrip() + "…"

    def _clip_block(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        keep = max_chars - 40
        return f"{text[:keep].rstrip()}\n… 已截断 {len(text) - keep} 字符"


# 全局 AgentConsole 实例
agent_console = AgentConsole()
