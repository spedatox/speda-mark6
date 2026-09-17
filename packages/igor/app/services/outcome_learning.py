# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Background entry point for objective/explicit countermeasure outcomes."""

from app.database import AsyncSessionLocal
from app.services.countermeasures import record_outcome


async def evaluate_countermeasure(*, user_id: int, payload: dict) -> None:
    """Persist an already-observed outcome; never infer success from silence."""
    outcome = payload.get("outcome", "unknown")
    async with AsyncSessionLocal() as db:
        await record_outcome(
            db,
            run_id=int(payload["run_id"]),
            user_id=user_id,
            outcome=outcome,
            outcome_source=payload.get("outcome_source", "model_interpretation"),
            outcome_score=payload.get("outcome_score"),
        )
