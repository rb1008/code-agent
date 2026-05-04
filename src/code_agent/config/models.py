"""Configuration models for Code Agent."""

import os
from pathlib import Path
from typing import Optional

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, field_validator


class LLMConfig(BaseModel):
    """LLM configuration."""

    base_url: str = Field(default="https://api.openai.com/v1", description="API base URL")
    api_key: str = Field(default="", description="API key")
    model_name: str = Field(default="gpt-4o", description="Model name")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(default=4096, ge=1, description="Maximum tokens per response")
    timeout: int = Field(default=60, ge=1, description="Request timeout in seconds")
    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts")

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v:
            v = os.getenv("OPENAI_API_KEY", "")
        return v


class ShellConfig(BaseModel):
    """Shell execution configuration."""

    allowed_commands: list[str] = Field(
        default=[
            "git",
            "npm",
            "pnpm",
            "yarn",
            "node",
            "pip",
            "python",
            "python3",
            "pytest",
            "ruff",
            "mypy",
            "uv",
            "cargo",
            "rustc",
            "go",
            "make",
            "ls",
            "pwd",
            "cat",
            "sed",
            "rg",
            "grep",
            "find",
        ],
        description="Allowed command prefixes",
    )
    blocked_commands: list[str] = Field(
        default=[
            "rm -rf /",
            "rm -rf /*",
            "rm -rf ~",
            "rm -rf .",
            "sudo",
            "su ",
            "chmod -R",
            "chown -R",
            "> /dev/sda",
            "mkfs",
            "dd if=/dev/zero",
            "shutdown",
            "reboot",
        ],
        description="Blocked command patterns",
    )
    workspace_root: str = Field(
        default=".",
        description="Workspace root. Shell cwd must stay within this directory.",
    )
    timeout: int = Field(default=30, ge=1, description="Command timeout in seconds")
    require_confirmation: bool = Field(
        default=True, description="Require confirmation for destructive commands"
    )
    sandbox_enabled: bool = Field(
        default=True,
        description="Run bash commands inside a workspace-scoped sandbox wrapper when possible",
    )
    sandbox_fail_if_unavailable: bool = Field(
        default=False,
        description="Fail shell execution when the configured sandbox cannot be applied",
    )
    sandbox_allow_network: bool = Field(
        default=True,
        description="Allow network access for sandboxed shell commands",
    )
    sandbox_writable_paths: list[str] = Field(
        default=["."],
        description="Workspace-relative paths that sandboxed shell commands may write",
    )
    sandbox_readonly_paths: list[str] = Field(
        default=[],
        description="Additional paths sandboxed shell commands may read",
    )
    sandbox_deny_write_paths: list[str] = Field(
        default=[
            ".git/config",
            ".code-agent/settings.yaml",
            ".code-agent/skills",
            ".code-agent/commands",
            "config.yaml",
        ],
        description="Workspace-relative paths shell commands may not write",
    )
    sandbox_excluded_commands: list[str] = Field(
        default=[],
        description="Command patterns that bypass the shell sandbox after permission approval",
    )


class FileConfig(BaseModel):
    """File operation configuration."""

    workspace_root: str = Field(
        default=".",
        description="Workspace root. File tools cannot access paths outside this directory.",
    )
    allow_absolute_paths: bool = Field(
        default=False,
        description="Allow absolute paths outside workspace root. Intended only for tests or trusted local runs.",
    )
    max_file_size: int = Field(
        default=1024 * 1024, description="Maximum file size in bytes (1MB)"
    )
    allowed_extensions: list[str] = Field(
        default=[".py", ".js", ".ts", ".tsx", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"],
        description="Allowed file extensions",
    )
    blocked_paths: list[str] = Field(
        default=[".git", "node_modules", "__pycache__", ".venv", "venv"],
        description="Blocked directory patterns",
    )


class AgentConfig(BaseModel):
    """Agent behavior configuration."""

    system_prompt: Optional[str] = Field(default=None, description="Custom system prompt")
    max_iterations: int = Field(default=15, ge=1, description="Maximum tool call iterations")
    verbose: bool = Field(default=False, description="Verbose output")
    auto_confirm: bool = Field(default=False, description="Auto-confirm tool executions")
    context_window: int = Field(
        default=20, ge=1, description="Number of messages to keep in context"
    )
    context_token_limit: int = Field(
        default=120000,
        ge=1000,
        description="Approximate prompt token budget before proactive compaction",
    )
    auto_compact_token_ratio: float = Field(
        default=0.85,
        ge=0.1,
        le=1.0,
        description="Compact before a turn when estimated context exceeds this budget ratio",
    )
    max_tool_result_chars: int = Field(
        default=12000,
        ge=1000,
        description="Maximum characters retained per tool observation in memory",
    )
    persistent_memory_enabled: bool = Field(
        default=True,
        description="Persist bounded long-term memory to a project-local Markdown file",
    )
    persistent_memory_path: str = Field(
        default=".code_agent_memory.md",
        description="Project-local Markdown file for bounded persistent memory",
    )
    persistent_memory_dir: str = Field(
        default=".code-agent/memory",
        description="Project-local directory for topic-based memory archives",
    )
    persistent_memory_max_chars: int = Field(
        default=12000,
        ge=1000,
        description="Maximum characters retained in persistent memory Markdown",
    )
    auto_dream_enabled: bool = Field(
        default=True,
        description="Automatically organize persistent memory when durable context grows",
    )
    auto_dream_min_messages: int = Field(
        default=14,
        ge=4,
        description="Minimum durable message count before auto dream can run",
    )
    lazy_tool_loading_enabled: bool = Field(
        default=True,
        description="Expose only relevant tool schemas to the model on each request",
    )
    max_active_tools: int = Field(
        default=18,
        ge=4,
        description="Maximum request-matched tool schemas exposed when lazy loading is enabled",
    )
    always_active_tools: list[str] = Field(
        default=[
            "tool_search",
            "read_file",
            "list_directory",
            "grep",
            "glob",
            "bash",
            "lsp_tool",
            "discover_skills",
            "use_skill",
        ],
        description="Tool names always exposed to the model while lazy loading is enabled",
    )
    poor_mode: bool = Field(
        default=False,
        description="Disable optional memory persistence and typing suggestions to reduce cost",
    )
    poor_mode_path: str = Field(
        default=".code-agent/runtime.yaml",
        description="Project-local runtime state file for poor mode",
    )
    project_instruction_files: list[str] = Field(
        default=[
            "CODE_AGENT.md",
            ".code-agent/instructions.md",
            "CLAUDE.md",
            "AGENTS.md",
        ],
        description="Project-local instruction files loaded into the system prompt",
    )
    project_instruction_max_chars: int = Field(
        default=12000,
        ge=1000,
        description="Maximum characters loaded from project instruction files",
    )
    transcript_enabled: bool = Field(
        default=True,
        description="Write an append-only project-local JSONL transcript for each session",
    )
    transcript_dir: str = Field(
        default=".code-agent/transcripts",
        description="Project-local directory for timestamped session transcripts",
    )
    transcript_max_event_chars: int = Field(
        default=20000,
        ge=1000,
        description="Maximum characters retained per transcript event",
    )
    permission_settings_path: str = Field(
        default=".code-agent/settings.yaml",
        description="Project-local permission rules and remembered decisions",
    )
    hook_settings_path: str = Field(
        default=".code-agent/hooks.yaml",
        description="Project-local hook command configuration",
    )
    plan_store_path: str = Field(
        default=".code-agent/approved_plan.md",
        description="Project-local file used to store the last approved plan",
    )
    mcp_config_path: str = Field(
        default=".code-agent/mcp.yaml",
        description="Project-local MCP server configuration",
    )
    buddy_settings_path: str = Field(
        default=".code-agent/buddy.yaml",
        description="Project-local Buddy companion state",
    )
    skills_dir: str = Field(
        default=".code-agent/skills",
        description="Project-local directory containing skill-name/SKILL.md definitions",
    )
    commands_dir: str = Field(
        default=".code-agent/commands",
        description="Project-local directory containing custom slash command Markdown files",
    )
    workflows_dir: str = Field(
        default=".code-agent/workflows",
        description="Project-local directory containing workflow scripts",
    )
    monitor_max_output_chars: int = Field(
        default=12000,
        ge=1000,
        description="Maximum retained output per monitor task",
    )


class BuddyConfig(BaseModel):
    """Optional independent model channel for the Buddy companion UI."""

    enabled: bool = Field(
        default=False,
        description="Enable model-generated Buddy lines in the window UI",
    )
    base_url: str = Field(
        default="",
        description="Buddy model base URL. Empty means reuse llm.base_url",
    )
    api_key: str = Field(
        default="",
        description="Buddy model API key. Empty means reuse the main LLM API key",
    )
    model_name: str = Field(
        default="",
        description="Buddy model name. Empty means reuse llm.model_name",
    )
    temperature: float = Field(
        default=0.9,
        ge=0.0,
        le=2.0,
        description="Buddy voice sampling temperature",
    )
    max_tokens: int = Field(
        default=80,
        ge=16,
        description="Maximum tokens for one Buddy voice line",
    )
    timeout: int = Field(
        default=8,
        ge=1,
        description="Buddy model timeout in seconds",
    )
    max_retries: int = Field(
        default=0,
        ge=0,
        description="Buddy model retry attempts",
    )
    proactive_enabled: bool = Field(
        default=True,
        description="Allow Buddy to proactively refresh its side-panel line while the window is idle",
    )
    proactive_interval_seconds: int = Field(
        default=90,
        ge=15,
        description="Seconds between proactive Buddy checks in window mode",
    )
    proactive_min_idle_seconds: int = Field(
        default=45,
        ge=5,
        description="Minimum idle seconds before Buddy may proactively speak",
    )


class MCPServerConfig(BaseModel):
    """Configuration for one MCP server."""

    command: str = Field(description="Command used to start the MCP server")
    args: list[str] = Field(default_factory=list, description="Arguments for the command")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    enabled: bool = Field(default=True, description="Whether this server is enabled")


class MCPConfig(BaseModel):
    """MCP integration configuration."""

    enabled: bool = Field(default=False, description="Enable MCP tool/resource bridge")
    servers: dict[str, MCPServerConfig] = Field(
        default_factory=dict,
        description="Named MCP servers to connect to",
    )


class ProjectConfig(BaseModel):
    """Project analysis configuration."""

    workspace_root: str = Field(
        default=".",
        description="Workspace root. Project tools cannot inspect paths outside this directory.",
    )
    allow_absolute_paths: bool = Field(
        default=False,
        description="Allow absolute paths outside workspace root. Intended only for tests or trusted local runs.",
    )
    ignore_patterns: list[str] = Field(
        default=[
            ".git",
            "node_modules",
            "__pycache__",
            ".idea",
            ".vscode",
            "*.pyc",
            "*.pyo",
            ".venv",
            "venv",
            "dist",
            "build",
        ],
        description="Patterns to ignore in project analysis",
    )
    max_context_files: int = Field(default=20, ge=1, description="Maximum files in context")


class Settings(BaseModel):
    """Code Agent settings.

    不使用 pydantic-settings，直接通过 YAML 文件加载配置。
    支持环境变量覆盖。
    """

    llm: LLMConfig = Field(default_factory=LLMConfig)
    shell: ShellConfig = Field(default_factory=ShellConfig)
    file: FileConfig = Field(default_factory=FileConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    buddy: BuddyConfig = Field(default_factory=BuddyConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    @classmethod
    def from_yaml(cls, path: Optional[Path] = None) -> "Settings":
        """Load settings from YAML file.

        只读取指定的 YAML 文件，不读取其他来源。
        环境变量 OPENAI_API_KEY 仍然会被读取。

        Args:
            path: YAML 文件路径，默认查找项目目录的 config.yaml

        Returns:
            Settings instance
        """
        if path is None:
            # 查找项目目录的 config.yaml
            path = Path.cwd() / "config.yaml"

        if not path.exists():
            return cls()

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)

    def to_yaml(self, path: Optional[Path] = None) -> None:
        """Save settings to YAML file.

        Args:
            path: YAML 文件路径
        """
        if path is None:
            path = Path.cwd() / "config.yaml"

        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, allow_unicode=True)

    def get_api_key(self) -> str:
        """Get API key with fallback to environment variable.

        优先级:
        1. 配置文件中的 api_key（如果不是 ${...} 格式）
        2. 环境变量 OPENAI_API_KEY

        Returns:
            API key string
        """
        key = self.llm.api_key

        # 如果是 ${VAR} 格式，从环境变量读取
        if key.startswith("${") and key.endswith("}"):
            var_name = key[2:-1]
            key = os.getenv(var_name, "")
        elif not key:
            key = os.getenv("OPENAI_API_KEY", "")

        return key

    def get_buddy_api_key(self) -> str:
        """Get Buddy model API key, falling back to the main model key."""
        key = self.buddy.api_key
        if key.startswith("${") and key.endswith("}"):
            key = os.getenv(key[2:-1], "")
        return key or self.get_api_key()

    def get_buddy_base_url(self) -> str:
        """Return Buddy model base URL, falling back to the main model URL."""
        return self.buddy.base_url or self.llm.base_url

    def get_buddy_model_name(self) -> str:
        """Return Buddy model name, falling back to the main model name."""
        return self.buddy.model_name or self.llm.model_name
