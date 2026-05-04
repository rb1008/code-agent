"""Tests for core agent integration glue."""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage, ToolMessage

from code_agent.agent import core
from code_agent.agent.core import CodeAgent
from code_agent.agent.memory import Message
from code_agent.agent.prompts import SYSTEM_PROMPT
from code_agent.config.models import AgentConfig, LLMConfig, Settings
from code_agent.config.models import FileConfig
from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.tools.file import WriteFileTool
from code_agent.tools.registry import ToolRegistry
from code_agent.ui.permission import PermissionManager, PermissionMode
from code_agent.utils.cost_tracker import CostTracker
from code_agent.utils.plan import ApprovedPlan, PlanStore
from code_agent.utils.tool_hooks import ToolHookManager
from code_agent.utils.transcript import SessionTranscript


class DummyTypedTool(BaseTool):
    """Tool with required and optional parameters for schema conversion."""

    name = "dummy_typed"
    description = "Dummy typed tool"
    parameters = {
        "text": {"type": "string", "description": "Text value", "required": True},
        "count": {"type": "integer", "description": "Optional count", "required": False},
        "flag": {"type": "boolean", "description": "Optional flag", "required": False},
    }
    permission = ToolPermission(require_confirmation=False)

    async def execute(self, text: str, count: int = 1, flag: bool = False) -> ToolResult:
        return ToolResult.ok(f"{text}:{count}:{flag}")


def make_agent(
    registry: ToolRegistry | None = None,
    max_iterations: int = 4,
    project_instructions: str = "",
    agent_config: AgentConfig | None = None,
    transcript: SessionTranscript | None = None,
    hook_manager: ToolHookManager | None = None,
    plan_store: PlanStore | None = None,
) -> CodeAgent:
    """Create an agent with fake credentials and non-interactive permissions."""
    settings = Settings(
        llm=LLMConfig(api_key="test-key"),
        agent=agent_config or AgentConfig(max_iterations=max_iterations),
    )
    return CodeAgent(
        settings=settings,
        tool_registry=registry or ToolRegistry(),
        permission_manager=PermissionManager(PermissionMode.BYPASS),
        project_instructions=project_instructions,
        transcript=transcript,
        hook_manager=hook_manager,
        plan_store=plan_store,
    )


def test_convert_to_langchain_tools_uses_pydantic_v2_model() -> None:
    """Dynamic LangChain args schemas must work on Pydantic v2."""
    registry = ToolRegistry()
    registry.register(DummyTypedTool())
    agent = make_agent(registry)

    tools = agent._convert_to_langchain_tools()

    assert len(tools) == 1
    schema = tools[0].args_schema.model_json_schema()
    assert schema["required"] == ["text"]
    assert {"type": "integer"} in schema["properties"]["count"]["anyOf"]
    assert {"type": "boolean"} in schema["properties"]["flag"]["anyOf"]


@pytest.mark.asyncio
async def test_run_uses_current_langgraph_prompt_argument(monkeypatch) -> None:
    """LangGraph no longer accepts state_modifier; run should use prompt."""
    captured = {}
    CostTracker().clear()

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            return {
                "messages": [
                    AIMessage(
                        content="done",
                        usage_metadata={
                            "input_tokens": 12,
                            "output_tokens": 5,
                            "total_tokens": 17,
                        },
                    )
                ]
            }

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    agent = make_agent(max_iterations=3)

    result = await agent.run("hello")

    assert result == "done"
    assert captured["prompt"].startswith(SYSTEM_PROMPT)
    assert "state_modifier" not in captured["kwargs"]
    assert captured["config"] == {"recursion_limit": 7}
    assert captured["payload"]["messages"][-1].content == "hello"
    assert CostTracker().get_total_tokens()["total"] == 17


@pytest.mark.asyncio
async def test_run_writes_user_and_assistant_to_transcript(monkeypatch, tmp_path) -> None:
    """A session transcript should preserve the visible conversation."""

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            return {"messages": [AIMessage(content="done")]}

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    transcript = SessionTranscript(tmp_path / "session.jsonl")
    agent = make_agent(transcript=transcript)

    await agent.run("hello")

    kinds = [event.kind for event in transcript.tail(10)]
    assert kinds == ["user", "assistant"]


@pytest.mark.asyncio
async def test_run_stream_yields_chunks_and_records_final_state(monkeypatch, tmp_path) -> None:
    """Streaming should expose chunks early while memory/transcript keep the final answer."""
    captured = {}
    CostTracker().clear()

    class FakeCompiledAgent:
        async def astream(self, payload, config=None, stream_mode=None):
            captured["payload"] = payload
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            yield ("values", {"messages": []})
            yield (
                "messages",
                (AIMessageChunk(content="你"), {"langgraph_node": "agent"}),
            )
            yield (
                "messages",
                (AIMessageChunk(content="好"), {"langgraph_node": "agent"}),
            )
            yield (
                "values",
                {
                    "messages": [
                        AIMessage(
                            content="你好",
                            usage_metadata={
                                "input_tokens": 3,
                                "output_tokens": 2,
                                "total_tokens": 5,
                            },
                        )
                    ]
                },
            )

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        captured["prompt"] = prompt
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    transcript = SessionTranscript(tmp_path / "session.jsonl")
    agent = make_agent(transcript=transcript)

    chunks = [chunk async for chunk in agent.run_stream("hello")]

    assert chunks == ["你", "好"]
    assert captured["stream_mode"] == ["messages", "values"]
    assert captured["config"] == {"recursion_limit": 9}
    assert agent.memory.messages[-1].role == "assistant"
    assert agent.memory.messages[-1].content == "你好"
    assert [event.kind for event in transcript.tail(10)] == ["user", "assistant"]
    assert CostTracker().get_total_tokens()["total"] == 5


@pytest.mark.asyncio
async def test_run_stream_falls_back_to_final_value_when_provider_does_not_stream(monkeypatch) -> None:
    """A provider that only returns final values should still show a response."""

    class FakeCompiledAgent:
        async def astream(self, payload, config=None, stream_mode=None):
            yield ("values", {"messages": []})
            yield ("values", {"messages": [AIMessage(content="done")]})

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    agent = make_agent()

    chunks = [chunk async for chunk in agent.run_stream("hello")]

    assert chunks == ["done"]
    assert agent.memory.messages[-1].content == "done"


@pytest.mark.asyncio
async def test_tool_wrapper_writes_call_and_result_to_transcript(tmp_path) -> None:
    """Tool execution details should be available outside prompt memory."""
    transcript = SessionTranscript(tmp_path / "session.jsonl")
    agent = make_agent(transcript=transcript)
    tool = agent._create_langchain_tool(DummyTypedTool())

    result = await tool.ainvoke({"text": "hello", "count": 2})

    assert result == "hello:2:False"
    events = transcript.tail(10)
    assert [event.kind for event in events] == ["tool_call", "tool_result"]
    assert events[0].metadata["tool"] == "dummy_typed"
    assert events[1].metadata["success"] is True


@pytest.mark.asyncio
async def test_approved_write_file_tool_creates_file(tmp_path) -> None:
    """Interactive approval should let the agent create a workspace file."""
    registry = ToolRegistry()
    write_tool = WriteFileTool(FileConfig(workspace_root=str(tmp_path), allowed_extensions=[".py"]))
    registry.register(write_tool)
    agent = CodeAgent(
        settings=Settings(llm=LLMConfig(api_key="test-key")),
        tool_registry=registry,
        permission_manager=PermissionManager(
            PermissionMode.INTERACTIVE,
            input_callback=lambda _prompt: "y",
        ),
    )
    tool = agent._create_langchain_tool(write_tool)

    result = await tool.ainvoke({"path": "Dockerfile", "content": "FROM python:3.13\n"})

    assert "已写入" in result
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == "FROM python:3.13\n"


@pytest.mark.asyncio
async def test_pre_tool_hook_can_block_execution(tmp_path) -> None:
    """A failing pre_tool_use hook should prevent the tool from running."""
    hook_file = tmp_path / "hooks.yaml"
    hook_file.write_text(
        "hooks:\n"
        "  pre_tool_use:\n"
        "    - command: \"python -c 'raise SystemExit(2)'\"\n"
        "      continue_on_error: false\n",
        encoding="utf-8",
    )
    agent = make_agent(hook_manager=ToolHookManager(hook_file, cwd=tmp_path))
    tool = agent._create_langchain_tool(DummyTypedTool())

    result = await tool.ainvoke({"text": "hello"})

    assert "Pre-tool hook blocked" in result


@pytest.mark.asyncio
async def test_approved_plan_is_injected_once(monkeypatch, tmp_path) -> None:
    """Approved plans should guide exactly the next execution turn."""
    captured = {}

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            captured.setdefault("messages", []).append(payload["messages"][-1].content)
            return {"messages": [AIMessage(content="done")]}

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    store = PlanStore(tmp_path / "plan.md")
    agent = make_agent(plan_store=store)
    agent.approved_plan = ApprovedPlan("1. Edit file\n2. Run tests")

    await agent.run("go")
    await agent.run("next")

    assert "Approved plan" in captured["messages"][0]
    assert "Approved plan" not in captured["messages"][1]
    assert store.exists() is False


def test_system_prompt_includes_active_tool_guidance() -> None:
    """Prompt rendering should include compact guidance for active tools."""
    registry = ToolRegistry()
    registry.register(DummyTypedTool())
    agent = make_agent(registry=registry)

    prompt = agent._build_system_prompt()

    assert "# Tool Use" in prompt
    assert "dummy_typed" in prompt


@pytest.mark.asyncio
async def test_run_includes_project_instructions_in_system_prompt(monkeypatch) -> None:
    """Project-local instruction files should become bounded prompt context."""
    captured = {}

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            return {"messages": [AIMessage(content="done")]}

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        captured["prompt"] = prompt
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    agent = make_agent(project_instructions="### CODE_AGENT.md\n\nUse pytest and ruff.")

    await agent.run("hello")

    assert "## Project Instructions" in captured["prompt"]
    assert "Use pytest and ruff." in captured["prompt"]


@pytest.mark.asyncio
async def test_run_lets_agent_discover_skills_without_preloading_summaries(monkeypatch, tmp_path) -> None:
    """Prompt should expose skill discovery policy, not preselected skill content."""
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
    captured = {}

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            return {"messages": [AIMessage(content="done")]}

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        captured["prompt"] = prompt
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    agent = make_agent(
        agent_config=AgentConfig(skills_dir=str(tmp_path / "skills")),
    )

    await agent.run("请严格审查这个模块")

    assert "## Project Skill Discovery" in captured["prompt"]
    assert "2 project skills are available" in captured["prompt"]
    assert "discover_skills" in captured["prompt"]
    assert "use_skill" in captured["prompt"]
    assert "严格审查代码风险" not in captured["prompt"]
    assert "发布部署流程" not in captured["prompt"]


@pytest.mark.asyncio
async def test_run_records_tool_observations_as_memory_notes(monkeypatch) -> None:
    """Tool results should be retained without replaying invalid ToolMessage history."""
    captured = {}

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            captured["payload"] = payload
            return {
                "messages": [
                    ToolMessage(
                        content="README contents",
                        tool_call_id="call_1",
                        name="read_file",
                    ),
                    AIMessage(content="done"),
                ]
            }

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    agent = make_agent(max_iterations=3)

    await agent.run("read README")

    tool_messages = [msg for msg in agent.memory.messages if msg.role == "tool"]
    assert tool_messages
    assert tool_messages[0].metadata["tool"] == "read_file"
    assert tool_messages[0].metadata["tool_call_id"] == "call_1"

    history_messages = []
    for msg in agent.memory.messages:
        if msg.role == "tool":
            history_messages.append(
                SystemMessage(content=f"Previous tool observation from `{msg.metadata['tool']}`:\n{msg.content}")
            )

    assert isinstance(history_messages[0], SystemMessage)


@pytest.mark.asyncio
async def test_tool_observations_are_budgeted_before_memory_write(monkeypatch) -> None:
    """Large tool outputs should not be replayed in full on later turns."""

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            return {
                "messages": [
                    ToolMessage(
                        content="x" * 5000,
                        tool_call_id="call_1",
                        name="read_file",
                    ),
                    AIMessage(content="done"),
                ]
            }

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    agent = make_agent(
        agent_config=AgentConfig(
            max_iterations=3,
            max_tool_result_chars=1000,
        )
    )

    await agent.run("read a large file")

    tool_messages = [msg for msg in agent.memory.messages if msg.role == "tool"]
    assert tool_messages[0].metadata["truncated"] is True
    assert len(tool_messages[0].content) <= 1000
    assert "truncated" in tool_messages[0].content


def test_context_token_budget_triggers_proactive_compaction() -> None:
    """The agent should compact before a turn can exceed the context budget."""
    agent = make_agent(
        agent_config=AgentConfig(
            context_window=6,
            context_token_limit=1000,
            auto_compact_token_ratio=0.1,
        )
    )
    agent.memory.messages = [
        Message(role="user", content=f"message {i} " + "x" * 500)
        for i in range(10)
    ]

    did_compact = agent._compact_context_if_needed("system prompt")

    assert did_compact is True
    assert len(agent.memory.messages) == 3
    assert agent.memory.summary


@pytest.mark.asyncio
async def test_run_ultraplan_enters_plan_mode_and_uses_enhanced_prompt(monkeypatch) -> None:
    """Ultraplan should generate a plan under planning-only permissions."""
    captured = {}

    class FakeCompiledAgent:
        async def ainvoke(self, payload, config=None):
            captured["last_user"] = payload["messages"][-1].content
            return {"messages": [AIMessage(content="plan")]}

    def fake_create_react_agent(*, model, tools, prompt=None, **kwargs):
        captured["prompt"] = prompt
        return FakeCompiledAgent()

    monkeypatch.setattr(core, "create_react_agent", fake_create_react_agent)
    agent = make_agent()

    result = await agent.run_ultraplan("重构 CLI")

    assert result == "plan"
    assert agent.is_plan_mode is True
    assert "Ultraplan Mode" in captured["prompt"]
    assert "增强计划模式" in captured["last_user"]
