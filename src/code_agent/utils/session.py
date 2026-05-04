"""会话持久化模块 - 保存和恢复会话状态

支持：
- 保存会话到文件（包括记忆、任务、配置）
- 从文件恢复会话
- 列出历史会话
- 自动保存
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast

from code_agent.agent.memory import ConversationMemory

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SessionManager:
    """会话管理器
    
    管理会话的保存、恢复和列表。
    会话文件存储在 ~/.config/code-agent/sessions/ 目录下。
    """
    
    def __init__(self, sessions_dir: Optional[Path] = None):
        """初始化会话管理器
        
        Args:
            sessions_dir: 会话存储目录，默认 ~/.config/code-agent/sessions/
        """
        if sessions_dir is None:
            sessions_dir = Path.home() / ".config" / "code-agent" / "sessions"
        
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        
        # 当前会话文件名
        self._current_session_file: Optional[Path] = None
    
    def save(
        self,
        memory: ConversationMemory,
        metadata: Optional[dict[str, Any]] = None,
        session_name: Optional[str] = None,
    ) -> Path:
        """保存会话到文件
        
        Args:
            memory: 会话记忆
            metadata: 额外元数据（如模型名称、工作目录等）
            session_name: 会话名称，默认使用时间戳
            
        Returns:
            保存的文件路径
        """
        if session_name is None:
            session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        session_file = self._session_path(session_name)
        
        # 构建会话数据
        session_data = {
            "version": "1.0",
            "saved_at": datetime.now().isoformat(),
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "metadata": msg.metadata,
                }
                for msg in memory.messages
            ],
            "summary": memory.summary,
            "metadata": metadata or {},
        }
        
        # 写入文件
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        
        self._current_session_file = session_file
        return session_file
    
    def load(
        self,
        session_name: str,
        memory: Optional[ConversationMemory] = None,
    ) -> tuple[ConversationMemory, dict[str, Any]]:
        """从文件加载会话
        
        Args:
            session_name: 会话名称（不含扩展名）
            memory: 可选的现有记忆对象，会覆盖其内容
            
        Returns:
            (记忆对象, 元数据)
        """
        session_file = self._session_path(session_name)
        
        if not session_file.exists():
            raise FileNotFoundError(f"Session not found: {session_name}")
        
        with open(session_file, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        
        # 创建或更新记忆
        if memory is None:
            memory = ConversationMemory()
        else:
            memory.clear()
        
        # 恢复消息
        for msg_data in session_data.get("messages", []):
            memory.add(
                role=msg_data["role"],
                content=msg_data["content"],
                **msg_data.get("metadata", {}),
            )
        
        # 恢复摘要
        memory.summary = session_data.get("summary", "")
        
        self._current_session_file = session_file
        
        return memory, session_data.get("metadata", {})
    
    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有保存的会话
        
        Returns:
            会话信息列表
        """
        sessions = []
        
        for session_file in sorted(self.sessions_dir.glob("*.json"), reverse=True):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # 统计消息数
                msg_count = len(data.get("messages", []))
                
                sessions.append({
                    "name": session_file.stem,
                    "saved_at": data.get("saved_at", "unknown"),
                    "message_count": msg_count,
                    "has_summary": bool(data.get("summary")),
                    "metadata": data.get("metadata", {}),
                })
            except Exception:
                # 跳过损坏的会话文件
                continue
        
        return sessions
    
    def delete(self, session_name: str) -> bool:
        """删除会话
        
        Args:
            session_name: 会话名称
            
        Returns:
            是否成功删除
        """
        session_file = self._session_path(session_name)
        
        if session_file.exists():
            session_file.unlink()
            return True
        return False
    
    def get_latest(self) -> Optional[str]:
        """获取最新的会话名称
        
        Returns:
            最新会话名称，如果没有则返回 None
        """
        sessions = self.list_sessions()
        if sessions:
            return cast(str, sessions[0]["name"])
        return None
    
    def auto_save(
        self,
        memory: ConversationMemory,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[Path]:
        """自动保存当前会话
        
        使用固定文件名 'auto_save'，每次覆盖。
        
        Args:
            memory: 会话记忆
            metadata: 元数据
            
        Returns:
            保存的文件路径
        """
        return self.save(memory, metadata, session_name="auto_save")
    
    def load_auto_save(
        self,
        memory: Optional[ConversationMemory] = None,
    ) -> Optional[tuple[ConversationMemory, dict[str, Any]]]:
        """加载自动保存的会话
        
        Args:
            memory: 可选的现有记忆对象
            
        Returns:
            如果存在自动保存则返回 (记忆, 元数据)，否则 None
        """
        auto_save_file = self.sessions_dir / "auto_save.json"
        
        if not auto_save_file.exists():
            return None
        
        return self.load("auto_save", memory)

    def _session_path(self, session_name: str) -> Path:
        """Return a safe path for one session name inside sessions_dir."""
        if not SESSION_NAME_RE.fullmatch(session_name):
            raise ValueError(
                "会话名称只能包含字母、数字、下划线、点和短横线，不能包含路径分隔符。"
            )
        path = (self.sessions_dir / f"{session_name}.json").resolve()
        root = self.sessions_dir.resolve()
        try:
            path.relative_to(root)
        except ValueError as e:
            raise ValueError("会话路径必须位于会话目录内。") from e
        return path
