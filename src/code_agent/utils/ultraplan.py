"""Enhanced planning helpers inspired by Claude Code's ultraplan flow."""

from __future__ import annotations

import re


ULTRAPLAN_WORD = "ultraplan"


def contains_ultraplan_trigger(text: str) -> bool:
    """Return whether user text intentionally asks for ultraplan.

    只识别独立关键字，避免把路径或文件名里的 ultraplan 误判为命令。
    """
    return bool(re.search(r"(^|[\s:/])ultraplan($|[\s,.!?，。！？])", text, re.IGNORECASE))


def strip_ultraplan_trigger(text: str) -> str:
    """Remove the trigger keyword while preserving the user's actual task."""
    cleaned = re.sub(r"\bultraplan\b", "", text, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip()


def build_ultraplan_request(task: str) -> str:
    """Build the user message used to ask the model for an enhanced plan."""
    task = task.strip() or "请基于当前项目状态制定一个增强计划。"
    return (
        "请进入 Ultraplan 增强计划模式，先不要修改文件或执行写入类操作。\n\n"
        "请围绕下面任务输出一个可批准、可执行、可验证的计划：\n"
        f"{task}\n\n"
        "计划必须包含：\n"
        "1. 当前需要先确认的事实和文件\n"
        "2. 分阶段执行步骤，每一步的目标、涉及文件和预期结果\n"
        "3. 需要用户批准的工具/命令/风险点\n"
        "4. 验证方案，包括测试、lint、类型检查或手动验收\n"
        "5. 完成判定标准\n\n"
        "输出只写计划，不要声称已经完成。"
    )
