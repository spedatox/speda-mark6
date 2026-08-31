# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime

from pydantic import BaseModel


class AgentRegistration(BaseModel):
    type: str = "agent_register"
    agent_id: str
    agent_name: str
    domain: str
    capabilities: list[str]
    status: str = "online"
    model_preference: str = "haiku"

    # Which MACHINE this peer runs on. One agent may attach from several at
    # once (Optimus on the server and on the owner's PC) and is still one
    # agent — same profile, same memory, same sessions. These three fields are
    # what let the backend route a turn to the right machine; see
    # app/core/peer_routing.py. All default so a peer that sends none of them
    # behaves exactly as before: one linux host, accepting any POSIX path.
    host: str = "default"        # distinguishes peers of the SAME agent
    platform: str = "linux"      # "linux" | "windows" — how to validate a path
    roots: list[str] = []        # directories this peer will work in; [] = any


class AgentStatus(BaseModel):
    agent_id: str
    agent_name: str
    domain: str
    status: str
    last_seen: datetime | None
    capabilities: list[str]


class AgentCommEntry(BaseModel):
    """One inter-agent exchange, as shown in the comms tray."""

    id: int
    request_id: str
    from_agent: str
    to_agent: str
    kind: str
    protocol: str
    task: str
    result: str | None
    status: str
    duration_ms: int | None
    # The chat session this exchange was ordered from — the "room" it belongs to
    # in the group-chat view. None for traffic logged before the column existed.
    origin_session_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class HousePartyState(BaseModel):
    engaged: bool


class HousePartySet(BaseModel):
    """Engage/stand-down request. Engaging requires the owner's authorization
    passphrase (validated server-side); standing down needs none.

    `platform` is the surface asking. Engaging is desktop-only — the war room
    exists nowhere else — so a client that does not say what it is cannot
    engage. Standing down is unconditional from anywhere: a protocol you can
    start but not stop is a trap, and the phone must always be able to stop it.
    """

    engaged: bool
    passphrase: str | None = None
    platform: str | None = None


class LockdownState(BaseModel):
    """Lockdown Protocol state. `engaged` is the flag; `rules` is what the host
    firewall actually shows, keyed by what each rule seals. They are reported
    separately because a drift between them (flag on, rules gone) is exactly the
    failure the owner needs to see rather than have averaged into one boolean.

    An EMPTY `rules` means the host could not be reached, not that nothing is
    sealed — render it as unknown. A host that has sealed itself against its own
    bridge is unreachable precisely because containment is working."""

    engaged: bool
    enabled: bool = False
    rules: dict[str, bool] = {}
    report: str | None = None


class LockdownSet(BaseModel):
    """Engage/stand-down request. Engaging requires the owner's authorization
    passphrase (the same one House Party uses); standing down needs none — the
    way out of containment must never be gated."""

    engaged: bool
    passphrase: str | None = None


class AgentModelInfo(BaseModel):
    """One agent's model allocation, as shown in the model routing UI."""

    agent_id: str
    name: str
    domain: str
    override: str | None      # owner's runtime pin; None = profile policy
    telegram_override: str | None = None
    default_main: str         # profile's interactive-grade model
    default_background: str   # profile's background-tier model


class AgentModelSet(BaseModel):
    agent_id: str
    model: str | None = None  # None/empty = clear the pin, back to profile policy


class AgentTelegramModelSet(BaseModel):
    agent_id: str
    model: str | None = None  # None/empty = clear the pin, use desktop model


class LegionModelInfo(BaseModel):
    """One Legion worker type's model allocation, for the Legion model UI.

    Legionnaires have no profile of their own — their model is derived from
    effort against whatever the DEPLOYING agent is running on, so there is no
    single "default model" to show. `effort` and `derived_from` describe that
    rule instead, and `override` is the owner's pin on top of it."""

    worker_id: str
    when_to_use: str
    effort: str
    derived_from: str          # human-readable description of the effort rule
    override: str | None       # owner's runtime pin; None = effort policy
    deployment_pin: str | None # LEGION_MODEL_OVERRIDE — overrides everything


class LegionModelSet(BaseModel):
    worker_id: str
    model: str | None = None  # None/empty = clear the pin, back to effort policy


class PendingAskEntry(BaseModel):
    """One irreversible operation an external peer is waiting to be told about.

    `action_key` is the exact command or path the peer's gate stopped. It is
    shown verbatim and never truncated — an owner approving a force-push needs
    to see which branch."""

    ask_id: str
    agent_id: str
    tool: str
    action_key: str
    reason: str
    job_id: str = ""
    chat_id: str | None = None
    seconds_left: float


class AskAnswer(BaseModel):
    """The owner's decision. `remember` records this exact action on the peer's
    allow-list so the same command stops asking; it never generalises to a
    pattern."""

    approved: bool
    remember: bool = False
    note: str = ""
