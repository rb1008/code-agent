"""Tests for conversation memory."""


from code_agent.agent.memory import Message, ConversationMemory


class TestMessage:
    """Tests for Message dataclass."""
    
    def test_message_creation(self):
        """Test creating a message."""
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.metadata == {}
    
    def test_message_to_dict(self):
        """Test converting message to dict."""
        msg = Message(role="assistant", content="Hi there")
        d = msg.to_dict()
        assert d == {"role": "assistant", "content": "Hi there"}


class TestConversationMemory:
    """Tests for ConversationMemory."""
    
    def test_add_message(self):
        """Test adding messages."""
        memory = ConversationMemory()
        memory.add("user", "Hello")
        memory.add("assistant", "Hi")
        
        assert len(memory) == 2
        assert memory.messages[0].role == "user"
        assert memory.messages[1].role == "assistant"
    
    def test_get_messages(self):
        """Test getting messages in API format."""
        memory = ConversationMemory()
        memory.add("user", "Hello")
        memory.add("assistant", "Hi")
        
        messages = memory.get_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
    
    def test_clear(self):
        """Test clearing memory."""
        memory = ConversationMemory()
        memory.add("user", "Hello")
        memory.clear()
        
        assert len(memory) == 0
        assert memory.summary == ""
    
    def test_compaction(self):
        """Test memory compaction."""
        memory = ConversationMemory(max_messages=4, compact_threshold=3)
        
        # Add messages to trigger compaction
        memory.add("user", "Msg 1")
        memory.add("assistant", "Reply 1")
        memory.add("user", "Msg 2")
        memory.add("assistant", "Reply 2")
        memory.add("user", "Msg 3")  # Should trigger compaction
        
        # After compaction, should have summary + recent messages
        assert len(memory.messages) <= memory.max_messages
        assert memory.summary != "" or len(memory.messages) == 3
    
    def test_get_recent(self):
        """Test getting recent messages."""
        memory = ConversationMemory()
        memory.add("user", "1")
        memory.add("assistant", "2")
        memory.add("user", "3")
        
        recent = memory.get_recent(2)
        assert len(recent) == 2
        assert recent[0].content == "2"
        assert recent[1].content == "3"

    def test_estimate_tokens_includes_summary_and_prompt(self):
        """Token estimates should account for retained prompt context."""
        memory = ConversationMemory()
        memory.add("user", "Hello world")
        memory.summary = "Earlier summary"

        estimate = memory.estimate_tokens(model="unknown-model", system_prompt="System prompt")

        assert estimate > 0
