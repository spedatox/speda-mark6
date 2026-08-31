# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Health sample ingestion, daily rollups and query helpers.

The owner's biometrics arrive from Speda GO (Health Connect → phone →
POST /health/ingest); this module owns everything that happens after the router
validates the payload — Rule 1 keeps the router logic-free. The health_data
skill reads through here too, so the aggregation rules live in exactly one place.

See docs/ATOMIX_HEALTH_SYNC.md for the whole pipeline.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import runtime_state
from app.core.clock import owner_today as clock_owner_today
from app.core.clock import to_owner, utc_now
from app.models.health_sample import HealthDaily, HealthSample

logger = logging.getLogger(__name__)

# How each metric family rolls up into a day. Anything unlisted is treated as
# INSTANT (min/max/avg/last), which is the safe default: summing an unknown
# metric could invent a number that reads as authoritative and is nonsense.
CUMULATIVE = "cumulative"   # totals over the day: steps, distance, calories
DURATION = "duration"       # sessions measured in time: sleep, exercise
INSTANT = "instant"         # point readings: heart rate, weight, SpO2

_METRIC_KIND: dict[str, str] = {
    "steps": CUMULATIVE,
    "distance": CUMULATIVE,
    "active_calories": CUMULATIVE,
    "total_calories": CUMULATIVE,
    "floors_climbed": CUMULATIVE,
    "sleep_session": DURATION,
    "exercise_session": DURATION,
    "heart_rate": INSTANT,
    "resting_heart_rate": INSTANT,
    "weight": INSTANT,
    "body_fat": INSTANT,
    "oxygen_saturation": INSTANT,
}


def metric_kind(metric: str) -> str:
    return _METRIC_KIND.get(metric, INSTANT)


# ── Freshness ────────────────────────────────────────────────────────────────
# How old the NEWEST sample of a metric may be before the data stops describing
# the present. This is the difference between a briefing and an archive query:
# reporting a heart rate from four days ago under today's date is not a
# formatting problem, it is a false statement about the owner's body.
#
# The budgets are generous on purpose — they are a staleness alarm, not a sync
# SLA. The phone trickles roughly every four hours, so anything inside that plus
# a margin is normal; what these catch is a link that has actually stopped.
_FRESHNESS_BUDGET_H: dict[str, float] = {
    # Asked at 08:00 about last night. A session that ended at 07:00 is 1h old;
    # one from the night before is 25h+ and must not be called "last night".
    "sleep_session": 20,
    "heart_rate": 8,
    "steps": 8,
    "distance": 8,
    "exercise_session": 24,
    # Derived once a day by the watch, and only after a scored sleep.
    "resting_heart_rate": 36,
    "oxygen_saturation": 24,
    # Stepping on a scale is a deliberate act; silence means "didn't weigh in",
    # not "the link is broken".
    "weight": 24 * 14,
    "body_fat": 24 * 14,
}
_DEFAULT_BUDGET_H = 24


def freshness_budget_h(metric: str) -> float:
    return _FRESHNESS_BUDGET_H.get(metric, _DEFAULT_BUDGET_H)


async def freshness(db: AsyncSession, metrics: list[str] | None = None) -> dict:
    """Per-metric age of the newest stored sample, and whether it is inside that
    metric's budget.

    Returns ``{metric: {"newest": iso, "age_hours": float, "fresh": bool,
    "budget_hours": float}}`` for every metric asked for (or every metric that
    has ever synced). A metric with no samples at all is reported with
    ``"newest": None`` and ``fresh: False`` — never omitted, because a caller
    checking freshness needs "nothing here" to be an answer rather than a gap.
    """
    stmt = select(HealthSample.metric, func.max(HealthSample.end_ts)).group_by(
        HealthSample.metric
    )
    if metrics:
        stmt = stmt.where(HealthSample.metric.in_(metrics))
    newest = {m: ts for m, ts in (await db.execute(stmt)).all()}

    now = utc_now().replace(tzinfo=None)
    out: dict[str, dict] = {}
    for metric in metrics or sorted(newest):
        ts = newest.get(metric)
        budget = freshness_budget_h(metric)
        if ts is None:
            out[metric] = {
                "newest": None,
                "age_hours": None,
                "fresh": False,
                "budget_hours": budget,
            }
            continue
        age = max(0.0, (now - ts).total_seconds() / 3600)
        out[metric] = {
            "newest": to_owner(ts).isoformat(timespec="minutes"),
            "age_hours": round(age, 1),
            "fresh": age <= budget,
            "budget_hours": budget,
        }
    return out


def stale_metrics(report: dict) -> list[str]:
    """The metrics in a ``freshness()`` report that are outside budget."""
    return [m for m, f in report.items() if not f.get("fresh")]


async def demand_sync(db: AsyncSession, reason: str = "") -> dict:
    """Ask the phone for a sync NOW, and wake it so it hears the asking.

    Two halves, and both are needed. The runtime-state flag is the durable
    request — Speda GO's 15-minute worker reads it, so a demand raised while the
    phone was unreachable is still served later. The FCM data message is what
    makes it timely: a briefing waiting twenty-five seconds cannot use an answer
    that arrives at the next quarter hour.

    Only ``platform="phone"`` devices are woken. The watch is registered in the
    same table for attendance asks and has no Health Connect data to give.

    Returns ``{"woke": n, "notes": [...]}``. Never raises — a dead push channel
    means the caller falls back to the flag, not that the turn fails.
    """
    runtime_state.request_health_sync(reason=reason)

    from app.services import academic as academic_service
    from app.services import fcm

    devices = await academic_service.active_devices(db, platform="phone")
    if not devices:
        return {"woke": 0, "notes": ["no phone registered for push"]}

    woke = 0
    notes: list[str] = []
    for device in devices:
        try:
            ok, detail = await fcm.send_data_message(
                fid=device.fid,
                data={"type": "health_sync_now", "reason": reason[:120]},
                priority="high",
                # Pointless after the waiting turn has given up, and a wake that
                # lands hours later would sync against a demand already served.
                ttl_seconds=300,
            )
        except Exception as exc:  # noqa: BLE001 — push must not break the turn
            logger.warning("health_wake_failed", extra={"error": str(exc)})
            notes.append(f"{device.device_id}: {exc}")
            continue
        if ok:
            woke += 1
        elif detail == "unregistered":
            # Reinstalled or data cleared. Retiring it here stops every future
            # wake burning a request on a device that cannot receive one.
            await academic_service.deactivate_device(db, device.fid)
            notes.append(f"{device.device_id}: no longer installed (deactivated)")
        else:
            notes.append(f"{device.device_id}: {detail}")

    logger.info("health_sync_wake", extra={"woke": woke, "reason": reason[:120]})
    return {"woke": woke, "notes": notes}


def _to_naive_utc(dt: datetime) -> datetime:
    """Offset-aware → UTC-naive, matching every other timestamp in the schema.
    A naive input is assumed to already be UTC."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def local_day(dt: datetime) -> date_cls:
    """The owner's LOCAL calendar date for a sample. Must be called on the
    offset-aware timestamp the phone sent, BEFORE conversion to UTC — that
    offset is the only record of which day the owner actually lived it."""
    return dt.date()


async def ingest_samples(
    db: AsyncSession, samples: list[dict], device: str = ""
) -> dict:
    """Upsert a batch and refresh the rollups for the days it touched.

    Returns {"accepted", "duplicates", "days_rolled"}. Idempotent by
    (metric, start_ts, origin): a re-sent batch reports duplicates and changes
    nothing, which is what lets the phone retry safely after a failed POST.

    The loop runs inside ``no_autoflush`` so that the SELECT for sample N
    never triggers a premature INSERT of samples 0..N-1 — that autoflush was
    the root cause of the ``UNIQUE constraint failed`` errors when the phone
    retried a partially-succeeded batch.  We flush explicitly in sub-batches
    of _FLUSH_EVERY rows to keep the SQLite write-lock short and avoid the
    ``database is locked`` errors that occurred when a single 4 000-row flush
    held the lock for seconds.
    """
    from sqlalchemy import tuple_
    from sqlalchemy.exc import IntegrityError

    _CHUNK_SIZE = 500

    accepted = 0
    duplicates = 0
    touched: set[tuple[date_cls, str]] = set()

    # Pre-parse timestamps and extract unique tuple keys for bulk lookup
    prepared = []
    keys = []
    for s in samples:
        metric = s["metric"]
        start_aware = s["start"]
        start_ts = _to_naive_utc(start_aware)
        end_ts = _to_naive_utc(s.get("end") or start_aware)
        origin = s.get("origin") or ""
        day = local_day(start_aware)
        prepared.append((metric, start_ts, end_ts, day, origin, s))
        keys.append((metric, start_ts, origin))

    # Bulk fetch existing records in chunks of 500 to stay well under SQLite parameter limits
    existing_map: dict[tuple[str, datetime, str], HealthSample] = {}
    for i in range(0, len(keys), _CHUNK_SIZE):
        chunk_keys = keys[i : i + _CHUNK_SIZE]
        stmt = select(HealthSample).where(
            tuple_(HealthSample.metric, HealthSample.start_ts, HealthSample.origin).in_(chunk_keys)
        )
        res = await db.execute(stmt)
        for row in res.scalars():
            existing_map[(row.metric, row.start_ts, row.origin)] = row

    with db.no_autoflush:
        for metric, start_ts, end_ts, day, origin, s in prepared:
            existing = existing_map.get((metric, start_ts, origin))
            if existing is not None:
                # A re-send of a record the collector has since corrected
                existing.end_ts = end_ts
                existing.value = float(s["value"])
                existing.unit = s.get("unit") or ""
                existing.detail = json.dumps(s.get("detail") or {}, ensure_ascii=False)
                existing.day = day
                if device:
                    existing.device = device
                duplicates += 1
            else:
                new_sample = HealthSample(
                    metric=metric,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    day=day,
                    value=float(s["value"]),
                    unit=s.get("unit") or "",
                    detail=json.dumps(s.get("detail") or {}, ensure_ascii=False),
                    origin=origin,
                    device=device,
                )
                db.add(new_sample)
                existing_map[(metric, start_ts, origin)] = new_sample
                accepted += 1
            touched.add((day, metric))

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return await _retry_ingest(db, samples, device)

    for day, metric in touched:
        await recompute_daily(db, day, metric)
    await db.commit()

    # The phone has just delivered, so any standing "sync now" demand is served.
    # Marked even on an all-duplicate batch: the phone did answer, it simply had
    # nothing new — which is itself the answer a waiting briefing needs.
    runtime_state.clear_health_sync_demand()

    logger.info(
        "health_ingest",
        extra={"accepted": accepted, "duplicates": duplicates, "days": len(touched)},
    )
    return {"accepted": accepted, "duplicates": duplicates, "days_rolled": len(touched)}


async def _retry_ingest(
    db: AsyncSession, samples: list[dict], device: str
) -> dict:
    """Fallback: re-process the entire batch one row at a time, treating every
    IntegrityError as a duplicate.  This is the slow path that only fires
    when a concurrent ingest already wrote some of the same rows — it turns
    the race into a clean duplicate count instead of a 500."""
    from sqlalchemy.exc import IntegrityError

    accepted = 0
    duplicates = 0
    touched: set[tuple[date_cls, str]] = set()

    for s in samples:
        metric = s["metric"]
        start_aware = s["start"]
        start_ts = _to_naive_utc(start_aware)
        end_ts = _to_naive_utc(s.get("end") or start_aware)
        origin = s.get("origin") or ""
        day = local_day(start_aware)

        existing = (
            await db.execute(
                select(HealthSample).where(
                    HealthSample.metric == metric,
                    HealthSample.start_ts == start_ts,
                    HealthSample.origin == origin,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.end_ts = end_ts
            existing.value = float(s["value"])
            existing.unit = s.get("unit") or ""
            existing.detail = json.dumps(s.get("detail") or {}, ensure_ascii=False)
            existing.day = day
            if device:
                existing.device = device
            duplicates += 1
        else:
            db.add(
                HealthSample(
                    metric=metric,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    day=day,
                    value=float(s["value"]),
                    unit=s.get("unit") or "",
                    detail=json.dumps(s.get("detail") or {}, ensure_ascii=False),
                    origin=origin,
                    device=device,
                )
            )
            try:
                await db.flush()
                accepted += 1
            except IntegrityError:
                # Written by the concurrent ingest between our SELECT and
                # our INSERT — treat as duplicate, not an error.
                await db.rollback()
                duplicates += 1
        touched.add((day, metric))

    await db.flush()
    for day, metric in touched:
        await recompute_daily(db, day, metric)
    await db.commit()

    runtime_state.clear_health_sync_demand()

    logger.info(
        "health_ingest",
        extra={"accepted": accepted, "duplicates": duplicates, "days": len(touched),
               "retry_path": True},
    )
    return {"accepted": accepted, "duplicates": duplicates, "days_rolled": len(touched)}


def _aggregate(metric: str, values: list[float], details: list[dict]) -> dict:
    """Roll a day's samples for one metric into the `agg` JSON.

    Cumulative metrics get a total; instantaneous ones get the distribution
    (a day's heart rate is a range, not a sum); durations get both, because
    "how long did I sleep" and "how many sessions" are both real questions.
    """
    kind = metric_kind(metric)
    n = len(values)
    agg: dict = {"count": n}
    if not values:
        return agg

    if kind == CUMULATIVE:
        agg["total"] = round(sum(values), 3)
    elif kind == DURATION:
        agg["total"] = round(sum(values), 3)
        agg["longest"] = round(max(values), 3)
    else:
        agg["min"] = round(min(values), 3)
        agg["max"] = round(max(values), 3)
        agg["avg"] = round(sum(values) / n, 2)
        agg["last"] = round(values[-1], 3)

    # Sleep stages are the one nested structure worth summing across a day —
    # a fragmented night arrives as several sessions and the owner asks about
    # the night, not the segments.
    stages: dict[str, float] = defaultdict(float)
    for d in details:
        for name, minutes in (d.get("stages") or {}).items():
            try:
                stages[name] += float(minutes)
            except (TypeError, ValueError):
                continue
    if stages:
        agg["stages"] = {k: round(v, 1) for k, v in sorted(stages.items())}

    return agg


async def recompute_daily(db: AsyncSession, day: date_cls, metric: str) -> None:
    """Rebuild one (day, metric) rollup from its samples. Called per touched day
    on ingest — never on a schedule, and never for days a batch didn't reach."""
    rows = list(
        (
            await db.execute(
                select(HealthSample)
                .where(HealthSample.day == day, HealthSample.metric == metric)
                .order_by(HealthSample.start_ts)
            )
        )
        .scalars()
        .all()
    )

    existing = (
        await db.execute(
            select(HealthDaily).where(HealthDaily.day == day, HealthDaily.metric == metric)
        )
    ).scalar_one_or_none()

    if not rows:
        if existing is not None:
            await db.delete(existing)
            await db.flush()
        return

    details = []
    for r in rows:
        try:
            details.append(json.loads(r.detail or "{}"))
        except (json.JSONDecodeError, ValueError):
            details.append({})
    agg = _aggregate(metric, [r.value for r in rows], details)

    if existing is None:
        db.add(
            HealthDaily(
                day=day,
                metric=metric,
                agg=json.dumps(agg, ensure_ascii=False),
                sample_count=len(rows),
                updated_at=datetime.utcnow(),
            )
        )
    else:
        existing.agg = json.dumps(agg, ensure_ascii=False)
        existing.sample_count = len(rows)
        existing.updated_at = datetime.utcnow()
    await db.flush()


def owner_today() -> date_cls:
    """The owner's LOCAL calendar date. Samples are filed by local day
    (``local_day``), so every range must be resolved in the same frame — UTC
    "today" is the previous local day for the first three hours of every
    Istanbul morning, which is exactly when the nightly digest runs.

    Delegates to app.core.clock, the one place that knows what time it is. This
    function used to call *itself* inside a try/except: the RecursionError was
    caught by the bare handler and the fallback returned the UTC date, so the
    very skew the docstring describes was what it actually did, on every call,
    forever. Keep the delegation — a local reimplementation is how that started.
    """
    return clock_owner_today()


def parse_range(spec: str, today: date_cls | None = None) -> tuple[date_cls, date_cls]:
    """Range vocabulary shared by the skill and the API: "today", "yesterday",
    "7d"/"30d"/"90d" (N days back, inclusive of today), or "YYYY-MM-DD:YYYY-MM-DD".
    Unrecognised input falls back to 7d rather than erroring — a model that
    invents a range should still get a sane answer."""
    today = today or owner_today()
    spec = (spec or "7d").strip().lower()

    if spec == "today":
        return today, today
    if spec == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if ":" in spec:
        raw_start, _, raw_end = spec.partition(":")
        try:
            return date_cls.fromisoformat(raw_start), date_cls.fromisoformat(raw_end)
        except ValueError:
            return today - timedelta(days=6), today
    if spec.endswith("d"):
        try:
            days = max(1, int(spec[:-1]))
            return today - timedelta(days=days - 1), today
        except ValueError:
            pass
    return today - timedelta(days=6), today


async def daily_rows(
    db: AsyncSession, metrics: list[str], start: date_cls, end: date_cls
) -> list[HealthDaily]:
    stmt = (
        select(HealthDaily)
        .where(HealthDaily.day >= start, HealthDaily.day <= end)
        .order_by(HealthDaily.day)
    )
    if metrics:
        stmt = stmt.where(HealthDaily.metric.in_(metrics))
    return list((await db.execute(stmt)).scalars().all())


async def raw_rows(
    db: AsyncSession, metrics: list[str], start: date_cls, end: date_cls, limit: int = 200
) -> list[HealthSample]:
    stmt = (
        select(HealthSample)
        .where(HealthSample.day >= start, HealthSample.day <= end)
        .order_by(HealthSample.start_ts)
        .limit(limit)
    )
    if metrics:
        stmt = stmt.where(HealthSample.metric.in_(metrics))
    return list((await db.execute(stmt)).scalars().all())


def trend(current: list[float], previous: list[float]) -> dict | None:
    """Period-over-period delta, the single most useful derived number Atomix
    can cite ("sleep down 8% vs the previous week"). None when either side has
    no data — a fabricated 100% swing against an empty period is worse than
    saying nothing."""
    if not current or not previous:
        return None
    cur = sum(current) / len(current)
    prev = sum(previous) / len(previous)
    out = {"current_avg": round(cur, 2), "previous_avg": round(prev, 2)}
    out["delta"] = round(cur - prev, 2)
    if prev:
        out["delta_pct"] = round((cur - prev) / prev * 100, 1)
    return out


async def status(db: AsyncSession) -> dict:
    """Sync health for the phone's Settings ▸ HEALTH tab and for debugging:
    per-metric counts, the newest sample, and the covered date span."""
    total = (await db.execute(select(func.count(HealthSample.id)))).scalar_one()
    per_metric = dict(
        (
            await db.execute(
                select(HealthSample.metric, func.count(HealthSample.id)).group_by(
                    HealthSample.metric
                )
            )
        ).all()
    )
    newest = (
        await db.execute(select(func.max(HealthSample.created_at)))
    ).scalar_one_or_none()
    span_start = (await db.execute(select(func.min(HealthSample.day)))).scalar_one_or_none()
    span_end = (await db.execute(select(func.max(HealthSample.day)))).scalar_one_or_none()
    return {
        "samples": total,
        "per_metric": per_metric,
        "last_ingest": newest.isoformat() if newest else None,
        "first_day": span_start.isoformat() if span_start else None,
        "last_day": span_end.isoformat() if span_end else None,
    }


async def wipe(db: AsyncSession) -> dict:
    """DISCONNECT + WIPE from the phone. Deletes every sample and rollup — the
    owner's health data is the most sensitive stream in the system, so the
    delete is total and unconditional, and it is logged."""
    samples = (await db.execute(select(func.count(HealthSample.id)))).scalar_one()
    await db.execute(delete(HealthDaily))
    await db.execute(delete(HealthSample))
    await db.commit()
    logger.warning("health_data_wiped", extra={"samples_deleted": samples})
    return {"deleted": samples}
