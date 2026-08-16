"""Wire schemas for the n8n-facing Outlook watch. See services/outlook_watch.py."""

from pydantic import BaseModel, Field

from app.services.outlook_watch import SEEN_CATEGORY


class OutlookScanRequest(BaseModel):
    """One poll. Every field has a default that is safe to leave alone — n8n
    normally sends only `domain`."""

    domain: str = Field(max_length=253, description="Sender domain, e.g. 'ostimteknik.edu.tr'.")
    max_results: int = Field(default=10, ge=1, le=25)
    newer_than_days: int = Field(default=2, ge=0, le=30)
    unread_only: bool = False
    include_body: bool = True
    body_chars: int = Field(default=2000, ge=200, le=20000)
    category: str = Field(default=SEEN_CATEGORY, max_length=64)
    # Well-known name ('inbox') or folder id. Empty = the whole mailbox, which is
    # what you want when the university's mail is auto-filed into a subfolder.
    folder: str = Field(default="", max_length=256)


class OutlookMessage(BaseModel):
    id: str
    thread_id: str = ""
    from_: str = Field(default="", alias="from")
    from_email: str = ""
    subject: str = ""
    date: str = ""
    snippet: str = ""
    body: str = ""
    web_link: str = ""
    unread: bool = False
    has_attachments: bool = False

    model_config = {"populate_by_name": True}


class OutlookScanResponse(BaseModel):
    # ok | disconnected | error — n8n branches on this, so it is never absent.
    status: str
    query: str = ""
    detail: str = ""
    count: int = 0
    message_ids: list[str] = Field(default_factory=list)
    messages: list[OutlookMessage] = Field(default_factory=list)


class OutlookSeenRequest(BaseModel):
    message_ids: list[str] = Field(default_factory=list, max_length=25)
    category: str = Field(default=SEEN_CATEGORY, max_length=64)


class OutlookSeenResponse(BaseModel):
    status: str
    marked: int = 0
    category: str = SEEN_CATEGORY
    detail: str = ""
