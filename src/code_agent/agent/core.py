"""Core Agent implementation for Code Agent.

实现 ReAct Agent 模式，参考 Claude Code 的 Agent Loop 设计：
- 用户输入 → 构建 Prompt → 发送给 LLM → 解析工具调用 → 执行工具 → 观察结果 → 循环
"""

from collections.abc import AsyncIterator
from typing import Any, Optional, cast

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool as LangChainBaseTool
from langgraph.prebuilt import create_react_agent
from pydantic import SecretStr

from code_agent.config.models import Settings
from code_agent.tools.base import BaseTool
from code_agent.tools.registry import ToolRegistry
from code_agent.agent.memory import ConversationMemory
from code_agent.agent.prompts import SYSTEM_PROMPT, PLAN_MODE_PROMPT, ULTRAPLAN_PROMPT, build_tool_prompt
from code_agent.ui.permission import PermissionManager, PermissionMode
from code_agent.ui.console import agent_console
from code_agent.utils.cost_tracker import CostTracker
from code_agent.utils.plan import ApprovedPlan, PlanStore, build_plan_execution_prefix
from code_agent.utils.ultraplan import build_ultraplan_request
from code_agent.utils.tool_hooks import ToolHookManager
from code_agent.utils.transcript import SessionTranscript
from code_agent.utils.token_budget import truncate_for_budget
from code_agent.utils.custom_commands import CustomCommandLoader
from code_agent.utils.skills import SkillLoader


class CodeAgent:
    """Main agent class that orchestrates the AI coding assistant.
    
    核心职责：
    1. 管理 LLM 连接（通过 LangChain）
    2. 维护工具注册表
    3. 执行 Agent Loop（思考-行动-观察循环）
    4. 管理会话记忆
    5. 处理用户确认（对于破坏性操作）
    6. 支持计划模式
    """
    
    def __init__(
        self,
        settings: Settings,
        tool_registry: Optional[ToolRegistry] = None,
        memory: Optional[ConversationMemory] = None,
        permission_manager: Optional[PermissionManager] = None,
        project_instructions: str = "",
        transcript: Optional[SessionTranscript] = None,
        hook_manager: Optional[ToolHookManager] = None,
        plan_store: Optional[PlanStore] = None,
    ):
        """Initialize the agent.
        
        Args:
            settings: Application settings including LLM config
            tool_registry: Registry of available tools
            memory: Conversation memory manager
            permission_manager: Permission manager for tool confirmations
        """
        self.settings = settings
        self.tool_registry = tool_registry or ToolRegistry()
        self.project_instructions = project_instructions.strip()
        self.transcript = transcript
        self.hook_manager = hook_manager
        self.plan_store = plan_store
        self.approved_plan: Optional[ApprovedPlan] = plan_store.load() if plan_store else None
        self.memory = memory or ConversationMemory(
            max_messages=settings.agent.context_window,
            compact_threshold=settings.agent.context_window - 5,
        )
        # 初始化权限管理器
        mode = PermissionMode.AUTO if settings.agent.auto_confirm else PermissionMode.INTERACTIVE
        self.permission_manager = permission_manager or PermissionManager(mode=mode)
        
        # 初始化 LLM
        self.llm = self._create_llm()
        
        # 计划模式状态
        self._plan_mode = False
        self._pre_plan_permission_mode: Optional[PermissionMode] = None
        self._turn_extension_context = ""
        self._ultraplan_mode = False
        
    def _create_llm(self) -> ChatOpenAI:
        """Create and configure the LLM client.
        
        Returns:
            Configured ChatOpenAI instance
        """
        llm_config = self.settings.llm
        
        return ChatOpenAI(
            base_url=llm_config.base_url,
            api_key=SecretStr(llm_config.api_key),
            model=llm_config.model_name,
            temperature=llm_config.temperature,
            max_completion_tokens=llm_config.max_tokens,
            timeout=llm_config.timeout,
            max_retries=llm_config.max_retries,
            default_headers={
                "User-Agent": "Code-Agent/0.1.0",
                "Accept": "application/json",
            },
        )
    
    def _convert_to_langchain_tools(self) -> list[LangChainBaseTool]:
        """Convert our tools to LangChain tools.
        
        Returns:
            List of LangChain-compatible tools
        """
        lc_tools = []
        
        tool_names = (
            self.tool_registry.list_active_tools()
            if getattr(self.settings.agent, "lazy_tool_loading_enabled", True)
            else self.tool_registry.list_tools()
        )
        for tool_name in tool_names:
            tool = self.tool_registry.get(tool_name)
            if tool:
                # 创建 LangChain 工具包装器（带权限检查）
                lc_tool = self._create_langchain_tool(tool)
                lc_tools.append(lc_tool)
                
        return lc_tools
    
    def _create_langchain_tool(self, tool: BaseTool) -> LangChainBaseTool:
        """Create a LangChain tool from our BaseTool.
        
        包装后的工具会在执行前调用权限管理器检查权限。
        
        Args:
            tool: Our base tool instance
            
        Returns:
            LangChain-compatible tool
        """
        from langchain_core.tools import StructuredTool
        from pydantic import Field, create_model

        if hasattr(tool, "memory") and getattr(tool, "memory") is None:
            setattr(tool, "memory", self.memory)
        if hasattr(tool, "permission_manager") and getattr(tool, "permission_manager") is None:
            setattr(tool, "permission_manager", self.permission_manager)
        
        # 动态创建参数模型
        fields = {}
        for param_name, param_info in tool.parameters.items():
            field_type: Any = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict,
            }.get(param_info.get("type"), str)

            description = param_info.get("description", "")
            if param_info.get("required", False):
                fields[param_name] = (field_type, Field(..., description=description))
            else:
                fields[param_name] = (Optional[field_type], Field(default=None, description=description))
        
        params_model_name = "".join(part.capitalize() for part in tool.name.split("_")) + "Params"
        ParamsModel = create_model(params_model_name, **cast(Any, fields))
        
        async def tool_func(**kwargs: Any) -> str:
            """Execute the tool with permission check."""
            clean_kwargs = {key: value for key, value in kwargs.items() if value is not None}
            summary = tool.get_tool_use_summary(clean_kwargs)
            return await self._execute_tool_lifecycle(tool, clean_kwargs, summary)
        
        return StructuredTool(
            name=tool.name,
            description=tool.description,
            func=tool_func,
            args_schema=ParamsModel,
            coroutine=tool_func,
        )

    async def _execute_tool_lifecycle(
        self,
        tool: BaseTool,
        clean_kwargs: dict[str, Any],
        summary: str,
    ) -> str:
        """Run one tool from display through permission, execution, hooks, and transcript."""
        agent_console.print_tool_call(tool.name, clean_kwargs, summary=summary)
        if self.transcript:
            self.transcript.append_tool_call(tool.name, clean_kwargs, summary=summary)

        hook_error = await self._run_tool_hooks("pre_tool_use", tool.name, clean_kwargs)
        if hook_error:
            agent_console.print_tool_result(False, hook_error)
            if self.transcript:
                self.transcript.append_tool_result(tool.name, False, hook_error)
            return hook_error

        has_permission = await self.permission_manager.check_permission(
            tool_name=tool.name,
            tool_params=clean_kwargs,
            permission=tool.permission,
            tool=tool,
        )
        if not has_permission:
            denied = f"权限已拒绝：{tool.name}"
            agent_console.print_tool_result(False, denied)
            if self.transcript:
                self.transcript.append_tool_result(tool.name, False, denied)
            await self._run_tool_hooks(
                "tool_error",
                tool.name,
                clean_kwargs,
                output=denied,
                success=False,
            )
            return denied

        try:
            result = await tool.execute(**clean_kwargs)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            agent_console.print_tool_result(False, error)
            if self.transcript:
                self.transcript.append_tool_result(tool.name, False, error)
            await self._run_tool_hooks(
                "tool_error",
                tool.name,
                clean_kwargs,
                output=error,
                success=False,
            )
            raise

        if result.success:
            agent_console.print_tool_result(True, result.output)
            if self.transcript:
                self.transcript.append_tool_result(tool.name, True, result.output)
            await self._run_tool_hooks(
                "post_tool_use",
                tool.name,
                clean_kwargs,
                output=result.output,
                success=True,
            )
            return result.output

        error = result.error or ""
        agent_console.print_tool_result(False, error)
        if self.transcript:
            self.transcript.append_tool_result(tool.name, False, error)
        await self._run_tool_hooks(
            "tool_error",
            tool.name,
            clean_kwargs,
            output=error,
            success=False,
        )
        return f"Error: {result.error}"
    
    async def run(self, user_input: str) -> str:
        """Run the agent with user input.
        
        这是核心的 Agent Loop：
        1. 将用户输入添加到记忆
        2. 构建完整的 Prompt（系统提示 + 历史 + 当前输入）
        3. 发送给 LLM
        4. 解析响应（工具调用或最终回答）
        5. 如果调用工具，执行并返回结果（带权限检查）
        6. 重复直到任务完成或达到最大迭代次数
        
        Args:
            user_input: User's natural language input
            
        Returns:
            Agent's final response
        """
        # 添加用户输入到记忆
        original_user_input = user_input
        self.memory.add("user", original_user_input)
        if self.transcript:
            self.transcript.append_user(original_user_input)

        if self.approved_plan and not self._plan_mode:
            user_input = (
                f"{build_plan_execution_prefix(self.approved_plan)}\n\n"
                f"Current user request:\n{original_user_input}"
            )
            self.approved_plan = None
            if self.plan_store:
                self.plan_store.clear()
        
        # 每轮请求先刷新“当前暴露给模型的工具集合”，避免把几十个 schema 一次性塞进上下文。
        if hasattr(self.tool_registry, "prepare_for_request"):
            always_include = list(self.settings.agent.always_active_tools)
            for name in ("discover_skills", "use_skill"):
                if name not in always_include:
                    always_include.append(name)
            self.tool_registry.prepare_for_request(
                original_user_input,
                always_include=always_include,
                limit=self.settings.agent.max_active_tools,
            )

        # 获取 LangChain 工具
        lc_tools = self._convert_to_langchain_tools()
        
        # 本轮只注入项目扩展的使用规则；具体技能由模型按需调用
        # discover_skills 再决定是否 use_skill，避免系统替模型预选技能。
        self._turn_extension_context = self._build_turn_extension_context(original_user_input)
        try:
            # 确定系统提示词
            system_prompt = self._build_system_prompt()
            self._compact_context_if_needed(system_prompt)

            # 使用 LangGraph 的 create_react_agent 创建 Agent
            agent = create_react_agent(
                model=self.llm,
                tools=lc_tools,
                prompt=system_prompt,
            )

            # 准备聊天历史
            messages: list[BaseMessage] = []
            if self.memory.summary:
                messages.append(SystemMessage(content=f"Previous conversation summary:\n{self.memory.summary}"))

            for msg in self.memory.messages[:-1]:  # 排除刚添加的用户消息
                if msg.role == "user":
                    messages.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    messages.append(AIMessage(content=msg.content))
                elif msg.role == "tool":
                    tool_name = msg.metadata.get("tool", "unknown")
                    messages.append(
                        SystemMessage(
                            content=(
                                f"Previous tool observation from `{tool_name}`:\n"
                                f"{msg.content}"
                            )
                        )
                    )

            # 添加当前用户输入
            messages.append(HumanMessage(content=user_input))

            # 执行 Agent
            recursion_limit = max(2, self.settings.agent.max_iterations * 2 + 1)
            result = await agent.ainvoke(
                {"messages": messages},
                config={"recursion_limit": recursion_limit},
            )
            
            # 获取最后一条 AI 消息
            output_messages = result.get("messages", [])
            self._record_usage(output_messages)
            self._record_tool_observations(output_messages)
            if output_messages:
                last_message = output_messages[-1]
                output_content = (
                    last_message.content if hasattr(last_message, "content") else str(last_message)
                )
                output = output_content if isinstance(output_content, str) else str(output_content)
            else:
                output = "模型没有返回可展示的回复。"
            
            # 添加助手响应到记忆
            self.memory.add("assistant", output)
            if self.transcript:
                self.transcript.append_assistant(output)
            
            return output

        except Exception as e:
            error_msg = f"Agent execution failed: {str(e)}"
            self.memory.add("assistant", error_msg)
            if self.transcript:
                self.transcript.append_assistant(error_msg, {"error": True})
            return error_msg
        finally:
            self._turn_extension_context = ""

    async def run_stream(self, user_input: str) -> AsyncIterator[str]:
        """Run the agent and yield assistant text as soon as model chunks arrive.

        LangGraph 的普通 `ainvoke` 只有最终状态，窗口模式会等到整轮结束才显示
        Agent 回复。这里使用 `stream_mode=["messages", "values"]`：
        - `messages` 提供模型 token/chunk，用于 UI 实时展示。
        - `values` 保留最终完整状态，用于记录 token、工具观察和长期记忆。
        """
        # 添加用户输入到记忆
        original_user_input = user_input
        self.memory.add("user", original_user_input)
        if self.transcript:
            self.transcript.append_user(original_user_input)

        if self.approved_plan and not self._plan_mode:
            user_input = (
                f"{build_plan_execution_prefix(self.approved_plan)}\n\n"
                f"Current user request:\n{original_user_input}"
            )
            self.approved_plan = None
            if self.plan_store:
                self.plan_store.clear()

        # 准备工具和系统提示
        if hasattr(self.tool_registry, "prepare_for_request"):
            always_include = list(self.settings.agent.always_active_tools)
            for name in ("discover_skills", "use_skill"):
                if name not in always_include:
                    always_include.append(name)
            self.tool_registry.prepare_for_request(
                original_user_input,
                always_include=always_include,
                limit=self.settings.agent.max_active_tools,
            )

        lc_tools = self._convert_to_langchain_tools()
        self._turn_extension_context = self._build_turn_extension_context(original_user_input)

        try:
            system_prompt = self._build_system_prompt()
            self._compact_context_if_needed(system_prompt)

            agent = create_react_agent(
                model=self.llm,
                tools=lc_tools,
                prompt=system_prompt,
            )

            # 准备消息
            messages: list[BaseMessage] = []
            if self.memory.summary:
                messages.append(SystemMessage(content=f"Previous conversation summary:\n{self.memory.summary}"))

            for msg in self.memory.messages[:-1]:
                if msg.role == "user":
                    messages.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    messages.append(AIMessage(content=msg.content))
                elif msg.role == "tool":
                    tool_name = msg.metadata.get("tool", "unknown")
                    messages.append(
                        SystemMessage(
                            content=(
                                f"Previous tool observation from `{tool_name}`:\n"
                                f"{msg.content}"
                            )
                        )
                    )

            messages.append(HumanMessage(content=user_input))

            recursion_limit = max(2, self.settings.agent.max_iterations * 2 + 1)
            streamed_parts: list[str] = []
            final_values: dict[str, Any] = {}

            async for event in agent.astream(
                {"messages": messages},
                config={"recursion_limit": recursion_limit},
                stream_mode=["messages", "values"],
            ):
                if not isinstance(event, tuple) or len(event) != 2:
                    continue
                mode, payload = event
                if mode == "values" and isinstance(payload, dict):
                    final_values = payload
                    continue
                if mode != "messages":
                    continue

                message = payload[0] if isinstance(payload, tuple) and payload else payload
                chunk_text = self._stream_chunk_text(message)
                if not chunk_text:
                    continue
                streamed_parts.append(chunk_text)
                yield chunk_text

            output_messages = final_values.get("messages", [])
            self._record_usage(output_messages)
            self._record_tool_observations(output_messages)

            streamed_text = "".join(streamed_parts)
            final_output = self._last_ai_text(output_messages) or streamed_text
            if not final_output:
                final_output = "模型没有返回可展示的回复。"

            if not streamed_text:
                yield final_output
            elif final_output.startswith(streamed_text) and final_output != streamed_text:
                yield final_output[len(streamed_text) :]
            elif final_output != streamed_text:
                # 极少数 provider 会在最终状态里修正流式文本；给 UI 一个明确校准。
                yield f"\n\n[最终回复]\n{final_output}"

            self.memory.add("assistant", final_output)
            if self.transcript:
                self.transcript.append_assistant(final_output)

        except Exception as e:
            error_msg = f"Agent execution failed: {str(e)}"
            self.memory.add("assistant", error_msg)
            if self.transcript:
                self.transcript.append_assistant(error_msg, {"error": True})
            yield error_msg  # 在异步生成器中使用yield而不是return
        finally:
            self._turn_extension_context = ""

    def _stream_chunk_text(self, message: Any) -> str:
        """Extract user-visible text from one model stream chunk."""
        if isinstance(message, ToolMessage):
            return ""
        if getattr(message, "tool_call_chunks", None) or getattr(message, "tool_calls", None):
            return ""
        return self._message_content_to_text(getattr(message, "content", ""))

    def _last_ai_text(self, output_messages: list[Any]) -> str:
        """Return the last visible AI message from a LangGraph final state."""
        for message in reversed(output_messages):
            if isinstance(message, AIMessage):
                text = self._message_content_to_text(message.content)
                if text:
                    return text
        return ""

    def _message_content_to_text(self, content: Any) -> str:
        """Normalize provider message content into plain display text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        if content is None:
            return ""
        return str(content)

    def _build_system_prompt(self) -> str:
        """Build the active system prompt with project instructions and plan mode."""
        base_prompt = self.settings.agent.system_prompt or SYSTEM_PROMPT
        sections = [base_prompt]
        tool_names = (
            self.tool_registry.list_active_tools()
            if getattr(self.settings.agent, "lazy_tool_loading_enabled", True)
            else self.tool_registry.list_tools()
        )
        active_tools = [
            tool
            for tool_name in tool_names
            if (tool := self.tool_registry.get(tool_name)) is not None
        ]
        if active_tools:
            sections.append(build_tool_prompt(active_tools))

        if self.project_instructions:
            sections.append(
                "## Project Instructions\n\n"
                "The following files are project-local guidance. Follow them when "
                "they do not conflict with higher-priority instructions.\n\n"
                f"{self.project_instructions}"
            )

        if self._turn_extension_context:
            sections.append(self._turn_extension_context)

        if self._plan_mode:
            sections.append(PLAN_MODE_PROMPT)
        if self._ultraplan_mode:
            sections.append(ULTRAPLAN_PROMPT)

        return "\n\n".join(sections)

    def _build_turn_extension_context(self, user_input: str) -> str:
        """Render only request-relevant skill/command metadata for this turn."""
        sections: list[str] = []

        skill_loader = SkillLoader(self.settings.agent.skills_dir)
        command_loader = CustomCommandLoader(self.settings.agent.commands_dir)

        available_skills = [
            skill for skill in skill_loader.load() if not skill.disable_model_invocation
        ]
        if available_skills:
            sections.append(
                "## Project Skill Discovery\n\n"
                f"{len(available_skills)} project skills are available. "
                "Do not guess or preload skill content. If a reusable skill may help this task, "
                "first call `discover_skills` with a concise task query, inspect the returned "
                "names/reasons, then call `use_skill` for at most one clearly relevant skill. "
                "If no skill is clearly relevant, continue without using skills."
            )

        commands = command_loader.matching(user_input, limit=5)
        if commands:
            listing = "\n".join(
                f"- /{command.name}: {command.description or command.argument_hint or 'custom workflow'}"
                for command in commands
            )
            sections.append(
                "## Relevant Project Commands\n\n"
                "These command summaries matched the current request and are shown as workflow hints only.\n\n"
                f"{listing}"
            )

        if (
            not sections
            and (
                any(not command.disable_model_invocation for command in command_loader.load())
            )
        ):
            sections.append(
                "## Project Extensions\n\n"
                "Project custom commands are available, but none were injected for this turn. "
                "Use `tool_search` only if the current task clearly needs an extension."
            )

        return "\n\n".join(sections)

    async def _run_tool_hooks(
        self,
        event: str,
        tool_name: str,
        tool_params: dict[str, Any],
        output: str = "",
        success: Optional[bool] = None,
    ) -> Optional[str]:
        """Run lifecycle hooks and return a blocking error when applicable."""
        if not self.hook_manager:
            return None
        results = await self.hook_manager.run(
            event,
            tool_name=tool_name,
            tool_params=tool_params,
            output=output,
            success=success,
        )
        for result in results:
            if self.transcript:
                self.transcript.append_event(
                    "tool_hook",
                    result.output,
                    {
                        "event": event,
                        "command": result.command,
                        "success": result.success,
                        "tool": tool_name,
                    },
                )
            if event == "pre_tool_use" and not result.success:
                return f"Pre-tool hook blocked `{tool_name}`: {result.output}"
        return None

    def _record_tool_observations(self, output_messages: list[Any]) -> None:
        """Store tool results as context notes without replaying protocol messages."""
        for message in output_messages:
            if not isinstance(message, ToolMessage):
                continue

            content = message.content if isinstance(message.content, str) else str(message.content)
            if not content:
                continue

            tool_name = getattr(message, "name", None) or "tool"
            tool_call_id = getattr(message, "tool_call_id", None)
            metadata: dict[str, Any] = {"tool": tool_name}
            if tool_call_id:
                metadata["tool_call_id"] = tool_call_id
            content, truncated = truncate_for_budget(
                content,
                self.settings.agent.max_tool_result_chars,
            )
            if truncated:
                metadata["truncated"] = True
            self.memory.add("tool", content, **metadata)

    def _compact_context_if_needed(self, system_prompt: str) -> bool:
        """Compact proactively when estimated prompt tokens approach the budget."""
        limit = self.settings.agent.context_token_limit
        threshold = int(limit * self.settings.agent.auto_compact_token_ratio)
        if threshold <= 0:
            return False

        model = self.settings.llm.model_name
        did_compact = False
        while len(self.memory.messages) > 2:
            estimated = self.memory.estimate_tokens(model=model, system_prompt=system_prompt)
            if estimated <= threshold:
                break
            before = len(self.memory.messages)
            self.memory._compact()
            did_compact = True
            if len(self.memory.messages) >= before:
                break
        return did_compact

    def _record_usage(self, output_messages: list[Any]) -> None:
        """Record token usage from LangChain messages when providers return it."""
        for message in output_messages:
            usage = getattr(message, "usage_metadata", None)
            response_metadata = getattr(message, "response_metadata", {}) or {}

            input_tokens = 0
            output_tokens = 0
            if isinstance(usage, dict):
                input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                output_tokens = int(
                    usage.get("output_tokens") or usage.get("completion_tokens") or 0
                )
            elif isinstance(response_metadata, dict):
                token_usage = response_metadata.get("token_usage", {})
                if isinstance(token_usage, dict):
                    input_tokens = int(token_usage.get("prompt_tokens") or 0)
                    output_tokens = int(token_usage.get("completion_tokens") or 0)

            if input_tokens or output_tokens:
                CostTracker().record_usage(
                    model=self.settings.llm.model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    operation="chat",
                )
    
    async def run_with_confirmation(
        self, 
        user_input: str,
        confirm_callback: Optional[Any] = None
    ) -> str:
        """Run agent with user confirmation for destructive operations.
        
        Args:
            user_input: User's input
            confirm_callback: Optional callback for confirmation
            
        Returns:
            Agent's response
        """
        # 如果设置了自动确认，直接运行
        if self.settings.agent.auto_confirm:
            return await self.run(user_input)
        
        # 否则，使用权限管理器运行
        return await self.run(user_input)
    
    def enter_plan_mode(self) -> None:
        """进入计划模式"""
        self._plan_mode = True
        self._pre_plan_permission_mode = self.permission_manager.mode
        self.permission_manager.mode = PermissionMode.PLAN
        self.permission_manager.enter_plan_mode()
        agent_console.print_info("已进入计划模式。Agent 会先给出计划，不会直接执行修改。")

    async def run_ultraplan(self, task: str) -> str:
        """Generate a deep, approval-first plan for a task."""
        if not self._plan_mode:
            self.enter_plan_mode()
        self._ultraplan_mode = True
        try:
            return await self.run(build_ultraplan_request(task))
        finally:
            self._ultraplan_mode = False
    
    def exit_plan_mode(self) -> None:
        """退出计划模式"""
        self._plan_mode = False
        if self.permission_manager.mode == PermissionMode.PLAN:
            self.permission_manager.mode = self._pre_plan_permission_mode or PermissionMode.INTERACTIVE
        self._pre_plan_permission_mode = None
        self.permission_manager.exit_plan_mode()
        agent_console.print_info("已退出计划模式。批准计划后，可再次发送执行请求。")

    def approve_last_plan(self, plan_text: Optional[str] = None) -> ApprovedPlan:
        """Approve the last assistant plan for the next execution turn."""
        content = plan_text
        if not content:
            for message in reversed(self.memory.messages):
                if message.role == "assistant" and message.content.strip():
                    content = message.content
                    break
        if not content:
            content = "没有找到明确的计划文本。请谨慎执行用户已批准的请求。"
        plan = ApprovedPlan(content=content, source="conversation")
        self.approved_plan = plan
        if self.plan_store:
            self.plan_store.save(plan)
        if self._plan_mode:
            self.exit_plan_mode()
        return plan

    def clear_approved_plan(self) -> None:
        """Clear the stored approved plan."""
        self.approved_plan = None
        if self.plan_store:
            self.plan_store.clear()
    
    def toggle_plan_mode(self) -> bool:
        """切换计划模式状态
        
        Returns:
            当前计划模式状态
        """
        if self._plan_mode:
            self.exit_plan_mode()
        else:
            self.enter_plan_mode()
        return self._plan_mode
    
    @property
    def is_plan_mode(self) -> bool:
        """是否处于计划模式"""
        return self._plan_mode
    
    def clear_memory(self) -> None:
        """Clear conversation memory."""
        self.memory.clear()
    
    def get_stats(self) -> dict[str, Any]:
        """Get agent statistics.
        
        Returns:
            Dictionary with agent stats
        """
        return {
            "model": self.settings.llm.model_name,
            "memory_messages": len(self.memory),
            "tools_available": len(self.tool_registry),
            "max_iterations": self.settings.agent.max_iterations,
            "plan_mode": self._plan_mode,
            "estimated_context_tokens": self.memory.estimate_tokens(
                model=self.settings.llm.model_name,
                system_prompt=self._build_system_prompt(),
            ),
        }
