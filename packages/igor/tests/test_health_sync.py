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
    for path in ("/health/ingest", "/health/status", "/health/data",
                 "/health/freshness", "/health/sync-demand"):
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


def test_ranges_resolve_against_the_owners_local_day_not_utc():
    # Samples are filed by LOCAL day (see local_day), so an unanchored range
    # must resolve in the same frame. UTC "today" is the previous local day for
    # the first three hours of every Istanbul morning — precisely when the
    # nightly digest runs, which used to shift its whole window by a day.
    assert hs.parse_range("today")[0] == hs.owner_today()
    assert hs.parse_range("7d")[1] == hs.owner_today()


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


async def test_empty_window_over_a_live_pipe_reports_coverage_not_a_dead_link(
    monkeypatch, db
):
    """The briefing bug: an automated turn queries a window it guessed wrong,
    gets nothing back, and tells the owner health sync isn't set up — over a
    database full of samples. An empty window must be distinguishable from an
    empty pipe, with enough coverage detail to re-query."""
    from app.skills.health_data import HealthDataSkill

    now = datetime.now(TZ).replace(hour=9, minute=0, second=0, microsecond=0)
    await hs.ingest_samples(
        db, [_sample("steps", now - timedelta(days=i), 2000, "count") for i in range(5)]
    )
    monkeypatch.setattr("app.skills.health_data.AsyncSessionLocal", lambda: _Passthrough(db))

    out = await HealthDataSkill().execute(
        {"metrics": ["steps"], "range": "2025-03-01:2025-03-07"}, None
    )
    assert "pipe IS live" in out
    assert "Nothing has EVER synced" not in out
    assert "steps (5)" in out                     # what exists
    assert hs.owner_today().isoformat() in out    # and what "today" actually is


async def test_metric_aliases_are_normalised_not_silently_dropped(monkeypatch, db):
    # Only Anthropic enforces the schema enum on the wire; a background-tier or
    # open-weight model on an automated run sends "sleep", and an unmapped name
    # matched zero rows — indistinguishable from "you have no sleep data".
    from app.skills.health_data import HealthDataSkill

    night = datetime.now(TZ).replace(hour=23, minute=40, second=0, microsecond=0)
    await hs.ingest_samples(db, [_sample("sleep_session", night - timedelta(days=1), 400, "min")])
    monkeypatch.setattr("app.skills.health_data.AsyncSessionLocal", lambda: _Passthrough(db))

    payload = json.loads(
        await HealthDataSkill().execute({"metrics": ["sleep"], "range": "7d"}, None)
    )
    assert "sleep_session" in payload["daily"]


async def test_unmappable_metric_returns_a_corrective_error(monkeypatch, db):
    from app.skills.health_data import HealthDataSkill

    monkeypatch.setattr("app.skills.health_data.AsyncSessionLocal", lambda: _Passthrough(db))
    out = await HealthDataSkill().execute({"metrics": ["vo2max"], "range": "7d"}, None)
    assert "Unrecognised metric" in out and "resting_heart_rate" in out


async def test_partly_unknown_metric_list_still_answers_and_says_what_it_dropped(
    monkeypatch, db
):
    from app.skills.health_data import HealthDataSkill

    await hs.ingest_samples(
        db, [_sample("steps", datetime.now(TZ).replace(hour=9), 4000, "count")]
    )
    monkeypatch.setattr("app.skills.health_data.AsyncSessionLocal", lambda: _Passthrough(db))
    payload = json.loads(
        await HealthDataSkill().execute({"metrics": ["steps", "vo2max"], "range": "7d"}, None)
    )
    assert "steps" in payload["daily"]
    assert "vo2max" in payload["note"]


class _Passthrough:
    """Hands the skill the test's session without closing it on exit."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


# ── Freshness contract ──────────────────────────────────────────────────────
# The failure this guards: a briefing that reports a heart rate from four days
# ago under today's date. The store is full, every query succeeds, and nothing
# in the output says the numbers describe another week.

@pytest.mark.asyncio
async def test_freshness_flags_a_stale_metric_and_passes_a_current_one(db):
    now = datetime.now(TZ)
    await hs.ingest_samples(db, [
        _sample("heart_rate", now - timedelta(hours=1), 64.0, "bpm"),
        _sample("sleep_session", now - timedelta(days=5), 7.5 * 3600, "s"),
    ])

    report = await hs.freshness(db, ["heart_rate", "sleep_session"])
    assert report["heart_rate"]["fresh"] is True
    assert report["sleep_session"]["fresh"] is False
    assert report["sleep_session"]["age_hours"] > hs.freshness_budget_h("sleep_session")
    assert hs.stale_metrics(report) == ["sleep_session"]


@pytest.mark.asyncio
async def test_freshness_reports_never_synced_metrics_rather_than_omitting_them(db):
    """A caller checking freshness needs "nothing here" to be an answer. An
    omitted key reads as "not asked about" and is how a missing metric becomes
    an invented one."""
    report = await hs.freshness(db, ["steps"])
    assert report["steps"] == {
        "newest": None, "age_hours": None, "fresh": False,
        "budget_hours": hs.freshness_budget_h("steps"),
    }


@pytest.mark.asyncio
async def test_owner_today_is_local_not_utc():
    """owner_today() used to call itself; the RecursionError was swallowed and
    the UTC date returned, so every range resolved in the wrong frame for the
    first three hours of each Istanbul day."""
    from app.core import clock
    assert hs.owner_today() == clock.owner_today()


# ── The gate: a stale store must not answer a present-tense question ────────
# Reproduces the briefing that started this: on 4 August, sleep_session's newest
# row was 30 July and heart rate's was 1 August. Atomix reported both under
# today's date, and nothing in the output said the numbers were from last week.

@pytest_asyncio.fixture
async def skill_db(db, monkeypatch):
    import app.skills.health_data as hd
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(hd, "AsyncSessionLocal", _session)
    # The gate's live path waits on a phone that will never answer; keep the
    # test to the timeout logic rather than the wall clock.
    monkeypatch.setattr(hd, "_LIVE_WAIT_INTERACTIVE_S", 0.05)
    monkeypatch.setattr(hd, "_LIVE_WAIT_AUTOMATED_S", 0.05)
    monkeypatch.setattr(hd, "_LIVE_POLL_S", 0.01)
    return db


@pytest.mark.asyncio
async def test_stale_store_refuses_a_live_query_instead_of_reporting_old_vitals(skill_db):
    from app.skills.health_data import HealthDataSkill

    now = datetime.now(TZ)
    await hs.ingest_samples(skill_db, [
        _sample("sleep_session", now - timedelta(days=5), 7.5 * 3600, "s"),
        _sample("heart_rate", now - timedelta(days=3), 98.0, "bpm"),
    ])

    out = await HealthDataSkill().execute(
        {"metrics": ["sleep_session", "heart_rate"], "range": "today", "live": True}, None
    )
    assert out.startswith("REFUSED")
    assert "sleep_session" in out and "heart_rate" in out
    # The refusal must carry no numbers to report — the whole failure mode was
    # an agent finding something quotable in the error and quoting it.
    assert "7.5" not in out and "98" not in out
    # No device is registered in this DB, so the wake could not be sent at all —
    # which the refusal must say, because "the phone ignored us" and "we never
    # reached the phone" have different fixes for the owner.
    assert "could not even be woken" in out


@pytest.mark.asyncio
async def test_refusal_says_the_phone_was_woken_when_a_device_is_registered(
    skill_db, monkeypatch,
):
    """The other branch: a phone that WAS pushed and still delivered nothing is
    a sync fault, not a setup fault."""
    from app.services import academic as academic_service
    from app.services import fcm
    from app.skills.health_data import HealthDataSkill

    await academic_service.register_device(skill_db, "pixel", "phone", "fid-123")

    async def _delivered(**kwargs):
        return True, "delivered"

    monkeypatch.setattr(fcm, "send_data_message", _delivered)

    now = datetime.now(TZ)
    await hs.ingest_samples(skill_db, [
        _sample("heart_rate", now - timedelta(days=3), 98.0, "bpm"),
    ])

    out = await HealthDataSkill().execute(
        {"metrics": ["heart_rate"], "range": "today", "live": True}, None
    )
    assert out.startswith("REFUSED")
    assert "woken and asked to sync" in out


@pytest.mark.asyncio
async def test_a_delivered_batch_serves_an_outstanding_sync_demand(skill_db):
    """The handshake the live wait turns on: ingest is what clears the demand,
    so a phone that answers releases the waiting turn."""
    from app.core import runtime_state

    runtime_state.request_health_sync(reason="test")
    assert not runtime_state.get_health_sync_demand().get("served_at")

    now = datetime.now(TZ)
    await hs.ingest_samples(skill_db, [_sample("steps", now, 120.0, "count")])

    assert runtime_state.get_health_sync_demand().get("served_at")


@pytest.mark.asyncio
async def test_fresh_store_answers_normally_and_states_the_age(skill_db):
    from app.skills.health_data import HealthDataSkill

    now = datetime.now(TZ)
    await hs.ingest_samples(skill_db, [
        _sample("heart_rate", now - timedelta(hours=1), 64.0, "bpm"),
    ])

    out = await HealthDataSkill().execute(
        {"metrics": ["heart_rate"], "range": "today", "live": True}, None
    )
    payload = json.loads(out)
    assert payload["freshness"]["stale"] == []
    assert payload["freshness"]["metrics"]["heart_rate"]["fresh"] is True
    assert payload["daily"]["heart_rate"]


@pytest.mark.asyncio
async def test_history_questions_are_not_gated_by_freshness(skill_db):
    """'How was last month' is a past-tense question. Old data is the answer,
    not a defect — gating it would make the archive unreadable."""
    from app.skills.health_data import HealthDataSkill

    now = datetime.now(TZ)
    await hs.ingest_samples(skill_db, [
        _sample("steps", now - timedelta(days=20), 9000.0, "count"),
    ])

    out = await HealthDataSkill().execute(
        {"metrics": ["steps"], "range": "30d"}, None
    )
    assert not out.startswith("REFUSED")
    payload = json.loads(out)
    assert payload["daily"]["steps"]
    # …but the age still rides along, so nothing can quote it as current.
    assert payload["freshness"]["stale"] == ["steps"]


@pytest.mark.asyncio
async def test_empty_store_says_the_link_was_never_set_up(skill_db):
    from app.skills.health_data import HealthDataSkill

    out = await HealthDataSkill().execute({"range": "today", "live": True}, None)
    assert out.startswith("REFUSED")
    assert "has ever synced" in out
    assert "Settings" in out


@pytest.mark.asyncio
async def test_a_delivered_batch_clears_an_outstanding_sync_demand(skill_db):
    """Exactly-once in the other direction: the phone answering is what ends the
    demand, so a demand raised while it was asleep survives until it does."""
    from app.core import runtime_state

    runtime_state.request_health_sync(reason="test")
    assert not runtime_state.get_health_sync_demand().get("served_at")

    await hs.ingest_samples(skill_db, [
        _sample("steps", datetime.now(TZ), 120.0, "count"),
    ])
    assert runtime_state.get_health_sync_demand().get("served_at")


# ── The wake budget ─────────────────────────────────────────────────────────
# Regression, 2026-08-05: the 08:00 health briefing demanded a sync at
# 05:00:16Z, gave up at 05:00:41Z on the flat 25s budget, and the phone
# delivered 4,210 samples at 05:02:18Z — 97 seconds too late. The owner had
# worn the watch all day and synced that morning; the briefing announced a link
# outage anyway. Nobody was waiting on that turn: it was an n8n cron with
# output_mode="push", so the 25s budget was protecting a spinner that did not
# exist.


def _ctx(output_mode: str, triggered_by: str = "n8n"):
    from app.core.context import AgentContext

    return AgentContext(
        agent_id="atomix", user_id=1, session_id=1, request_id="req",
        triggered_by=triggered_by, trigger_payload={}, output_mode=output_mode,
        model="m", system_prompt="", conversation_history=[], db=None,
        timezone="Europe/Istanbul",
    )


def test_an_interactive_turn_keeps_the_short_budget():
    """The owner is watching a spinner — do not hang on it."""
    from app.skills.health_data import _LIVE_WAIT_INTERACTIVE_S, _wake_budget

    assert _wake_budget(_ctx("respond", "user")) == _LIVE_WAIT_INTERACTIVE_S


@pytest.mark.parametrize("mode", ["push", "silent"])
def test_an_automated_run_waits_far_longer(mode):
    """Nobody is waiting, and the alternative is a worthless briefing."""
    from app.skills.health_data import _LIVE_WAIT_AUTOMATED_S, _wake_budget

    assert _wake_budget(_ctx(mode)) == _LIVE_WAIT_AUTOMATED_S


def test_the_automated_budget_outlasts_a_real_phone_wake():
    """122s was the observed demand→delivery latency. The budget must clear it."""
    from app.skills.health_data import _LIVE_WAIT_AUTOMATED_S

    assert _LIVE_WAIT_AUTOMATED_S > 122, (
        "the budget is shorter than the wake that caused this regression"
    )
