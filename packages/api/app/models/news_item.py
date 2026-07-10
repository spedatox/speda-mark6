from datetime import datetime

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsItem(Base):
    """
    One deduplicated news headline ingested by the Tier-1 RSS collector
    (app/news/collector.py). Rows are the always-on watch store: the daily
    briefing and "bugün ne oldu?" turns read them via the news_headlines skill.

    The same story published by several outlets collapses to ONE row (dedup by
    canonical URL, then normalized title hash); the extra outlets are recorded
    in `also_in` so corroboration ("also in 3 outlets") is a free signal that
    never spends the Tier-2 API budget. The collector prunes rows older than the
    retention window on each poll.
    """

    __tablename__ = "news_items"
    __table_args__ = (
        Index("ix_news_items_fetched", "fetched_at"),
        Index("ix_news_items_title_hash", "title_hash"),
        Index("ix_news_items_flagged", "flagged"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    title: Mapped[str] = mapped_column(Text)
    title_hash: Mapped[str] = mapped_column(String(64))          # normalized-title dedup key
    outlet: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(48), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    fetched_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    # JSON array of other outlets that ran the same story (dedup corroboration).
    also_in: Mapped[str] = mapped_column(Text, default="[]")
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged_keyword: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
