"""Sub-agent orchestration helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from code_agent.agent.memory import ConversationMemory
from code_agent.config.models import Settings
from code_agent.ui.permission import PermissionManager, PermissionMode


@dataclass
class SubAgentResult:
    """Result returned by a sub-agent."""

    title: str
    output: str


class SubAgentRunner:
    """Create isolated child agents for fork/coordinator workflows."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(
        self,
        *,
        title: str,
        prompt: str,
        inherited_memory: Optional[ConversationMemory] = None,
        permission_manager: Optional[PermissionManager] = None,
    ) -> SubAgentResult:
        from code_agent.agent.core import CodeAgent
        from code_agent.tools import create_default_registry

        memory = ConversationMemory(
            max_messages=self.settings.agent.context_window,
            compact_threshold=self.settings.agent.context_window - 5,
        )
        if inherited_memory:
            memory.summary = inherited_memory.summary
            memory.messages = list(inherited_memory.messages)

        if permission_manager:
            child_permission_manager = PermissionManager(
                mode=permission_manager.mode,
                rule_store=permission_manager.rule_store,
                input_callback=permission_manager.input_callback,
            )
        else:
            child_permission_manager = PermissionManager(PermissionMode.INTERACTIVE)

        agent = CodeAgent(
            settings=self.settings,
            tool_registry=create_default_registry(self.settings),
            memory=memory,
            permission_manager=child_permission_manager,
            project_instructions=(
                "You are an isolated sub-agent. Complete only the assigned task, "
                "summarize evidence, and do not assume the parent can see your tool results."
            ),
        )
        output = await agent.run(prompt)
        return SubAgentResult(title=title, output=output)

    async def run_many(
        self,
        tasks: list[tuple[str, str]],
        inherited_memory: Optional[ConversationMemory] = None,
        permission_manager: Optional[PermissionManager] = None,
    ) -> list[SubAgentResult]:
        return await asyncio.gather(
            *[
                self.run(
                    title=title,
                    prompt=prompt,
                    inherited_memory=inherited_memory,
                    permission_manager=permission_manager,
                )
                for title, prompt in tasks
            ]
        )
