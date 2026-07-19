from datetime import datetime

from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsWatch(Base):
    """
    A keyword the owner (or an agent on their behalf) wants flagged the instant
    it appears in the RSS stream — e.g. "siber", "OSTİM", a tracked company.

    The Tier-1 collector matches every new headline against active keywords
    (case- and diacritic-insensitive). A hit flags the news_item and escalates
    to a NightCrawler push turn, but only once per keyword per cooldown window
    (`last_hit_at`) so a developing story does not fire twenty notifications.
    Managed from chat via the news_watch skill and from the news router.
    """

    __tablename__ = "news_watches"
    __table_args__ = (Index("ix_news_watches_active", "active"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(128))
    # Normalized (lowercased, diacritics folded) form used for matching.
    keyword_norm: Mapped[str] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="owner")  # owner | <agent_id>
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_hit_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    hit_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
