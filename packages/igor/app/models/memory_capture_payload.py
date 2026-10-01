# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Complete, durable intake for one capture; legacy evidence alone cannot resume."""
from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class MemoryCapturePayload(Base):
    __tablename__ = "memory_capture_payloads"
    capture_job_id: Mapped[int] = mapped_column(ForeignKey("memory_capture_jobs.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    identity_hash: Mapped[str] = mapped_column(String(64))
