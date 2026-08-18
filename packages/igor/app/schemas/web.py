"""Wire schemas for the n8n-facing web watch. See services/web_watch.py."""

from pydantic import BaseModel, Field


class WebScanRequest(BaseModel):
    """One poll of one page."""

    # Stable key for this page's snapshot history. Changing it re-baselines the
    # watch (one silent scan), so keep it fixed for the life of the watcher.
    watch_id: str = Field(max_length=64, description="e.g. 'akademik_takvim'.")
    url: str = Field(max_length=2048)
    # Comma-separated or a list. Empty = report ANY new line on the page.
    look_for: str | list[str] = ""
    # Regex for lines to drop before diffing — clocks, counters, "son güncelleme".
    ignore: str = Field(default="", max_length=512)
    max_added: int = Field(default=40, ge=1, le=200)


class WebScanResponse(BaseModel):
    # ok | baseline | error — n8n branches on this, so it is never absent.
    status: str
    changed: bool = False
    watch_id: str = ""
    url: str = ""
    title: str = ""
    detail: str = ""
    fingerprint: str = ""
    added: list[str] = Field(default_factory=list)
    added_count: int = 0
    truncated: bool = False
    removed_count: int = 0
    matched_terms: list[str] = Field(default_factory=list)
    # True when the plain HTTP fetch found nothing readable and the scan fell
    # back to a browser render (app/services/web_watch.py). Reported rather than
    # left implicit: a watch that quietly started costing a render every poll is
    # something the owner should be able to see, not deduce.
    rendered: bool = False


class WebAckRequest(BaseModel):
    watch_id: str = Field(max_length=64)
    # From the scan response. Guards against committing a snapshot the agent was
    # never actually told about.
    fingerprint: str = Field(default="", max_length=64)


class WebAckResponse(BaseModel):
    # ok | noop | stale
    status: str
    watch_id: str = ""
    fingerprint: str = ""
    detail: str = ""
