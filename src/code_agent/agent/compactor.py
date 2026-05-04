"""上下文压缩模块 - 使用 LLM 生成智能摘要

当会话记忆超过阈值时，调用 LLM 将旧消息压缩为摘要，
保留关键信息的同时减少 token 使用量。
"""


from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from code_agent.agent.memory import ConversationMemory, Message


COMPACTION_PROMPT = """Create a precise continuation summary for a coding-agent conversation.

Rules:
- Respond with plain text only. Do not request or simulate tool calls.
- Preserve the user's explicit requests, later corrections, and current intent.
- Preserve concrete technical details: file paths, function names, commands, test results, provider/model settings, and errors.
- Distinguish completed work from pending work.
- If recent work was interrupted, identify the exact next step that follows from the latest user request.
- Omit filler, greetings, and vague claims.

Use these sections:
1. Primary request and intent
2. Key technical context
3. Files and code areas
4. Commands, checks, and results
5. Errors and corrections
6. Completed work
7. Pending work and next step

Conversation to summarize:
{conversation}
"""


class ContextCompactor:
    """上下文压缩器
    
    使用 LLM 将历史消息压缩为结构化摘要。
    """
    
    def __init__(self, llm: ChatOpenAI):
        """初始化压缩器
        
        Args:
            llm: LLM 实例，用于生成摘要
        """
        self.llm = llm
    
    async def compact(self, memory: ConversationMemory) -> str:
        """压缩会话记忆
        
        将记忆中最旧的消息压缩为摘要，保留最近的消息。
        
        Args:
            memory: 会话记忆
            
        Returns:
            生成的摘要
        """
        # 获取所有消息
        all_messages = memory.messages.copy()
        
        if len(all_messages) < 3:
            # 消息太少，不需要压缩
            return ""
        
        # 保留最近的消息（约 1/3）
        keep_count = max(2, len(all_messages) // 3)
        to_compact = all_messages[:-keep_count]
        
        if not to_compact:
            return ""
        
        # 构建对话文本
        conversation_text = self._format_messages(to_compact)
        
        # 调用 LLM 生成摘要
        summary = await self._generate_summary(conversation_text)
        
        # 更新记忆
        memory.messages = all_messages[-keep_count:]
        
        # 合并新旧摘要
        if memory.summary:
            memory.summary = f"{memory.summary}\n\n---\n\n{summary}"
        else:
            memory.summary = summary
        
        return summary
    
    def _format_messages(self, messages: list[Message]) -> str:
        """将消息格式化为对话文本
        
        Args:
            messages: 消息列表
            
        Returns:
            格式化的对话文本
        """
        lines = []
        
        for msg in messages:
            role_label = {
                "user": "User",
                "assistant": "Assistant",
                "tool": "Tool",
                "system": "System",
            }.get(msg.role, msg.role)
            
            content = msg.content
            # 截断过长的内容
            if len(content) > 500:
                content = content[:500] + "... [truncated]"
            
            lines.append(f"[{role_label}]: {content}")
        
        return "\n\n".join(lines)
    
    async def _generate_summary(self, conversation_text: str) -> str:
        """使用 LLM 生成摘要
        
        Args:
            conversation_text: 对话文本
            
        Returns:
            生成的摘要
        """
        try:
            prompt = COMPACTION_PROMPT.format(conversation=conversation_text)
            
            messages = [
                SystemMessage(content="You are a helpful assistant that summarizes conversations."),
                HumanMessage(content=prompt),
            ]
            
            response = await self.llm.ainvoke(messages)
            content = response.content if hasattr(response, "content") else str(response)
            summary = content if isinstance(content, str) else str(content)
            
            return summary.strip()
            
        except Exception as e:
            # 如果 LLM 调用失败，返回简单的统计摘要
            return f"[Summary generation failed: {str(e)}]\n[Conversation contained {conversation_text.count(chr(10))} lines of dialogue]"
    
    async def compact_if_needed(
        self,
        memory: ConversationMemory,
        threshold: int = 15,
    ) -> bool:
        """如果需要则压缩记忆
        
        Args:
            memory: 会话记忆
            threshold: 触发压缩的消息数量阈值
            
        Returns:
            是否执行了压缩
        """
        if len(memory.messages) >= threshold:
            await self.compact(memory)
            return True
        return False
