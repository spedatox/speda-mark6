from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.context import AgentContext


@dataclass
class DiagnosisReport:
    healthy: bool
    message: str
    details: dict | None = None


@dataclass
class RecoveryResult:
    recovered: bool
    message: str


class OSSAdapter(ABC):
    """
    Base class for Tier 3 OSS Adapter integrations.
    Wraps full OSS applications via HTTP or subprocess.

    Rules:
    - description must be 3–4 sentences minimum (CLAUDE.md Rule 11).
    - health_check() is required — Ratchet polls this.
    - diagnose() and recover() are optional but recommended.
    """

    name: str
    description: str
    input_schema: dict
    health_check_interval_seconds: int = 60

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the OSS application is reachable and responsive."""
        ...

    async def diagnose(self) -> DiagnosisReport:
        """Optional — perform detailed diagnostics. Called when health_check fails."""
        healthy = await self.health_check()
        return DiagnosisReport(healthy=healthy, message="Basic health check only.")

    async def recover(self) -> RecoveryResult:
        """Optional — attempt to recover a degraded adapter."""
        return RecoveryResult(recovered=False, message="No recovery strategy implemented.")

    @abstractmethod
    async def execute(self, args: dict, context: "AgentContext") -> str:
        """Execute the adapter and return a string result for Claude."""
        ...

    def to_tool_definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
