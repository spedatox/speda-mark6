"""Wire schemas for the health sync pipe (docs/ATOMIX_HEALTH_SYNC.md §3.1).
Mirrored on the phone by HealthDtos.kt."""

from datetime import datetime

from pydantic import BaseModel, Field


class HealthSampleIn(BaseModel):
    metric: str = Field(max_length=48)
    # Offset-aware ISO-8601 from the phone ("2026-07-18T23:41:00+03:00"). The
    # offset is load-bearing: services.health derives the owner's LOCAL day from
    # it before storing UTC, and a bare naive timestamp will be read as UTC.
    start: datetime
    end: datetime | None = None
    value: float
    unit: str = Field(default="", max_length=24)
    detail: dict = Field(default_factory=dict)
    origin: str = Field(default="", max_length=128)


class HealthIngestRequest(BaseModel):
    device: str = Field(default="", max_length=96)
    samples: list[HealthSampleIn] = Field(default_factory=list, max_length=5000)
