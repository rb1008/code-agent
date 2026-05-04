"""子 Agent 工具 - 创建子 Agent 执行特定子任务

参考主流编码代理的子任务隔离工作流实现。
允许 Agent 创建子 Agent 来处理独立的子任务，实现多 Agent 协作。
"""

from typing import Optional, TYPE_CHECKING

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult
from code_agent.config.models import Settings
from code_agent.ui.permission import PermissionManager, PermissionMode

if TYPE_CHECKING:
    pass


class AgentTool(BaseTool):
    """创建子 Agent 工具
    
    创建一个独立的子 Agent 来执行特定任务，
    子 Agent 有自己的记忆和工具集，可以并行工作。
    """
    
    name = "create_sub_agent"
    description = (
        "创建一个子 Agent 独立处理指定子任务。"
        "适合可并行或需要隔离上下文的工作；子 Agent 拥有相同工具但独立记忆。"
    )
    parameters = {
        "task": {
            "type": "string",
            "description": "子 Agent 需要完成的具体任务",
            "required": True,
        },
        "context": {
            "type": "string",
            "description": "给子 Agent 的额外上下文或要求",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=True, allowed_in_auto_mode=False, destructive=False
    )
    
    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings
        self.permission_manager: Optional[PermissionManager] = None
    
    async def execute(
        self,
        task: str,
        context: str = "",
    ) -> ToolResult:
        """创建子 Agent 并执行任务
        
        Args:
            task: 子任务描述
            context: 额外上下文
            
        Returns:
            子 Agent 的执行结果
        """
        try:
            if not self.settings:
                return ToolResult.fail("缺少 Settings，无法创建子 Agent。")
            
            # 延迟导入避免循环依赖
            from code_agent.agent.core import CodeAgent
            from code_agent.tools import create_default_registry

            if self.permission_manager:
                child_permission_manager = PermissionManager(
                    mode=self.permission_manager.mode,
                    rule_store=self.permission_manager.rule_store,
                    input_callback=self.permission_manager.input_callback,
                )
            else:
                child_permission_manager = PermissionManager(PermissionMode.INTERACTIVE)
            
            # 创建子 Agent（独立的记忆）
            sub_agent = CodeAgent(
                settings=self.settings,
                # 子 Agent 必须继承当前 settings；否则文件根目录、shell sandbox、
                # MCP、技能目录等会退回默认值，和主 Agent 的真实项目环境不一致。
                tool_registry=create_default_registry(self.settings),
                permission_manager=child_permission_manager,
            )
            
            # 构建完整的任务提示
            full_prompt = task
            if context:
                full_prompt = f"上下文：{context}\n\n任务：{task}"
            
            # 执行子任务
            result = await sub_agent.run(full_prompt)
            
            output = "子 Agent 已完成任务：\n"
            output += f"任务：{task}\n"
            output += f"结果：\n{result}"
            
            return ToolResult.ok(
                output,
                task=task,
                result=result,
            )
            
        except Exception as e:
            return ToolResult.fail(f"子 Agent 执行失败：{str(e)}")
