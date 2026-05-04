"""CLI entry point for Code Agent.

这是应用程序的主入口，负责：
1. 解析命令行参数
2. 加载配置
3. 初始化 Agent
4. 运行交互式会话或单次命令
"""

import asyncio
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Coroutine, Optional

import click
from langchain_core.messages import HumanMessage
from rich import box
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

from code_agent import __version__
from code_agent.agent.core import CodeAgent
from code_agent.config.models import Settings
from code_agent.tools import create_default_registry
from code_agent.tools.base import BaseTool, ToolResult
from code_agent.ui.console import agent_console
from code_agent.ui.chat import ChatInterface
from code_agent.ui.permission import PermissionManager, PermissionMode
from code_agent.utils.api_diagnostics import ApiDiagnosticError, ModelListResult, list_models
from code_agent.utils.buddy import BuddyRenderer, BuddyStore, update_buddy_state
from code_agent.utils.context_report import build_context_report
from code_agent.utils.custom_commands import CustomCommandLoader
from code_agent.utils.persistent_memory import PersistentMemory
from code_agent.utils.poor_mode import PoorModeState
from code_agent.utils.permissions import PermissionRuleStore
from code_agent.utils.permissions import PermissionBehavior
from code_agent.utils.plan import PlanStore
from code_agent.utils.project_instructions import ProjectInstructions, load_project_instructions
from code_agent.utils.skills import SkillLoader
from code_agent.utils.tool_hooks import ToolHookManager
from code_agent.utils.transcript import SessionTranscript
from code_agent.utils.ultraplan import contains_ultraplan_trigger, strip_ultraplan_trigger


@click.command(add_help_option=False)
@click.help_option("-h", "--help", help="显示帮助信息并退出。")
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=False),
    envvar="CODE_AGENT_CONFIG",
    help="配置文件路径",
)
@click.option(
    "--base-url",
    envvar="CODE_AGENT_LLM__BASE_URL",
    help="LLM API 基础 URL",
)
@click.option(
    "--api-key",
    envvar="CODE_AGENT_LLM__API_KEY",
    help="LLM API Key",
)
@click.option(
    "--model",
    "-m",
    envvar="CODE_AGENT_LLM__MODEL_NAME",
    help="要使用的模型名称",
)
@click.option(
    "--prompt",
    "-p",
    help="执行单次 prompt（非交互模式）",
)
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True),
    help="从文件读取 prompt",
)
@click.option(
    "--auto-confirm",
    is_flag=True,
    help="自动确认工具执行",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="启用详细输出",
)
@click.option(
    "--check-api",
    is_flag=True,
    help="发送最小请求检查 LLM API 配置并退出",
)
@click.option(
    "--list-models",
    "list_models_flag",
    is_flag=True,
    help="列出当前 OpenAI-compatible 服务端返回的模型并退出",
)
@click.option(
    "--doctor",
    is_flag=True,
    help="运行本地与服务商诊断并退出",
)
@click.option(
    "--window",
    "window_mode",
    is_flag=True,
    help="启动全屏窗口式终端 UI",
)
@click.version_option(version=__version__, prog_name="code-agent", help="显示版本并退出。")
def main(
    config: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    prompt: Optional[str],
    file: Optional[str],
    auto_confirm: bool,
    verbose: bool,
    check_api: bool,
    list_models_flag: bool,
    doctor: bool,
    window_mode: bool,
) -> None:
    """Code Agent - AI 驱动的 CLI 编码助手。

    使用示例：

    \b
    # 交互式模式（默认）
    code-agent

    \b
    # 单次执行模式
    code-agent -p "阅读 README 文件"

    \b
    # 指定模型和 API
    code-agent --base-url https://api.example.com/v1 --api-key sk-xxx --model gpt-4o

    \b
    # 从文件读取提示
    code-agent -f prompt.txt
    """
    # 加载配置
    settings = _load_settings(config)
    config_path = _find_config_path(config)
    _normalize_workspace_roots(settings, config_path)
    _normalize_project_feature_paths(settings, config_path)

    # 命令行参数覆盖配置
    if base_url:
        settings.llm.base_url = base_url
    if api_key:
        settings.llm.api_key = api_key
    if model:
        settings.llm.model_name = model
    if auto_confirm:
        settings.agent.auto_confirm = True
    if verbose:
        settings.agent.verbose = True

    if settings.llm.base_url.rstrip("/") == "https://api.nttd.ca":
        settings.llm.base_url = "https://api.nttd.ca/v1"
        agent_console.print_warning(
            "Adjusted base URL to https://api.nttd.ca/v1. "
            "OpenAI-compatible endpoints must include /v1."
        )

    # 验证 API key
    if not settings.get_api_key():
        agent_console.print_error(
            "API key is required. Set it via:\n"
            "  1. --api-key option\n"
            "  2. CODE_AGENT_LLM__API_KEY environment variable\n"
            "  3. OPENAI_API_KEY environment variable\n"
            "  4. Config file"
        )
        sys.exit(1)

    # 确保 API key 被设置
    settings.llm.api_key = settings.get_api_key()

    project_root = _project_root(config_path)
    poor_mode = _load_poor_mode(settings, config_path)
    settings.agent.poor_mode = poor_mode.active
    project_instructions = _load_project_instructions(settings, config_path)
    _load_mcp_config(settings, config_path)

    # 创建工具注册表
    tool_registry = create_default_registry(settings)
    permission_store = _create_permission_store(settings, config_path)
    hook_manager = _create_hook_manager(settings, config_path)
    plan_store = _create_plan_store(settings, config_path)
    permission_mode = PermissionMode.AUTO if settings.agent.auto_confirm else PermissionMode.INTERACTIVE
    permission_manager = PermissionManager(mode=permission_mode, rule_store=permission_store)

    # 创建 Agent
    agent = CodeAgent(
        settings=settings,
        tool_registry=tool_registry,
        permission_manager=permission_manager,
        project_instructions=project_instructions.content,
        hook_manager=hook_manager,
        plan_store=plan_store,
    )

    if check_api:
        _check_api(agent)
        return
    if list_models_flag:
        if not _print_models(settings):
            sys.exit(1)
        return
    if doctor:
        _print_doctor(settings, config_path, project_root, agent, project_instructions)
        return

    transcript = _create_session_transcript(settings, config_path)
    if transcript:
        agent.transcript = transcript
        transcript.append_system(
            "Session started",
            {"model": settings.llm.model_name, "cwd": str(Path.cwd())},
        )

    persistent_memory = _create_persistent_memory(settings, config_path)
    if persistent_memory and not settings.agent.poor_mode and persistent_memory.load_into(agent.memory):
        agent_console.print_info(f"已加载持久化记忆：{persistent_memory.path}")
    elif settings.agent.poor_mode:
        agent_console.print_info("穷鬼模式已开启：本次启动跳过持久化记忆加载。")
    if transcript:
        agent_console.print_info(f"会话记录文件：{transcript.path}")
    if project_instructions.sources:
        sources = ", ".join(path.relative_to(project_root).as_posix() for path in project_instructions.sources)
        agent_console.print_info(f"已加载项目指令：{sources}")
    if hook_manager and hook_manager.describe():
        agent_console.print_info(f"已加载工具 hooks：{hook_manager.path}")

    # 确定运行模式
    if prompt:
        # 单次执行模式
        _run_single(agent, prompt, persistent_memory)
    elif file:
        # 从文件读取提示
        with open(file, "r", encoding="utf-8") as f:
            file_prompt = f.read()
        _run_single(agent, file_prompt, persistent_memory)
    elif window_mode:
        _run_windowed(agent, persistent_memory, poor_mode)
    else:
        # 交互式模式
        _run_interactive(agent, persistent_memory, poor_mode)


def _run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """运行异步协程，兼容已有事件循环的环境。

    在 Jupyter/IPython/PyCharm Console 等已有事件循环的环境中，
    asyncio.run() 会报错。此函数自动检测环境并选择合适的方式运行协程。

    Args:
        coro: 异步协程对象

    Returns:
        协程的返回值
    """
    # 首先尝试 nest_asyncio 方案（最可靠）
    try:
        import nest_asyncio  # type: ignore[import-untyped]
        nest_asyncio.apply()
        return asyncio.run(coro)
    except ImportError:
        pass
    except RuntimeError:
        pass

    # 尝试标准 asyncio.run
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        if "cannot be called from a running event loop" not in str(e):
            raise

    # 已有事件循环在运行，使用线程方式执行
    import threading
    result: Any = None
    exception: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal result, exception
        try:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            result = new_loop.run_until_complete(coro)
            new_loop.close()
        except Exception as e:
            exception = e

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=300)
    if thread.is_alive():
        raise TimeoutError("Async operation did not finish within 300 seconds")
    if exception:
        raise exception
    return result


def _load_settings(config_path: Optional[str]) -> Settings:
    """Load settings from config file or create defaults.

    只读取项目目录下的 config.yaml，不读取外部配置文件。
    从当前文件位置向上查找项目根目录。

    Args:
        config_path: Path to config file

    Returns:
        Settings instance
    """
    resolved_config = _find_config_path(config_path)
    if resolved_config:
        return Settings.from_yaml(resolved_config)

    return Settings()


def _find_config_path(config_path: Optional[str]) -> Optional[Path]:
    """Find the config file from explicit path, cwd parents, or source checkout."""
    if config_path:
        path = Path(config_path).expanduser()
        if path.exists():
            return path
        raise click.ClickException(f"Config file not found: {config_path}")

    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / "config.yaml"
        if candidate.exists():
            return candidate

    current_file = Path(__file__).resolve()
    for directory in [current_file.parent, *current_file.parents]:
        candidate = directory / "config.yaml"
        if candidate.exists():
            return candidate

    return None


def _create_persistent_memory(
    settings: Settings,
    config_path: Optional[Path],
) -> Optional[PersistentMemory]:
    """Create project-local persistent memory manager if enabled."""
    if not settings.agent.persistent_memory_enabled:
        return None

    memory_path = Path(settings.agent.persistent_memory_path).expanduser()
    if not memory_path.is_absolute():
        memory_path = _project_root(config_path) / memory_path

    memory_dir = Path(settings.agent.persistent_memory_dir).expanduser()
    if not memory_dir.is_absolute():
        memory_dir = _project_root(config_path) / memory_dir

    return PersistentMemory(
        memory_path,
        max_chars=settings.agent.persistent_memory_max_chars,
        memory_dir=memory_dir,
        auto_dream_enabled=settings.agent.auto_dream_enabled,
        auto_dream_min_messages=settings.agent.auto_dream_min_messages,
    )


def _load_poor_mode(settings: Settings, config_path: Optional[Path]) -> PoorModeState:
    """Load project-local poor mode without touching config.yaml."""
    state_path = Path(settings.agent.poor_mode_path).expanduser()
    if not state_path.is_absolute():
        state_path = _project_root(config_path) / state_path
    return PoorModeState.load(state_path, default=settings.agent.poor_mode)


def _create_session_transcript(
    settings: Settings,
    config_path: Optional[Path],
) -> Optional[SessionTranscript]:
    """Create a project-local transcript writer if enabled."""
    if not settings.agent.transcript_enabled:
        return None

    transcript_dir = Path(settings.agent.transcript_dir).expanduser()
    if not transcript_dir.is_absolute():
        transcript_dir = _project_root(config_path) / transcript_dir

    return SessionTranscript.create_in_dir(
        transcript_dir,
        max_event_chars=settings.agent.transcript_max_event_chars,
    )


def _create_permission_store(settings: Settings, config_path: Optional[Path]) -> PermissionRuleStore:
    """Create the project-local permission rule store."""
    return PermissionRuleStore(
        _resolve_project_file(settings.agent.permission_settings_path, config_path)
    )


def _create_hook_manager(
    settings: Settings,
    config_path: Optional[Path],
) -> Optional[ToolHookManager]:
    """Create tool hook manager if a hook file exists."""
    path = _resolve_project_file(settings.agent.hook_settings_path, config_path)
    if not path.exists():
        return None
    return ToolHookManager(path, cwd=_project_root(config_path))


def _create_plan_store(settings: Settings, config_path: Optional[Path]) -> PlanStore:
    """Create approved-plan storage."""
    return PlanStore(_resolve_project_file(settings.agent.plan_store_path, config_path))


def _create_buddy_store(settings: Settings, config_path: Optional[Path]) -> BuddyStore:
    """Create project-local Buddy storage."""
    return BuddyStore(
        _resolve_project_file(settings.agent.buddy_settings_path, config_path),
        seed_text=f"{_project_root(config_path).resolve()}:{os.getenv('USER', '')}",
    )


def _resolve_project_file(path_text: str, config_path: Optional[Path]) -> Path:
    """Resolve a configured project-local file path."""
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    return _project_root(config_path) / path


def _load_mcp_config(settings: Settings, config_path: Optional[Path]) -> None:
    """Merge optional project-local MCP config into settings."""
    import yaml  # type: ignore[import-untyped]
    from code_agent.config.models import MCPConfig

    path = _resolve_project_file(settings.agent.mcp_config_path, config_path)
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings.mcp = MCPConfig(**data.get("mcp", data))


def _project_root(config_path: Optional[Path]) -> Path:
    """Return the project root used for project-local files."""
    return config_path.parent if config_path else Path.cwd()


def _load_project_instructions(
    settings: Settings,
    config_path: Optional[Path],
) -> ProjectInstructions:
    """Load project-local instructions configured for the active workspace."""
    return load_project_instructions(
        project_root=_project_root(config_path),
        file_names=settings.agent.project_instruction_files,
        max_chars=settings.agent.project_instruction_max_chars,
    )


def _normalize_workspace_roots(settings: Settings, config_path: Optional[Path]) -> None:
    """Resolve relative workspace roots against the project config directory."""
    if not config_path:
        return

    project_root = config_path.parent
    for section in (settings.shell, settings.file, settings.project):
        root = Path(section.workspace_root).expanduser()
        if not root.is_absolute():
            section.workspace_root = str((project_root / root).resolve())


def _normalize_project_feature_paths(settings: Settings, config_path: Optional[Path]) -> None:
    """Resolve project-local extension directories and UI state files."""
    project_root = _project_root(config_path)
    for attr in ("skills_dir", "commands_dir", "workflows_dir"):
        path = Path(getattr(settings.agent, attr)).expanduser()
        if not path.is_absolute():
            setattr(settings.agent, attr, str((project_root / path).resolve()))
    for attr in ("buddy_settings_path",):
        path = Path(getattr(settings.agent, attr)).expanduser()
        if not path.is_absolute():
            setattr(settings.agent, attr, str((project_root / path).resolve()))


def _build_project_extension_context(settings: Settings) -> str:
    """Render a tiny model-visible note about project extensions.

    完整技能/命令摘要不在启动时一次性注入；CodeAgent 会在每轮请求里按相关性
    挑选少量摘要，真正需要技能正文时再调用 `use_skill` 懒加载单个技能。
    """
    sections: list[str] = []
    skill_count = len([skill for skill in SkillLoader(settings.agent.skills_dir).load() if not skill.disable_model_invocation])
    if skill_count:
        sections.append(
            "## Project Skills\n\n"
            f"{skill_count} project skills are available through `list_skills` and `use_skill`. "
            "Only request-relevant skill summaries are injected per turn."
        )

    commands = [
        command
        for command in CustomCommandLoader(settings.agent.commands_dir).load()
        if not command.disable_model_invocation
    ]
    if commands:
        sections.append(
            "## Project Commands\n\n"
            f"{len(commands)} project slash commands are available. "
            "Only request-relevant command summaries are injected per turn."
        )

    return "\n\n".join(sections)


def _join_context(*parts: str) -> str:
    """Join optional prompt/context sections."""
    return "\n\n".join(part.strip() for part in parts if part.strip())


def _run_single(
    agent: CodeAgent,
    prompt: str,
    persistent_memory: Optional[PersistentMemory] = None,
) -> None:
    """Run a single prompt and exit.

    同步函数，内部使用 _run_sync 调用异步的 agent.run。

    Args:
        agent: CodeAgent instance
        prompt: User prompt
    """
    agent_console.print_info(f"正在执行：{prompt[:50]}...")

    _run_sync(_stream_agent_response_for_cli(agent, prompt))
    _save_persistent_memory(agent, persistent_memory)


async def _stream_agent_response_for_cli(agent: CodeAgent, prompt: str) -> str:
    """Render one assistant response as a live-updating Rich panel."""
    content = ""
    live: Live | None = None

    try:
        async for delta in agent.run_stream(prompt):
            if not delta:
                continue
            content += delta
            panel = _assistant_stream_panel(f"{content} ▌")
            if live is None:
                agent_console.console.print()
                live = Live(
                    panel,
                    console=agent_console.console,
                    refresh_per_second=12,
                    transient=False,
                )
                live.start()
            else:
                live.update(panel)
    finally:
        if live is not None:
            live.update(_assistant_stream_panel(content or "模型没有返回可展示的回复。"))
            live.stop()

    if not content:
        content = "模型没有返回可展示的回复。"
        agent_console.print_message("assistant", content)
    return content


def _assistant_stream_panel(content: str) -> Panel:
    """Build the streaming assistant panel used by normal CLI mode."""
    return Panel(
        Markdown(content or " "),
        title="[green bold]助手[/green bold]",
        title_align="left",
        border_style="assistant_box",
        box=box.SQUARE,
        padding=(0, 1),
    )


def _handle_project_command(
    agent: CodeAgent,
    command: str,
    args: list[str],
    persistent_memory: Optional[PersistentMemory] = None,
) -> bool:
    """Handle P1 project extension slash commands."""
    argument_text = " ".join(args)

    if command == "/skills":
        _print_skills(agent)
        return True
    if command == "/buddy":
        _handle_buddy_command(agent, args)
        return True
    if command == "/discover-skills":
        if not argument_text:
            agent_console.print_warning("用法：/discover-skills <任务或关键词>")
            return True
        result = _run_sync(
            _execute_tool_for_cli(
                agent,
                "discover_skills",
                {"query": argument_text, "limit": 8},
            )
        )
        _print_cli_tool_result(result)
        return True
    if command == "/skill":
        if not args:
            agent_console.print_warning("用法：/skill <技能名> [参数]")
            return True
        _run_skill(agent, args[0], " ".join(args[1:]), persistent_memory)
        return True
    if command == "/commands":
        _print_custom_commands(agent)
        return True
    if command == "/workflows":
        _print_cli_tool_result(_run_sync(_execute_tool_for_cli(agent, "workflow_list", {})))
        return True
    if command == "/workflow":
        if not args:
            agent_console.print_warning("用法：/workflow <脚本名> [参数]")
            return True
        result = _run_sync(
            _execute_tool_for_cli(
                agent,
                "workflow_run",
                {"name": args[0], "arguments": " ".join(args[1:])},
            )
        )
        _print_cli_tool_result(result)
        return True
    if command == "/monitor":
        if not argument_text:
            agent_console.print_warning("用法：/monitor <命令>")
            return True
        result = _run_sync(
            _execute_tool_for_cli(agent, "monitor_start", {"command": argument_text})
        )
        _print_cli_tool_result(result)
        return True
    if command == "/monitors":
        _print_cli_tool_result(_run_sync(_execute_tool_for_cli(agent, "monitor_list", {})))
        return True
    if command == "/monitor-read":
        if not args:
            agent_console.print_warning("用法：/monitor-read <任务ID>")
            return True
        result = _run_sync(
            _execute_tool_for_cli(agent, "monitor_read", {"monitor_id": args[0]})
        )
        _print_cli_tool_result(result)
        return True
    if command == "/monitor-stop":
        if not args:
            agent_console.print_warning("用法：/monitor-stop <任务ID>")
            return True
        result = _run_sync(
            _execute_tool_for_cli(agent, "monitor_stop", {"monitor_id": args[0]})
        )
        _print_cli_tool_result(result)
        return True
    if command == "/fork":
        if not argument_text:
            agent_console.print_warning("用法：/fork <任务>")
            return True
        with agent_console.status("正在运行派生子 Agent"):
            result = _run_sync(
                _execute_tool_for_cli(
                    agent,
                    "fork_agent",
                    {"task": argument_text, "title": "slash-fork"},
                )
            )
        _print_cli_tool_result(result)
        return True
    if command == "/coordinator":
        if not argument_text:
            agent_console.print_warning("用法：/coordinator <标题: 任务; 标题: 任务>")
            return True
        tasks = argument_text.replace(";", "\n")
        with agent_console.status("正在运行并行 worker"):
            result = _run_sync(_execute_tool_for_cli(agent, "coordinator_run", {"tasks": tasks}))
        _print_cli_tool_result(result)
        return True

    custom = CustomCommandLoader(agent.settings.agent.commands_dir).get(command)
    if custom and custom.user_invocable:
        _run_custom_command(agent, custom.name, argument_text, persistent_memory)
        return True

    return False


def _run_skill(
    agent: CodeAgent,
    name: str,
    arguments: str,
    persistent_memory: Optional[PersistentMemory] = None,
) -> None:
    loader = SkillLoader(agent.settings.agent.skills_dir)
    skill = loader.get(name)
    if not skill or not skill.user_invocable:
        agent_console.print_warning(f"未找到技能：{name}")
        agent_console.print_message("system", loader.listing())
        return
    _run_single(agent, skill.render(arguments), persistent_memory)


def _run_custom_command(
    agent: CodeAgent,
    name: str,
    arguments: str,
    persistent_memory: Optional[PersistentMemory] = None,
) -> None:
    loader = CustomCommandLoader(agent.settings.agent.commands_dir)
    command = loader.get(name)
    if not command or not command.user_invocable:
        agent_console.print_warning(f"未找到自定义命令：/{name}")
        return
    _run_single(agent, command.render(arguments), persistent_memory)


async def _execute_tool_for_cli(
    agent: CodeAgent,
    tool_name: str,
    params: dict[str, Any],
) -> ToolResult:
    """Execute a tool from a slash command with the same permission model."""
    tool = agent.tool_registry.get(tool_name)
    if not tool:
        return ToolResult.fail(f"未找到工具：{tool_name}")

    _bind_tool_runtime_context(agent, tool)
    clean_params = {key: value for key, value in params.items() if value is not None}
    summary = tool.get_tool_use_summary(clean_params)
    agent_console.print_tool_call(tool.name, clean_params, summary=summary)
    if agent.transcript:
        agent.transcript.append_tool_call(tool.name, clean_params, summary=summary)

    hook_error = await agent._run_tool_hooks("pre_tool_use", tool.name, clean_params)
    if hook_error:
        return ToolResult.fail(hook_error)

    allowed = await agent.permission_manager.check_permission(
        tool_name=tool.name,
        tool_params=clean_params,
        permission=tool.permission,
        tool=tool,
    )
    if not allowed:
        return ToolResult.fail(f"权限已拒绝：{tool.name}")

    try:
        result = await tool.execute(**clean_params)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        await agent._run_tool_hooks(
            "tool_error",
            tool.name,
            clean_params,
            output=error,
            success=False,
        )
        return ToolResult.fail(error)

    event = "post_tool_use" if result.success else "tool_error"
    output = result.output if result.success else result.error or result.output
    await agent._run_tool_hooks(
        event,
        tool.name,
        clean_params,
        output=output,
        success=result.success,
    )
    if agent.transcript:
        agent.transcript.append_tool_result(tool.name, result.success, output)
    return result


def _bind_tool_runtime_context(agent: CodeAgent, tool: BaseTool) -> None:
    """Give direct CLI tool calls the current runtime context when they need it."""
    if hasattr(tool, "memory") and getattr(tool, "memory") is None:
        setattr(tool, "memory", agent.memory)
    if hasattr(tool, "permission_manager") and getattr(tool, "permission_manager") is None:
        setattr(tool, "permission_manager", agent.permission_manager)


def _print_cli_tool_result(result: ToolResult) -> None:
    if result.success:
        agent_console.print_tool_result(True, result.output)
    else:
        agent_console.print_tool_result(False, result.error or result.output)


def _print_skills(agent: CodeAgent) -> None:
    loader = SkillLoader(agent.settings.agent.skills_dir)
    rows = [
        {
            "名称": skill.name,
            "说明": skill.description or skill.when_to_use or "-",
            "参数": skill.argument_hint or "-",
            "路径": skill.path,
        }
        for skill in loader.load()
        if skill.user_invocable
    ]
    if rows:
        agent_console.print_table(rows, title="项目技能")
    else:
        agent_console.print_info("没有找到项目技能。")


def _print_custom_commands(agent: CodeAgent) -> None:
    rows = CustomCommandLoader(agent.settings.agent.commands_dir).listing()
    if rows:
        agent_console.print_table(rows, title="项目自定义命令")
    else:
        agent_console.print_info("没有找到项目自定义命令。")


def _skill_names(agent: CodeAgent) -> list[str]:
    return [
        skill.name
        for skill in SkillLoader(agent.settings.agent.skills_dir).load()
        if skill.user_invocable
    ]


def _workflow_names(agent: CodeAgent) -> list[str]:
    workflows_dir = Path(agent.settings.agent.workflows_dir)
    if not workflows_dir.exists():
        return []
    return sorted(
        path.name
        for path in workflows_dir.iterdir()
        if path.is_file() and os.access(path, os.X_OK)
    )


def _custom_command_names(agent: CodeAgent) -> list[str]:
    return [
        command.name
        for command in CustomCommandLoader(agent.settings.agent.commands_dir).load()
        if command.user_invocable
    ]


async def _check_api_async(agent: CodeAgent) -> str:
    """Send a minimal LLM request without tools."""
    response = await agent.llm.ainvoke([HumanMessage(content="Reply with exactly: OK")])
    content = response.content if hasattr(response, "content") else str(response)
    return content if isinstance(content, str) else str(content)


def _check_api(agent: CodeAgent, exit_on_failure: bool = True) -> bool:
    """Verify the configured LLM endpoint with a minimal request."""
    try:
        response = _run_sync(_check_api_async(agent))
    except Exception as e:
        hint = ""
        if "model_dump" in str(e):
            hint = (
                "\n提示：服务商返回了非标准响应。请确认 base_url 包含 OpenAI-compatible "
                "/v1 路径，并确认所选模型被该服务商支持。"
            )
        agent_console.print_error(
            "API 检查失败。这是服务商/API 配置问题，不是工具执行问题。\n"
            f"{type(e).__name__}: {str(e)}{hint}"
        )
        if exit_on_failure:
            sys.exit(1)
        return False

    agent_console.print_success(
        f"API 检查通过，模型 {agent.settings.llm.model_name} 返回：{response.strip()}"
    )
    return True


def _probe_models(settings: Settings) -> ModelListResult:
    """List models using the active provider configuration."""
    return list_models(
        base_url=settings.llm.base_url,
        api_key=settings.get_api_key(),
        timeout=min(settings.llm.timeout, 30),
    )


def _print_models(settings: Settings) -> bool:
    """Print available provider model IDs."""
    try:
        result = _probe_models(settings)
    except ApiDiagnosticError as e:
        agent_console.print_error(f"模型列表获取失败：{e}")
        return False

    current = settings.llm.model_name
    rows = [
        {
            "Model ID": model,
            "当前": "是" if model == current else "",
        }
        for model in result.models
    ]
    agent_console.print_table(rows, title=f"来自 {result.base_url} 的模型")
    if result.contains(current):
        agent_console.print_success(f"当前配置模型可用：{current}")
    else:
        agent_console.print_warning(
            f"服务商模型列表未返回当前配置模型：{current}"
        )
    return result.contains(current)


def _run_interactive(
    agent: CodeAgent,
    persistent_memory: Optional[PersistentMemory] = None,
    poor_mode: Optional[PoorModeState] = None,
) -> None:
    """Run interactive chat session.

    同步函数，内部使用 _run_sync 调用异步的 agent.run。

    Args:
        agent: CodeAgent instance
    """
    # 创建聊天界面
    history_file = Path.home() / ".config" / "code-agent" / "history"
    chat = ChatInterface(
        history_file=str(history_file),
        skill_names=lambda: _skill_names(agent),
        workflow_names=lambda: _workflow_names(agent),
        custom_commands=lambda: _custom_command_names(agent),
        tool_names=lambda: agent.tool_registry.list_tools(),
        suggestions_enabled=not agent.settings.agent.poor_mode,
    )

    # 打印欢迎信息
    chat.print_welcome(
        version=__version__,
        model=agent.settings.llm.model_name,
        cwd=str(Path.cwd()),
        memory_path=str(persistent_memory.path) if persistent_memory else None,
        transcript_path=str(agent.transcript.path) if agent.transcript else None,
    )
    _print_runtime_status_line(agent, persistent_memory)

    # 主循环
    while True:
        try:
            # 获取用户输入
            user_input = chat.get_input()

            # 跳过空输入
            if not user_input.strip():
                continue

            # 处理特殊命令
            if chat.is_slash_command(user_input):
                command, args = chat.parse_command(user_input)

                if command in ("/exit", "/quit"):
                    agent_console.print_info("再见。")
                    break
                elif command == "/help":
                    chat.print_help()
                elif command == "/clear":
                    agent.clear_memory()
                    if persistent_memory:
                        persistent_memory.clear()
                    agent_console.print_success("对话记忆已清空")
                elif command == "/config":
                    _print_config(agent.settings)
                elif command == "/tools":
                    _print_tools(agent.tool_registry)
                elif command == "/tool-search":
                    _print_tool_search(agent.tool_registry, " ".join(args))
                elif command == "/status":
                    _print_status(agent)
                elif command == "/context":
                    _print_context(agent)
                elif command == "/model" and args:
                    agent.settings.llm.model_name = args[0]
                    # 重新创建 LLM
                    agent.llm = agent._create_llm()
                    agent_console.print_success(f"已切换模型：{args[0]}")
                elif command == "/compact":
                    agent.memory._compact()
                    agent_console.print_success("记忆已压缩")
                elif command == "/dream":
                    _run_dream(agent, persistent_memory)
                elif command == "/poor":
                    state = _toggle_poor_mode(agent, poor_mode)
                    chat.set_suggestions_enabled(not agent.settings.agent.poor_mode)
                    agent_console.print_info(state.render())
                elif command == "/plan":
                    is_plan = agent.toggle_plan_mode()
                    if is_plan:
                        agent_console.print_info("计划模式已开启")
                    else:
                        agent_console.print_info("计划模式已关闭")
                elif command == "/ultraplan":
                    if not args:
                        agent_console.print_warning("用法：/ultraplan <任务>")
                    else:
                        _run_ultraplan(agent, " ".join(args), persistent_memory)
                elif command == "/approve-plan":
                    plan = agent.approve_last_plan(" ".join(args) if args else None)
                    agent_console.print_success(
                        "计划已批准。你可以发送执行请求，或运行 /execute-plan。"
                    )
                    agent_console.print_message("system", plan.content)
                elif command == "/execute-plan":
                    if not agent.approved_plan:
                        agent_console.print_warning("没有待执行的已批准计划。")
                    else:
                        _run_sync(_stream_agent_response_for_cli(agent, "Execute the approved plan now."))
                        _save_persistent_memory(agent, persistent_memory)
                elif command == "/clear-plan":
                    agent.clear_approved_plan()
                    agent_console.print_success("已清除批准计划")
                elif command == "/permissions":
                    _print_permissions(agent)
                elif command in ("/allow", "/deny", "/allow-project", "/deny-project"):
                    _add_permission_rule(agent, command, args)
                elif command == "/hooks":
                    _print_hooks(agent)
                elif command == "/buddy":
                    _handle_buddy_command(agent, args)
                elif command == "/resume":
                    from code_agent.utils.session import SessionManager
                    session_manager = SessionManager()
                    loaded = session_manager.load_auto_save(agent.memory)
                    if loaded:
                        agent_console.print_success("已恢复上次会话")
                    else:
                        agent_console.print_warning("没有找到可恢复的会话")
                elif command == "/save":
                    from code_agent.utils.session import SessionManager
                    session_manager = SessionManager()
                    name = args[0] if args else None
                    session_manager.save(agent.memory, {"model": agent.settings.llm.model_name}, name)
                    agent_console.print_success(f"会话已保存：{name or 'auto_save'}")
                elif command == "/cost":
                    from code_agent.utils.cost_tracker import CostTracker
                    tracker = CostTracker()
                    agent_console.console.print(tracker.get_report())
                elif command == "/memory":
                    _print_memory(agent, persistent_memory)
                elif command == "/transcript":
                    _print_transcript(agent)
                elif command == "/export-transcript":
                    output_path = Path(args[0]).expanduser() if args else None
                    _export_transcript(agent, output_path)
                elif command == "/check-api":
                    _check_api(agent, exit_on_failure=False)
                elif command == "/models":
                    _print_models(agent.settings)
                elif command == "/doctor":
                    config_path = _find_config_path(None)
                    project_instructions = _load_project_instructions(agent.settings, config_path)
                    _print_doctor(
                        agent.settings,
                        config_path,
                        _project_root(config_path),
                        agent,
                        project_instructions,
                    )
                elif _handle_project_command(agent, command, args, persistent_memory):
                    pass
                else:
                    agent_console.print_warning(f"未知命令：{command}")

                continue

            # 处理普通用户输入 - 支持自然语言里触发 ultraplan。
            if contains_ultraplan_trigger(user_input):
                _run_ultraplan(agent, strip_ultraplan_trigger(user_input), persistent_memory)
                continue

            _run_sync(_stream_agent_response_for_cli(agent, user_input))
            _save_persistent_memory(agent, persistent_memory)

        except KeyboardInterrupt:
            agent_console.print_info("\n已中断。输入 /exit 退出。")
            continue
        except Exception as e:
            agent_console.print_error(f"错误：{str(e)}")
            if agent.settings.agent.verbose:
                agent_console.print_code(traceback.format_exc(), language="python", title="Traceback")
            continue


def _run_ultraplan(
    agent: CodeAgent,
    task: str,
    persistent_memory: Optional[PersistentMemory] = None,
) -> None:
    """Generate and display an enhanced approval-first plan."""
    with agent_console.status("正在生成 Ultraplan 增强计划"):
        response = _run_sync(agent.run_ultraplan(task))
    agent_console.print_message("assistant", response)
    agent_console.print_info("确认无误后运行 /approve-plan；需要改计划就直接指出修改点。")
    _save_persistent_memory(agent, persistent_memory)


def _run_windowed(
    agent: CodeAgent,
    persistent_memory: Optional[PersistentMemory] = None,
    poor_mode: Optional[PoorModeState] = None,
) -> None:
    """Run the full-screen terminal UI."""
    from code_agent.ui.window import WindowedChatInterface

    window = WindowedChatInterface(
        agent=agent,
        persistent_memory=persistent_memory,
        poor_mode=poor_mode,
    )
    _run_sync(window.run())


def _print_config(settings: Settings) -> None:
    """Print current configuration.

    Args:
        settings: Current settings
    """
    rows = [
        ("Base URL", settings.llm.base_url),
        ("模型", settings.llm.model_name),
        ("采样温度", settings.llm.temperature),
        ("最大输出 token", settings.llm.max_tokens),
        ("最大迭代次数", settings.agent.max_iterations),
        ("自动确认", settings.agent.auto_confirm),
        ("详细输出", settings.agent.verbose),
        ("上下文窗口", settings.agent.context_window),
        ("上下文 token 上限", settings.agent.context_token_limit),
        ("自动压缩比例", settings.agent.auto_compact_token_ratio),
        ("工具结果预算", f"{settings.agent.max_tool_result_chars} 字符"),
        ("持久化记忆", settings.agent.persistent_memory_enabled),
        ("穷鬼模式", settings.agent.poor_mode),
        ("穷鬼状态文件", settings.agent.poor_mode_path),
        ("记忆文件", settings.agent.persistent_memory_path),
        ("会话记录", settings.agent.transcript_enabled),
        ("会话记录目录", settings.agent.transcript_dir),
        ("权限设置", settings.agent.permission_settings_path),
        ("Hook 设置", settings.agent.hook_settings_path),
        ("计划存储", settings.agent.plan_store_path),
        ("Buddy 设置", settings.agent.buddy_settings_path),
        ("Buddy 独立模型", "开" if settings.buddy.enabled else "关"),
        ("Buddy 模型", settings.get_buddy_model_name() if settings.buddy.enabled else "-"),
        ("技能目录", settings.agent.skills_dir),
        ("命令目录", settings.agent.commands_dir),
        ("Workflow 目录", settings.agent.workflows_dir),
        ("Monitor 输出预算", f"{settings.agent.monitor_max_output_chars} 字符"),
        ("MCP 启用", settings.mcp.enabled),
        ("MCP 服务器", ", ".join(settings.mcp.servers.keys()) or "-"),
        ("项目指令文件", ", ".join(settings.agent.project_instruction_files)),
        ("Shell sandbox", settings.shell.sandbox_enabled),
        ("Sandbox 网络", settings.shell.sandbox_allow_network),
        ("Shell 工作区", settings.shell.workspace_root),
        ("Shell 超时", f"{settings.shell.timeout}s"),
        ("File 工作区", settings.file.workspace_root),
        ("最大文件大小", _format_bytes(settings.file.max_file_size)),
    ]
    agent_console.print_key_values(rows, title="当前配置")


def _print_tools(registry: Any) -> None:
    """Print available tools.

    Args:
        registry: Tool registry
    """
    table_data = []
    metadata = registry.list_metadata() if hasattr(registry, "list_metadata") else []
    for item in metadata:
        table_data.append({
            "名称": item.name,
            "状态": "已激活" if item.active else "按需",
            "说明": item.description[:50] + "..." if len(item.description) > 50 else item.description,
            "只读": "是" if item.read_only else "否",
            "破坏性": "是" if item.destructive else "否",
        })

    agent_console.print_table(table_data, title="可用工具")


def _print_tool_search(registry: Any, query: str) -> None:
    """Search tools by capability."""
    matches = registry.search_metadata(query) if hasattr(registry, "search_metadata") else []
    if query and hasattr(registry, "activate_matching"):
        registry.activate_matching(query, limit=8, pinned=True)
        matches = registry.search_metadata(query)
    rows = [
        {
            "名称": tool.name,
            "状态": "已激活" if tool.active else "按需",
            "提示": tool.search_hint or "-",
            "只读": "是" if tool.read_only else "否",
            "破坏性": "是" if tool.destructive else "否",
            "别名": ", ".join(tool.aliases) or "-",
        }
        for tool in matches
    ]
    title = f"工具搜索：{query or '全部'}"
    agent_console.print_table(rows, title=title)


def _print_status(agent: CodeAgent) -> None:
    """Print agent status.

    Args:
        agent: CodeAgent instance
    """
    stats = agent.get_stats()

    rows = [
        ("模型", stats["model"]),
        ("记忆消息数", stats["memory_messages"]),
        ("摘要字符数", len(agent.memory.summary)),
        ("可用工具数", stats["tools_available"]),
        ("最大迭代次数", stats["max_iterations"]),
        ("计划模式", stats["plan_mode"]),
        ("穷鬼模式", "开" if agent.settings.agent.poor_mode else "关"),
        ("已批准计划", "是" if agent.approved_plan else "否"),
        ("估算上下文 token", stats["estimated_context_tokens"]),
        ("项目指令", "已加载" if agent.project_instructions else "未找到"),
        ("会话记录", agent.transcript.path if agent.transcript else "未启用"),
    ]
    agent_console.print_key_values(rows, title="Agent 状态")


def _print_context(agent: CodeAgent) -> None:
    """Print context budget and memory pressure."""
    report = build_context_report(
        memory=agent.memory,
        model=agent.settings.llm.model_name,
        system_prompt=agent._build_system_prompt(),
        limit=agent.settings.agent.context_token_limit,
        threshold_ratio=agent.settings.agent.auto_compact_token_ratio,
    )
    rows = report.rows()
    rows.append(("工具结果预算", f"{agent.settings.agent.max_tool_result_chars} 字符"))
    agent_console.print_key_values(rows, title="上下文")


def _print_runtime_status_line(
    agent: CodeAgent,
    persistent_memory: Optional[PersistentMemory] = None,
) -> None:
    """Print a compact status line for the interactive UI."""
    used_tokens = agent.memory.estimate_tokens(
        model=agent.settings.llm.model_name,
        system_prompt=agent._build_system_prompt(),
    )
    memory_state = "暂停" if agent.settings.agent.poor_mode else ("开" if persistent_memory else "关")
    agent_console.print_status_line(
        model=agent.settings.llm.model_name,
        used_tokens=used_tokens,
        token_limit=agent.settings.agent.context_token_limit,
        tools=len(agent.tool_registry),
        plan_mode=agent.is_plan_mode,
        memory=memory_state,
    )


def _print_doctor(
    settings: Settings,
    config_path: Optional[Path],
    project_root: Path,
    agent: CodeAgent,
    project_instructions: ProjectInstructions,
) -> None:
    """Print local and provider diagnostics."""
    key = settings.get_api_key()
    memory_path = _create_persistent_memory(settings, config_path)
    transcript_dir = Path(settings.agent.transcript_dir).expanduser()
    if not transcript_dir.is_absolute():
        transcript_dir = project_root / transcript_dir
    instruction_sources = project_instructions.sources
    if not instruction_sources and agent.project_instructions:
        instruction_sources_display = "loaded"
    elif instruction_sources:
        instruction_sources_display = ", ".join(
            path.relative_to(project_root).as_posix() for path in instruction_sources
        )
    else:
        instruction_sources_display = "not found"

    rows: list[tuple[str, Any]] = [
        ("配置文件", config_path or "未找到"),
        ("项目根目录", project_root),
        ("当前工作目录", Path.cwd()),
        ("Base URL", settings.llm.base_url),
        ("模型", settings.llm.model_name),
        ("API key", _mask_secret(key) if key else "missing"),
        ("工具数", len(agent.tool_registry)),
        (
            "估算上下文 token",
            agent.memory.estimate_tokens(
                model=settings.llm.model_name,
                system_prompt=agent._build_system_prompt(),
            ),
        ),
        ("记忆文件", memory_path.path if memory_path else "未启用"),
        ("会话记录目录", transcript_dir if settings.agent.transcript_enabled else "未启用"),
        ("权限设置", _resolve_project_file(settings.agent.permission_settings_path, config_path)),
        ("Hooks", _resolve_project_file(settings.agent.hook_settings_path, config_path)),
        ("Buddy", _resolve_project_file(settings.agent.buddy_settings_path, config_path)),
        ("Buddy 独立模型", "开" if settings.buddy.enabled else "关"),
        ("Buddy 模型", settings.get_buddy_model_name() if settings.buddy.enabled else "-"),
        ("已批准计划", agent.approved_plan.source if agent.approved_plan else "无"),
        ("技能", f"{len(SkillLoader(settings.agent.skills_dir).load())} 个"),
        ("命令", f"{len(CustomCommandLoader(settings.agent.commands_dir).load())} 个"),
        ("Workflow 目录", settings.agent.workflows_dir),
        ("MCP", f"开（{len(settings.mcp.servers)} 个服务器）" if settings.mcp.enabled else "关"),
        ("项目指令文件", instruction_sources_display),
        ("Git 仓库内", "是" if _inside_git_repo(project_root) else "否"),
    ]

    try:
        model_result = _probe_models(settings)
        rows.extend(
            [
                ("服务商 /models", f"正常（{len(model_result.models)} 个模型）"),
                (
                    "当前模型在列表中",
                    "是" if model_result.contains(settings.llm.model_name) else "否",
                ),
            ]
        )
    except ApiDiagnosticError as e:
        rows.append(("服务商 /models", f"失败：{e}"))

    agent_console.print_key_values(rows, title="诊断")


def _inside_git_repo(path: Path) -> bool:
    """Return whether path appears to be inside a Git checkout."""
    current = path.resolve()
    for directory in [current, *current.parents]:
        if (directory / ".git").exists():
            return True
    return False


def _mask_secret(value: str) -> str:
    """Mask a secret for diagnostic output."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _print_memory(
    agent: CodeAgent,
    persistent_memory: Optional[PersistentMemory] = None,
) -> None:
    """Print current memory status and durable summary."""
    rows = [
        ("内存消息数", len(agent.memory.messages)),
        ("摘要字符数", len(agent.memory.summary)),
        ("持久化文件", persistent_memory.path if persistent_memory else "未启用"),
        ("穷鬼模式", "开" if agent.settings.agent.poor_mode else "关"),
        ("会话记录文件", agent.transcript.path if agent.transcript else "未启用"),
    ]
    agent_console.print_key_values(rows, title="记忆")
    if agent.memory.summary:
        agent_console.print_message("system", agent.memory.summary)
    else:
        agent_console.print_info("暂无记忆摘要。")


def _run_dream(agent: CodeAgent, persistent_memory: Optional[PersistentMemory]) -> None:
    """主动整理持久化记忆。"""
    if agent.settings.agent.poor_mode:
        agent_console.print_warning("穷鬼模式已开启，已暂停 /dream。运行 /poor 可恢复。")
        return
    if not persistent_memory:
        agent_console.print_warning("持久化记忆未启用，无法执行 /dream。")
        return
    result = persistent_memory.dream(agent.memory)
    agent_console.print_message("system", result.render())


def _toggle_poor_mode(
    agent: CodeAgent,
    poor_mode: Optional[PoorModeState],
) -> PoorModeState:
    """Toggle poor mode and persist it outside config.yaml."""
    if poor_mode is None:
        poor_mode = PoorModeState(Path(".code-agent/runtime.yaml"), active=agent.settings.agent.poor_mode)
    poor_mode.toggle()
    agent.settings.agent.poor_mode = poor_mode.active
    return poor_mode


def _save_persistent_memory(
    agent: CodeAgent,
    persistent_memory: Optional[PersistentMemory],
) -> bool:
    """Save memory unless poor mode has disabled optional persistence."""
    if not persistent_memory or agent.settings.agent.poor_mode:
        return False
    persistent_memory.save(agent.memory)
    result = persistent_memory.maybe_auto_dream(agent.memory)
    if result:
        agent_console.print_info(f"自动整理记忆完成：{result.path}")
    return True


def _print_permissions(agent: CodeAgent) -> None:
    """Print active permission rules."""
    rows = agent.permission_manager.list_rules()
    if not rows:
        agent_console.print_info("没有显式权限规则，当前使用默认策略。")
        return
    agent_console.print_table(rows, title="权限规则")


def _add_permission_rule(agent: CodeAgent, command: str, args: list[str]) -> None:
    """Add a session permission rule from a slash command."""
    if not args:
        agent_console.print_warning(f"用法：{command} <工具名> [匹配内容]")
        return
    tool = args[0]
    pattern = args[1] if len(args) > 1 else None
    behavior: PermissionBehavior = "allow" if "allow" in command else "deny"
    project = command.endswith("-project")
    agent.permission_manager.add_rule(
        tool=tool,
        behavior=behavior,
        content=pattern,
        project=project,
    )
    scope = "项目" if project else "本会话"
    action = "允许" if behavior == "allow" else "拒绝"
    agent_console.print_success(f"已添加{scope}规则：{action} {tool} {pattern or ''}".strip())


def _print_hooks(agent: CodeAgent) -> None:
    """Print configured lifecycle hooks."""
    if not agent.hook_manager:
        agent_console.print_info("未加载 hook 文件。")
        return
    rows = agent.hook_manager.describe()
    if not rows:
        agent_console.print_info(f"{agent.hook_manager.path} 中没有配置 hook。")
        return
    agent_console.print_table(rows, title=f"工具 Hooks：{agent.hook_manager.path}")


def _handle_buddy_command(agent: CodeAgent, args: list[str]) -> None:
    """Manage the project-local Buddy companion from normal CLI mode."""
    config_path = _find_config_path(None)
    store = _create_buddy_store(agent.settings, config_path)
    renderer = BuddyRenderer()
    action = args[0].lower() if args else "show"

    if action in ("show", "hatch", "on"):
        state = store.hatch()
        update_buddy_state(
            state,
            "idle",
            detail="普通 CLI 模式显示卡片；右下角常驻 UI 请用 --window。",
        )
        store.save(state)
        agent_console.print_message("system", renderer.render_card(state))
        return
    if action == "card":
        agent_console.print_message("system", renderer.render_card(store.ensure()))
        return
    interactions = {
        "pet": ("pet", "互动完成"),
        "摸摸": ("pet", "互动完成"),
        "cheer": ("cheer", "打气完成"),
        "加油": ("cheer", "打气完成"),
        "joke": ("joke", "讲笑话完成"),
        "笑话": ("joke", "讲笑话完成"),
        "roast": ("roast", "轻吐槽完成"),
        "吐槽": ("roast", "轻吐槽完成"),
        "snack": ("snack", "投喂完成"),
        "投喂": ("snack", "投喂完成"),
        "喂食": ("snack", "投喂完成"),
        "chat": ("chat", "主动聊天"),
        "聊聊": ("chat", "主动聊天"),
        "说话": ("chat", "主动聊天"),
    }
    if action in interactions:
        mood, detail = interactions[action]
        if action in {"chat", "聊聊", "说话"} and len(args) > 1:
            detail = "聊：" + " ".join(args[1:])[:36]
        state = store.ensure()
        state.enabled = True
        update_buddy_state(state, mood, detail=detail)
        store.save(state)
        agent_console.print_message("system", renderer.render_panel(state))
        return
    if action == "mute":
        state = store.ensure()
        state.enabled = True
        state.muted = True
        store.save(state)
        agent_console.print_success("Buddy 已静音，动作仍会更新。")
        return
    if action == "unmute":
        state = store.ensure()
        state.enabled = True
        state.muted = False
        store.save(state)
        agent_console.print_success("Buddy 已恢复语言。")
        return
    if action == "off":
        state = store.ensure()
        state.enabled = False
        store.save(state)
        agent_console.print_success("Buddy 已关闭。再次输入 /buddy 可开启。")
        return
    if action == "reset":
        state = store.hatch(reset=True)
        agent_console.print_message("system", renderer.render_card(state))
        return

    agent_console.print_warning("用法：/buddy [hatch|card|pet|cheer|joke|roast|snack|chat|mute|unmute|off|reset]")


def _print_transcript(agent: CodeAgent) -> None:
    """Print transcript path and recent events."""
    if not agent.transcript:
        agent_console.print_warning("会话记录未启用。")
        return

    rows: list[tuple[str, Any]] = [
        ("文件", agent.transcript.path),
        ("最近事件数", len(agent.transcript.tail(20))),
    ]
    agent_console.print_key_values(rows, title="会话记录")
    events = agent.transcript.tail(6)
    if not events:
        agent_console.print_info("暂无会话记录事件。")
        return

    preview = "\n".join(
        f"{event.kind}: {_clip_inline(event.content, 120)}"
        for event in events
    )
    agent_console.print_message("system", preview)


def _export_transcript(agent: CodeAgent, output_path: Optional[Path] = None) -> None:
    """Export the active transcript as Markdown."""
    if not agent.transcript:
        agent_console.print_warning("会话记录未启用。")
        return

    exported = agent.transcript.export_markdown(output_path)
    agent_console.print_success(f"会话记录已导出：{exported}")


def _format_bytes(size: int) -> str:
    """Format bytes for CLI output."""
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _clip_inline(text: str, max_chars: int) -> str:
    """Return one-line bounded text for status previews."""
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "..."


if __name__ == "__main__":
    main()
