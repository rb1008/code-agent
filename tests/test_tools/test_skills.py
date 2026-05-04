"""Tests for skill tools."""

import pytest

from code_agent.tools.skills import DiscoverSkillsTool, ListSkillsTool, UseSkillTool


@pytest.mark.asyncio
async def test_skill_tools_list_and_render_project_skill(tmp_path) -> None:
    """Skill tools should expose project-local SKILL.md prompts."""
    skill_dir = tmp_path / "skills" / "debug"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Debug carefully\n---\nDebug $ARGUMENTS.",
        encoding="utf-8",
    )

    listed = await ListSkillsTool(tmp_path / "skills").execute()
    used = await UseSkillTool(tmp_path / "skills").execute("debug", "the CLI")

    assert listed.success is True
    assert "- debug: Debug carefully" in listed.output
    assert used.success is True
    assert "Debug the CLI." in used.output


@pytest.mark.asyncio
async def test_use_skill_reports_available_skills_on_missing_name(tmp_path) -> None:
    """Missing skills should fail with a useful listing."""
    skill_dir = tmp_path / "skills" / "known"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Known skill", encoding="utf-8")

    result = await UseSkillTool(tmp_path / "skills").execute("missing")

    assert result.success is False
    assert "未找到技能：missing" in (result.error or "")
    assert "known" in (result.error or "")


@pytest.mark.asyncio
async def test_discover_skills_returns_ranked_matches(tmp_path) -> None:
    """Discover tool should recommend skill names without loading the full body."""
    skill_dir = tmp_path / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: 严格审查代码风险\nkeywords: [安全]\n---\nSECRET PROMPT BODY",
        encoding="utf-8",
    )

    result = await DiscoverSkillsTool(tmp_path / "skills").execute("安全审查")

    assert result.success is True
    assert "review" in result.output
    assert "SECRET PROMPT BODY" not in result.output
    assert result.metadata["matches"][0]["name"] == "review"
