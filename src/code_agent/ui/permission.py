"""权限确认模块 - 处理工具执行前的用户确认

参考 Claude Code 的权限系统设计：
- 每个工具调用前检查权限
- 支持自动模式、计划模式、交互模式
- 记录用户的权限决策，支持记住选择
"""

from enum import Enum
from inspect import isawaitable
from typing import Any, Awaitable, Callable, Optional

from code_agent.tools.base import BaseTool, ToolPermission
from code_agent.ui.console import agent_console
from code_agent.utils.permissions import (
    DenialTracker,
    PermissionBehavior,
    PermissionDecision,
    PermissionRuleStore,
)
from code_agent.utils.bash_classifier import classify_bash_command


class PermissionMode(Enum):
    """权限确认模式"""

    INTERACTIVE = "interactive"
    DEFAULT = "default"
    AUTO = "auto"
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"
    DONT_ASK = "dont_ask"
    BYPASS = "bypass"


class PermissionManager:
    """管理工具执行前的权限确认
    
    核心功能：
    1. 根据工具权限配置决定是否需确认
    2. 支持多种确认模式
    3. 记住用户的选择（当前会话）
    4. 展示工具调用详情供用户决策
    """
    
    def __init__(
        self,
        mode: PermissionMode = PermissionMode.INTERACTIVE,
        rule_store: Optional[PermissionRuleStore] = None,
        input_callback: Optional[Callable[[str], str | Awaitable[str]]] = None,
    ):
        """初始化权限管理器
        
        Args:
            mode: 默认权限模式
        """
        self.mode = mode
        self.rule_store = rule_store
        self.input_callback = input_callback
        self.denials = DenialTracker()
        # 记住用户的决策：tool_name -> bool (True=允许, False=拒绝)
        self._remembered_decisions: dict[str, bool] = {}
        # 计划模式下待确认的批量操作
        self._pending_confirmations: list[dict] = []
        self._in_plan_mode = False
        # 保护工具调用显示和权限检查的原子性
        self._check_lock: Optional[Any] = None

    def _ensure_lock(self) -> Any:
        """延迟初始化锁（需要运行中的事件循环）"""
        if self._check_lock is None:
            import asyncio
            try:
                self._check_lock = asyncio.Lock()
            except RuntimeError:
                # 没有运行中的事件循环，返回None
                pass
        return self._check_lock
    
    async def check_permission(
        self,
        tool_name: str,
        tool_params: dict,
        permission: ToolPermission,
        tool: Optional[BaseTool] = None,
    ) -> bool:
        """检查是否允许执行工具
        
        Args:
            tool_name: 工具名称
            tool_params: 工具参数
            permission: 工具权限配置
            
        Returns:
            True 允许执行，False 拒绝执行
        """
        # 绕过模式（仅用于自动化测试）
        if self.mode == PermissionMode.BYPASS:
            self.denials.record_success(tool_name)
            return True

        is_read_only = (
            tool.is_read_only()
            if tool
            else not permission.require_confirmation and not permission.destructive
        )

        # Plan mode allows read-only inspection but blocks writes/execution.
        if self._in_plan_mode or self.mode == PermissionMode.PLAN:
            if is_read_only:
                return True
            self._pending_confirmations.append({
                "tool_name": tool_name,
                "tool_params": tool_params,
                "permission": permission,
            })
            self.denials.record_denial(tool_name)
            return False

        decision = self._rule_decision(tool_name, tool_params, permission, is_read_only)
        if decision.behavior == "allow":
            self.denials.record_success(tool_name)
            return True
        if decision.behavior == "deny":
            self.denials.record_denial(tool_name)
            agent_console.print_warning(f"权限已拒绝：{decision.reason}")
            if self.denials.should_fallback(tool_name):
                agent_console.print_warning(self.denials.message(tool_name))
            return False

        classifier_allowed = self._bash_classifier_decision(tool_name, tool_params, tool)
        if classifier_allowed is not None:
            return classifier_allowed

        # 如果不需要确认，直接允许
        if not permission.require_confirmation:
            self.denials.record_success(tool_name)
            return True
        
        # 自动模式只自动放行显式声明 allowed_in_auto_mode 的工具。
        # 这让 git_add、子 Agent、workflow 等“非 destructive 但仍需同意”的工具
        # 不会因为 auto mode 而绕过确认语义。
        if self.mode in (PermissionMode.AUTO, PermissionMode.DONT_ASK):
            if permission.require_confirmation and not permission.allowed_in_auto_mode:
                return await self._interactive_confirm(tool_name, tool_params, permission)
            self.denials.record_success(tool_name)
            return True

        if self.mode == PermissionMode.ACCEPT_EDITS and _looks_like_edit_tool(tool_name):
            self.denials.record_success(tool_name)
            return True
        
        # 交互模式：询问用户
        return await self._interactive_confirm(tool_name, tool_params, permission)

    def _bash_classifier_decision(
        self,
        tool_name: str,
        tool_params: dict[str, Any],
        tool: Optional[BaseTool],
    ) -> Optional[bool]:
        """Run a local bash classifier before the generic confirmation UI.

        返回 None 表示分类器认为仍需走普通确认流程；返回 bool 表示已经做出
        allow/deny 决策。分类器只处理 bash 工具，不影响其他工具。
        """
        if tool_name != "bash":
            return None

        command = str(tool_params.get("command") or "").strip()
        if not command:
            self.denials.record_denial(tool_name)
            agent_console.print_warning("Bash 分类器：命令为空，已拒绝。")
            return False

        config = getattr(tool, "config", None)
        decision = classify_bash_command(
            command,
            allowed_commands=getattr(config, "allowed_commands", None),
            blocked_commands=getattr(config, "blocked_commands", None),
        )
        agent_console.print_info(decision.render())
        if decision.should_deny:
            self.denials.record_denial(tool_name)
            return False
        if decision.should_allow:
            self.denials.record_success(tool_name)
            return True
        return None

    def _rule_decision(
        self,
        tool_name: str,
        tool_params: dict[str, Any],
        permission: ToolPermission,
        is_read_only: bool,
    ) -> PermissionDecision:
        if self.rule_store:
            return self.rule_store.decide(
                tool_name=tool_name,
                tool_params=tool_params,
                permission=permission,
                is_read_only=is_read_only,
            )
        if is_read_only:
            return PermissionDecision("allow", "read-only tool")
        return PermissionDecision("ask", "no rule store")
    
    async def _interactive_confirm(
        self,
        tool_name: str,
        tool_params: dict,
        permission: ToolPermission,
    ) -> bool:
        """交互式确认

        Args:
            tool_name: 工具名称
            tool_params: 工具参数
            permission: 工具权限配置

        Returns:
            用户是否允许
        """
        # 使用锁确保权限确认串行处理
        lock = self._ensure_lock()
        if lock:
            async with lock:
                return await self._do_interactive_confirm(tool_name, tool_params, permission)
        return await self._do_interactive_confirm(tool_name, tool_params, permission)

    async def _do_interactive_confirm(
        self,
        tool_name: str,
        tool_params: dict,
        permission: ToolPermission,
    ) -> bool:
        """执行交互式确认的实际逻辑"""
        # 检查是否有记住的决策
        if tool_name in self._remembered_decisions:
            allowed = self._remembered_decisions[tool_name]
            if allowed:
                self.denials.record_success(tool_name)
            else:
                self.denials.record_denial(tool_name)
            return allowed

        # 如果是破坏性操作，显示警告
        if permission.destructive:
            agent_console.print_warning("这是破坏性操作，请确认你确实要执行。")

        # 询问用户
        prompt = "是否允许执行？[Y=允许/n=拒绝/a=本会话总是允许/e=本会话总是拒绝/p=项目级允许/d=详情]: "
        while True:
            try:
                response = (await self._read_input(prompt)).strip().lower()

                if response in ("", "y", "yes"):
                    self.denials.record_success(tool_name)
                    return True
                elif response in ("n", "no"):
                    self.denials.record_denial(tool_name)
                    return False
                elif response in ("a", "always"):
                    # 记住允许此工具
                    self._remembered_decisions[tool_name] = True
                    if self.rule_store:
                        self.rule_store.add_rule(
                            tool=tool_name,
                            behavior="allow",
                            source="session",
                        )
                    self.denials.record_success(tool_name)
                    return True
                elif response in ("e", "never"):
                    # 记住拒绝此工具
                    self._remembered_decisions[tool_name] = False
                    if self.rule_store:
                        self.rule_store.add_rule(
                            tool=tool_name,
                            behavior="deny",
                            source="session",
                        )
                    self.denials.record_denial(tool_name)
                    return False
                elif response in ("p", "project"):
                    if self.rule_store:
                        self.rule_store.add_rule(
                            tool=tool_name,
                            behavior="allow",
                            source="project",
                        )
                    self.denials.record_success(tool_name)
                    return True
                elif response in ("d", "details"):
                    # 显示更多详情
                    self._show_details(tool_name, tool_params, permission)
                else:
                    agent_console.print_info("无效选择，请输入：Y / n / a / e / p / d")

            except (KeyboardInterrupt, EOFError):
                self.denials.record_denial(tool_name)
                return False
            except Exception as e:
                # 捕获所有其他异常（包括超时、取消等），默认拒绝
                agent_console.print_warning(f"权限确认出错：{e}，默认拒绝。")
                self.denials.record_denial(tool_name)
                return False

    async def _read_input(self, prompt: str) -> str:
        """读取权限确认输入，窗口模式可注入异步输入回调。"""
        if self.input_callback:
            value = self.input_callback(prompt)
            if isawaitable(value):
                value = await value
            return str(value)
        return agent_console.input(prompt)
    
    def _show_details(
        self,
        tool_name: str,
        tool_params: dict,
        permission: ToolPermission,
    ) -> None:
        """显示工具调用的详细信息
        
        Args:
            tool_name: 工具名称
            tool_params: 工具参数
            permission: 工具权限配置
        """
        details = f"""
[bold]工具:[/bold] {tool_name}
[bold]权限:[/bold] {permission}
[bold]破坏性:[/bold] {'是' if permission.destructive else '否'}
[bold]参数:[/bold]
"""
        for key, value in tool_params.items():
            details += f"  {key}: {value}\n"
        
        agent_console.console.print(details)
    
    def enter_plan_mode(self) -> None:
        """进入计划模式 - 记录被阻止的工具调用意图"""
        self._in_plan_mode = True
        self._pending_confirmations = []
        agent_console.print_info(
            "已进入计划模式。工具调用会被记录为计划备注，但不会执行。"
        )
    
    def exit_plan_mode(self) -> bool:
        """退出计划模式 - 展示计划模式中被阻止的工具调用
        
        Returns:
            True if plan mode exited
        """
        self._in_plan_mode = False
        
        if not self._pending_confirmations:
            agent_console.print_info("没有记录到被阻止的工具调用。")
            return True
        
        # 展示所有待确认操作
        agent_console.print_header(
            f"计划模式：{len(self._pending_confirmations)} 个被阻止的工具调用",
            "这些只是计划备注，不会自动重放执行"
        )
        
        for i, op in enumerate(self._pending_confirmations, 1):
            agent_console.console.print(f"\n[bold]{i}.[/bold] {op['tool_name']}")
            for key, value in op['tool_params'].items():
                agent_console.console.print(f"   {key}: {value}")
        
        self._pending_confirmations = []
        agent_console.print_info("计划模式已退出。请重新发送已批准的请求来执行。")
        return True
    
    def get_pending_operations(self) -> list[dict]:
        """获取待确认的操作列表（计划模式用）
        
        Returns:
            待确认操作列表
        """
        return self._pending_confirmations.copy()
    
    def clear_remembered(self) -> None:
        """清除所有记住的决策"""
        self._remembered_decisions.clear()
        if self.rule_store:
            self.rule_store.clear_session()
        agent_console.print_info("已清除本会话记住的权限决策")

    def add_rule(
        self,
        *,
        tool: str,
        behavior: PermissionBehavior,
        content: Optional[str] = None,
        project: bool = False,
    ) -> None:
        """Add a permission rule."""
        if not self.rule_store:
            self._remembered_decisions[tool] = behavior == "allow"
            return
        self.rule_store.add_rule(
            tool=tool,
            behavior=behavior,
            content=content,
            source="project" if project else "session",
        )

    def list_rules(self) -> list[dict[str, Any]]:
        """Return current rules for display."""
        if not self.rule_store:
            return [
                {"tool": tool, "behavior": "allow" if allowed else "deny", "source": "session"}
                for tool, allowed in self._remembered_decisions.items()
            ]
        return [
            {
                "tool": rule.tool,
                "behavior": rule.behavior,
                "content": rule.content or "",
                "source": rule.source,
            }
            for rule in self.rule_store.rules()
        ]


def _looks_like_edit_tool(tool_name: str) -> bool:
    return tool_name in {
        "write_file",
        "replace_code",
        "insert_code",
        "delete_code",
        "apply_diff",
    }
