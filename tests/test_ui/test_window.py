"""Tests for window-mode interaction flow."""

import asyncio
from types import SimpleNamespace

import pytest

from code_agent.config.models import Settings
from prompt_toolkit.keys import Keys

from code_agent.ui.window import WindowedChatInterface, _window_line_style
from code_agent.utils.buddy import BuddyRenderer, BuddyStore


class _Buffer:
    def __init__(self, text: str) -> None:
        self.text = text


class _ToolRegistryStub:
    def list_tools(self) -> list[str]:
        return []

    def __len__(self) -> int:
        return 0


def _window_shell() -> WindowedChatInterface:
    window = WindowedChatInterface.__new__(WindowedChatInterface)
    window._busy = False
    window._input_future = None
    window._input_prompt = None
    window._input_lock = asyncio.Lock()
    window._transcript_chunks = []
    window._max_transcript_chars = 80000
    window._process_events = []
    window._process_chunk_index = None
    window._process_done = False
    window._process_max_visible = 8
    window._stream_chunk_index = None
    window._stream_buffer = ""
    window._scroll_offset_lines = 0
    window._turn_failed = False
    window._preserve_buddy_mood = False
    window._last_user_text = ""
    window._buddy_voice_generation = 0
    window._buddy_voice_task = None
    window._buddy_proactive_task = None
    window._buddy_proactive_counter = 0
    window._last_interaction_at = 0.0
    window._main_min_columns = 64
    window._buddy_columns = 36
    window.buddy_brain = SimpleNamespace(enabled=False)
    window.buddy_state = SimpleNamespace(enabled=False)
    window._messages = []
    window.output = SimpleNamespace(text="", buffer=SimpleNamespace(cursor_position=0))
    window.app = SimpleNamespace(invalidate=lambda: None, exit=lambda: None)

    def append(role: str, content: str) -> None:
        window._messages.append((role, content))

    window._append = append
    return window


def _window_instance(tmp_path) -> WindowedChatInterface:
    settings = Settings()
    settings.agent.buddy_settings_path = str(tmp_path / "buddy.yaml")
    agent = SimpleNamespace(
        settings=settings,
        tool_registry=_ToolRegistryStub(),
        permission_manager=SimpleNamespace(input_callback=None),
        memory=SimpleNamespace(estimate_tokens=lambda **kwargs: 0),
        _build_system_prompt=lambda: "",
        is_plan_mode=False,
    )
    return WindowedChatInterface(agent)  # type: ignore[arg-type]


def test_busy_window_rejects_new_requests_until_current_flow_finishes() -> None:
    """Window mode should not start another command while an agent turn is active."""
    window = _window_shell()
    window._busy = True
    buffer = _Buffer("/workflow test")

    accepted = window._accept_input(buffer)

    assert accepted is True
    assert buffer.text == ""
    assert "等待工具完成或权限确认结束" in window._messages[-1][1]


@pytest.mark.asyncio
async def test_permission_prompt_waits_for_user_input_without_timeout() -> None:
    """Permission prompts should wait until the user answers in the input box."""
    window = _window_shell()

    task = asyncio.create_task(window._prompt_for_input("是否允许执行？"))
    await asyncio.sleep(0)
    assert window._input_future is not None

    window._accept_input(_Buffer("y"))

    assert await task == "y"
    assert window._input_future is None


@pytest.mark.asyncio
async def test_permission_prompt_accepts_blank_default_choice() -> None:
    """Blank Enter during permission confirmation should reach the manager as default yes."""
    window = _window_shell()

    task = asyncio.create_task(window._prompt_for_input("是否允许执行？"))
    await asyncio.sleep(0)

    window._accept_input(_Buffer(""))

    assert await task == ""
    assert "默认允许" in window._messages[-1][1]


@pytest.mark.asyncio
async def test_window_clear_removes_internal_transcript_chunks() -> None:
    """Clearing the window should not resurrect old transcript on the next append."""
    window = _window_shell()
    window._transcript_chunks = ["old content"]
    window.output.text = "old content"

    await window._handle_command("/clear")
    window._push_transcript("new content")

    assert window._transcript_chunks == ["new content"]
    assert "old content" not in window.output.text


@pytest.mark.asyncio
async def test_window_sets_busy_for_slash_command_execution() -> None:
    """Slash commands should also occupy the single interaction lane."""
    window = _window_shell()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_command(text: str) -> None:
        started.set()
        await release.wait()

    window._handle_input = slow_command

    window._accept_input(_Buffer("/workflow test"))
    await started.wait()

    assert window._busy is True

    window._accept_input(_Buffer("next request"))
    assert "等待工具完成或权限确认结束" in window._messages[-1][1]

    release.set()
    await asyncio.sleep(0)
    assert window._busy is False


@pytest.mark.asyncio
async def test_window_marks_turn_complete_after_handler_finishes() -> None:
    """A finished turn should leave an explicit completion marker in the transcript."""
    window = _window_shell()

    async def fake_handle_input(text: str) -> None:
        window._append("assistant", "done")

    window._handle_input = fake_handle_input

    await window._run_input("hello")

    assert window._busy is False
    assert window._phase == "就绪"
    assert window._messages[-1] == ("status", "本轮完成，可以继续输入。")


@pytest.mark.asyncio
async def test_window_streams_agent_reply_into_one_mutable_block() -> None:
    """Streaming chunks should appear before the turn completes without many Agent blocks."""
    window = _window_shell()
    settings = Settings()

    class FakeAgent:
        async def run_stream(self, text: str):  # type: ignore[no-untyped-def]
            yield "你"
            await asyncio.sleep(0)
            yield "好"

    window.agent = FakeAgent()
    window.agent.settings = settings
    window.persistent_memory = None

    await window._handle_input("hello")

    agent_chunks = [chunk for chunk in window._transcript_chunks if "╔═ Agent" in chunk]
    assert len(agent_chunks) == 1
    assert "你好" in agent_chunks[0]
    assert "▌" not in agent_chunks[0]
    assert "你好" in window.output.text


@pytest.mark.asyncio
async def test_window_keeps_buddy_error_mood_after_failed_turn(tmp_path) -> None:
    """A failed turn should leave Buddy in an error mood instead of immediately going idle."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")
    window.buddy_state = window.buddy_store.hatch()

    async def failing_handle_input(text: str) -> None:
        raise RuntimeError("boom")

    window._handle_input = failing_handle_input

    await window._run_input("触发失败")

    assert window._phase == "就绪"
    assert window.buddy_state.mood == "error"
    assert "稳住陪跑" in window.buddy_area.text


@pytest.mark.asyncio
async def test_window_buddy_command_preserves_pet_mood_after_turn(tmp_path) -> None:
    """Buddy commands should not be overwritten by the generic turn-complete mood."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")

    async def buddy_command(text: str) -> None:
        window._handle_buddy_command(["cheer"])

    window._handle_input = buddy_command

    await window._run_input("/buddy cheer")

    assert window.buddy_state.mood == "cheer"
    assert "士气在线" in window.buddy_area.text


def test_window_shell_type_hints() -> None:
    """Keep the test helper honest for dynamic attributes."""
    assert isinstance(_window_shell()._messages, list)


def test_window_input_is_bottom_band_and_ime_safe(tmp_path) -> None:
    """Window input should avoid live completion and stay outside the Buddy side pane."""
    window = _window_instance(tmp_path)

    assert type(window.app.layout.container).__name__ == "HSplit"
    assert window.input.buffer.complete_while_typing() is False
    assert len(window.app.layout.container.children) == 3
    assert type(window.app.layout.container.children[0]).__name__ == "VSplit"
    assert len(window.app.layout.container.children[0].children) == 2


def test_window_buddy_inner_width_does_not_overconstrain_frame(tmp_path) -> None:
    """Buddy side pane should leave room for padding and borders."""
    window = _window_instance(tmp_path)

    assert window._buddy_columns == 36
    assert window.output.window.right_margins == []
    assert window.output.wrap_lines is False
    assert window.buddy_area.window.width.preferred == 32
    assert window.buddy_area.window.height.preferred == 10000


def test_window_page_keys_are_global_and_eager(tmp_path) -> None:
    """PageUp/PageDown must win even while the input box owns focus."""
    window = _window_instance(tmp_path)
    bindings = {
        binding.keys[0]: binding
        for binding in window.app.key_bindings.bindings
        if binding.keys
    }

    assert bindings[Keys.PageUp].eager() is True
    assert bindings[Keys.PageUp].is_global() is True
    assert bindings[Keys.PageDown].eager() is True
    assert bindings[Keys.PageDown].is_global() is True


def test_window_process_panel_replaces_itself_and_collapses_old_steps() -> None:
    """Many tool calls should stay in one compact process panel."""
    window = _window_shell()

    for index in range(10):
        window._record_process_call(
            f"tool_{index}",
            {"path": f"very/long/path/{index}.py", "query": "hello"},
            f"tool_{index}: compact summary",
        )
        window._record_process_result(True, "ok")

    process_chunks = [chunk for chunk in window._transcript_chunks if "执行过程" in chunk]

    assert len(process_chunks) == 1
    assert "已折叠较早 2 步" in process_chunks[0]
    assert "┊ ✓ 10. tool_9" in process_chunks[0]
    assert "1. tool_0" not in process_chunks[0]


def test_window_manual_transcript_scroll_changes_visible_output() -> None:
    """PageUp/PageDown should scroll the transcript even while input owns focus."""
    window = _window_shell()
    window._transcript_view_height = lambda: 8  # type: ignore[method-assign]

    window._push_transcript("\n".join(f"line {index}" for index in range(30)))

    assert "line 29" in window.output.text

    window._scroll_transcript(-6)

    assert "滚动视图" in window.output.text
    assert "line 29" not in window.output.text

    window._scroll_transcript(6)

    assert "滚动视图" not in window.output.text
    assert "line 29" in window.output.text


def test_window_scroll_counts_wrapped_visual_lines() -> None:
    """A single long Agent line should still be scrollable after visual wrapping."""
    window = _window_shell()
    window._transcript_view_height = lambda: 6  # type: ignore[method-assign]
    window._transcript_view_width = lambda: 24  # type: ignore[method-assign]

    window._push_transcript("║ " + "中文内容" * 40)

    assert len(window._visual_transcript_lines(window._transcript_chunks[0])) > 6
    assert "滚动视图" not in window.output.text

    window._scroll_transcript(-4)

    assert "滚动视图" in window.output.text
    assert window._scroll_offset_lines > 0


def test_window_line_styles_distinguish_roles() -> None:
    """Window transcript color classes should separate user, agent, system, and process lines."""
    assert _window_line_style("┏━ 你 ━") == "class:window-user"
    assert _window_line_style("╔═ Agent ─") == "class:window-assistant"
    assert _window_line_style("┌─ 系统 ─") == "class:window-system"
    assert _window_line_style("╭─ 执行过程 · 1/1 步") == "class:window-process"


def test_window_buddy_command_toggles_right_side_panel(tmp_path) -> None:
    """Buddy should persist in the side panel until the user turns it off."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")

    card = window._handle_buddy_command([])

    assert "Buddy:" in card
    assert window.buddy_state.enabled is True
    assert window.buddy_area.text

    message = window._handle_buddy_command(["off"])

    assert "已关闭" in message
    assert window.buddy_state.enabled is False
    assert window.buddy_area.text == ""


def test_window_buddy_cheer_command_boosts_emotion(tmp_path) -> None:
    """The cheer command should give the user an explicit feel-good interaction."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")

    window._handle_buddy_command([])
    energy_before = window.buddy_state.energy
    bond_before = window.buddy_state.bond

    panel = window._handle_buddy_command(["cheer"])

    assert "士气在线" in panel
    assert window.buddy_state.energy > energy_before
    assert window.buddy_state.bond > bond_before


@pytest.mark.asyncio
async def test_window_buddy_quick_action_does_not_call_agent(tmp_path) -> None:
    """Tiny pet inputs like cheer should go to Buddy, not the Agent loop."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")
    window.buddy_state = window.buddy_store.hatch()

    await window._handle_input("cheer")

    assert window.buddy_state.mood == "cheer"
    assert ("user", "cheer") in window._messages
    assert window._messages[-1][0] == "system"


@pytest.mark.asyncio
async def test_window_routes_explicit_buddy_chat_without_agent_turn(tmp_path) -> None:
    """Typing `buddy ...` should talk to Buddy instead of invoking the main Agent."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")
    window.buddy_state = window.buddy_store.hatch()

    await window._handle_input("buddy 你怎么看这个卡顿")

    assert window.buddy_state.mood == "chat"
    assert "主动陪聊" in window.buddy_area.text
    assert ("user", "buddy 你怎么看这个卡顿") in window._messages
    assert window._messages[-1][0] == "system"


def test_window_buddy_proactively_speaks_when_idle(tmp_path) -> None:
    """Idle Buddy should refresh the side panel without appending transcript noise."""
    window = _window_shell()
    settings = Settings()
    settings.buddy.proactive_enabled = True
    settings.buddy.proactive_min_idle_seconds = 5
    window.agent = SimpleNamespace(settings=settings)
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")
    window.buddy_state = window.buddy_store.hatch()
    window._last_interaction_at = 0
    window._loop_time = lambda: 20.0  # type: ignore[method-assign]

    spoke = window._maybe_proactive_buddy_message()

    assert spoke is True
    assert window.buddy_state.mood in {"chat", "cheer", "joke", "roast", "snack"}
    assert window.buddy_area.text
    assert window._messages == []


def test_window_phase_updates_buddy_when_enabled(tmp_path) -> None:
    """Execution phases should translate into visible Buddy moods."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")
    window.buddy_state = window.buddy_store.hatch()

    window._set_phase("等待批准", "请在输入框回复 Y/n")

    assert window.buddy_state.mood == "approval"
    assert "等你点头" in window.buddy_area.text
    assert "请在输入框回复" in window.buddy_area.text


def test_window_buddy_voice_uses_latest_user_request(tmp_path) -> None:
    """Buddy should react to the user's topic instead of showing only static status copy."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")
    window.buddy_state = window.buddy_store.hatch()
    window._last_user_text = "请修复窗口 UI 展示卡顿"

    window._set_phase("思考中", "Agent 正在分析请求")

    assert window.buddy_state.mood == "thinking"
    assert "这次界面体验" in window.buddy_state.message


@pytest.mark.asyncio
async def test_window_buddy_model_voice_updates_panel_without_blocking(tmp_path) -> None:
    """Optional Buddy model output should update only the side panel."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_renderer = BuddyRenderer()
    window.buddy_area = SimpleNamespace(text="")
    window.buddy_state = window.buddy_store.hatch()
    window._last_user_text = "优化宠物展示"

    class FakeBuddyBrain:
        enabled = True

        async def generate(self, state, *, user_text: str, detail: str):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0)
            return "模型宠物上线，右下角开始营业。"

    window.buddy_brain = FakeBuddyBrain()

    window._set_phase("思考中", "Agent 正在分析请求")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert window.buddy_state.message == "模型宠物上线，右下角开始营业。"
    assert "模型宠物上线" in window.buddy_area.text


def test_window_hides_buddy_panel_when_terminal_is_too_narrow(tmp_path) -> None:
    """Buddy should not compete with the input frame on narrow terminals."""
    window = _window_shell()
    window.buddy_store = BuddyStore(tmp_path / "buddy.yaml", seed_text="window-test")
    window.buddy_state = window.buddy_store.hatch()
    window._terminal_columns = lambda: 90  # type: ignore[method-assign]

    assert window._should_show_buddy_panel() is False

    window._terminal_columns = lambda: 120  # type: ignore[method-assign]

    assert window._should_show_buddy_panel() is True
