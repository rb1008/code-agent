"""Approximate token budgeting for agent context management."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol


class MessageLike(Protocol):
    """Small protocol for memory messages used by the estimator."""

    role: str
    content: str


def estimate_text_tokens(text: str, model: str = "") -> int:
    """Estimate token count with tiktoken when available, otherwise use a heuristic."""
    if not text:
        return 0
    if os.getenv("CODE_AGENT_USE_TIKTOKEN") != "1":
        return max(1, len(text) // 4)

    try:
        import tiktoken  # type: ignore[import-untyped]

        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def estimate_messages_tokens(messages: Sequence[MessageLike], model: str = "") -> int:
    """Estimate chat message tokens including a small per-message envelope."""
    total = 0
    for message in messages:
        total += 4
        total += estimate_text_tokens(message.role, model)
        total += estimate_text_tokens(message.content, model)
    return total + 2


def truncate_for_budget(content: str, max_chars: int) -> tuple[str, bool]:
    """Truncate large tool results while preserving useful head and tail context."""
    if len(content) <= max_chars:
        return content, False

    marker = f"\n\n[... truncated {len(content) - max_chars} chars ...]\n\n"
    available = max(0, max_chars - len(marker))
    head_chars = max(1, int(available * 0.7))
    tail_chars = max(1, available - head_chars)
    return f"{content[:head_chars]}{marker}{content[-tail_chars:]}", True
