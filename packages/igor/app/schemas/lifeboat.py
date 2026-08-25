"""Wire schemas for the Lifeboat Protocol watch. See services/lifeboat.py."""

from pydantic import BaseModel, Field


class LifeboatScanResponse(BaseModel):
    """One poll of the host's resource headroom. Costs zero tokens.

    `changed` is the cost boundary the whole workflow turns on: the Gate node
    stops the branch on false, and only a true here is ever allowed to spend an
    agentic turn.
    """

    # ok | error | disabled — n8n branches on this, so it is never absent.
    status: str
    changed: bool = False
    # escalated | recovered | still_unhealthy | no_change. Says WHICH edge fired,
    # so the trigger's intent can differ: an escalation asks for a decision, a
    # recovery is one line of good news, and confusing the two is how a push that
    # says "the host is fine" arrives worded like an emergency.
    reason: str = "no_change"
    level: str = "healthy"
    previous_level: str = "healthy"
    by_resource: dict[str, str] = Field(default_factory=dict)
    pressed: list[str] = Field(default_factory=list)
    # Everything the probe read. Rides into the trigger payload so the turn does
    # not re-fetch what this call already paid for.
    readings: dict = Field(default_factory=dict)
    summary: str = ""
    recommendation: str = ""
    # Whether the protocol would currently let Orion run Tier 1 without waiting
    # for the owner. Advisory only — bail() re-derives this from the host and
    # never trusts a payload that claims it.
    tier1_autonomous: bool = False
    target_free_gb: int = 0
    detail: str = ""


class LifeboatAckRequest(BaseModel):
    """Commit the scanned level as "the owner has now been told".

    Sent only after the trigger was accepted. The level must match what the scan
    parked — acknowledging blind would mark a state as reported that nobody ever
    heard about.
    """

    level: str = Field(max_length=16)


class LifeboatAckResponse(BaseModel):
    acked: bool = False
    level: str = "healthy"
    detail: str = ""


class LifeboatState(BaseModel):
    """The owner-facing assessment: what the host is, and what to do about it.

    Read-only and safe to call at any time — it reclaims nothing. The verdict is
    the WORST of disk, inodes and memory, never their average: a full inode table
    on a 40%-full disk is as fatal as a full disk, and averaging reports it fine.
    """

    # ok | error | disabled
    status: str
    level: str = "healthy"
    by_resource: dict[str, str] = Field(default_factory=dict)
    pressed: list[str] = Field(default_factory=list)
    readings: dict = Field(default_factory=dict)
    summary: str = ""
    recommendation: str = ""
    target_free_gb: int = 0
    detail: str = ""
