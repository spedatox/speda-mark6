"""Closed write and maintenance contracts. Unknown dates stay unknown."""
from datetime import date
import math
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    source_id: int = Field(gt=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=8000)


class Entity(Strict):
    entity_type: Literal["person", "organization", "place", "project", "course"]
    aliases: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=4000)


class Claim(Strict):
    text: str = Field(min_length=1, max_length=8000)
    mode: Literal["owner_report", "reported_speech", "tool_observation", "inference"] = "owner_report"
    valid_from: date | None = None
    valid_until: date | None = None
    scope: str = Field(default="personal", max_length=128)
    conflict_group: str | None = Field(default=None, max_length=128)


class Episode(Strict):
    description: str = Field(min_length=1, max_length=8000)
    occurred_on: date | None = None
    occurred_until: date | None = None
    time_wording: str = Field(default="", max_length=256)
    outcome: str = Field(default="", max_length=2000)


class State(Strict):
    summary: str = Field(min_length=1, max_length=4000)
    status: Literal["planned", "active", "waiting", "completed", "cancelled", "superseded"]
    review_on: date
    verified_on: date
    starts_on: date | None = None
    ends_on: date | None = None
    closed_on: date | None = None
    question: str = Field(min_length=1, max_length=500)
    salience: Literal["high", "normal", "low"] = "normal"

    @model_validator(mode="after")
    def lifecycle(self):
        terminal = self.status in {"completed", "cancelled", "superseded"}
        if terminal != (self.closed_on is not None):
            raise ValueError("Only a terminal state has closed_on; an outcome requires evidence")
        if self.review_on < self.verified_on:
            raise ValueError("Review precedes verification")
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("Invalid state interval")
        return self


class Document(Strict):
    content: str = Field(min_length=1, max_length=1000000)
    authority: Literal["native_document", "hisar_reference"] = "native_document"
    document_id: str | None = Field(default=None, max_length=128)
    revision_id: str | None = Field(default=None, max_length=128)


class Link(Strict):
    target_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    relation: Literal["about", "participant", "place", "project", "supports", "corrects", "supersedes", "reference"]


class Command(Strict):
    command_id: str = Field(min_length=1, max_length=128)
    record_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    expected_version: int = Field(default=0, ge=0)
    kind: Literal["entity", "claim", "preference", "episode", "state", "document"]
    title: str = Field(min_length=1, max_length=256)
    payload: dict = Field(json_schema_extra={"oneOf": [model.model_json_schema() for model in
        (Entity, Claim, Episode, State, Document)]})
    evidence: list[Evidence] = Field(min_length=1, max_length=32)
    links: list[Link] = Field(default_factory=list, max_length=32)
    retract: bool = False

    @model_validator(mode="after")
    def typed(self):
        model = {"entity": Entity, "claim": Claim, "preference": Claim,
                 "episode": Episode, "state": State, "document": Document}[self.kind]
        self.payload = model.model_validate(self.payload).model_dump(mode="json")
        for start, end in (("valid_from", "valid_until"), ("occurred_on", "occurred_until")):
            if self.payload.get(start) and self.payload.get(end) and self.payload[end] < self.payload[start]:
                raise ValueError("Invalid temporal interval")
        if bool(self.record_id) != bool(self.expected_version):
            raise ValueError("Updates require record_id and expected_version; creates require neither")
        if self.retract and not self.record_id:
            raise ValueError("Retraction requires an existing record")
        return self


class MaintenancePolicy(Strict):
    paused: bool = False
    daily_calls: int = Field(default=0, ge=0, le=10000)
    daily_tokens: int = Field(default=0, ge=0)
    daily_microdollars: int = Field(default=0, ge=0)
    # Conservative microdollars per token, keyed by effective model reference.
    prices: dict[str, float] = Field(default_factory=dict)
    run_seconds: int = Field(default=30, ge=1, le=300)
    units_per_run: int = Field(default=8, ge=1, le=64)
    telegram_enabled: bool = False
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=9, ge=0, le=23)

    @model_validator(mode="after")
    def bounded_rates(self):
        if any(not math.isfinite(p) or p <= 0 for p in self.prices.values()):
            raise ValueError("Rates must be finite positive microdollars per token")
        return self


class PolicyUpdate(Strict):
    expected_version: int = Field(ge=0)
    policy: MaintenancePolicy


class SourceIntake(Strict):
    key: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=100000)


class WorkResolution(Strict):
    expected_status: Literal["held", "quarantined", "pending"]
    action: Literal["discard", "resume_saved"]


class FollowupResolution(Strict):
    expected_status: Literal["muted", "unknown", "sending", "sent", "snoozed", "needs_review", "reply_pending"]
    action: Literal["mute", "snooze", "confirmed_not_sent", "confirmed_sent"]
    message_id: int | None = Field(default=None, gt=0)
