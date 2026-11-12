"""Owner input and collaboration contracts; models cannot change authority or budgets."""
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.llm import ThinkingRequest

ShortText = Annotated[str, Field(min_length=1, max_length=1000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Price(Contract):
    microdollars_per_token: float = Field(gt=0, allow_inf_nan=False)
    # Required for separately billed/unbounded-by-visible-output reasoning.
    extra_output_ceiling: int = Field(default=0, ge=0, le=1000000)
    image_token_ceiling: int = Field(default=0, ge=0, le=1000000)


class RunLimits(Contract):
    calls: int = Field(default=16, ge=0, le=1000)
    tokens: int = Field(default=200000, ge=0, le=20000000)
    microdollars: int = Field(default=0, ge=0)
    seconds: int = Field(default=0, ge=0, le=86400, description="Zero disables the overall run deadline; per-call and command timeouts still apply.")
    contributions: int = Field(default=6, ge=1, le=32)
    concurrency: int = Field(default=3, ge=1, le=8)


class SpendingPolicy(Contract):
    paused: bool = True
    enforce_limits: bool = Field(default=True, description="Enforce daily allowances, run spending ceilings and configured prices. Per-run execution bounds still apply when disabled.")
    daily_calls: int = Field(default=0, ge=0)
    daily_tokens: int = Field(default=0, ge=0)
    daily_microdollars: int = Field(default=0, ge=0)
    concurrent_calls: int = Field(default=8, ge=1, le=32)
    run: RunLimits = Field(default_factory=RunLimits)
    prices: dict[str, Price] = Field(default_factory=dict)


class SpendingUpdate(Contract):
    expected_version: int = Field(ge=0)
    policy: SpendingPolicy


class PersonaStyle(Contract):
    instructions: str = Field(default="", max_length=6000)
    length: Literal["default", "brief", "detailed"] = "default"
    humor: Literal["default", "restrained", "expressive", 'off', 'dry', 'playful'] = "default"
    tone: Literal['default','familiar','professional'] = 'default'
    directness: Literal['default','gentle','direct'] = 'default'


class PersonaUpdate(Contract):
    expected_version: int = Field(ge=0)
    settings: PersonaStyle


class CreateRoom(Contract):
    title: str = Field(default="Conversation", min_length=1, max_length=255)
    lead: str = "speda"
    members: list[str] = Field(default_factory=list, max_length=8)
    project_id: str | None = None


class RoomInput(Contract):
    reply_to: int | None = Field(default=None, ge=1)
    input_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w-]+$")
    message: str = Field(min_length=1, max_length=32000)
    agent_id: str | None = None
    model: str | None = None
    thinking: ThinkingRequest | None = None
    on_busy: Literal["queue", "reject"] = "queue"
    attachments: list[str] = Field(default_factory=list, max_length=8)
    replay_message_id: int | None = Field(default=None, gt=0)
    owner_instructions: str = Field(default='', max_length=6000)


class Membership(Contract):
    expected_version: int = Field(ge=1)
    action: Literal["invite", "dismiss", "handoff"]
    agent_id: str


class Collaboration(Contract):
    action: Literal["invite", "address", "consult", "handoff", "dismiss"]
    agent_id: str
    message: str = Field(default="", max_length=6000, description=
        "Your actual question or contribution addressed to this colleague. Address and consult return "
        "their answer to you automatically. Preserve who is asking whom; do not turn a peer question "
        "into a request for the owner to manage the exchange.")
    reply_to: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def needs_message(self):
        if self.action in {"invite", "address", "consult"} and not self.message.strip():
            raise ValueError("A collaboration request needs a concrete question or task")
        return self


class CreateProject(Contract):
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=2000)


class Checkpoint(Contract):
    expected_revision: int = Field(ge=0)
    objective: str = Field(min_length=1, max_length=3000)
    acceptance: list[ShortText] = Field(default_factory=list, max_length=30)
    progress: str = Field(default="", max_length=4000)
    next_steps: list[ShortText] = Field(default_factory=list, max_length=30)
    blockers: list[ShortText] = Field(default_factory=list, max_length=30)
    checks: list[ShortText] = Field(default_factory=list, max_length=30)
    status: Literal["active", "paused", "blocked", "complete"] = "active"


class Delegate(Contract):
    role: Literal["autobot", "decepticon", "reviewer"]
    task: str = Field(min_length=1, max_length=12000)
    acceptance: list[ShortText] = Field(min_length=1, max_length=20)
    dependencies: list[str] = Field(default_factory=list, max_length=20)


class Decision(Contract):
    id: str | None = None
    expected_version: int = Field(default=0, ge=0)
    proposal: str = Field(min_length=1, max_length=4000)
    evidence: list[int] = Field(default_factory=list, max_length=20)
    objections: list[ShortText] = Field(default_factory=list, max_length=20)
    disposition: Literal["open", "revised", "recommended", "accepted", "rejected", "deferred"] = "open"


class Steering(Contract):
    input_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w-]+$")
    message: str = Field(min_length=1, max_length=6000)


class SupervisorSteering(Steering):
    expected_step_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


class ArtifactReview(Contract):
    task_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    expected_version: int = Field(ge=0)
    artifact_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    verdict: Literal['accepted', 'needs_changes', 'blocked']
    rationale: str = Field(min_length=1, max_length=4000)
    checks: list[str] = Field(default_factory=list, max_length=30)


class PrepareRelease(Contract):
    task_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    artifact_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    target: str = Field(min_length=1, max_length=80, pattern=r'^[\w-]+$')


class ApproveRelease(Contract):
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['approve', 'reject']


class ReconcileRelease(Contract):
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed_driver_stopped: Literal[True]
    outcome: Literal['published', 'rolled_back', 'failed']
    evidence: str = Field(min_length=10, max_length=4000)


class RelocateProject(Contract):
    expected_location_version: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=2000)
    expected_content_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class IntegrateProject(Contract):
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_source_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_artifact_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
