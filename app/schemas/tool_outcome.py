"""Trusted executor outcomes. Tool text cannot request a runtime transition."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolObservation:
    status: Literal["accepted", "verified", "queued", "denied", "unavailable"]
    code: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class CompletedToolResult:
    text: str
    observation: ToolObservation | None = None


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
