"""Tools package for Code Agent."""

from typing import Any

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.tool_search import ToolSearchTool
from code_agent.tools.file import (
    ReadFileTool,
    WriteFileTool,
    ListDirectoryTool,
    SearchFilesTool,
    FileExistsTool,
)
from code_agent.tools.code_edit import (
    ReplaceCodeTool,
    InsertCodeTool,
    DeleteCodeTool,
    ApplyDiffTool,
)
from code_agent.tools.shell import BashTool, GlobTool, GrepTool
from code_agent.tools.git import (
    GitStatusTool,
    GitDiffTool,
    GitLogTool,
    GitAddTool,
    GitCommitTool,
    GitBranchTool,
)
from code_agent.tools.project import (
    GetProjectStructureTool,
    SummarizeFileTool,
    GetDependenciesTool,
)
from code_agent.tools.lsp import LSPTool
from code_agent.tools.web import WebFetchTool, WebSearchTool
from code_agent.tools.task import TaskCreateTool, TaskUpdateTool, TaskListTool
from code_agent.tools.agent_tool import AgentTool
from code_agent.tools.mcp import CallMCPTool, ListMCPResourcesTool, ReadMCPResourceTool
from code_agent.tools.skills import DiscoverSkillsTool, ListSkillsTool, UseSkillTool
from code_agent.tools.workflow import WorkflowListTool, WorkflowRunTool
from code_agent.tools.monitor import (
    MonitorListTool,
    MonitorReadTool,
    MonitorStartTool,
    MonitorStopTool,
)
from code_agent.tools.coordinator import CoordinatorRunTool, ForkAgentTool

__all__ = [
    # Base classes
    "BaseTool",
    "ToolPermission",
    "ToolResult",
    "ToolRegistry",
    "ToolSearchTool",
    # File tools
    "ReadFileTool",
    "WriteFileTool",
    "ListDirectoryTool",
    "SearchFilesTool",
    "FileExistsTool",
    # Code edit tools
    "ReplaceCodeTool",
    "InsertCodeTool",
    "DeleteCodeTool",
    "ApplyDiffTool",
    # Shell tools
    "BashTool",
    "GlobTool",
    "GrepTool",
    # Git tools
    "GitStatusTool",
    "GitDiffTool",
    "GitLogTool",
    "GitAddTool",
    "GitCommitTool",
    "GitBranchTool",
    # Project tools
    "GetProjectStructureTool",
    "SummarizeFileTool",
    "GetDependenciesTool",
    "LSPTool",
    # Web tools
    "WebFetchTool",
    "WebSearchTool",
    # Task tools
    "TaskCreateTool",
    "TaskUpdateTool",
    "TaskListTool",
    # Agent tool
    "AgentTool",
    # MCP tools
    "ListMCPResourcesTool",
    "ReadMCPResourceTool",
    "CallMCPTool",
    # P1 extensibility tools
    "ListSkillsTool",
    "UseSkillTool",
    "DiscoverSkillsTool",
    "WorkflowListTool",
    "WorkflowRunTool",
    "MonitorListTool",
    "MonitorReadTool",
    "MonitorStartTool",
    "MonitorStopTool",
    "CoordinatorRunTool",
    "ForkAgentTool",
]


def create_default_registry(settings: Any = None) -> ToolRegistry:
    """Create a tool registry with all default tools registered.

    Args:
        settings: Optional settings object to pass to tools

    Returns:
        ToolRegistry with all tools registered
    """
    registry = ToolRegistry()

    file_config = getattr(settings, "file", None)
    shell_config = getattr(settings, "shell", None)
    project_config = getattr(settings, "project", None)
    mcp_config = getattr(settings, "mcp", None)
    agent_config = getattr(settings, "agent", None)

    if agent_config:
        registry.lazy_enabled = getattr(agent_config, "lazy_tool_loading_enabled", True)

    # tool_search 是模型的工具发现入口，始终直接注册并激活。
    registry.register(ToolSearchTool(registry), active=True, pinned=True)

    # 默认工具都通过工厂注册，只有当前请求需要时才会创建实例。
    registry.register_lazy(ReadFileTool, lambda: ReadFileTool(file_config))
    registry.register_lazy(WriteFileTool, lambda: WriteFileTool(file_config))
    registry.register_lazy(ListDirectoryTool, lambda: ListDirectoryTool(file_config))
    registry.register_lazy(SearchFilesTool, lambda: SearchFilesTool(file_config))
    registry.register_lazy(FileExistsTool, lambda: FileExistsTool(file_config))

    registry.register_lazy(ReplaceCodeTool, lambda: ReplaceCodeTool(file_config))
    registry.register_lazy(InsertCodeTool, lambda: InsertCodeTool(file_config))
    registry.register_lazy(DeleteCodeTool, lambda: DeleteCodeTool(file_config))
    registry.register_lazy(ApplyDiffTool, lambda: ApplyDiffTool(file_config))

    registry.register_lazy(BashTool, lambda: BashTool(shell_config))
    registry.register_lazy(GlobTool, lambda: GlobTool(shell_config))
    registry.register_lazy(GrepTool, lambda: GrepTool(shell_config))

    registry.register_lazy(GitStatusTool, GitStatusTool)
    registry.register_lazy(GitDiffTool, GitDiffTool)
    registry.register_lazy(GitLogTool, GitLogTool)
    registry.register_lazy(GitAddTool, GitAddTool)
    registry.register_lazy(GitCommitTool, GitCommitTool)
    registry.register_lazy(GitBranchTool, GitBranchTool)

    registry.register_lazy(GetProjectStructureTool, lambda: GetProjectStructureTool(project_config))
    registry.register_lazy(SummarizeFileTool, lambda: SummarizeFileTool(project_config))
    registry.register_lazy(GetDependenciesTool, lambda: GetDependenciesTool(project_config))
    registry.register_lazy(LSPTool, lambda: LSPTool(project_config))

    registry.register_lazy(WebFetchTool, WebFetchTool)
    registry.register_lazy(WebSearchTool, WebSearchTool)

    registry.register_lazy(TaskCreateTool, TaskCreateTool)
    registry.register_lazy(TaskUpdateTool, TaskUpdateTool)
    registry.register_lazy(TaskListTool, TaskListTool)

    if agent_config:
        # Project-local extensibility
        registry.register_lazy(DiscoverSkillsTool, lambda: DiscoverSkillsTool(agent_config.skills_dir))
        registry.register_lazy(ListSkillsTool, lambda: ListSkillsTool(agent_config.skills_dir))
        registry.register_lazy(UseSkillTool, lambda: UseSkillTool(agent_config.skills_dir))
        registry.register_lazy(WorkflowListTool, lambda: WorkflowListTool(agent_config.workflows_dir))
        registry.register_lazy(
            WorkflowRunTool,
            lambda: WorkflowRunTool(agent_config.workflows_dir, shell_config),
        )

        # Session-local background monitors. These are intentionally not daemonized;
        # they live only inside the current CLI process.
        registry.register_lazy(
            MonitorStartTool,
            lambda: MonitorStartTool(shell_config, agent_config.monitor_max_output_chars),
        )
        registry.register_lazy(MonitorListTool, MonitorListTool)
        registry.register_lazy(MonitorReadTool, MonitorReadTool)
        registry.register_lazy(MonitorStopTool, MonitorStopTool)

    # Plan mode is controlled by the interactive slash command. These standalone
    # tools cannot mutate an agent instance, so registering them would mislead
    # the model into thinking plan mode changed when it did not.

    # Agent tool
    if settings:
        registry.register_lazy(AgentTool, lambda: AgentTool(settings=settings))
        registry.register_lazy(ForkAgentTool, lambda: ForkAgentTool(settings=settings))
        registry.register_lazy(CoordinatorRunTool, lambda: CoordinatorRunTool(settings=settings))

    # MCP bridge tools
    if mcp_config and getattr(mcp_config, "enabled", False):
        registry.register_lazy(ListMCPResourcesTool, lambda: ListMCPResourcesTool(mcp_config))
        registry.register_lazy(ReadMCPResourceTool, lambda: ReadMCPResourceTool(mcp_config))
        registry.register_lazy(CallMCPTool, lambda: CallMCPTool(mcp_config))

    if agent_config:
        always_include = list(getattr(agent_config, "always_active_tools", []))
        for name in ("discover_skills", "use_skill"):
            if name not in always_include:
                always_include.append(name)
        registry.prepare_for_request(
            "",
            always_include=always_include,
            limit=getattr(agent_config, "max_active_tools", 18),
        )

    return registry
