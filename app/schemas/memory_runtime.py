"""Request-owned budgets and source-addressed serving results; no prompt logic."""
from dataclasses import dataclass, field
from datetime import date
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def token_upper_bound(text: str) -> int:
    """Conservative UTF-8 byte budget, explicitly not measured provider tokens."""
    return len(text.encode("utf-8"))


class EvidenceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: int
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=8000)
    attribution: Literal["owner_statement", "canonical_record", "quoted_material"]
    valid_from: date | None = None
    valid_until: date | None = None
    review_on: date | None = None
    historical: bool = False
    fragment: bool = False

    @model_validator(mode="after")
    def valid_span(self):
        if self.end <= self.start:
            raise ValueError("Evidence span must have positive length")
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValueError("Empty temporal interval")
        return self


@dataclass
class MemoryBudget:
    automatic_limit: int = 6000
    lookup_limit: int = 4000
    turn_limit: int = 10000
    lookup_batches: int = 0
    used: int = 0
    elapsed_seconds: float = 0
    seen: set[str] = field(default_factory=set)


@dataclass
class MemoryPacket:
    request_id: str
    user_id: int
    publication_epoch: int = 0
    source_boundary: int = 0
    blocks: list[str] = field(default_factory=list)
    evidence_handles: list[str] = field(default_factory=list)
    unit_revisions: dict[str, int] = field(default_factory=dict)
    omissions: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    coverage: str = "partial"
    freshness: str = "source_overlay"
    token_upper_bound: int = 0
    elapsed_ms: float = 0
    status: str = "partial"

    def diagnostics(self) -> dict:
        return {"epoch": self.publication_epoch, "source_boundary": self.source_boundary,
                "selected": len(self.evidence_handles), "omitted": len(self.omissions),
                "coverage": self.coverage, "freshness": self.freshness,
                "status": self.status, "budget_upper_bound": self.token_upper_bound,
                "elapsed_ms": round(self.elapsed_ms, 3)}


@dataclass
class Deadline:
    expires: float

    @classmethod
    def after(cls, seconds: float):
        return cls(monotonic() + seconds)

    def remaining(self) -> float:
        return max(0.0, self.expires - monotonic())
