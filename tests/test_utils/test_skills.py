"""Tests for project-local skill loading."""

from code_agent.utils.skills import SkillLoader


def test_skill_loader_reads_frontmatter_and_renders_arguments(tmp_path) -> None:
    """Skills should load metadata and substitute standard placeholders."""
    skill_dir = tmp_path / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: strict-review\n"
        "description: Find risky defects\n"
        "allowed_tools: [read_file, grep]\n"
        "argument_hint: target path\n"
        "---\n"
        "Review $ARGUMENTS from ${CODE_AGENT_SKILL_DIR}.",
        encoding="utf-8",
    )

    skill = SkillLoader(tmp_path / "skills").get("strict-review")

    assert skill is not None
    assert skill.description == "Find risky defects"
    assert skill.allowed_tools == ["read_file", "grep"]
    rendered = skill.render("src")
    assert "# Skill: strict-review" in rendered
    assert "Review src from" in rendered
    assert "User arguments:\nsrc" in rendered


def test_skill_loader_listing_hides_model_disabled_skills(tmp_path) -> None:
    """Disabled skills should not appear in model-visible listings."""
    visible = tmp_path / "skills" / "visible"
    hidden = tmp_path / "skills" / "hidden"
    visible.mkdir(parents=True)
    hidden.mkdir(parents=True)
    (visible / "SKILL.md").write_text("Visible body", encoding="utf-8")
    (hidden / "SKILL.md").write_text(
        "---\ndisable_model_invocation: true\n---\nHidden body",
        encoding="utf-8",
    )

    listing = SkillLoader(tmp_path / "skills").listing()

    assert "visible" in listing
    assert "hidden" not in listing


def test_skill_loader_matching_returns_only_relevant_skills(tmp_path) -> None:
    """Per-turn skill selection should avoid injecting unrelated skills."""
    review = tmp_path / "skills" / "review"
    deploy = tmp_path / "skills" / "deploy"
    review.mkdir(parents=True)
    deploy.mkdir(parents=True)
    (review / "SKILL.md").write_text(
        "---\ndescription: 严格审查代码风险\n---\nReview code.",
        encoding="utf-8",
    )
    (deploy / "SKILL.md").write_text(
        "---\ndescription: 发布部署流程\n---\nDeploy service.",
        encoding="utf-8",
    )

    matches = SkillLoader(tmp_path / "skills").matching("请严格审查这个模块", limit=1)

    assert [skill.name for skill in matches] == ["review"]


def test_skill_loader_discover_returns_reasons_and_keywords(tmp_path) -> None:
    """Skill discovery should rank metadata matches without loading full prompts."""
    review = tmp_path / "skills" / "review"
    review.mkdir(parents=True)
    (review / "SKILL.md").write_text(
        "---\n"
        "description: 严格审查代码风险\n"
        "keywords: [安全, review]\n"
        "argument_hint: 目标路径\n"
        "---\n"
        "Full prompt should not be included in discovery.",
        encoding="utf-8",
    )

    matches = SkillLoader(tmp_path / "skills").discover("请做安全 review", limit=3)

    assert len(matches) == 1
    assert matches[0].skill.name == "review"
    assert matches[0].score > 0
    assert "匹配" in matches[0].reason
