"""计划模式工具 - 控制计划模式的进入和退出

参考主流编码代理的计划/执行模式工作流。
允许 Agent 在计划模式和执行模式之间切换。
"""


from code_agent.tools.base import BaseTool, ToolPermission, ToolResult


class EnterPlanModeTool(BaseTool):
    """进入计划模式工具
    
    当 Agent 需要制定复杂计划时使用此工具。
    进入计划模式后，所有工具调用会被收集起来，
    最后由用户统一确认后再执行。
    """
    
    name = "enter_plan_mode"
    description = (
        "进入计划模式。此模式下 agent 会先制定详细计划，"
        "再等待用户确认后执行。适合多步骤复杂任务。"
    )
    parameters = {
        "reason": {
            "type": "string",
            "description": "进入计划模式的原因",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )
    
    async def execute(self, reason: str = "") -> ToolResult:
        """进入计划模式
        
        Args:
            reason: 进入计划模式的原因
            
        Returns:
            进入计划模式的结果
        """
        output = "已进入计划模式。\n"
        if reason:
            output += f"原因：{reason}\n"
        output += (
            "Agent 会先制定详细计划，再进入执行阶段。\n"
            "所有操作都会先汇总给你审阅。\n"
            "使用 exit_plan_mode 可退出计划模式。"
        )
        
        return ToolResult.ok(
            output,
            plan_mode=True,
            reason=reason,
        )


class ExitPlanModeTool(BaseTool):
    """退出计划模式工具
    
    当计划制定完成，准备执行时使用此工具。
    退出计划模式后，收集的操作会展示给用户确认。
    """
    
    name = "exit_plan_mode"
    description = (
        "退出计划模式并把计划展示给用户确认。"
        "用户可以审阅计划后选择执行或取消。"
    )
    parameters = {
        "execute": {
            "type": "boolean",
            "description": "是否立即执行计划，默认 false，等待用户确认",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )
    
    async def execute(self, execute: bool = False) -> ToolResult:
        """退出计划模式
        
        Args:
            execute: 是否立即执行计划
            
        Returns:
            退出计划模式的结果
        """
        output = "已退出计划模式。\n"
        
        if execute:
            output += "计划将开始执行。\n"
        else:
            output += (
                "计划已准备好审阅。\n"
                "请确认计划中的操作后再执行。"
            )
        
        return ToolResult.ok(
            output,
            plan_mode=False,
            execute=execute,
        )
