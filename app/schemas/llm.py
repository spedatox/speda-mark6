"""Provider-independent policy; provider-owned continuation stays opaque."""
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


class ThinkingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["provider_default", "off", "enabled", "effort", "budget", "inherit"] = "provider_default"
    value: StrictStr | StrictInt | None = None


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: str
    protocol: Literal["anthropic", "responses", "chat", "google"]
    control: Literal["none", "effort", "budget", "adaptive", "toggle"] = "none"
    efforts: tuple[str, ...] = ()
    can_disable: bool = False
    vision: bool = False
    supports_summary: bool = False
    daybreak_program: Literal["daybreak_blue", "daybreak_red"] | None = None
    requires_safety_identifier: bool = False
    budget_min: int = 1024
    budget_max: int = 32768
    revision: int = 1


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    main_model: str | None = None
    background_model: str | None = None
    agent_models: dict[str, str] = Field(default_factory=dict)
    telegram_models: dict[str,str] = Field(default_factory=dict)
    worker_models: dict[str, str] = Field(default_factory=dict)
    workers: dict[str, ThinkingRequest] = Field(default_factory=dict)
    models: dict[str, ThinkingRequest] = Field(default_factory=dict)
    cyber_programs: dict[str, Literal["standard", "daybreak_blue", "daybreak_red"]] = Field(default_factory=dict)
    roles: dict[str, ThinkingRequest] = Field(default_factory=dict)
    agents: dict[str, ThinkingRequest] = Field(default_factory=dict)
    show_thinking: bool = False
    budget_mode: bool = False
    fallbacks: list[str] = Field(default_factory=list, max_length=3)
    descriptors: list[ModelSpec] = Field(default_factory=list, max_length=100)

    @field_validator('worker_models', 'workers')
    @classmethod
    def canonical_worker_keys(cls, values):
        from app.profiles.workers import normalize_worker_mapping
        return normalize_worker_mapping(values)


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    policy: PolicyDocument


@dataclass(frozen=True)
class EffectivePolicy:
    spec: ModelSpec
    requested: ThinkingRequest
    wire: dict
    source: str
    revision: int
    show_thinking: bool
    deadline: float
    fallbacks: tuple[str, ...] = ()
    snapshot: PolicyDocument = field(default_factory=PolicyDocument, repr=False)

    def receipt(self):
        return {"model": self.spec.ref, "protocol": self.spec.protocol,
                "descriptor_revision": self.spec.revision, "policy_revision": self.revision,
                "requested": self.requested.model_dump(), "applied": self.wire,
                "source": self.source, "show_thinking": self.show_thinking}

    def remaining(self):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise ProviderError("deadline", "The model request deadline was reached")
        return remaining


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class Block:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self):
        return dict(self.__dict__)


@dataclass
class LLMMessage:
    content: list[Block]
    stop_reason: str
    usage: dict = field(default_factory=dict)
    continuation: dict | None = None
    policy: EffectivePolicy | None = None
    attempts: int = 1


@dataclass
class StreamEvent:
    kind: Literal["text", "thinking"]
    text: str
