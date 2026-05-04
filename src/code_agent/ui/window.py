"""Full-screen terminal window interface for Code Agent."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition, to_filter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.layout import ConditionalContainer, Dimension, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import HorizontalAlign, VerticalAlign
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Box, Frame, Label, TextArea

from code_agent.agent.core import CodeAgent
from code_agent.tools.base import BaseTool, ToolResult
from code_agent.ui.console import agent_console
from code_agent.ui.commands import COMMAND_SPECS
from code_agent.ui.completion import CodeAgentCompleter
from code_agent.utils.buddy import BuddyBrain, BuddyRenderer, BuddyState, BuddyStore, update_buddy_state
from code_agent.utils.context_report import build_context_report
from code_agent.utils.custom_commands import CustomCommandLoader
from code_agent.utils.persistent_memory import PersistentMemory
from code_agent.utils.poor_mode import PoorModeState
from code_agent.utils.skills import SkillLoader
from code_agent.utils.ultraplan import contains_ultraplan_trigger, strip_ultraplan_trigger


def format_window_message(role: str, content: str) -> str:
    """Format a transcript message for the window buffer.

    窗口模式没有真正的富文本气泡，所以用不同边框和角色标签强制区分：
    用户消息是“你”，模型消息是“Agent”，工具/系统再单独成块。
    """
    spec = {
        "user": ("┏━ 你 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "┃ ", "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"),
        "assistant": ("╔═ Agent ─────────────────────────────────", "║ ", "╚──────────────────────────────────────────"),
        "system": ("┌─ 系统 ──────────────────────────────────", "│ ", "└──────────────────────────────────────────"),
        "status": ("┄─ 状态 ──────────────────────────────────", "┆ ", "┄──────────────────────────────────────────"),
        "tool": ("┌─ 工具输出 ──────────────────────────────", "│ ", "└──────────────────────────────────────────"),
    }.get(role, (f"┌─ {role.upper()} ─────────────────────────", "│ ", "└──────────────────────────────────────────"))
    header, prefix, footer = spec
    body = indent_block(content.strip(), prefix=prefix) if content.strip() else prefix.rstrip()
    return f"{header}\n{body}\n{footer}".rstrip()


def indent_block(text: str, prefix: str = "  ") -> str:
    """Indent a multiline block for transcript display."""
    return "\n".join(f"{prefix}{line}" if line else "" for line in text.splitlines())


class WindowTranscriptLexer(Lexer):
    """Apply lightweight colors to transcript lines based on their block markers."""

    def lex_document(self, document: Any) -> Any:
        def get_line(lineno: int) -> list[tuple[str, str]]:
            line = document.lines[lineno]
            return [(_window_line_style(line), line)]

        return get_line


WINDOW_STYLE = Style.from_dict(
    {
        "window-user": "ansiblue bold",
        "window-assistant": "ansigreen",
        "window-system": "ansiyellow",
        "window-status": "ansicyan",
        "window-process": "ansimagenta",
        "window-tool": "ansimagenta",
        "window-muted": "ansibrightblack",
        "window-error": "ansired bold",
    }
)


def _window_line_style(line: str) -> str:
    """Return a prompt-toolkit style class for one transcript line."""
    if line.startswith(("┏━ 你", "┃ ", "┗━")):
        return "class:window-user"
    if line.startswith(("╔═ Agent", "║ ", "╚─")):
        return "class:window-assistant"
    if line.startswith(("╭─ 执行过程", "├ ", "┊ ", "╰─")):
        return "class:window-process"
    if line.startswith(("┄─ 状态", "┆ ", "┄─")):
        return "class:window-status"
    if line.startswith(("┌─ 系统", "│ ", "└─")):
        return "class:window-system"
    if line.startswith(("┌─ 工具输出", "└─")):
        return "class:window-tool"
    if "错误" in line or "失败" in line or "拒绝" in line:
        return "class:window-error"
    return "class:window-muted"


class WindowedChatInterface:
    """Prompt-toolkit based full-screen chat UI."""

    def __init__(
        self,
        agent: CodeAgent,
        persistent_memory: Optional[PersistentMemory] = None,
        poor_mode: Optional[PoorModeState] = None,
    ) -> None:
        self.agent = agent
        self.persistent_memory = persistent_memory
        self.poor_mode = poor_mode
        self._busy = False
        self._original_console_methods: dict[str, Callable[..., Any]] = {}
        self._transcript_chunks: list[str] = []
        self._max_transcript_chars = 80000
        self._input_future: asyncio.Future[str] | None = None
        self._input_prompt: str | None = None
        self._input_lock = asyncio.Lock()  # 保护权限请求流程
        self._tool_counter = 0
        self._phase = "就绪"
        self._phase_detail = "可以输入请求"
        self._turn_failed = False
        self._preserve_buddy_mood = False
        self._process_events: list[dict[str, str]] = []
        self._process_chunk_index: int | None = None
        self._process_done = False
        self._process_max_visible = 8
        self._stream_chunk_index: int | None = None
        self._stream_buffer = ""
        self._scroll_offset_lines = 0
        self._last_user_text = ""
        self._buddy_voice_generation = 0
        self._buddy_voice_task: asyncio.Task[None] | None = None
        self._buddy_proactive_task: asyncio.Task[None] | None = None
        self._buddy_proactive_counter = 0
        self._last_interaction_at = 0.0
        self._main_min_columns = 64
        self._buddy_columns = 36
        fill_height = Dimension(min=1, preferred=10000, weight=1)
        fill_width = Dimension(min=1, preferred=10000, weight=1)
        main_width = Dimension(min=self._main_min_columns, preferred=10000, weight=1)
        buddy_width = Dimension(
            min=self._buddy_columns,
            preferred=self._buddy_columns,
            max=self._buddy_columns,
        )
        buddy_content_width = Dimension(min=28, preferred=32, max=32)
        self.buddy_store = BuddyStore(
            self._buddy_state_path(),
            seed_text=f"{Path.cwd().resolve()}:{os.getenv('USER', '')}",
        )
        self.buddy_state = self.buddy_store.load()
        self.buddy_renderer = BuddyRenderer()
        self.buddy_brain = self._create_buddy_brain()

        self.completer = CodeAgentCompleter(
            skill_names=self._skill_names,
            workflow_names=self._workflow_names,
            custom_commands=self._custom_command_names,
            tool_names=lambda: self.agent.tool_registry.list_tools(),
        )

        self.output = TextArea(
            text="",
            read_only=True,
            scrollbar=False,
            wrap_lines=False,
            focusable=True,
            lexer=WindowTranscriptLexer(),
            width=fill_width,
            height=fill_height,
        )
        self.input = TextArea(
            height=1,
            prompt=HTML("<ansiblue><b>你 › </b></ansiblue>"),
            multiline=False,
            wrap_lines=True,
            accept_handler=self._accept_input,
            completer=self.completer,
            complete_while_typing=False,
            dont_extend_height=True,
        )
        self.status = Label(text=self._status_text)
        self.help = Label(
            text=HTML(
                "<ansibrightblack>Enter 发送 │ Tab 补全 │ Ctrl-R 历史搜索 │ PageUp/PageDown 滚动 │ Ctrl-Q 退出</ansibrightblack>"
            )
        )
        self.buddy_area = TextArea(
            text="",
            read_only=True,
            focusable=False,
            scrollbar=False,
            wrap_lines=True,
            width=buddy_content_width,
            height=fill_height,
        )
        self._refresh_buddy()

        main_panel = Box(
            HSplit(
                [
                    VSplit([self.status], width=fill_width, height=1),
                    Frame(self.output, title="对话记录", width=fill_width, height=fill_height),
                ],
                width=main_width,
                height=fill_height,
                align=VerticalAlign.JUSTIFY,
            ),
            width=main_width,
            height=fill_height,
        )
        buddy_panel = ConditionalContainer(
            Box(
                Frame(self.buddy_area, title="Buddy", height=fill_height),
                width=buddy_width,
                height=fill_height,
                padding_left=1,
            ),
            filter=Condition(self._should_show_buddy_panel),
        )
        top_panel = VSplit(
            [main_panel, buddy_panel],
            width=fill_width,
            height=fill_height,
            align=HorizontalAlign.JUSTIFY,
            padding=1,
            padding_char=" ",
        )
        input_panel = HSplit(
            [
                Window(height=1, char="─", style="class:window-muted"),
                self.input,
            ],
            height=2,
            width=fill_width,
        )
        root = HSplit(
            [
                top_panel,
                input_panel,
                Box(self.help, padding_left=1, height=1),
            ],
            width=fill_width,
            height=fill_height,
            align=VerticalAlign.JUSTIFY,
        )

        self.app: Application[Any] = Application(
            layout=Layout(root, focused_element=self.input),
            key_bindings=self._key_bindings(),
            full_screen=True,
            mouse_support=True,
            style=WINDOW_STYLE,
        )

    async def run(self) -> None:
        """Run the full-screen UI until the user exits."""
        self._install_console_bridge()
        self.agent.permission_manager.input_callback = self._prompt_for_input
        self._last_interaction_at = self._loop_time()
        self._buddy_proactive_task = asyncio.create_task(self._buddy_proactive_loop())
        self._append("system", "窗口模式已启动。输入 `/help` 查看命令，按 Tab 可补全命令和路径。")
        if self.buddy_state.enabled:
            self._set_buddy("idle", "窗口模式已启动")
        try:
            await self.app.run_async()
        finally:
            if self._buddy_proactive_task:
                self._buddy_proactive_task.cancel()
                try:
                    await self._buddy_proactive_task
                except asyncio.CancelledError:
                    pass
            self.agent.permission_manager.input_callback = None
            self._restore_console_bridge()

    def _key_bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("c-q")
        def _exit(event: Any) -> None:
            event.app.exit()

        @bindings.add("c-l")
        def _clear(event: Any) -> None:
            self._clear_transcript()
            event.app.invalidate()

        @bindings.add(Keys.PageUp, eager=True, is_global=True)
        def _page_up(event: Any) -> None:
            self._scroll_transcript(-self._scroll_step())
            event.app.invalidate()

        @bindings.add(Keys.PageDown, eager=True, is_global=True)
        def _page_down(event: Any) -> None:
            self._scroll_transcript(self._scroll_step())
            event.app.invalidate()

        @bindings.add(Keys.ScrollUp, eager=True, is_global=True)
        def _mouse_scroll_up(event: Any) -> None:
            self._scroll_transcript(-3)
            event.app.invalidate()

        @bindings.add(Keys.ScrollDown, eager=True, is_global=True)
        def _mouse_scroll_down(event: Any) -> None:
            self._scroll_transcript(3)
            event.app.invalidate()

        return bindings

    def _accept_input(self, buffer: Any) -> bool:
        text = buffer.text.strip()
        buffer.text = ""
        if text:
            self._touch_interaction()
        if self._input_future and not self._input_future.done():
            display = text or "默认允许"
            self._append("system", f"权限选择：{display}")
            try:
                self._input_future.set_result(text)
            except (asyncio.InvalidStateError, RuntimeError) as e:
                self._append("system", f"设置输入结果失败：{e}")
            self._input_prompt = None
            self.app.invalidate()
            return True
        if not text:
            return True
        if self._busy:
            phase_detail = getattr(self, "_phase_detail", "本轮请求仍在处理")
            self._append(
                "system",
                f"当前请求仍在执行：{phase_detail}。请等待工具完成或权限确认结束后，再发送下一条请求。",
            )
            return True
        self._busy = True
        self._last_user_text = text
        self._set_phase("排队", "已收到输入，准备开始处理")
        self._scroll_offset_lines = 0
        asyncio.create_task(self._run_input(text))
        return True

    async def _show_progress_indicator(self) -> None:
        """显示进度提示，让用户知道系统正在处理"""
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        idx = 0
        start_time = asyncio.get_event_loop().time()

        try:
            while True:
                await asyncio.sleep(0.5)
                elapsed = int(asyncio.get_event_loop().time() - start_time)
                minutes, seconds = divmod(elapsed, 60)
                time_str = f"{minutes}分{seconds}秒" if minutes > 0 else f"{seconds}秒"

                # update_buddy=False 避免影响宠物状态
                self._set_phase(
                    "处理中",
                    f"{spinner[idx]} 正在处理请求，已等待 {time_str}",
                    update_buddy=False
                )
                idx = (idx + 1) % len(spinner)
                self.app.invalidate()
        except asyncio.CancelledError:
            pass

    async def _run_input(self, text: str) -> None:
        """Run one accepted input to completion before accepting the next request."""
        self._turn_failed = False
        self._preserve_buddy_mood = False
        try:
            self._set_phase("处理中", "本轮开始")
            self._reset_process_panel()
            await self._handle_input(text)
        except Exception as e:
            self._turn_failed = True
            self._set_phase("工具错误", "本轮异常结束")
            self._append("system", f"错误：{e}")
        finally:
            self._finish_process_panel()
            self._set_phase("就绪", "本轮完成，可以继续输入", update_buddy=False)
            if self.buddy_state.enabled and not self._preserve_buddy_mood:
                if self._turn_failed:
                    self._set_buddy("error", "本轮结束，有问题需要继续处理", nonce="turn-failed")
                else:
                    self._set_buddy("success", "本轮完成，可以继续输入", nonce="turn-done")
            self._append("status", "本轮完成，可以继续输入。")
            self._touch_interaction()
            self._busy = False
            self.app.invalidate()

    async def _handle_input(self, text: str) -> None:
        quick_buddy = self._buddy_quick_action(text)
        if quick_buddy:
            self._last_user_text = text
            self._append("user", text)
            self._append("system", self._handle_buddy_command([quick_buddy]))
            return
        buddy_chat = self._buddy_chat_text(text)
        if buddy_chat is not None:
            self._last_user_text = text
            self._append("user", text)
            self._append("system", await self._handle_buddy_chat(buddy_chat))
            return

        if text.startswith("/"):
            self._last_user_text = text
            self._append("user", text)
            await self._handle_command(text)
            return

        self._last_user_text = text
        self._append("user", text)
        self.app.invalidate()
        try:
            self._set_phase("思考中", "Agent 正在分析请求")
            self._append("status", "Agent 已开始处理：正在分析请求。")

            # 启动进度提示任务
            progress_task = asyncio.create_task(self._show_progress_indicator())

            try:
                if contains_ultraplan_trigger(text):
                    self._set_phase("增强计划", "正在生成 Ultraplan")
                    response = await self.agent.run_ultraplan(strip_ultraplan_trigger(text))
                    self._append("system", "Ultraplan 已生成。确认无误后输入 /approve-plan；需要调整就直接指出修改点。")
                else:
                    response_parts: list[str] = []
                    async for delta in self.agent.run_stream(text):
                        if not delta:
                            continue
                        if not response_parts:
                            progress_task.cancel()
                            try:
                                await progress_task
                            except asyncio.CancelledError:
                                pass
                            self._set_phase("回复中", "Agent 正在流式输出")
                            self._start_assistant_stream()
                        response_parts.append(delta)
                        self._append_assistant_stream(delta)
                    response = "".join(response_parts)
            finally:
                # 停止进度提示任务
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

            if response.startswith("Agent execution failed:"):
                self._turn_failed = True
            self._set_phase("回复中", "Agent 正在展示最终回复")
            if self._stream_chunk_index is not None:
                self._finish_assistant_stream()
            else:
                self._append("assistant", response or "模型没有返回可展示的回复。")
            if self.persistent_memory and not self.agent.settings.agent.poor_mode:
                self.persistent_memory.save(self.agent.memory)
                auto_dream = self.persistent_memory.maybe_auto_dream(self.agent.memory)
                if auto_dream:
                    self._append("system", f"自动整理记忆完成：{auto_dream.path}")
        except Exception as e:
            self._turn_failed = True
            self._set_phase("工具错误", "Agent 处理异常")
            self._append("system", f"错误：{e}")

    async def _handle_command(self, text: str) -> None:
        parts = text.split()
        command = parts[0].lower()
        args = parts[1:]

        if command in ("/exit", "/quit"):
            self.app.exit()
        elif command == "/help":
            self._append(
                "system",
                "\n".join(
                    [
                        "Tab 补全命令/路径，Ctrl-R 搜索历史，PageUp/PageDown 滚动输出。",
                        *[f"{spec.usage} - {spec.summary}" for spec in COMMAND_SPECS],
                    ]
                ),
            )
        elif command == "/clear":
            self._clear_transcript()
        elif command == "/context":
            self._append("system", self._context_report())
        elif command == "/status":
            self._append("system", self._status_report())
        elif command == "/tools":
            tool_names = ", ".join(self.agent.tool_registry.list_tools())
            self._append("system", f"工具：{tool_names}")
        elif command == "/tool-search":
            query = " ".join(args)
            if query:
                self.agent.tool_registry.activate_matching(query, limit=8, pinned=True)
            rows = self.agent.tool_registry.search_metadata(query)
            if not rows:
                self._append("system", f"没有找到匹配工具：{query}")
            else:
                self._append(
                    "system",
                    "\n".join(
                        f"- `{item.name}` {'已激活' if item.active else '未激活'}：{item.description}"
                        for item in rows[:12]
                    ),
                )
        elif command == "/transcript":
            self._append("system", self._transcript_report())
        elif command == "/export-transcript":
            self._append("system", self._export_transcript(args[0] if args else None))
        elif command == "/plan":
            is_plan = self.agent.toggle_plan_mode()
            self._append("system", f"计划模式已{'开启' if is_plan else '关闭'}。")
        elif command == "/ultraplan":
            if not args:
                self._append("system", "用法：/ultraplan <任务>")
            else:
                await self._run_ultraplan(" ".join(args))
                return
        elif command == "/approve-plan":
            plan = self.agent.approve_last_plan(" ".join(args) if args else None)
            self._append("system", f"计划已批准，可执行。\n\n{plan.content}")
        elif command == "/execute-plan":
            if not self.agent.approved_plan:
                self._append("system", "没有待执行的已批准计划。")
            else:
                await self._handle_input("现在执行已批准的计划。")
                return
        elif command == "/clear-plan":
            self.agent.clear_approved_plan()
            self._append("system", "已清除批准计划。")
        elif command == "/permissions":
            permission_rows = self.agent.permission_manager.list_rules()
            if not permission_rows:
                self._append("system", "没有显式权限规则，当前使用默认策略。")
            else:
                self._append(
                    "system",
                    "\n".join(
                        f"{row.get('source')}: {row.get('behavior')} {row.get('tool')} {row.get('content', '')}"
                        for row in permission_rows
                    ),
                )
        elif command == "/compact":
            self.agent.memory._compact()
            self._append("system", "记忆已压缩。")
        elif command == "/dream":
            self._append("system", self._dream_report())
        elif command == "/buddy":
            if args and args[0].lower() in {"chat", "say", "聊聊", "说话"}:
                self._append("system", await self._handle_buddy_chat(" ".join(args[1:]) or "打个招呼"))
            else:
                self._append("system", self._handle_buddy_command(args))
        elif command == "/poor":
            self._append("system", self._toggle_poor_mode())
        elif command == "/model" and args:
            self.agent.settings.llm.model_name = args[0]
            self.agent.llm = self.agent._create_llm()
            self._append("system", f"已切换模型：{args[0]}")
        elif await self._handle_project_command(command, args):
            pass
        else:
            self._append("system", f"未知命令：{command}")

        self.app.invalidate()

    def _buddy_quick_action(self, text: str) -> str | None:
        """Map tiny Buddy-only inputs before they waste an Agent turn."""
        normalized = text.strip().lower()
        if not normalized or normalized.startswith("/"):
            return None
        if normalized in {"cheer", "加油", "打气"}:
            return "cheer"
        if normalized in {"pet", "摸摸", "摸一下"}:
            return "pet"
        if normalized in {"joke", "笑话", "讲笑话", "讲个笑话"}:
            return "joke"
        if normalized in {"roast", "吐槽", "毒舌"}:
            return "roast"
        if normalized in {"snack", "投喂", "喂食", "吃点"}:
            return "snack"
        return None

    def _buddy_chat_text(self, text: str) -> str | None:
        """Route explicit Buddy chats away from the main Agent loop."""
        stripped = text.strip()
        lowered = stripped.lower()
        prefixes = ("buddy ", "pet ", "宠物 ", "伙伴 ", "小伙伴 ")
        for prefix in prefixes:
            if lowered.startswith(prefix):
                return stripped[len(prefix) :].strip() or "打个招呼"
        return None

    async def _handle_buddy_chat(self, text: str) -> str:
        """Let the user talk to Buddy without spending a main Agent turn."""
        self._preserve_buddy_mood = True
        self.buddy_state = self.buddy_store.ensure()
        self.buddy_state.enabled = True
        detail = f"聊：{self._clip_inline(text, 36)}"
        self._set_buddy("chat", detail, nonce=f"chat-{self._buddy_proactive_counter}", user_text=text)
        return self.buddy_renderer.render_panel(self.buddy_state)

    async def _handle_project_command(self, command: str, args: list[str]) -> bool:
        argument_text = " ".join(args)
        if command == "/skills":
            self._append("system", self._skills_report())
            return True
        if command == "/discover-skills":
            if not argument_text:
                self._append("system", "用法：/discover-skills <任务或关键词>")
                return True
            self._append_tool_result(
                await self._execute_tool(
                    "discover_skills",
                    {"query": argument_text, "limit": 8},
                )
            )
            return True
        if command == "/skill":
            if not args:
                self._append("system", "用法：/skill <技能名> [参数]")
                return True
            loader = SkillLoader(self.agent.settings.agent.skills_dir)
            skill = loader.get(args[0])
            if not skill or not skill.user_invocable:
                self._append("system", f"未找到技能：{args[0]}\n\n{loader.listing()}")
                return True
            await self._handle_input(skill.render(" ".join(args[1:])))
            return True
        if command == "/commands":
            self._append("system", self._commands_report())
            return True
        custom = CustomCommandLoader(self.agent.settings.agent.commands_dir).get(command)
        if custom and custom.user_invocable:
            await self._handle_input(custom.render(argument_text))
            return True
        if command == "/workflows":
            self._append_tool_result(await self._execute_tool("workflow_list", {}))
            return True
        if command == "/workflow":
            if not args:
                self._append("system", "用法：/workflow <脚本名> [参数]")
                return True
            self._append_tool_result(
                await self._execute_tool(
                    "workflow_run",
                    {"name": args[0], "arguments": " ".join(args[1:])},
                )
            )
            return True
        if command == "/monitor":
            if not argument_text:
                self._append("system", "用法：/monitor <命令>")
                return True
            self._append_tool_result(await self._execute_tool("monitor_start", {"command": argument_text}))
            return True
        if command == "/monitors":
            self._append_tool_result(await self._execute_tool("monitor_list", {}))
            return True
        if command == "/monitor-read":
            if not args:
                self._append("system", "用法：/monitor-read <任务ID>")
                return True
            self._append_tool_result(await self._execute_tool("monitor_read", {"monitor_id": args[0]}))
            return True
        if command == "/monitor-stop":
            if not args:
                self._append("system", "用法：/monitor-stop <任务ID>")
                return True
            self._append_tool_result(await self._execute_tool("monitor_stop", {"monitor_id": args[0]}))
            return True
        if command == "/fork":
            if not argument_text:
                self._append("system", "用法：/fork <任务>")
                return True
            self._append_tool_result(
                await self._execute_tool(
                    "fork_agent",
                    {"task": argument_text, "title": "window-fork"},
                )
            )
            return True
        if command == "/coordinator":
            if not argument_text:
                self._append("system", "用法：/coordinator <标题: 任务; 标题: 任务>")
                return True
            self._append_tool_result(
                await self._execute_tool(
                    "coordinator_run",
                    {"tasks": argument_text.replace(";", "\n")},
                )
            )
            return True
        return False

    async def _run_ultraplan(self, task: str) -> None:
        """Generate an enhanced plan without leaving window mode."""
        self._last_user_text = task
        self._set_phase("增强计划", "正在生成 Ultraplan")
        response = await self.agent.run_ultraplan(task)
        self._append("assistant", response)
        self._append("system", "确认无误后输入 /approve-plan；需要调整就直接指出修改点。")

    async def _execute_tool(self, name: str, params: dict[str, Any]) -> ToolResult:
        tool = self.agent.tool_registry.get(name)
        if not tool:
            return ToolResult.fail(f"未找到工具：{name}")
        self._bind_tool(tool)
        clean_params = {key: value for key, value in params.items() if value is not None}
        summary = tool.get_tool_use_summary(clean_params)
        self._set_phase("工具准备", f"准备执行 {tool.name}")
        agent_console.print_tool_call(tool.name, clean_params, summary=summary)
        allowed = await self.agent.permission_manager.check_permission(
            tool_name=tool.name,
            tool_params=clean_params,
            permission=tool.permission,
            tool=tool,
        )
        if not allowed:
            self._set_phase("权限拒绝", f"{tool.name} 未执行")
            denied = f"权限已拒绝：{tool.name}"
            agent_console.print_tool_result(False, denied)
            return ToolResult.fail(denied)
        self._set_phase("工具执行", f"正在执行 {tool.name}")
        result = await tool.execute(**clean_params)
        self._set_phase("工具完成" if result.success else "工具失败", tool.name)
        agent_console.print_tool_result(result.success, result.output if result.success else result.error or result.output)
        return result

    def _bind_tool(self, tool: BaseTool) -> None:
        if hasattr(tool, "memory") and getattr(tool, "memory") is None:
            setattr(tool, "memory", self.agent.memory)
        if hasattr(tool, "permission_manager") and getattr(tool, "permission_manager") is None:
            setattr(tool, "permission_manager", self.agent.permission_manager)

    def _append_tool_result(self, result: ToolResult) -> None:
        if not result.success:
            self._turn_failed = True
        role = "tool" if result.success else "system"
        self._append(role, result.output if result.success else result.error or result.output)

    def _append(self, role: str, content: str) -> None:
        entry = format_window_message(role, content)
        self._push_transcript(entry)

    def _start_assistant_stream(self) -> None:
        """Create one mutable Agent message block for token streaming."""
        self._stream_buffer = ""
        self._stream_chunk_index = len(self._transcript_chunks)
        self._push_transcript(format_window_message("assistant", "▌"))

    def _append_assistant_stream(self, delta: str) -> None:
        """Append one model chunk to the current Agent block without adding new blocks."""
        if self._stream_chunk_index is None:
            self._start_assistant_stream()
        self._stream_buffer += delta
        self._replace_stream_chunk(final=False)

    def _finish_assistant_stream(self) -> None:
        """Remove the streaming cursor while keeping the final text in the same block."""
        self._replace_stream_chunk(final=True)
        self._stream_chunk_index = None
        self._stream_buffer = ""

    def _replace_stream_chunk(self, *, final: bool) -> None:
        """Replace the mutable stream block and keep transcript scrolling consistent."""
        if self._stream_chunk_index is None:
            return
        suffix = "" if final else "▌"
        rendered = format_window_message(
            "assistant",
            f"{self._stream_buffer}{suffix}" or " ",
        )
        if 0 <= self._stream_chunk_index < len(self._transcript_chunks):
            self._transcript_chunks[self._stream_chunk_index] = rendered
            self._render_transcript()
        else:
            self._stream_chunk_index = len(self._transcript_chunks)
            self._push_transcript(rendered)
        self.app.invalidate()

    def _set_phase(self, phase: str, detail: str = "", *, update_buddy: bool = True) -> None:
        """Update status-bar phase so the user can tell whether a turn is finished."""
        self._phase = phase
        self._phase_detail = detail or phase
        if update_buddy:
            self._update_buddy_for_phase(phase, self._phase_detail)
        self.app.invalidate()

    def _buddy_state_path(self) -> Path:
        """Resolve Buddy state to a project-local file without using global config."""
        raw_path = Path(self.agent.settings.agent.buddy_settings_path).expanduser()
        if raw_path.is_absolute():
            return raw_path
        return Path.cwd() / raw_path

    def _should_show_buddy_panel(self) -> bool:
        """Only render Buddy when the terminal has enough columns for two panes."""
        return bool(
            self.buddy_state.enabled
            and self._terminal_columns() >= self._main_min_columns + self._buddy_columns
        )

    def _terminal_columns(self) -> int:
        """Read the current terminal width with a safe fallback for tests."""
        try:
            return self.app.output.get_size().columns
        except Exception:
            return shutil.get_terminal_size((120, 24)).columns

    def _refresh_buddy(self) -> None:
        """Refresh the right-side Buddy panel after state changes."""
        self.buddy_area.text = self.buddy_renderer.render_panel(self.buddy_state)

    async def _buddy_proactive_loop(self) -> None:
        """Let Buddy occasionally refresh its side-panel line while the app is idle."""
        try:
            while True:
                await asyncio.sleep(self._buddy_proactive_interval())
                self._maybe_proactive_buddy_message()
        except asyncio.CancelledError:
            return

    def _maybe_proactive_buddy_message(self) -> bool:
        """Return True when Buddy proactively says something in the side panel."""
        if (
            not self._buddy_proactive_enabled()
            or not self.buddy_state.enabled
            or self.buddy_state.muted
            or self._busy
            or (self._input_future is not None and not self._input_future.done())
        ):
            return False

        now = self._loop_time()
        min_idle = self._buddy_proactive_min_idle()
        if now - self._last_interaction_at < min_idle:
            return False

        self._buddy_proactive_counter += 1
        mood = self._next_proactive_buddy_mood()
        self._set_buddy(
            mood,
            "主动陪伴",
            nonce=f"proactive-{self._buddy_proactive_counter}",
            user_text=self._last_user_text or "窗口空闲",
        )
        self.app.invalidate()
        return True

    def _next_proactive_buddy_mood(self) -> str:
        """Rotate proactive moods so idle Buddy does not feel repetitive."""
        moods = ["chat", "cheer", "joke", "chat"]
        if self.buddy_state.snark >= 60:
            moods.append("roast")
        if self.buddy_state.energy <= 30:
            moods.append("snack")
        return moods[self._buddy_proactive_counter % len(moods)]

    def _buddy_proactive_enabled(self) -> bool:
        return bool(getattr(getattr(self.agent.settings, "buddy", None), "proactive_enabled", True))

    def _buddy_proactive_interval(self) -> int:
        value = int(getattr(getattr(self.agent.settings, "buddy", None), "proactive_interval_seconds", 90))
        return max(15, value)

    def _buddy_proactive_min_idle(self) -> int:
        value = int(getattr(getattr(self.agent.settings, "buddy", None), "proactive_min_idle_seconds", 45))
        return max(5, value)

    def _touch_interaction(self) -> None:
        """Record user activity so proactive Buddy does not interrupt immediately."""
        self._last_interaction_at = self._loop_time()

    def _loop_time(self) -> float:
        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:
            return 0.0

    def _create_buddy_brain(self) -> BuddyBrain:
        """Create the optional Buddy-only model channel."""
        settings = self.agent.settings
        buddy_config = getattr(settings, "buddy", None)
        enabled = bool(getattr(buddy_config, "enabled", False))
        if not enabled:
            return BuddyBrain(enabled=False)
        return BuddyBrain(
            enabled=True,
            base_url=settings.get_buddy_base_url(),
            api_key=settings.get_buddy_api_key(),
            model_name=settings.get_buddy_model_name(),
            temperature=getattr(buddy_config, "temperature", 0.9),
            max_tokens=getattr(buddy_config, "max_tokens", 80),
            timeout=getattr(buddy_config, "timeout", 8),
            max_retries=getattr(buddy_config, "max_retries", 0),
        )

    def _set_buddy(
        self,
        mood: str,
        detail: str = "",
        *,
        nonce: str = "",
        user_text: str | None = None,
    ) -> None:
        """Persist and redraw Buddy when the visible execution phase changes."""
        if not self.buddy_state.enabled:
            return
        update_buddy_state(
            self.buddy_state,
            mood,
            detail=detail,
            nonce=nonce,
            user_text=self._last_user_text if user_text is None else user_text,
        )
        self.buddy_store.save(self.buddy_state)
        self._refresh_buddy()
        self._schedule_buddy_voice(
            detail=detail,
            user_text=self._last_user_text if user_text is None else user_text,
        )

    def _schedule_buddy_voice(self, *, detail: str, user_text: str) -> None:
        """Ask the optional Buddy model for a richer line without blocking the UI."""
        model_voice_moods = {
            "idle",
            "queued",
            "thinking",
            "ultraplan",
            "approval",
            "success",
            "error",
            "pet",
            "cheer",
            "joke",
            "roast",
            "snack",
            "chat",
        }
        if (
            not self.buddy_brain.enabled
            or self.buddy_state.muted
            or self.buddy_state.mood not in model_voice_moods
        ):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        self._buddy_voice_generation += 1
        generation = self._buddy_voice_generation
        if self._buddy_voice_task and not self._buddy_voice_task.done():
            self._buddy_voice_task.cancel()
        snapshot = BuddyState.from_dict(self.buddy_state.to_dict())
        self._buddy_voice_task = loop.create_task(
            self._refresh_buddy_voice(generation, snapshot, detail=detail, user_text=user_text)
        )

    async def _refresh_buddy_voice(
        self,
        generation: int,
        snapshot: BuddyState,
        *,
        detail: str,
        user_text: str,
    ) -> None:
        """Apply model-generated Buddy voice only if the panel is still on the same scene."""
        try:
            message = await self.buddy_brain.generate(snapshot, user_text=user_text, detail=detail)
        except asyncio.CancelledError:
            return
        except Exception:
            return
        if not message:
            return
        if generation != self._buddy_voice_generation:
            return
        if (
            not self.buddy_state.enabled
            or self.buddy_state.muted
            or self.buddy_state.mood != snapshot.mood
            or self.buddy_state.detail != snapshot.detail
        ):
            return
        self.buddy_state.message = message
        self.buddy_store.save(self.buddy_state)
        self._refresh_buddy()
        self.app.invalidate()

    def _update_buddy_for_phase(self, phase: str, detail: str) -> None:
        """Map Chinese UI phases to Buddy moods."""
        mood = {
            "排队": "queued",
            "处理中": "thinking",
            "思考中": "thinking",
            "增强计划": "ultraplan",
            "工具准备": "tool_prepare",
            "工具执行": "tool_running",
            "工具完成": "success",
            "工具错误": "error",
            "工具失败": "error",
            "权限拒绝": "error",
            "等待批准": "approval",
            "继续执行": "tool_running",
            "回复中": "success",
            "就绪": "idle",
        }.get(phase)
        if mood:
            self._set_buddy(mood, detail, nonce=phase)

    def _handle_buddy_command(self, args: list[str]) -> str:
        """Handle Buddy commands inside the full-screen UI."""
        action = args[0].lower() if args else "show"
        self._preserve_buddy_mood = True
        if action in ("show", "hatch", "on"):
            self.buddy_state = self.buddy_store.hatch()
            self._set_buddy("idle", "右侧面板已开启", nonce=action)
            return self.buddy_renderer.render_card(self.buddy_state)
        if action == "card":
            self.buddy_state = self.buddy_store.ensure()
            self._refresh_buddy()
            return self.buddy_renderer.render_card(self.buddy_state)
        interactions = {
            "pet": ("pet", "互动完成"),
            "摸摸": ("pet", "互动完成"),
            "cheer": ("cheer", "打气完成"),
            "加油": ("cheer", "打气完成"),
            "joke": ("joke", "讲笑话完成"),
            "笑话": ("joke", "讲笑话完成"),
            "roast": ("roast", "轻吐槽完成"),
            "吐槽": ("roast", "轻吐槽完成"),
            "snack": ("snack", "投喂完成"),
            "投喂": ("snack", "投喂完成"),
            "喂食": ("snack", "投喂完成"),
            "chat": ("chat", "主动聊天"),
            "聊聊": ("chat", "主动聊天"),
        }
        if action in interactions:
            mood, detail = interactions[action]
            self.buddy_state = self.buddy_store.ensure()
            self.buddy_state.enabled = True
            self._set_buddy(mood, detail, nonce=action)
            return self.buddy_renderer.render_panel(self.buddy_state)
        if action == "mute":
            self.buddy_state = self.buddy_store.ensure()
            self.buddy_state.enabled = True
            self.buddy_state.muted = True
            self.buddy_store.save(self.buddy_state)
            self._refresh_buddy()
            return "Buddy 已静音，动作仍会更新。"
        if action == "unmute":
            self.buddy_state = self.buddy_store.ensure()
            self.buddy_state.enabled = True
            self.buddy_state.muted = False
            self.buddy_store.save(self.buddy_state)
            self._refresh_buddy()
            return "Buddy 已恢复语言。"
        if action == "off":
            self.buddy_state = self.buddy_store.ensure()
            self.buddy_state.enabled = False
            self.buddy_store.save(self.buddy_state)
            self._refresh_buddy()
            return "Buddy 已关闭。再次输入 /buddy 可开启。"
        if action == "reset":
            self.buddy_state = self.buddy_store.hatch(reset=True)
            self._set_buddy("idle", "重新孵化完成", nonce=action)
            return self.buddy_renderer.render_card(self.buddy_state)
        return "用法：/buddy [hatch|card|pet|cheer|joke|roast|snack|chat|mute|unmute|off|reset]"

    def _append_raw(self, text: str) -> None:
        self._push_transcript(text.rstrip())
        self.app.invalidate()

    def _push_transcript(self, text: str) -> None:
        self._transcript_chunks.append(text.rstrip())
        self._render_transcript()

    def _render_transcript(self) -> None:
        rendered = "\n\n".join(chunk for chunk in self._transcript_chunks if chunk)
        while len(rendered) > self._max_transcript_chars and len(self._transcript_chunks) > 1:
            self._transcript_chunks.pop(0)
            if self._process_chunk_index is not None:
                self._process_chunk_index -= 1
                if self._process_chunk_index < 0:
                    self._process_chunk_index = None
            if self._stream_chunk_index is not None:
                self._stream_chunk_index -= 1
                if self._stream_chunk_index < 0:
                    self._stream_chunk_index = None
            rendered = "\n\n".join(chunk for chunk in self._transcript_chunks if chunk)
        visible = self._visible_transcript(rendered)
        self.output.text = f"{visible}\n" if visible else ""
        self.output.buffer.cursor_position = len(self.output.text)

    def _visible_transcript(self, rendered: str) -> str:
        """Return the currently visible transcript slice for manual scrolling."""
        if not rendered:
            return ""
        lines = self._visual_transcript_lines(rendered)
        height = self._transcript_view_height()
        if self._scroll_offset_lines <= 0 or len(lines) <= height:
            self._scroll_offset_lines = 0
            return "\n".join(lines)

        max_offset = max(0, len(lines) - height)
        self._scroll_offset_lines = min(self._scroll_offset_lines, max_offset)
        bottom = max(1, len(lines) - self._scroll_offset_lines)
        body_height = max(1, height - 1)
        start = max(0, bottom - body_height)
        below = len(lines) - bottom
        marker = f"┄─ 滚动视图 · 上方 {start} 行 · 下方 {below} 行，PageDown 返回最新 ─"
        return "\n".join([marker, *lines[start:bottom]])

    def _scroll_transcript(self, delta: int) -> None:
        """Scroll the read-only transcript independently from input focus."""
        rendered = "\n\n".join(chunk for chunk in self._transcript_chunks if chunk)
        total = len(self._visual_transcript_lines(rendered))
        max_offset = max(0, total - self._transcript_view_height())
        if max_offset <= 0:
            self._scroll_offset_lines = 0
        else:
            self._scroll_offset_lines = max(0, min(max_offset, self._scroll_offset_lines - delta))
        self._render_transcript()
        self.app.invalidate()

    def _scroll_step(self) -> int:
        """Scroll by most of the visible pane, leaving context between pages."""
        return max(4, self._transcript_view_height() - 4)

    def _transcript_view_height(self) -> int:
        """Estimate the output pane height for manual transcript paging."""
        try:
            rows = self.app.output.get_size().rows
        except Exception:
            rows = shutil.get_terminal_size((120, 24)).lines
        return max(6, rows - 7)

    def _transcript_view_width(self) -> int:
        """Estimate the usable text width inside the left transcript frame."""
        try:
            columns = self.app.output.get_size().columns
        except Exception:
            columns = shutil.get_terminal_size((120, 24)).columns
        if self._should_show_buddy_panel():
            columns -= self._buddy_columns + 2
        return max(32, columns - 6)

    def _visual_transcript_lines(self, rendered: str) -> list[str]:
        """Soft-wrap transcript text into terminal-width visual lines before paging."""
        width = self._transcript_view_width()
        lines: list[str] = []
        for line in rendered.splitlines() or [""]:
            lines.extend(self._wrap_visual_line(line, width))
        return lines

    def _wrap_visual_line(self, line: str, width: int) -> list[str]:
        """Wrap one plain-text line using terminal cell width, preserving block prefixes."""
        if not line or get_cwidth(line) <= width:
            return [line]

        prefix = self._line_wrap_prefix(line)
        if not prefix:
            return self._split_cells(line, width)

        content = line[len(prefix) :]
        content_width = max(8, width - get_cwidth(prefix))
        parts = self._split_cells(content, content_width)
        return [f"{prefix}{part}" for part in parts] or [prefix.rstrip()]

    def _line_wrap_prefix(self, line: str) -> str:
        """Return a repeated prefix for wrapped message body lines."""
        for prefix in ("┃ ", "║ ", "│ ", "┆ ", "┊ "):
            if line.startswith(prefix):
                return prefix
        return ""

    def _split_cells(self, text: str, width: int) -> list[str]:
        """Split text by display cells so Chinese wide chars do not break scrolling math."""
        if width <= 0:
            return [text]
        parts: list[str] = []
        current: list[str] = []
        current_width = 0
        for char in text:
            char_width = max(1, get_cwidth(char))
            if current and current_width + char_width > width:
                parts.append("".join(current).rstrip())
                current = []
                current_width = 0
            current.append(char)
            current_width += char_width
        if current:
            parts.append("".join(current).rstrip())
        return parts or [""]

    def _clear_transcript(self) -> None:
        """Clear visible transcript and reset transient process state."""
        self._transcript_chunks = []
        self.output.text = ""
        self._scroll_offset_lines = 0
        self._reset_process_panel()

    def _reset_process_panel(self) -> None:
        """Start a fresh compact process panel for the next user turn."""
        self._process_events = []
        self._process_chunk_index = None
        self._process_done = False

    def _record_process_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        summary: str | None,
    ) -> None:
        """Record a compact one-line tool step instead of appending a verbose block."""
        label = summary or tool_name
        detail_parts = []
        for key, value in list(params.items())[:2]:
            detail_parts.append(f"{key}={self._clip_inline(str(value), 72)}")
        detail = "，".join(detail_parts)
        self._process_events.append(
            {
                "status": "running",
                "tool": tool_name,
                "label": self._clip_inline(label, 96),
                "detail": detail,
                "result": "",
            }
        )
        self._upsert_process_panel()

    def _record_process_result(self, success: bool, output: str) -> None:
        """Attach a compact result to the most recent running tool step."""
        event = next(
            (item for item in reversed(self._process_events) if item.get("status") == "running"),
            None,
        )
        if event is None:
            event = {
                "status": "running",
                "tool": "tool",
                "label": "工具调用",
                "detail": "",
                "result": "",
            }
            self._process_events.append(event)

        event["status"] = "success" if success else "error"
        preview = self._clip_inline(output.strip().splitlines()[0] if output.strip() else "", 120)
        if preview and (not success or len(output.strip().splitlines()) <= 2):
            event["result"] = preview
        elif not success:
            event["result"] = "工具返回错误"
        else:
            event["result"] = ""
        self._upsert_process_panel()

    def _finish_process_panel(self) -> None:
        """Mark the current process panel as complete without adding another block."""
        if not self._process_events:
            return
        self._process_done = True
        self._upsert_process_panel()

    def _upsert_process_panel(self) -> None:
        """Insert or replace the compact process panel for the current turn."""
        if not self._process_events:
            return
        rendered = self._render_process_panel()
        if (
            self._process_chunk_index is not None
            and 0 <= self._process_chunk_index < len(self._transcript_chunks)
        ):
            self._transcript_chunks[self._process_chunk_index] = rendered
            self._render_transcript()
        else:
            self._process_chunk_index = len(self._transcript_chunks)
            self._push_transcript(rendered)
        self.app.invalidate()

    def _render_process_panel(self) -> str:
        """Render a bounded, readable process summary for long tool chains."""
        total = len(self._process_events)
        done_count = len([item for item in self._process_events if item["status"] != "running"])
        state = "已完成" if self._process_done else "进行中"
        lines = [f"╭─ 执行过程 · {done_count}/{total} 步 · {state}"]
        hidden = max(0, total - self._process_max_visible)
        if hidden:
            lines.append(f"├ 已折叠较早 {hidden} 步，可用 /transcript 查看完整记录")
        for index, event in enumerate(self._process_events[-self._process_max_visible :], total - min(total, self._process_max_visible) + 1):
            icon = {"running": "…", "success": "✓", "error": "×"}.get(event["status"], "•")
            line = f"┊ {icon} {index}. {event['tool']} · {event['label']}"
            if event.get("detail"):
                line += f" · {event['detail']}"
            if event.get("result"):
                line += f" → {event['result']}"
            lines.append(line)
        lines.append("╰─ 过程已压缩显示；最终结果会单独显示在 Agent 消息中")
        return "\n".join(lines)

    def _context_report(self) -> str:
        report = build_context_report(
            memory=self.agent.memory,
            model=self.agent.settings.llm.model_name,
            system_prompt=self.agent._build_system_prompt(),
            limit=self.agent.settings.agent.context_token_limit,
            threshold_ratio=self.agent.settings.agent.auto_compact_token_ratio,
        )
        return report.render_text()

    def _transcript_report(self) -> str:
        if not self.agent.transcript:
            return "会话记录未启用。"
        events = self.agent.transcript.tail(6)
        lines = [f"文件：{self.agent.transcript.path}", f"最近事件数：{len(events)}"]
        if events:
            lines.append("")
            lines.extend(f"{event.kind}: {self._clip_inline(event.content, 120)}" for event in events)
        return "\n".join(lines)

    def _export_transcript(self, output_path: str | None) -> str:
        if not self.agent.transcript:
            return "会话记录未启用。"
        from pathlib import Path

        path = Path(output_path).expanduser() if output_path else None
        exported = self.agent.transcript.export_markdown(path)
        return f"会话记录已导出：{exported}"

    def _status_report(self) -> str:
        stats = self.agent.get_stats()
        return "\n".join(f"{key}: {value}" for key, value in stats.items())

    def _skills_report(self) -> str:
        skills = SkillLoader(self.agent.settings.agent.skills_dir).load()
        if not skills:
            return "没有找到项目技能。"
        return "\n".join(
            f"/skill {skill.name} - {skill.description or skill.when_to_use or '项目技能'}"
            for skill in skills
            if skill.user_invocable
        )

    def _commands_report(self) -> str:
        commands = CustomCommandLoader(self.agent.settings.agent.commands_dir).listing()
        if not commands:
            return "没有找到项目自定义命令。"
        return "\n".join(
            f"{row['命令']} - {row['说明']} {row['参数']}".rstrip()
            for row in commands
        )

    def _dream_report(self) -> str:
        if self.agent.settings.agent.poor_mode:
            return "穷鬼模式已开启，已暂停 /dream。运行 /poor 可恢复。"
        if not self.persistent_memory:
            return "持久化记忆未启用，无法执行 /dream。"
        return self.persistent_memory.dream(self.agent.memory).render()

    def _toggle_poor_mode(self) -> str:
        if self.poor_mode is None:
            self.poor_mode = PoorModeState(
                Path(".code-agent/runtime.yaml"),
                active=self.agent.settings.agent.poor_mode,
            )
        self.poor_mode.toggle()
        self.agent.settings.agent.poor_mode = self.poor_mode.active
        self.input.buffer.complete_while_typing = to_filter(False)
        return self.poor_mode.render()

    def _skill_names(self) -> list[str]:
        return [
            skill.name
            for skill in SkillLoader(self.agent.settings.agent.skills_dir).load()
            if skill.user_invocable
        ]

    def _workflow_names(self) -> list[str]:
        workflows_dir = Path(self.agent.settings.agent.workflows_dir)
        if not workflows_dir.exists():
            return []
        return sorted(
            path.name
            for path in workflows_dir.iterdir()
            if path.is_file() and os.access(path, os.X_OK)
        )

    def _custom_command_names(self) -> list[str]:
        return [
            command.name
            for command in CustomCommandLoader(self.agent.settings.agent.commands_dir).load()
            if command.user_invocable
        ]

    def _status_text(self) -> Any:
        used = self.agent.memory.estimate_tokens(
            model=self.agent.settings.llm.model_name,
            system_prompt=self.agent._build_system_prompt(),
        )
        limit = self.agent.settings.agent.context_token_limit
        pct = int((used / limit) * 100) if limit else 0
        mode = "计划" if self.agent.is_plan_mode else "默认"
        busy = self._phase if self._busy else "就绪"
        if self._input_future and not self._input_future.done():
            busy = "等待批准"
        memory = "暂停" if self.agent.settings.agent.poor_mode else ("开" if self.persistent_memory else "关")
        poor = " │ 穷鬼" if self.agent.settings.agent.poor_mode else ""
        prompt = f" │ 等待输入：{self._input_prompt}" if self._input_prompt else ""
        return HTML(
            "<ansicyan>{model}</ansicyan> │ 上下文 {pct}% ({used}/{limit}) │ "
            "工具 {tools} │ 记忆 {memory} │ 模式 {mode}{poor} │ {busy}：{detail}{prompt}".format(
                model=self.agent.settings.llm.model_name,
                pct=pct,
                used=used,
                limit=limit,
                tools=len(self.agent.tool_registry),
                memory=memory,
                mode=mode,
                poor=poor,
                busy=busy,
                detail=self._phase_detail,
                prompt=prompt,
            )
        )

    async def _prompt_for_input(self, prompt: str) -> str:
        """在窗口模式内读取权限确认输入，避免 Rich input 卡住全屏 UI。"""
        async with self._input_lock:  # 确保权限请求串行处理
            self._set_phase("等待批准", "请在输入框回复 Y/n/a/e/p/d")
            self._input_prompt = prompt
            self._input_future = asyncio.get_running_loop().create_future()
            self._append(
                "system",
                f"{prompt}\n请输入选择后回车；批准后会立即继续执行该工具，完成前不会处理新的请求。",
            )
            self.app.invalidate()

            try:
                return await self._input_future
            except asyncio.CancelledError:
                self._append("system", "权限确认被取消，默认拒绝。")
                return "n"
            except Exception as e:
                self._append("system", f"权限确认出错：{e}，默认拒绝。")
                return "n"
            finally:
                self._input_prompt = None
                self._input_future = None
                if self._busy:
                    self._set_phase("继续执行", "已收到权限选择，继续处理本轮请求")

    def _install_console_bridge(self) -> None:
        self._original_console_methods = {
            "print_tool_call": agent_console.print_tool_call,
            "print_tool_result": agent_console.print_tool_result,
            "print_info": agent_console.print_info,
            "print_warning": agent_console.print_warning,
            "print_error": agent_console.print_error,
        }

        def print_tool_call(tool_name: str, params: dict[str, Any], summary: str | None = None) -> None:
            self._tool_counter += 1
            self._set_phase("工具准备", f"准备执行 {tool_name}")
            self._record_process_call(tool_name, params, summary)

        def print_tool_result(success: bool, output: str) -> None:
            label = "完成" if success else "错误"
            if not success:
                self._turn_failed = True
            self._set_phase("工具完成" if success else "工具错误", label)
            self._record_process_result(success, output)

        agent_console.print_tool_call = print_tool_call  # type: ignore[method-assign]
        agent_console.print_tool_result = print_tool_result  # type: ignore[method-assign]
        agent_console.print_info = lambda message: self._append("system", f"信息：{message}")  # type: ignore[method-assign]
        agent_console.print_warning = lambda message: self._append("system", f"警告：{message}")  # type: ignore[method-assign]
        agent_console.print_error = lambda message: self._append("system", f"错误：{message}")  # type: ignore[method-assign]

    def _restore_console_bridge(self) -> None:
        for name, method in self._original_console_methods.items():
            setattr(agent_console, name, method)

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
