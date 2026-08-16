"""Wire schemas for the n8n-facing mail watch. See services/mail_watch.py."""

from pydantic import BaseModel, Field, model_validator

from app.services.mail_watch import SEEN_LABEL


class MailScanRequest(BaseModel):
    """One poll. Every field has a default that is safe to leave alone — n8n
    normally sends only `domain`, or only `recipient` for a forwarded mailbox."""

    domain: str = Field(
        default="", max_length=253, description="Sender domain, e.g. 'tdv.org'."
    )
    # The forwarding case. Watching a mailbox that is forwarded into Gmail means
    # identifying mail by the address it was DELIVERED to, because the sender may
    # not survive the hop.
    recipient: str = Field(
        default="", max_length=320,
        description="Delivered-to address, e.g. '240106452@ostimteknik.edu.tr'.",
    )

    @model_validator(mode="after")
    def _needs_a_criterion(self):
        """Refuse a watch with neither. `domain` used to be required, so nothing
        could be empty by accident; now that both are optional, the pair has to
        be checked — an empty scan would match the entire mailbox and hand every
        message the owner owns to an agent."""
        if not (self.domain or self.recipient):
            raise ValueError("give a domain, a recipient, or both")
        return self
    # Any extra Gmail query terms, ANDed on: 'is:unread', 'has:attachment', …
    extra_query: str = Field(default="", max_length=256)
    max_results: int = Field(default=10, ge=1, le=25)
    newer_than_days: int = Field(default=2, ge=0, le=30)
    include_body: bool = True
    body_chars: int = Field(default=2000, ge=200, le=20000)
    label: str = Field(default=SEEN_LABEL, max_length=64)


class MailMessage(BaseModel):
    id: str
    thread_id: str = ""
    from_: str = Field(default="", alias="from")
    from_email: str = ""
    subject: str = ""
    date: str = ""
    snippet: str = ""
    body: str = ""

    model_config = {"populate_by_name": True}


class MailScanResponse(BaseModel):
    # ok | disconnected | error — n8n branches on this, so it is never absent.
    status: str
    query: str = ""
    detail: str = ""
    count: int = 0
    message_ids: list[str] = Field(default_factory=list)
    messages: list[MailMessage] = Field(default_factory=list)


class MailSeenRequest(BaseModel):
    message_ids: list[str] = Field(default_factory=list, max_length=100)
    label: str = Field(default=SEEN_LABEL, max_length=64)


class MailSeenResponse(BaseModel):
    status: str
    marked: int = 0
    label: str = SEEN_LABEL
    detail: str = ""
