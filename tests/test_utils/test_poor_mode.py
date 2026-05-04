"""Tests for project-local poor mode state."""

from code_agent.utils.poor_mode import PoorModeState


def test_poor_mode_state_persists_without_config_yaml(tmp_path) -> None:
    """Poor mode should be saved in a tiny state file, not the main config."""
    path = tmp_path / ".code-agent" / "runtime.yaml"
    state = PoorModeState.load(path)

    assert state.active is False

    state.toggle()
    loaded = PoorModeState.load(path)

    assert loaded.active is True
    assert "poor_mode: true" in path.read_text(encoding="utf-8")
    assert not (tmp_path / "config.yaml").exists()


def test_poor_mode_load_uses_default_when_state_missing(tmp_path) -> None:
    """Config defaults should seed the state until a runtime file exists."""
    state = PoorModeState.load(tmp_path / "missing.yaml", default=True)

    assert state.active is True
