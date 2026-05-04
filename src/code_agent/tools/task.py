"""任务管理工具 - 创建和跟踪子任务

参考主流编码代理的任务拆分和进度跟踪工作流实现。
允许 Agent 将复杂任务分解为可追踪的子任务。
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"         # 待处理
    IN_PROGRESS = "in_progress" # 进行中
    COMPLETED = "completed"     # 已完成
    FAILED = "failed"           # 失败
    CANCELLED = "cancelled"     # 已取消


@dataclass
class Task:
    """任务数据类"""
    id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    result: Optional[str] = None
    parent_id: Optional[str] = None  # 父任务ID，支持任务层级


class TaskManager:
    """任务管理器 - 管理所有子任务
    
    单例模式，在整个会话中共享任务状态。
    """
    _instance: ClassVar[Optional["TaskManager"]] = None
    _tasks: dict[str, Task]
    
    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tasks = {}
        return cls._instance
    
    def create(
        self,
        title: str,
        description: str = "",
        parent_id: Optional[str] = None,
    ) -> Task:
        """创建新任务
        
        Args:
            title: 任务标题
            description: 任务描述
            parent_id: 父任务ID
            
        Returns:
            创建的任务
        """
        task_id = str(uuid.uuid4())[:8]  # 短ID便于显示
        task = Task(
            id=task_id,
            title=title,
            description=description,
            parent_id=parent_id,
        )
        self._tasks[task_id] = task
        return task
    
    def update(
        self,
        task_id: str,
        status: Optional[str] = None,
        result: Optional[str] = None,
    ) -> Optional[Task]:
        """更新任务状态
        
        Args:
            task_id: 任务ID
            status: 新状态
            result: 结果描述
            
        Returns:
            更新后的任务，如果未找到则返回 None
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        
        if status:
            task.status = TaskStatus(status)
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                task.completed_at = datetime.now().isoformat()
        
        if result:
            task.result = result
        
        return task
    
    def get(self, task_id: str) -> Optional[Task]:
        """获取任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务对象或 None
        """
        return self._tasks.get(task_id)
    
    def list_all(self) -> list[Task]:
        """获取所有任务列表
        
        Returns:
            任务列表
        """
        return list(self._tasks.values())
    
    def list_by_status(self, status: TaskStatus) -> list[Task]:
        """按状态筛选任务
        
        Args:
            status: 目标状态
            
        Returns:
            符合条件的任务列表
        """
        return [t for t in self._tasks.values() if t.status == status]
    
    def clear(self) -> None:
        """清除所有任务"""
        self._tasks.clear()
    
    def get_stats(self) -> dict[str, int]:
        """获取任务统计
        
        Returns:
            各状态任务数量
        """
        stats = {"total": len(self._tasks)}
        for status in TaskStatus:
            stats[status.value] = len(self.list_by_status(status))
        return stats


class TaskCreateTool(BaseTool):
    """创建新任务工具"""
    
    name = "task_create"
    aliases = ["todo_create", "create_task"]
    search_hint = "创建 跟踪 任务 todo"
    description = (
        "创建一个可跟踪的子任务，用于记录复杂任务中的具体步骤。"
        "返回任务 ID，后续可用它更新状态。"
    )
    parameters = {
        "title": {
            "type": "string",
            "description": "任务短标题",
            "required": True,
        },
        "description": {
            "type": "string",
            "description": "任务需要完成内容的详细描述",
            "required": False,
        },
        "parent_id": {
            "type": "string",
            "description": "父任务 ID，可选",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )
    
    def __init__(self) -> None:
        super().__init__()
        self.task_manager = TaskManager()
    
    async def execute(
        self,
        title: str,
        description: str = "",
        parent_id: Optional[str] = None,
    ) -> ToolResult:
        """创建新任务
        
        Args:
            title: 任务标题
            description: 任务描述
            parent_id: 父任务ID
            
        Returns:
            创建结果
        """
        try:
            task = self.task_manager.create(
                title=title,
                description=description,
                parent_id=parent_id,
            )
            
            output = f"✅ Task created: [{task.id}] {task.title}\n"
            if task.description:
                output += f"   Description: {task.description}\n"
            if task.parent_id:
                output += f"   Parent: {task.parent_id}\n"
            
            return ToolResult.ok(
                output,
                task_id=task.id,
                title=task.title,
            )
            
        except Exception as e:
            return ToolResult.fail(f"Failed to create task: {str(e)}")


class TaskUpdateTool(BaseTool):
    """更新任务状态工具"""
    
    name = "task_update"
    aliases = ["todo_update", "update_task"]
    search_hint = "更新 任务 todo 状态"
    description = (
        "更新已有任务的状态，可标记为待处理、进行中、完成、失败或取消。"
        "状态值：pending、in_progress、completed、failed、cancelled"
    )
    parameters = {
        "task_id": {
            "type": "string",
            "description": "要更新的任务 ID",
            "required": True,
        },
        "status": {
            "type": "string",
            "description": "新状态：pending、in_progress、completed、failed、cancelled",
            "required": True,
        },
        "result": {
            "type": "string",
            "description": "任务结果或备注",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )
    
    def __init__(self) -> None:
        super().__init__()
        self.task_manager = TaskManager()
    
    async def execute(
        self,
        task_id: str,
        status: str,
        result: str = "",
    ) -> ToolResult:
        """更新任务状态
        
        Args:
            task_id: 任务ID
            status: 新状态
            result: 结果描述
            
        Returns:
            更新结果
        """
        try:
            # 验证状态值
            try:
                TaskStatus(status)
            except ValueError:
                valid_statuses = [s.value for s in TaskStatus]
                return ToolResult.fail(
                    f"Invalid status: {status}. Valid options: {valid_statuses}"
                )
            
            task = self.task_manager.update(
                task_id=task_id,
                status=status,
                result=result or None,
            )
            
            if not task:
                return ToolResult.fail(f"未找到任务：{task_id}")
            
            # 状态对应的图标
            icons = {
                "pending": "⏳",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }
            
            output = f"{icons.get(status, '•')} Task [{task.id}] updated: {status}\n"
            output += f"   Title: {task.title}\n"
            if result:
                output += f"   Result: {result}\n"
            
            return ToolResult.ok(
                output,
                task_id=task.id,
                status=task.status.value,
            )
            
        except Exception as e:
            return ToolResult.fail(f"Failed to update task: {str(e)}")


class TaskListTool(BaseTool):
    """列出所有任务工具"""
    
    name = "task_list"
    aliases = ["todo_list", "list_tasks"]
    search_hint = "列出 跟踪 任务 todo"
    description = (
        "列出所有任务及当前状态，用于查看子任务整体进度。"
    )
    parameters = {
        "status": {
            "type": "string",
            "description": "按状态筛选，可选",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )
    
    def __init__(self) -> None:
        super().__init__()
        self.task_manager = TaskManager()
    
    async def execute(self, status: str = "") -> ToolResult:
        """列出任务
        
        Args:
            status: 可选的状态筛选
            
        Returns:
            任务列表
        """
        try:
            if status:
                try:
                    task_status = TaskStatus(status)
                    tasks = self.task_manager.list_by_status(task_status)
                except ValueError:
                    valid = [s.value for s in TaskStatus]
                    return ToolResult.fail(f"Invalid status filter. Valid: {valid}")
            else:
                tasks = self.task_manager.list_all()
            
            if not tasks:
                return ToolResult.ok("No tasks found.")
            
            # 状态图标
            icons = {
                "pending": "⏳",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }
            
            output = f"Tasks ({len(tasks)} total):\n"
            output += "=" * 50 + "\n\n"
            
            for task in tasks:
                icon = icons.get(task.status.value, "•")
                output += f"{icon} [{task.id}] {task.title}\n"
                output += f"   Status: {task.status.value}\n"
                if task.description:
                    output += f"   Description: {task.description}\n"
                if task.result:
                    output += f"   Result: {task.result}\n"
                output += "\n"
            
            # 添加统计
            stats = self.task_manager.get_stats()
            output += f"\nSummary: {stats['completed']}/{stats['total']} completed"
            
            return ToolResult.ok(
                output,
                total=len(tasks),
                stats=stats,
            )
            
        except Exception as e:
            return ToolResult.fail(f"Failed to list tasks: {str(e)}")
