"""Approved plan storage and verification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ApprovedPlan:
    """A user-approved plan waiting for execution."""

    content: str
    source: str = "manual"


class PlanStore:
    """Persist the last plan approved by the user."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, plan: ApprovedPlan) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(plan.content.strip() + "\n", encoding="utf-8")

    def load(self) -> Optional[ApprovedPlan]:
        if not self.path.exists():
            return None
        content = self.path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        return ApprovedPlan(content=content, source=str(self.path))

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def exists(self) -> bool:
        return bool(self.load())


def build_plan_execution_prefix(plan: ApprovedPlan) -> str:
    """Create an execution instruction that keeps the approved plan in scope."""
    return (
        "Execute the following user-approved plan exactly. "
        "Before making changes, briefly state the current step. "
        "After finishing, verify which checklist items are complete.\n\n"
        "Approved plan:\n"
        f"{plan.content}"
    )
