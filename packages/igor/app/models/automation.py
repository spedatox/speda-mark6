from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Automation(Base):
    """
    A proactive watcher SPEDA set up for the owner. The actual scheduling and
    polling lives in n8n (the sole automation organ, per CLAUDE.md); this row is
    SPEDA's local metadata mapping a friendly automation to its n8n workflow id.

    `spec` stores the composed block spec (JSON) so the workflow can be rebuilt
    or inspected without re-querying n8n. `kind` is the composition type
    (schedule | web_watch | rss_watch | webhook | gmail_watch). `expires_at`
    powers "track this for a month" — past it, the watcher is auto-deactivated.
    """

    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), default=1)
    # Which in-process agent owns this watcher — it fires back through that
    # agent's /trigger/{agent_id}, so the push is composed in that agent's voice.
    agent_id: Mapped[str] = mapped_column(String(64), default="speda", index=True)
    n8n_workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))
    intent: Mapped[str] = mapped_column(Text)
    spec: Mapped[str] = mapped_column(Text)  # JSON-encoded composer spec
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    last_fired_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
