"""Trusted executor outcomes. Tool text cannot request a runtime transition."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CompletedToolResult:
    text: str


@dataclass(frozen=True)
class PendingOperation:
    executor: str
    operation_id: str
    fingerprint: str
    receipt_id: int
    deadline: float | None = None


ToolOutcome = CompletedToolResult | PendingOperation


class StepSuspended(Exception):
    """Internal control flow, never inferred from an executor's output text."""
