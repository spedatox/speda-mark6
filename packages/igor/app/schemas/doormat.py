"""Wire schemas for the Doormat Protocol. See services/doormat.py."""

from pydantic import BaseModel, Field


class DoormatChecklistItem(BaseModel):
    """One thing the owner has to change somewhere this server cannot reach.

    `value` is the exact string to paste. That is the point of generating these
    rather than writing them into a doc: a redirect URI transcribed by hand is a
    redirect URI with a trailing slash in it, and Azure rejects that with an
    error code that names nothing useful.
    """

    provider: str
    where: str
    field: str
    value: str
    note: str = ""


class DoormatState(BaseModel):
    """Where the protocol is, and what the host actually shows.

    Reported separately on purpose. `phase` is what this side believes;
    `current_domain` and `target_serving` are read off Caddy. A disagreement
    between them — staged but not serving, cut over but the process never
    restarted — is the failure worth seeing rather than averaging away.
    """

    enabled: bool = False
    # "" (idle) | "staged" | "cutover"
    phase: str = ""
    target: str = ""
    previous: str = ""
    staged_at: str = ""
    cutover_at: str = ""
    # What Caddy's own DOMAIN says it is serving right now.
    current_domain: str = ""
    # None while idle; otherwise whether https://target/health actually answers.
    target_serving: bool | None = None
    # True when cutover wrote the new settings but this process still holds the
    # old ones — i.e. the restart has not happened yet.
    restart_pending: bool = False
    checklist: list[DoormatChecklistItem] = Field(default_factory=list)
    detail: str = ""


class DoormatStageRequest(BaseModel):
    """Start a move. `domain` is a bare hostname; a scheme or path is trimmed."""

    domain: str = Field(max_length=253)
    # Skip the "does this resolve here" precondition. The ONE legitimate use is a
    # proxy in front (Cloudflare and friends), where the A record correctly points
    # somewhere else. Not an override for a record that simply has not propagated.
    force: bool = False


class DoormatActionResponse(BaseModel):
    ok: bool
    report: str
    state: DoormatState
