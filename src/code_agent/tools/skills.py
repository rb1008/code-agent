"""Skill execution tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.utils.skills import SkillLoader


class UseSkillTool(BaseTool):
    """Load a project skill prompt for the model to follow."""

    name = "use_skill"
    aliases = ["skill"]
    search_hint = "调用 项目技能 workflow prompt"
    description = (
        "按名称调用项目本地技能。技能是可复用的提示工作流，"
        "从 .code-agent/skills/<name>/SKILL.md 加载。"
    )
    parameters = {
        "name": {"type": "string", "description": "技能名称", "required": True},
        "arguments": {
            "type": "string",
            "description": "传给技能的参数或任务细节",
            "required": False,
        },
    }
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    def __init__(self, skills_dir: Path | str | None = None) -> None:
        super().__init__()
        self.skills_dir = Path(skills_dir) if skills_dir else Path(".code-agent/skills")

    async def execute(self, name: str, arguments: str = "") -> ToolResult:
        loader = SkillLoader(self.skills_dir)
        skill = loader.get(name)
        if not skill:
            return ToolResult.fail(
                f"未找到技能：{name}\n可用技能：\n{loader.listing()}"
            )
        return ToolResult.ok(
            skill.render(arguments),
            skill=name,
            context=skill.context,
            allowed_tools=skill.allowed_tools,
        )


class DiscoverSkillsTool(BaseTool):
    """Discover relevant project skills for the current task."""

    name = "discover_skills"
    aliases = ["skill_search", "find_skill"]
    search_hint = "发现 匹配 推荐 技能 skill semantic"
    description = (
        "根据当前任务发现相关项目技能，只返回名称、说明和匹配原因，"
        "不会加载完整 SKILL.md 正文。"
    )
    parameters = {
        "query": {"type": "string", "description": "当前任务或搜索关键词", "required": True},
        "limit": {"type": "integer", "description": "最多返回多少个技能，默认 5", "required": False},
    }
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    def __init__(self, skills_dir: Path | str | None = None) -> None:
        super().__init__()
        self.skills_dir = Path(skills_dir) if skills_dir else Path(".code-agent/skills")

    async def execute(self, query: str, limit: int = 5) -> ToolResult:
        loader = SkillLoader(self.skills_dir)
        matches = loader.discover(query, limit=limit or 5)
        if not matches:
            return ToolResult.ok(
                f"没有找到和当前任务相关的技能：{query}",
                matches=[],
            )

        lines = [
            f"找到 {len(matches)} 个相关技能：",
            "",
        ]
        payload = []
        for match in matches:
            skill = match.skill
            desc = skill.description or skill.when_to_use or "项目技能"
            lines.append(f"- {skill.name}（得分 {match.score}）：{desc}")
            lines.append(f"  原因：{match.reason}")
            if skill.argument_hint:
                lines.append(f"  参数提示：{skill.argument_hint}")
            payload.append(
                {
                    "name": skill.name,
                    "score": match.score,
                    "description": desc,
                    "reason": match.reason,
                    "argument_hint": skill.argument_hint,
                    "path": str(skill.path),
                }
            )
        lines.append("")
        lines.append("需要使用技能时，再调用 use_skill(name, arguments)。")
        return ToolResult.ok("\n".join(lines), matches=payload)


class ListSkillsTool(BaseTool):
    """List project-local skills."""

    name = "list_skills"
    aliases = ["skills"]
    search_hint = "列出 项目技能"
    description = "列出当前 agent 可用的项目本地技能。"
    parameters: dict[str, Any] = {}
    permission = ToolPermission(require_confirmation=False, allowed_in_auto_mode=True)

    def __init__(self, skills_dir: Path | str | None = None) -> None:
        super().__init__()
        self.skills_dir = Path(skills_dir) if skills_dir else Path(".code-agent/skills")

    async def execute(self) -> ToolResult:
        loader = SkillLoader(self.skills_dir)
        return ToolResult.ok(loader.listing(), skills=[skill.name for skill in loader.load()])
