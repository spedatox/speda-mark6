# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Reusable countermeasure strategies and auditable executions."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pattern import Countermeasure, CountermeasurePattern, CountermeasureRun

AUTONOMY_LEVELS = {"observe", "adapt", "suggest", "confirm", "execute"}
OUTCOMES = {"success", "partial", "failure", "unknown"}


async def create_countermeasure(
    db: AsyncSession,
    *,
    user_id: int,
    namespace: str,
    title: str,
    strategy: str,
    pattern_ids: list[int],
    trigger_json: dict | None = None,
    autonomy_level: str = "observe",
    created_by: str = "ace",
) -> Countermeasure:
    if autonomy_level not in AUTONOMY_LEVELS:
        raise ValueError(f"unknown autonomy level: {autonomy_level}")
    row = Countermeasure(
        user_id=user_id,
        namespace=namespace,
        title=title,
        strategy=strategy,
        trigger_json=trigger_json or {},
        autonomy_level=autonomy_level,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    for pattern_id in dict.fromkeys(pattern_ids):
        db.add(CountermeasurePattern(
            countermeasure_id=row.id,
            pattern_observation_id=pattern_id,
        ))
    await db.commit()
    await db.refresh(row)
    return row


async def start_run(
    db: AsyncSession,
    *,
    countermeasure_id: int,
    user_id: int,
    agent_id: str,
    session_id: int | None,
    request_id: str,
    trigger_patterns: list[int],
    context_snapshot: dict | None = None,
    tool_call_ids: list[int] | None = None,
) -> CountermeasureRun:
    countermeasure = await db.get(Countermeasure, countermeasure_id)
    if countermeasure is None or countermeasure.user_id != user_id:
        raise ValueError("countermeasure not found")
    run = CountermeasureRun(
        countermeasure_id=countermeasure_id,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        request_id=request_id,
        trigger_patterns=trigger_patterns,
        context_snapshot=context_snapshot or {},
        tool_call_ids=tool_call_ids or [],
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def record_outcome(
    db: AsyncSession,
    *,
    run_id: int,
    user_id: int,
    outcome: str,
    outcome_source: str,
    outcome_score: float | None = None,
) -> CountermeasureRun:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome}")
    run = (
        await db.execute(
            select(CountermeasureRun).where(
                CountermeasureRun.id == run_id,
                CountermeasureRun.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise ValueError("countermeasure run not found")
    if run.status == "completed":
        return run

    run.status = "completed"
    run.outcome = outcome
    run.outcome_source = outcome_source
    run.outcome_score = outcome_score
    run.completed_at = datetime.now(timezone.utc)

    countermeasure = await db.get(Countermeasure, run.countermeasure_id)
    countermeasure.attempt_count += 1
    if outcome == "success":
        countermeasure.positive_count += 1
    elif outcome == "failure":
        countermeasure.negative_count += 1
    elif outcome == "partial":
        countermeasure.partial_count += 1
    known = (
        countermeasure.positive_count
        + countermeasure.negative_count
        + countermeasure.partial_count
    )
    # A small Bayesian prior avoids 0%/100% claims after one outcome. Unknown
    # runs are attempts but deliberately do not count as positive evidence.
    countermeasure.effectiveness_score = (
        1.0 + countermeasure.positive_count + 0.5 * countermeasure.partial_count
    ) / (2.0 + known)
    countermeasure.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    return run


async def retire_pattern_countermeasures(
    db: AsyncSession, *, user_id: int, pattern_id: int
) -> int:
    rows = list(
        (
            await db.execute(
                select(Countermeasure)
                .join(
                    CountermeasurePattern,
                    CountermeasurePattern.countermeasure_id == Countermeasure.id,
                )
                .where(
                    Countermeasure.user_id == user_id,
                    CountermeasurePattern.pattern_observation_id == pattern_id,
                )
            )
        ).scalars().all()
    )
    for row in rows:
        row.status = "retired"
    if rows:
        await db.commit()
    return len(rows)
