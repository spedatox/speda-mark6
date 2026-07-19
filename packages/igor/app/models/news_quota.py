from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsQuota(Base):
    """
    The Tier-2 (NewsData.io) daily quota ledger. NewsData's free tier allows
    200 requests/day; this table tracks consumption so the news_deep_dive skill
    can refuse gracefully instead of hard-failing when a bucket is spent.

    One row per UTC date, with per-purpose counters so an owner deep-dive spree
    can't starve the auto-flag corroboration path. The day naturally "resets"
    because a new UTC date has no row yet — no scheduler required. The skill
    increments the relevant counter BEFORE the HTTP call (the request is
    consumed upstream even on most 4xx), refunding only on a connect failure.
    """

    __tablename__ = "news_quota"
    __table_args__ = (UniqueConstraint("day", name="uq_news_quota_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10))            # "YYYY-MM-DD" (UTC)
    deep_dive: Mapped[int] = mapped_column(default=0)
    auto_flag: Mapped[int] = mapped_column(default=0)
    digest: Mapped[int] = mapped_column(default=0)
