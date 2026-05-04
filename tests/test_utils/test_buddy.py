"""Tests for project-local Buddy companion state."""

from types import SimpleNamespace

import pytest

from code_agent.utils.buddy import BuddyBrain, BuddyRenderer, BuddyStore, update_buddy_state


def test_buddy_hatch_is_deterministic_for_project_seed(tmp_path) -> None:
    """The same project seed should hatch the same visible companion identity."""
    first = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    second = BuddyStore(tmp_path / "other.yaml", seed_text="project-a").hatch()

    assert first.name == second.name
    assert first.species == second.species
    assert first.rarity == second.rarity
    assert first.enabled is True


def test_buddy_store_persists_enabled_and_muted_state(tmp_path) -> None:
    """Buddy state should survive process restarts in the project YAML file."""
    store = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a")
    state = store.hatch()
    state.muted = True
    state.enabled = False
    store.save(state)

    loaded = store.load()

    assert loaded.muted is True
    assert loaded.enabled is False
    assert loaded.name == state.name


def test_buddy_renderer_wraps_long_detail_without_overwide_lines(tmp_path) -> None:
    """Long status text should wrap inside the right-side panel."""
    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    update_buddy_state(
        state,
        "tool_running",
        detail="正在执行一个非常非常长的工具调用摘要，需要在窄面板中自动换行，不能挤压对话消息。",
    )

    panel = BuddyRenderer().render_panel(state)

    assert "盯输出" in panel
    assert all(len(line) <= BuddyRenderer.width + 8 for line in panel.splitlines())


def test_buddy_mute_hides_language_but_keeps_action(tmp_path) -> None:
    """Muted Buddy should still render movement and status details."""
    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    update_buddy_state(state, "approval", detail="等待批准")
    state.muted = True

    panel = BuddyRenderer().render_panel(state)

    assert "等你点头" in panel
    assert "等待批准" in panel
    assert "“" not in panel


def test_buddy_voice_reacts_to_user_topic(tmp_path) -> None:
    """Buddy language should provide emotional value tied to the latest request."""
    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()

    update_buddy_state(
        state,
        "thinking",
        detail="Agent 正在分析请求",
        user_text="请优化窗口 UI 展示和交互体验",
    )

    assert "这次界面体验" in state.message
    assert state.message not in {"我在整理上下文。", "先看清楚，再动手。", "脑内索引正在转。"}


def test_buddy_sprite_changes_action_by_scene(tmp_path) -> None:
    """Different phases should visibly change the Buddy action and sprite."""
    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    update_buddy_state(state, "approval", detail="等待批准")
    approval_panel = BuddyRenderer().render_panel(state)

    update_buddy_state(state, "success", detail="完成")
    success_panel = BuddyRenderer().render_panel(state)

    assert "举着确认牌" in approval_panel
    assert "原地庆祝" in success_panel
    assert approval_panel != success_panel


def test_buddy_emotion_evolves_with_success_error_and_pet(tmp_path) -> None:
    """Buddy should feel alive through energy, bond, and streak changes."""
    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    starting_bond = state.bond
    starting_energy = state.energy

    update_buddy_state(state, "success", detail="完成")

    assert state.wins == 1
    assert state.streak == 1
    assert state.bond > starting_bond
    assert "开心发光" in BuddyRenderer().render_panel(state)

    update_buddy_state(state, "error", detail="失败")

    assert state.stumbles == 1
    assert state.streak == 0
    assert "稳住陪跑" in BuddyRenderer().render_panel(state)

    update_buddy_state(state, "pet", detail="打气完成")

    assert state.energy > starting_energy
    assert state.bond > starting_bond
    assert "被治愈了" in BuddyRenderer().render_panel(state)


def test_buddy_interactions_have_distinct_moods_and_badges(tmp_path) -> None:
    """Extra Buddy interactions should feel different instead of reusing pet copy."""
    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    renderer = BuddyRenderer()

    update_buddy_state(state, "cheer", detail="打气完成")
    cheer_panel = renderer.render_panel(state)
    assert state.mood == "cheer"
    assert "士气在线" in cheer_panel
    assert "互动 · pet cheer joke snack chat" in cheer_panel

    update_buddy_state(state, "joke", detail="讲笑话完成")
    joke_panel = renderer.render_panel(state)
    assert state.mood == "joke"
    assert "笑点营业" in joke_panel

    update_buddy_state(state, "snack", detail="投喂完成")
    snack_panel = renderer.render_panel(state)
    assert state.mood == "snack"
    assert "回血中" in snack_panel


def test_buddy_contextual_lines_react_to_long_term_state(tmp_path) -> None:
    """High bond and streak should influence Buddy's local deterministic voice."""
    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    state.bond = 85
    state.streak = 4

    update_buddy_state(state, "cheer", detail="继续推进", nonce="contextual")

    assert any(
        phrase in state.message
        for phrase in ("连续", "默契", "给你打气", "挥旗", "稳一点")
    )
    assert "默契搭子" in BuddyRenderer().render_panel(state)


@pytest.mark.asyncio
async def test_buddy_brain_generates_single_sanitized_line(tmp_path) -> None:
    """The optional Buddy model should be isolated and produce panel-safe text."""

    class FakeLLM:
        async def ainvoke(self, messages):  # type: ignore[no-untyped-def]
            self.messages = messages
            return SimpleNamespace(content="“这界面开始有点像活的了。”\n多余解释")

    state = BuddyStore(tmp_path / "buddy.yaml", seed_text="project-a").hatch()
    update_buddy_state(state, "thinking", detail="Agent 正在分析请求", user_text="优化宠物展示")
    brain = BuddyBrain(enabled=True, llm=FakeLLM())

    line = await brain.generate(state, user_text="优化宠物展示", detail="Agent 正在分析请求")

    assert line == "这界面开始有点像活的了。 多余解释"
    assert len(line) <= 40
