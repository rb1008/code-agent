"""Tool registry for managing and discovering tools.

这里的注册表同时负责两件事：
1. 保存工具元数据，方便模型/用户按能力搜索。
2. 按需实例化工具，避免启动时把所有工具都创建并塞进模型上下文。
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional, TypeAlias

from code_agent.tools.base import BaseTool, ToolResult

ToolFactory: TypeAlias = Callable[[], BaseTool]

_COMMON_CJK_TOOL_WORDS = (
    "搜索",
    "查询",
    "查找",
    "网页",
    "互联网",
    "网络",
    "文档",
    "读取",
    "查看",
    "文件",
    "目录",
    "创建",
    "写入",
    "覆盖",
    "修改",
    "替换",
    "插入",
    "删除",
    "运行",
    "测试",
    "构建",
    "提交",
    "分支",
    "状态",
    "差异",
    "定义",
    "引用",
    "符号",
    "诊断",
    "依赖",
    "记忆",
    "技能",
    "工作流",
)


def _query_tokens(text: str) -> list[str]:
    """Extract English words and useful Chinese fragments from a user/tool query."""
    tokens: list[str] = []
    tokens.extend(re.findall(r"[a-zA-Z_][\w.-]*", text))
    tokens.extend(word for word in _COMMON_CJK_TOOL_WORDS if word in text)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        tokens.extend(run[index : index + 2] for index in range(0, len(run) - 1))

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


@dataclass(frozen=True)
class ToolMetadata:
    """不实例化工具也能使用的轻量元数据。"""

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    search_hint: str = ""
    read_only: bool = False
    destructive: bool = False
    active: bool = False

    def matches(self, query: str) -> bool:
        """Return whether this metadata matches a search query."""
        return self.score(query) > 0

    def score(self, query: str) -> int:
        """Score search relevance without doing heavyweight parsing.

        中文用户常输入没有空格的整句，例如“帮我搜索网页”。如果只按空格切词，
        懒加载会漏掉 web_search/write_file 等工具。这里用英文词、常见中文
        工具词和中文 bigram 做轻量匹配；宁可多激活少量工具，也不能漏掉关键工具。
        """
        normalized = query.strip().lower()
        if not normalized:
            return 1
        tokens = _query_tokens(normalized)
        if not tokens:
            return 0

        score = 0
        haystack = " ".join(
            [
                self.name,
                self.description,
                self.search_hint,
                " ".join(self.aliases),
            ]
        ).lower()
        aliases = {alias.lower() for alias in self.aliases}
        matched = 0
        for part in tokens:
            if part == self.name.lower() or part in aliases:
                score += 6
                matched += 1
            elif part in self.name.lower():
                score += 4
                matched += 1
            elif part in self.search_hint.lower():
                score += 3
                matched += 1
            elif part in haystack:
                score += 1
                matched += 1
        if matched == 0:
            return 0
        if self.name.lower() in normalized or any(alias in normalized for alias in aliases):
            score += 10
        return score

    @classmethod
    def from_tool(cls, tool: BaseTool, *, active: bool = False) -> "ToolMetadata":
        """Build metadata from a live tool instance."""
        return cls(
            name=tool.name,
            description=tool.description,
            aliases=tuple(tool.aliases),
            search_hint=tool.search_hint,
            read_only=tool.is_read_only(),
            destructive=tool.is_destructive(),
            active=active,
        )

    @classmethod
    def from_class(cls, tool_cls: type[BaseTool], *, active: bool = False) -> "ToolMetadata":
        """Build metadata from class attributes, without constructing the tool."""
        permission = tool_cls.permission
        return cls(
            name=tool_cls.name,
            description=tool_cls.description,
            aliases=tuple(tool_cls.aliases),
            search_hint=tool_cls.search_hint,
            read_only=not permission.require_confirmation and not permission.destructive,
            destructive=permission.destructive,
            active=active,
        )


@dataclass
class _LazyTool:
    """A tool factory plus the metadata needed before instantiation."""

    metadata: ToolMetadata
    factory: ToolFactory
    instance: BaseTool | None = None

class ToolRegistry:
    """Registry for managing available tools."""

    def __init__(self) -> None:
        """Initialize an empty tool registry."""
        self._tools: dict[str, BaseTool] = {}
        self._lazy_tools: dict[str, _LazyTool] = {}
        self._aliases: dict[str, str] = {}
        self._active_tools: set[str] = set()
        self._pinned_tools: set[str] = set()
        self.lazy_enabled = True

    def register(self, tool: BaseTool, *, active: bool = True, pinned: bool = False) -> None:
        """Register an already-created tool."""
        self._tools[tool.name] = tool
        self._register_aliases(tool.name, tool.aliases)
        if active:
            self.activate(tool.name, pinned=pinned)

    def register_lazy(
        self,
        tool_cls: type[BaseTool],
        factory: ToolFactory,
        *,
        active: bool = False,
        pinned: bool = False,
    ) -> None:
        """Register a tool factory so the actual instance is created only when needed."""
        metadata = ToolMetadata.from_class(tool_cls, active=active or pinned)
        self._lazy_tools[metadata.name] = _LazyTool(metadata=metadata, factory=factory)
        self._register_aliases(metadata.name, metadata.aliases)
        if active or pinned:
            self.activate(metadata.name, pinned=pinned)

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        canonical = self._canonical_name(name)
        self._tools.pop(canonical, None)
        self._lazy_tools.pop(canonical, None)
        self._active_tools.discard(canonical)
        self._pinned_tools.discard(canonical)
        self._aliases = {
            alias: target for alias, target in self._aliases.items() if target != canonical
        }

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        canonical = self._canonical_name(name)
        if canonical in self._tools:
            return self._tools[canonical]
        lazy = self._lazy_tools.get(canonical)
        if lazy:
            # 只有真正需要执行/绑定 schema 时才创建工具实例。
            lazy.instance = lazy.instance or lazy.factory()
            self._tools[canonical] = lazy.instance
            self._register_aliases(canonical, lazy.instance.aliases)
            return lazy.instance
        return None

    def list_tools(self, *, active_only: bool = False) -> list[str]:
        """List all registered tool names."""
        names = set(self._tools) | set(self._lazy_tools)
        if active_only and self.lazy_enabled:
            names &= self._active_tools
        return sorted(names)

    def list_active_tools(self) -> list[str]:
        """List tools currently exposed to the model."""
        return self.list_tools(active_only=True)

    def activate(self, name: str, *, pinned: bool = False) -> bool:
        """Expose a tool to the model; pinned tools survive per-turn refreshes."""
        canonical = self._canonical_name(name)
        if canonical not in self._tools and canonical not in self._lazy_tools:
            return False
        self._active_tools.add(canonical)
        if pinned:
            self._pinned_tools.add(canonical)
        return True

    def deactivate(self, name: str) -> None:
        """Deactivate a tool unless it has been pinned by explicit search."""
        canonical = self._canonical_name(name)
        if canonical not in self._pinned_tools:
            self._active_tools.discard(canonical)

    def prepare_for_request(
        self,
        text: str,
        *,
        always_include: Sequence[str] = (),
        limit: int = 18,
    ) -> list[ToolMetadata]:
        """Refresh active tools for one user request and return the active metadata.

        这个方法是 lazy loading 的关键入口：每轮请求只激活基础工具、用户显式
        pin 住的工具，以及与当前输入最相关的一小组工具。
        """
        if not self.lazy_enabled:
            self._active_tools = set(self.list_tools())
            return self.list_metadata(active_only=True)

        active = set(self._pinned_tools)
        for name in always_include:
            canonical = self._canonical_name(name)
            if canonical in self._tools or canonical in self._lazy_tools:
                active.add(canonical)

        ranked = [
            (metadata.score(text), metadata.name)
            for metadata in self.search_metadata(text)
            if metadata.score(text) > 0 and metadata.name not in active
        ]
        ranked.sort(key=lambda item: (-item[0], item[1]))
        # max_active_tools 是对“本轮暴露给模型的 schema 总数”的上限；
        # always_include 和 pinned 工具优先保留，剩余名额再给自然语言匹配结果。
        remaining_slots = max(0, limit - len(active))
        for _score, name in ranked[:remaining_slots]:
            active.add(name)

        self._active_tools = active
        return self.list_metadata(active_only=True)

    def activate_matching(self, query: str, *, limit: int = 8, pinned: bool = True) -> list[ToolMetadata]:
        """Activate matching tools, usually from the model-facing tool_search tool."""
        matches = self.search_metadata(query)[:limit]
        for metadata in matches:
            self.activate(metadata.name, pinned=pinned)
        return self.list_metadata(active_only=True)

    def search(self, query: str) -> list[BaseTool]:
        """Search tools by name, alias, hint, or description."""
        return [
            tool
            for metadata in self.search_metadata(query)
            if (tool := self.get(metadata.name)) is not None
        ]

    def search_metadata(self, query: str) -> list[ToolMetadata]:
        """Search tool metadata without forcing lazy tools to instantiate."""
        matches = []
        for metadata in self._metadata_by_name().values():
            score = metadata.score(query)
            if score > 0:
                matches.append((score, metadata))
        matches.sort(key=lambda item: (-item[0], item[1].name))
        return [metadata for _score, metadata in matches]

    def list_metadata(self, *, active_only: bool = False) -> list[ToolMetadata]:
        """Return compact metadata for UI, prompt rendering, and diagnostics."""
        metadata = list(self._metadata_by_name().values())
        if active_only and self.lazy_enabled:
            metadata = [item for item in metadata if item.name in self._active_tools]
        return sorted(
            (self._with_active_flag(item) for item in metadata),
            key=lambda item: item.name,
        )

    def list_metadata_rows(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Return compact metadata for UI, prompt rendering, and diagnostics."""
        return [
            {
                "name": item.name,
                "aliases": ", ".join(item.aliases),
                "hint": item.search_hint,
                "read_only": item.read_only,
                "destructive": item.destructive,
                "active": item.active,
                "description": item.description,
            }
            for item in self.list_metadata(active_only=active_only)
        ]

    def get_schemas(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Get schemas for all registered tools."""
        return [
            tool.get_schema()
            for name in self.list_tools(active_only=active_only)
            if (tool := self.get(name)) is not None
        ]

    async def execute(self, name: str, **kwargs: Any) -> ToolResult:
        """Execute a tool by name with the given parameters."""
        tool = self.get(name)
        if not tool:
            return ToolResult.fail(f"未找到工具：{name}")

        try:
            return await tool.execute(**kwargs)
        except Exception as e:
            return ToolResult.fail(f"工具执行失败：{str(e)}")

    def __contains__(self, name: str) -> bool:
        canonical = self._canonical_name(name)
        return canonical in self._tools or canonical in self._lazy_tools

    def __len__(self) -> int:
        return len(set(self._tools) | set(self._lazy_tools))

    def _canonical_name(self, name: str) -> str:
        """Resolve aliases to canonical tool names."""
        return self._aliases.get(name, name)

    def _register_aliases(self, name: str, aliases: Sequence[str]) -> None:
        for alias in aliases:
            self._aliases[alias] = name

    def _metadata_by_name(self) -> dict[str, ToolMetadata]:
        metadata: dict[str, ToolMetadata] = {}
        for name, lazy in self._lazy_tools.items():
            metadata[name] = lazy.metadata
        for name, tool in self._tools.items():
            metadata[name] = ToolMetadata.from_tool(tool)
        return metadata

    def _with_active_flag(self, metadata: ToolMetadata) -> ToolMetadata:
        if metadata.active == (metadata.name in self._active_tools):
            return metadata
        return ToolMetadata(
            name=metadata.name,
            description=metadata.description,
            aliases=metadata.aliases,
            search_hint=metadata.search_hint,
            read_only=metadata.read_only,
            destructive=metadata.destructive,
            active=metadata.name in self._active_tools,
        )
