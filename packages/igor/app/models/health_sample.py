from datetime import date as date_cls
from datetime import datetime

from sqlalchemy import Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HealthSample(Base):
    """
    One biometric reading synced from the owner's phone — Samsung Health (or any
    other collector) → Health Connect → Heartbreaker Core → POST /health/ingest.
    See docs/ATOMIX_HEALTH_SYNC.md.

    Deliberately metric-generic: `metric` is a free string ("steps",
    "sleep_session", "heart_rate", …) and anything the flat columns can't carry
    — sleep stages, exercise type — rides in `detail` as JSON. Adding a record
    type is a checkbox on the phone, never a migration here.

    The unique constraint on (metric, start_ts, origin) is what makes ingest
    idempotent: the phone advances its Health Connect changes-token only after a
    successful POST, so a failed sync re-sends the same rows next cycle and they
    must collapse, not duplicate.
    """

    __tablename__ = "health_samples"
    __table_args__ = (
        UniqueConstraint("metric", "start_ts", "origin", name="uq_health_sample_identity"),
        Index("ix_health_samples_metric_start", "metric", "start_ts"),
        Index("ix_health_samples_day", "day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(String(48))
    # Both stored UTC-naive, like every other timestamp in the schema. `day` is
    # kept separately because it is the owner's LOCAL date — derived from the
    # offset the phone sent, which UTC alone cannot recover (a 00:30 +03:00
    # bedtime is 21:30 UTC the PREVIOUS day, and belongs to neither the previous
    # night's tally nor a UTC-derived rollup).
    start_ts: Mapped[datetime] = mapped_column()
    end_ts: Mapped[datetime] = mapped_column()
    day: Mapped[date_cls] = mapped_column()
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(24), default="")
    # JSON object; "{}" when the metric needs no extra structure.
    detail: Mapped[str] = mapped_column(Text, default="{}")
    # The writing app as Health Connect reports it (e.g.
    # com.sec.android.app.shealth). Part of the identity key so the same walk
    # recorded by two apps stays two rows rather than silently overwriting.
    origin: Mapped[str] = mapped_column(String(128), default="")
    device: Mapped[str] = mapped_column(String(96), default="")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class HealthDaily(Base):
    """
    Per-(day, metric) rollup, recomputed on ingest for exactly the days a batch
    touched. The health_data skill answers almost everything from this table —
    scanning raw samples for "how did I sleep this week?" would mean thousands of
    heart-rate rows for a seven-number answer.

    `agg` is a JSON object whose keys depend on the metric family (sum/count for
    cumulative totals, min/max/avg/last for instantaneous readings, plus merged
    sleep stages) — see services/health.py `_aggregate`.
    """

    __tablename__ = "health_daily"
    __table_args__ = (
        UniqueConstraint("day", "metric", name="uq_health_daily_identity"),
        Index("ix_health_daily_day", "day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date_cls] = mapped_column()
    metric: Mapped[str] = mapped_column(String(48))
    agg: Mapped[str] = mapped_column(Text, default="{}")
    sample_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
