"""Memory management for Code Agent conversations.

实现滑动窗口记忆 + 关键信息摘要，参考 Claude Code 的上下文管理策略。
"""

from dataclasses import dataclass, field
from typing import Any

from code_agent.utils.token_budget import estimate_messages_tokens, estimate_text_tokens


@dataclass
class Message:
    """A single message in the conversation."""
    
    role: str  # "user", "assistant", "tool", "system"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary format for LLM API."""
        return {
            "role": self.role,
            "content": self.content,
        }


class ConversationMemory:
    """Manages conversation history with sliding window and compaction.
    
    参考 Claude Code 的上下文管理策略：
    1. 维护最近 N 条消息的滑动窗口
    2. 当窗口满时，对旧消息进行摘要压缩
    3. 保留关键信息（如文件路径、重要决策）
    """
    
    def __init__(self, max_messages: int = 20, compact_threshold: int = 15):
        """Initialize memory.
        
        Args:
            max_messages: Maximum messages before forcing compaction
            compact_threshold: Threshold to trigger compaction
        """
        self.max_messages = max_messages
        self.compact_threshold = compact_threshold
        self.messages: list[Message] = []
        self.summary: str = ""  # 压缩后的摘要
        
    def add(self, role: str, content: str, **metadata: Any) -> None:
        """Add a message to memory.
        
        Args:
            role: Message role (user/assistant/tool/system)
            content: Message content
            **metadata: Additional metadata
        """
        message = Message(role=role, content=content, metadata=metadata)
        self.messages.append(message)
        
        # 检查是否需要压缩
        if len(self.messages) >= self.compact_threshold:
            self._compact()
    
    def _compact(self) -> None:
        """Compact old messages into a summary.
        
        保留最近的消息，将旧消息压缩为摘要。
        """
        if len(self.messages) <= self.max_messages // 2:
            return
            
        # 保留最近的一半消息
        keep_count = self.max_messages // 2
        to_compact = self.messages[:-keep_count]
        self.messages = self.messages[-keep_count:]
        
        # 生成摘要（简化版，实际可以调用 LLM 生成更好的摘要）
        summary_parts = []
        for msg in to_compact:
            if msg.role == "tool":
                # 工具执行结果，保留关键信息
                summary_parts.append(f"[Tool {msg.metadata.get('tool', 'unknown')} executed]")
            elif msg.role == "assistant" and msg.metadata.get("is_plan"):
                # 计划类消息
                summary_parts.append(f"[Plan: {msg.content[:100]}...]")
        
        # 如果没有特定的摘要内容，生成一个通用的摘要
        if not summary_parts:
            user_msgs = [m for m in to_compact if m.role == "user"]
            assistant_msgs = [m for m in to_compact if m.role == "assistant"]
            summary_parts.append(
                f"[Conversation: {len(user_msgs)} user messages, "
                f"{len(assistant_msgs)} assistant responses]"
            )
        
        if summary_parts:
            new_summary = "\n".join(summary_parts)
            if self.summary:
                self.summary = f"{self.summary}\n{new_summary}"
            else:
                self.summary = new_summary
    
    def get_messages(self, include_summary: bool = True) -> list[dict[str, str]]:
        """Get all messages in LLM API format.
        
        Args:
            include_summary: Whether to include summary as system message
            
        Returns:
            List of message dictionaries
        """
        result = []
        
        # 如果有摘要，作为第一条消息
        if include_summary and self.summary:
            result.append({
                "role": "system",
                "content": f"Previous conversation summary:\n{self.summary}"
            })
        
        # 添加所有消息
        for msg in self.messages:
            result.append(msg.to_dict())
            
        return result
    
    def get_recent(self, n: int = 5) -> list[Message]:
        """Get the most recent n messages.
        
        Args:
            n: Number of recent messages to get
            
        Returns:
            List of recent messages
        """
        return self.messages[-n:]

    def estimate_tokens(self, model: str = "", system_prompt: str = "") -> int:
        """Estimate tokens retained in this memory plus optional system prompt."""
        total = estimate_messages_tokens(self.messages, model)
        if self.summary:
            total += estimate_text_tokens(f"Previous conversation summary:\n{self.summary}", model)
        if system_prompt:
            total += estimate_text_tokens(system_prompt, model)
        return total
    
    def clear(self) -> None:
        """Clear all messages and summary."""
        self.messages = []
        self.summary = ""
    
    def __len__(self) -> int:
        return len(self.messages)
    
    def __repr__(self) -> str:
        return f"ConversationMemory(messages={len(self.messages)}, summary_length={len(self.summary)})"
