"""
Route geometry, kept server-side so no model ever retypes it.

A Google encoded polyline is ~500 characters of delta-encoded, escape-sensitive
text. Asking an LLM to copy one into a JSON fence produced two failure modes in
the wild, both from the same cause:

  * a lone backslash → invalid JSON escape → the client shows PARSE ERROR;
  * a single mistyped character → still valid JSON, but every point after it
    inherits the error, so the route silently walks 19 km off course while
    still looking like a road. That one is worse: it is confidently wrong.

So the model never sees the geometry. `get_route` stores it here and hands back
a ten-character id; the client fetches the real thing. A corrupted id fails
loudly and visibly, which is the only acceptable way for this to fail.

Rows are pruned after RETENTION_DAYS. They only need to outlive the messages
that reference them being re-opened and re-rendered from history.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

RETENTION_DAYS = 45


class RouteGeometry(Base):
    __tablename__ = "route_geometries"
    __table_args__ = (Index("ix_route_geometries_created", "created_at"),)

    # Short opaque token ("r_1a2b3c4d"). Short on purpose: the model has to
    # reproduce it exactly, and ten characters it can either get right or get
    # obviously wrong — unlike 500 characters it can get subtly wrong.
    id: Mapped[str] = mapped_column(String(24), primary_key=True)

    polyline: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(160), default="")
    mode: Mapped[str] = mapped_column(String(16), default="drive")

    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    no_traffic_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    origin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
