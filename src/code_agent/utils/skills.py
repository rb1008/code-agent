"""Project-local skill loading and rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
import re
from typing import Any, Optional

import yaml  # type: ignore[import-untyped]


@dataclass
class SkillDefinition:
    """A reusable prompt workflow loaded from SKILL.md."""

    name: str
    description: str
    prompt: str
    path: Path
    when_to_use: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str = ""
    model: Optional[str] = None
    effort: Optional[str] = None
    context: str = "inline"
    paths: list[str] = field(default_factory=list)
    user_invocable: bool = True
    disable_model_invocation: bool = False
    keywords: list[str] = field(default_factory=list)

    def render(self, arguments: str = "") -> str:
        """Render the skill prompt with simple argument placeholders."""
        text = self.prompt.replace("$ARGUMENTS", arguments)
        text = text.replace("${ARGUMENTS}", arguments)
        text = text.replace("${CODE_AGENT_SKILL_DIR}", str(self.path.parent))
        return (
            f"# Skill: {self.name}\n\n"
            f"{text.strip()}\n\n"
            f"User arguments:\n{arguments.strip() or '(none)'}"
        )

    def matches_path(self, file_path: str) -> bool:
        """Return whether this skill should activate for a path."""
        if not self.paths:
            return True
        return any(fnmatch(file_path, pattern) for pattern in self.paths)


@dataclass(frozen=True)
class SkillMatch:
    """A ranked skill discovery result."""

    skill: SkillDefinition
    score: int
    reason: str


class SkillLoader:
    """Load skills from a project-local skills directory."""

    def __init__(self, root: Path | str, max_description_chars: int = 250) -> None:
        self.root = Path(root)
        self.max_description_chars = max_description_chars

    def load(self) -> list[SkillDefinition]:
        if not self.root.exists():
            return []
        skills: list[SkillDefinition] = []
        seen: set[Path] = set()
        for child in sorted(self.root.iterdir()):
            skill_file = child / "SKILL.md"
            if not child.is_dir() or not skill_file.exists():
                continue
            real = skill_file.resolve()
            if real in seen:
                continue
            seen.add(real)
            skill = self._load_file(skill_file)
            if skill:
                skills.append(skill)
        return skills

    def get(self, name: str) -> Optional[SkillDefinition]:
        for skill in self.load():
            if skill.name == name:
                return skill
        return None

    def listing(
        self,
        *,
        query: str = "",
        limit: Optional[int] = None,
    ) -> str:
        """Return a compact listing suitable for prompts or CLI output."""
        skills = [skill for skill in self.load() if not skill.disable_model_invocation]
        if query.strip():
            skills = self.matching(query, limit=limit or len(skills))
        elif limit is not None:
            skills = skills[:limit]
        if not skills:
            return "没有加载项目技能。"
        rows = []
        for skill in skills:
            desc = skill.description or skill.when_to_use or "No description"
            if len(desc) > self.max_description_chars:
                desc = desc[: self.max_description_chars - 3].rstrip() + "..."
            rows.append(f"- {skill.name}: {desc}")
        return "\n".join(rows)

    def matching(self, query: str, *, limit: int = 5) -> list[SkillDefinition]:
        """Return skills whose metadata appears relevant to the current request.

        这里故意只看技能的轻量元数据，不读取/注入完整技能正文；真正需要时再由
        `use_skill` 加载单个 SKILL.md，避免启动时或每轮请求把所有技能塞进上下文。
        """
        return [match.skill for match in self.discover(query, limit=limit)]

    def discover(self, query: str, *, limit: int = 5) -> list[SkillMatch]:
        """Return ranked skill matches with compact reasons.

        这是给模型和 CLI 共用的技能发现入口。它只读取技能元数据，不注入
        SKILL.md 正文；真正要使用某个技能时仍然必须调用 `use_skill`。
        """
        scored: list[SkillMatch] = []
        tokens = _query_tokens(query)
        if not tokens:
            return []
        for skill in self.load():
            if skill.disable_model_invocation:
                continue
            fields = {
                "名称": skill.name,
                "说明": skill.description,
                "触发": skill.when_to_use,
                "参数": skill.argument_hint,
                "路径": " ".join(skill.paths),
                "关键词": " ".join(skill.keywords),
            }
            haystack = " ".join(fields.values()).lower()
            score = 0
            reasons: list[str] = []
            for token in tokens:
                if token == skill.name.lower():
                    score += 12
                    reasons.append(f"名称精确匹配 `{token}`")
                elif token in skill.name.lower():
                    score += 7
                    reasons.append(f"名称包含 `{token}`")
                elif token in " ".join(skill.keywords).lower():
                    score += 6
                    reasons.append(f"关键词匹配 `{token}`")
                elif token in skill.when_to_use.lower():
                    score += 5
                    reasons.append(f"使用场景匹配 `{token}`")
                elif token in skill.description.lower():
                    score += 4
                    reasons.append(f"说明匹配 `{token}`")
                elif token in " ".join(skill.paths).lower():
                    score += 4
                    reasons.append(f"路径匹配 `{token}`")
                elif token in haystack:
                    score += 2
                    reasons.append(f"元数据匹配 `{token}`")
            if score:
                scored.append(
                    SkillMatch(
                        skill=skill,
                        score=score,
                        reason=_dedupe_reasons(reasons),
                    )
                )
        scored.sort(key=lambda item: (-item.score, item.skill.name))
        return scored[:limit]

    def _load_file(self, path: Path) -> Optional[SkillDefinition]:
        raw = path.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(raw)
        name = str(meta.get("name") or path.parent.name)
        description = str(meta.get("description") or _first_paragraph(body) or "")
        return SkillDefinition(
            name=name,
            description=description,
            prompt=body.strip(),
            path=path,
            when_to_use=str(meta.get("when_to_use") or meta.get("when-to-use") or ""),
            allowed_tools=_as_list(meta.get("allowed_tools") or meta.get("allowed-tools")),
            argument_hint=str(meta.get("argument_hint") or meta.get("argument-hint") or ""),
            model=str(meta["model"]) if meta.get("model") else None,
            effort=str(meta["effort"]) if meta.get("effort") else None,
            context=str(meta.get("context") or "inline"),
            paths=_as_list(meta.get("paths")),
            user_invocable=bool(meta.get("user_invocable", meta.get("user-invocable", True))),
            disable_model_invocation=bool(
                meta.get("disable_model_invocation", meta.get("disable-model-invocation", False))
            ),
            keywords=_as_list(meta.get("keywords") or meta.get("tags")),
        )


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    try:
        meta = yaml.safe_load(parts[1]) or {}
        if not isinstance(meta, dict):
            meta = {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2]


def _first_paragraph(text: str) -> str:
    for block in text.strip().split("\n\n"):
        clean = " ".join(line.strip("# ").strip() for line in block.splitlines()).strip()
        if clean:
            return clean
    return ""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _query_tokens(text: str) -> list[str]:
    """Extract small English/Chinese tokens for extension relevance matching."""
    normalized = text.lower()
    tokens: list[str] = []
    tokens.extend(re.findall(r"[a-zA-Z_][\w.-]*", normalized))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        tokens.append(run)
        tokens.extend(run[index : index + 2] for index in range(0, len(run) - 1))

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


def _dedupe_reasons(reasons: list[str]) -> str:
    """Keep skill discovery reasons short and stable."""
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            deduped.append(reason)
    return "；".join(deduped[:3]) or "元数据相关"
