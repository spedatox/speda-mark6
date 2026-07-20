"""
Atomix health-sync tests (docs/ATOMIX_HEALTH_SYNC.md §5.1).

Covers the three things the design calls out as unit-testable — ingest
idempotency, daily-rollup math, and range parsing — plus the auth boundary,
which matters more than the rest put together: /health is unauthenticated and
the biometrics endpoints live under the same prefix.

Runs against a real in-memory SQLite so the unique constraint and the upsert
path are genuinely exercised, not mocked.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.middleware.auth import UNPROTECTED_PATHS, UNPROTECTED_PREFIXES
from app.models.health_sample import HealthDaily, HealthSample
from app.services import health as hs

TZ = timezone(timedelta(hours=3))  # the owner's +03:00


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


def _sample(metric, start, value, unit="", detail=None, end=None, origin="shealth"):
    return {
        "metric": metric,
        "start": start,
        "end": end or start,
        "value": value,
        "unit": unit,
        "detail": detail or {},
        "origin": origin,
    }


# ── Auth boundary ────────────────────────────────────────────────────────────


def test_health_subpaths_are_not_exempt_from_auth():
    # /health is exempt as an EXACT match. If this ever becomes a prefix match,
    # the owner's biometrics become world-readable — hence the explicit test.
    assert "/health" in UNPROTECTED_PATHS
    for path in ("/health/ingest", "/health/status", "/health/data"):
        assert path not in UNPROTECTED_PATHS
        assert not path.startswith(UNPROTECTED_PREFIXES)


# ── Ingest idempotency ───────────────────────────────────────────────────────


async def test_ingest_is_idempotent_on_resend(db):
    batch = [
        _sample("steps", datetime(2026, 7, 18, 9, 0, tzinfo=TZ), 8412, "count"),
        _sample("heart_rate", datetime(2026, 7, 18, 14, 0, tzinfo=TZ), 61, "bpm"),
    ]
    first = await hs.ingest_samples(db, batch, device="Galaxy S24 Ultra")
    assert first["accepted"] == 2 and first["duplicates"] == 0

    # The phone's POST "failed" (response lost), so it re-sends the same batch.
    second = await hs.ingest_samples(db, batch, device="Galaxy S24 Ultra")
    assert second["accepted"] == 0 and second["duplicates"] == 2

    rows = (await db.execute(HealthSample.__table__.select())).all()
    assert len(rows) == 2


async def test_resend_with_corrected_value_updates_in_place(db):
    start = datetime(2026, 7, 18, 9, 0, tzinfo=TZ)
    await hs.ingest_samples(db, [_sample("steps", start, 8000, "count")])
    await hs.ingest_samples(db, [_sample("steps", start, 8412, "count")])

    samples = list((await db.execute(HealthSample.__table__.select())).all())
    assert len(samples) == 1
    daily = list((await db.execute(HealthDaily.__table__.select())).all())
    assert json.loads(daily[0].agg)["total"] == 8412  # rollup followed the edit


async def test_same_reading_from_two_apps_stays_two_rows(db):
    start = datetime(2026, 7, 18, 9, 0, tzinfo=TZ)
    await hs.ingest_samples(
        db,
        [
            _sample("steps", start, 8412, "count", origin="shealth"),
            _sample("steps", start, 8390, "count", origin="fit"),
        ],
    )
    rows = list((await db.execute(HealthSample.__table__.select())).all())
    assert len(rows) == 2  # origin is part of the identity key


# ── Local-day attribution ────────────────────────────────────────────────────


async def test_day_comes_from_the_local_offset_not_utc(db):
    # 00:30 +03:00 on the 19th is 21:30 UTC on the 18th. The owner lived it on
    # the 19th, and that is the day the rollup must land on.
    await hs.ingest_samples(
        db, [_sample("steps", datetime(2026, 7, 19, 0, 30, tzinfo=TZ), 120, "count")]
    )
    sample = (await db.execute(HealthSample.__table__.select())).first()
    assert sample.day == date(2026, 7, 19)
    assert sample.start_ts == datetime(2026, 7, 18, 21, 30)  # stored UTC-naive


# ── Rollup math ──────────────────────────────────────────────────────────────


async def test_cumulative_metrics_total_and_instant_metrics_distribute(db):
    day = datetime(2026, 7, 18, tzinfo=TZ)
    await hs.ingest_samples(
        db,
        [
            _sample("steps", day.replace(hour=9), 4000, "count"),
            _sample("steps", day.replace(hour=18), 4412, "count"),
            _sample("heart_rate", day.replace(hour=9), 58, "bpm"),
            _sample("heart_rate", day.replace(hour=14), 61, "bpm"),
            _sample("heart_rate", day.replace(hour=20), 72, "bpm"),
        ],
    )
    rollups = {
        r.metric: json.loads(r.agg)
        for r in (await db.execute(HealthDaily.__table__.select())).all()
    }
    assert rollups["steps"]["total"] == 8412
    assert rollups["steps"]["count"] == 2
    # A day of heart rate is a range — summing it would be nonsense.
    assert "total" not in rollups["heart_rate"]
    assert rollups["heart_rate"] == {
        "count": 3, "min": 58, "max": 72, "avg": 63.67, "last": 72,
    }


async def test_unknown_metric_is_never_summed(db):
    # The safe default matters: inventing a total for an unrecognised metric
    # would read as authoritative and be meaningless.
    await hs.ingest_samples(
        db, [_sample("vo2_max", datetime(2026, 7, 18, 9, tzinfo=TZ), 47.5, "ml/kg/min")]
    )
    agg = json.loads((await db.execute(HealthDaily.__table__.select())).first().agg)
    assert "total" not in agg and agg["avg"] == 47.5


async def test_fragmented_sleep_sums_its_stages(db):
    night = datetime(2026, 7, 18, 23, 41, tzinfo=TZ)
    await hs.ingest_samples(
        db,
        [
            _sample("sleep_session", night, 240, "min",
                    detail={"stages": {"deep": 60, "rem": 50, "light": 130}}),
            _sample("sleep_session", night.replace(hour=3, minute=10), 151, "min",
                    detail={"stages": {"deep": 14, "rem": 38, "light": 71, "awake": 28}},
                    origin="shealth2"),
        ],
    )
    agg = json.loads((await db.execute(HealthDaily.__table__.select())).first().agg)
    assert agg["total"] == 391          # the night, not the segments
    assert agg["longest"] == 240
    assert agg["stages"] == {"awake": 28.0, "deep": 74.0, "light": 201.0, "rem": 88.0}


async def test_rollup_is_dropped_when_its_samples_are(db):
    day = date(2026, 7, 18)
    await hs.ingest_samples(
        db, [_sample("steps", datetime(2026, 7, 18, 9, tzinfo=TZ), 4000, "count")]
    )
    await db.execute(HealthSample.__table__.delete())
    await hs.recompute_daily(db, day, "steps")
    assert (await db.execute(HealthDaily.__table__.select())).first() is None


# ── Range parsing ────────────────────────────────────────────────────────────


def test_parse_range_vocabulary():
    today = date(2026, 7, 20)
    assert hs.parse_range("today", today) == (today, today)
    assert hs.parse_range("yesterday", today) == (date(2026, 7, 19), date(2026, 7, 19))
    # "7d" is inclusive of today — 7 days total, not 8.
    assert hs.parse_range("7d", today) == (date(2026, 7, 14), today)
    assert hs.parse_range("30d", today) == (date(2026, 6, 21), today)
    assert hs.parse_range("2026-07-01:2026-07-05", today) == (
        date(2026, 7, 1), date(2026, 7, 5),
    )
    # A model that invents a range still gets a sane window rather than an error.
    for junk in ("last fortnight", "", "xd", "2026-13-99:nope"):
        assert hs.parse_range(junk, today) == (date(2026, 7, 14), today)


def test_trend_refuses_to_compare_against_nothing():
    # A 100% swing measured against an empty period is worse than silence.
    assert hs.trend([1.0], []) is None
    assert hs.trend([], [1.0]) is None
    t = hs.trend([8.0, 6.0], [10.0, 10.0])
    assert t["current_avg"] == 7.0 and t["previous_avg"] == 10.0
    assert t["delta"] == -3.0 and t["delta_pct"] == -30.0


# ── Status + wipe ────────────────────────────────────────────────────────────


async def test_status_then_wipe_leaves_nothing(db):
    await hs.ingest_samples(
        db,
        [
            _sample("steps", datetime(2026, 7, 18, 9, tzinfo=TZ), 4000, "count"),
            _sample("weight", datetime(2026, 7, 19, 7, tzinfo=TZ), 78.4, "kg"),
        ],
        device="Galaxy S24 Ultra",
    )
    st = await hs.status(db)
    assert st["samples"] == 2
    assert st["per_metric"] == {"steps": 1, "weight": 1}
    assert st["first_day"] == "2026-07-18" and st["last_day"] == "2026-07-19"

    assert (await hs.wipe(db))["deleted"] == 2
    after = await hs.status(db)
    assert after["samples"] == 0 and after["per_metric"] == {}
    assert (await db.execute(HealthDaily.__table__.select())).first() is None


# ── The skill ────────────────────────────────────────────────────────────────


async def test_skill_says_so_when_nothing_has_synced(monkeypatch, db):
    from app.skills.health_data import HealthDataSkill

    monkeypatch.setattr("app.skills.health_data.AsyncSessionLocal", lambda: _Passthrough(db))
    out = await HealthDataSkill().execute({"metrics": ["sleep_session"]}, None)
    # Must not be JSON the model can mistake for zeroed-out data.
    assert "No health data stored" in out
    assert "Settings ▸ Health" in out


async def test_skill_returns_dailies_and_a_trend(monkeypatch, db):
    from app.skills.health_data import HealthDataSkill

    today = datetime.now(TZ).replace(hour=9, minute=0, second=0, microsecond=0)
    batch = []
    for i in range(4):                       # current window: 2000 steps/day
        batch.append(_sample("steps", today - timedelta(days=i), 2000, "count"))
    for i in range(7, 11):                   # previous window: 1000 steps/day
        batch.append(_sample("steps", today - timedelta(days=i), 1000, "count"))
    await hs.ingest_samples(db, batch)

    monkeypatch.setattr("app.skills.health_data.AsyncSessionLocal", lambda: _Passthrough(db))
    payload = json.loads(
        await HealthDataSkill().execute({"metrics": ["steps"], "range": "7d"}, None)
    )
    assert payload["granularity"] == "daily"
    assert len(payload["daily"]["steps"]) == 4
    assert all(d["total"] == 2000 for d in payload["daily"]["steps"])
    trend = payload["trend_vs_previous_period"]["steps"]
    assert trend["current_avg"] == 2000 and trend["previous_avg"] == 1000
    assert trend["delta_pct"] == 100.0


class _Passthrough:
    """Hands the skill the test's session without closing it on exit."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False
