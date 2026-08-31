# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Health desk skill (Tier 1) — the owner's biometrics, read-only.

One tool over the health sync pipe (docs/ATOMIX_HEALTH_SYNC.md): the phone
collects via Health Connect and POSTs to /health/ingest; this reads what landed.
Primarily Atomix's, but deliberately NOT restricted_to={"atomix"} — "ask Atomix
how I slept" shouldn't cost a dispatch round-trip just to read a number, and the
roster is trusted in a single-owner system.

Answers come from the daily rollups by default; raw samples are opt-in, because
a week of heart-rate readings is thousands of rows for a question that wants
seven numbers.
"""

import asyncio
import json
import logging

from app.core import runtime_state
from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.services import health as health_service
from app.skills.base import Skill

logger = logging.getLogger(__name__)

# How long a `live` call waits for the phone to answer a sync demand, and how
# often it re-checks. The budget is set by WHO IS WAITING, because the cost of
# blocking is entirely different in the two cases.
#
# Interactive: the owner is watching a spinner. Twenty-five seconds is already
# a long time to stare at one, and a briefing that says the link is down beats
# a tool call that hangs.
#
# Automated (n8n cron → push/silent): NOBODY is waiting. Blocking costs nothing
# and the alternative is a worthless briefing. On 2026-08-05 the 08:00 health
# briefing demanded a sync at 05:00:16Z, gave up at 05:00:41Z, and the phone
# delivered 4,210 samples at 05:02:18Z — 97 seconds after the wait expired. The
# owner had worn the watch all day and synced that morning; the briefing still
# reported a link outage, and every staleness figure in it was correct for the
# instant it ran. Waking a sleeping phone, reading Health Connect and uploading
# a batch takes minutes, so the automated budget is set against that, not
# against a spinner nobody is looking at.
_LIVE_WAIT_INTERACTIVE_S = 25.0
_LIVE_WAIT_AUTOMATED_S = 180.0
_LIVE_POLL_S = 2.5

# Ranges that make a claim about the present. A stale answer to one of these is
# not "old data", it is a false statement — "you slept 7h30m" about a night the
# store has no record of. Trend ranges ('7d', '30d') are exempt: old days are
# the point of the question.
_PRESENT_TENSE = {"today", "yesterday"}


def _wake_budget(context) -> float:
    """How long this turn may block waiting for the phone.

    Keyed on whether a human is actually watching. `output_mode="respond"` is
    the only mode where someone is: push and silent are delivered after the
    fact, so the turn is free to take the minutes a real phone wake needs.
    """
    if getattr(context, "output_mode", "respond") == "respond":
        return _LIVE_WAIT_INTERACTIVE_S
    return _LIVE_WAIT_AUTOMATED_S

_KNOWN_METRICS = [
    "steps",
    "distance",
    "sleep_session",
    "heart_rate",
    "resting_heart_rate",
    "exercise_session",
    "weight",
    "body_fat",
    "oxygen_saturation",
]

# The names models actually reach for when they don't copy the enum verbatim.
# The schema declares the enum, but only Anthropic enforces it in the wire
# format — a background-tier or open-weight model on an automated run happily
# sends "sleep", and an unmapped name silently matches zero rows, which reads
# to the caller as "no health data exists". Normalise instead.
_METRIC_ALIASES = {
    "sleep": "sleep_session",
    "sleep_sessions": "sleep_session",
    "sleep_duration": "sleep_session",
    "hr": "heart_rate",
    "heartrate": "heart_rate",
    "heart_rate_bpm": "heart_rate",
    "rhr": "resting_heart_rate",
    "resting_hr": "resting_heart_rate",
    "restingheartrate": "resting_heart_rate",
    "exercise": "exercise_session",
    "exercise_sessions": "exercise_session",
    "workout": "exercise_session",
    "workouts": "exercise_session",
    "activity": "exercise_session",
    "step": "steps",
    "step_count": "steps",
    "steps_count": "steps",
    "body_weight": "weight",
    "bodyweight": "weight",
    "body_fat_percentage": "body_fat",
    "bodyfat": "body_fat",
    "spo2": "oxygen_saturation",
    "blood_oxygen": "oxygen_saturation",
}


def _normalise_metrics(raw: list[str]) -> tuple[list[str], list[str]]:
    """(recognised metric names, names we could not map). Unmapped names are
    reported back to the caller rather than quietly narrowing the query to
    nothing."""
    metrics: list[str] = []
    unknown: list[str] = []
    for name in raw:
        key = name.strip().lower().replace(" ", "_").replace("-", "_")
        resolved = key if key in _KNOWN_METRICS else _METRIC_ALIASES.get(key)
        if resolved is None:
            unknown.append(name)
        elif resolved not in metrics:
            metrics.append(resolved)
    return metrics, unknown


class HealthDataSkill(Skill):
    name = "health_data"
    deferred = True
    search_keywords = "heart rate sleep steps weight fitness workout gym vitals wearable watch body"
    description = (
        "Queries the owner's own biometrics synced from their phone and watch via "
        "Samsung Health / Health Connect: steps, distance, sleep sessions with "
        "stage breakdowns, heart rate, resting heart rate, exercise sessions, "
        "weight and body composition. Use it whenever the owner asks about their "
        "sleep, activity, fitness or body trends, and whenever real numbers would "
        "ground health coaching instead of generic advice — check the data before "
        "asserting anything about how they have been sleeping or moving. Do NOT "
        "use it for medical diagnosis, for anyone else's health, or for server and "
        "system health (that is Orion's system_ops domain, an entirely different "
        "meaning of the word). Returns compact JSON: per-day aggregates for the "
        "requested metrics and range, plus a period-over-period trend comparison "
        "against the immediately preceding window; pass granularity='raw' for "
        "individual samples instead. Ranges resolve against the owner's LOCAL "
        "calendar, and a night's sleep is filed under the day it STARTED — so "
        "last night's sleep is on yesterday's date when you ask in the morning. "
        "Every answer carries a `freshness` block giving the age of the newest "
        "reading per metric; pass live=true for anything describing the present "
        "(a morning briefing, 'how did I sleep', 'what is my heart rate') and "
        "the tool will demand a sync from the phone first and REFUSE to answer "
        "with stale data rather than let you report last week's numbers as "
        "today's. If nothing has synced yet it says so — tell the owner to set "
        "the link up in Settings ▸ Health on the Android app rather than "
        "guessing at numbers."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "metrics": {
                "type": "array",
                "items": {"type": "string", "enum": _KNOWN_METRICS},
                "description": (
                    "Which metrics to read. Omit for all of them — prefer naming "
                    "the one or two you actually need, the result stays smaller."
                ),
            },
            "range": {
                "type": "string",
                "description": (
                    "'today', 'yesterday', '7d' / '30d' / '90d' (N days back, "
                    "including today), or an explicit 'YYYY-MM-DD:YYYY-MM-DD'. "
                    "Defaults to 7d."
                ),
                "default": "7d",
            },
            "granularity": {
                "type": "string",
                "enum": ["daily", "raw"],
                "description": (
                    "'daily' (default) returns one aggregate per day per metric. "
                    "'raw' returns individual samples — only for questions a daily "
                    "total genuinely cannot answer, e.g. when a heart-rate spike "
                    "occurred during a session."
                ),
                "default": "daily",
            },
            "live": {
                "type": "boolean",
                "description": (
                    "Set true whenever the answer will describe the owner's "
                    "CURRENT state — a morning health briefing, 'how did I "
                    "sleep last night', 'what's my resting heart rate'. The "
                    "tool then asks the phone to sync before answering and "
                    "returns a hard error, with no numbers at all, if the data "
                    "is still too old to describe the present. Leave false for "
                    "history and trend questions ('how was last month'), where "
                    "old data is the point rather than a defect."
                ),
                "default": False,
            },
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        requested = [m for m in (args.get("metrics") or []) if isinstance(m, str)]
        metrics, unknown = _normalise_metrics(requested)
        if requested and not metrics:
            return (
                f"Unrecognised metric name(s): {', '.join(unknown)}. Valid metrics "
                f"are: {', '.join(_KNOWN_METRICS)}. Re-call with one of those, or "
                "omit `metrics` entirely to get everything."
            )
        range_spec = str(args.get("range") or "7d")
        granularity = str(args.get("granularity") or "daily").lower()
        live = bool(args.get("live"))

        start, end = health_service.parse_range(range_spec)
        span_days = (end - start).days + 1
        note = (
            f"Ignored unrecognised metric(s) {', '.join(unknown)}."
            if unknown else ""
        )

        # The freshness gate. A present-tense question answered from a store the
        # phone stopped feeding days ago is the failure this exists to prevent:
        # the briefing reads as current, the numbers are from another week, and
        # nothing in the output says so.
        gate_metrics = metrics or _KNOWN_METRICS
        if live or range_spec.strip().lower() in _PRESENT_TENSE:
            refusal = await self._freshness_gate(
                gate_metrics, live=live, wait_s=_wake_budget(context),
            )
            if refusal:
                return refusal

        async with AsyncSessionLocal() as db:
            # Attached to every answer, gate or no gate. A number is only worth
            # what its age says it is worth, and the caller cannot judge that
            # from a daily aggregate — 'steps: 1685' looks identical whether it
            # is today's running total at 08:00 or a dead sensor's last word.
            fresh_report = await health_service.freshness(db, metrics or _KNOWN_METRICS)

            if granularity == "raw":
                rows = await health_service.raw_rows(db, metrics, start, end)
                if not rows:
                    return await self._no_rows(db, metrics, start, end)
                payload = {
                    "range": {"start": start.isoformat(), "end": end.isoformat()},
                    "granularity": "raw",
                    "samples": [
                        {
                            "metric": r.metric,
                            "start": r.start_ts.isoformat(),
                            "end": r.end_ts.isoformat(),
                            "value": r.value,
                            "unit": r.unit,
                            **({"detail": json.loads(r.detail)} if r.detail not in ("", "{}") else {}),
                        }
                        for r in rows
                    ],
                }
                if len(payload["samples"]) >= 200:
                    payload["truncated"] = "Capped at 200 samples — narrow the range or metric."
                payload["freshness"] = _freshness_view(fresh_report)
                if note:
                    payload["note"] = note
                return json.dumps(payload, ensure_ascii=False)

            rows = await health_service.daily_rows(db, metrics, start, end)
            if not rows:
                return await self._no_rows(db, metrics, start, end)

            # The immediately preceding window of equal length, for the trend.
            from datetime import timedelta

            prev_end = start - timedelta(days=1)
            prev_start = prev_end - timedelta(days=span_days - 1)
            prev_rows = await health_service.daily_rows(db, metrics, prev_start, prev_end)

        by_metric: dict[str, list[dict]] = {}
        for r in rows:
            by_metric.setdefault(r.metric, []).append(
                {"day": r.day.isoformat(), **json.loads(r.agg or "{}")}
            )

        trends: dict[str, dict] = {}
        for metric in by_metric:
            cur = [_headline(metric, json.loads(r.agg or "{}")) for r in rows if r.metric == metric]
            prev = [
                _headline(metric, json.loads(r.agg or "{}"))
                for r in prev_rows
                if r.metric == metric
            ]
            t = health_service.trend(
                [v for v in cur if v is not None], [v for v in prev if v is not None]
            )
            if t:
                trends[metric] = t

        payload = {
            "range": {"start": start.isoformat(), "end": end.isoformat(), "days": span_days},
            "granularity": "daily",
            "daily": by_metric,
        }
        if trends:
            payload["trend_vs_previous_period"] = trends
        payload["freshness"] = _freshness_view(fresh_report)
        if note:
            payload["note"] = note
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    async def _freshness_gate(
        metrics: list[str], *, live: bool, wait_s: float = _LIVE_WAIT_INTERACTIVE_S,
    ) -> str | None:
        """None when the data may describe the present; otherwise the refusal to
        return INSTEAD of any numbers.

        On `live` the phone is asked to sync first and given `wait_s` to answer
        (see _wake_budget — an automated run waits far longer than an
        interactive one). It may still not answer: a phone that is asleep with
        FCM unavailable never reads the demand. That case is the one this is
        built around — the honest outcome of "I could not get today's data" is
        an error, not yesterday's data wearing today's date.
        """
        async with AsyncSessionLocal() as db:
            report = await health_service.freshness(db, metrics)
            stale = health_service.stale_metrics(report)
            if not stale:
                return None

            wake: dict = {}
            if live:
                wake = await health_service.demand_sync(db, reason="live health query")
                served = await HealthDataSkill._await_sync(wait_s)
                if served:
                    report = await health_service.freshness(db, metrics)
                    stale = health_service.stale_metrics(report)
                    if not stale:
                        return None
                logger.info(
                    "health_live_wake_result",
                    extra={
                        "woke": wake.get("woke"), "still_stale": stale,
                        "waited_s": wait_s, "served": served,
                    },
                )

            store = await health_service.status(db)

        if not store["samples"]:
            return (
                "REFUSED — no health data has ever synced. The Health Connect "
                "link is not set up; the owner enables it in Settings ▸ Health "
                "in the Android app. Do not produce a health briefing from "
                "memory, from an earlier turn, or from estimates: say the link "
                "is down and what to check, and stop there."
            )

        lines = []
        for metric in stale:
            f = report[metric]
            if f["newest"] is None:
                lines.append(f"- {metric}: nothing stored at all")
            else:
                lines.append(
                    f"- {metric}: newest reading {f['newest']} "
                    f"({f['age_hours']}h old, budget {f['budget_hours']}h)"
                )
        # Distinguish the two failures for the owner: a phone that was told and
        # stayed quiet is a sync problem, a phone that was never reachable is a
        # setup problem, and they have different fixes.
        if not live:
            asked = ""
        elif wake.get("woke"):
            asked = (
                "The phone was woken and asked to sync, and did not deliver in "
                "time. "
            )
        else:
            asked = (
                "The phone could not even be woken — it is not registered for "
                "push, or the push was rejected. It will still see the request "
                "on its next check, within about fifteen minutes. "
            )
        return (
            "REFUSED — the data is too old to describe the present:\n"
            + "\n".join(lines)
            + f"\n\n{asked}This is a broken sync, not a health finding. Report "
            "it as such: tell the owner which metrics stopped arriving and when "
            "the last reading was, and suggest checking that the watch is worn "
            "and paired and that Speda GO's Health tab still shows the link "
            "live. Do NOT report the last stored values as if they were "
            "current, do NOT describe them as 'the most recent data' inside a "
            "briefing about today, and do NOT fill the gap with estimates. If "
            "the whole briefing depended on this data, the briefing is the "
            "sync failure — that is a complete and correct answer.\n\n"
            f"(Historical figures are still readable for genuinely past-tense "
            f"questions: call again without live=true and with an explicit past "
            f"range. Stored data covers {store['first_day']} → {store['last_day']}.)"
        )

    @staticmethod
    async def _await_sync(wait_s: float = _LIVE_WAIT_INTERACTIVE_S) -> bool:
        """Wait for the phone to serve the outstanding demand. False on timeout."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        while loop.time() < deadline:
            await asyncio.sleep(_LIVE_POLL_S)
            if runtime_state.get_health_sync_demand().get("served_at"):
                logger.info("health_live_sync_served")
                return True
        logger.warning("health_live_sync_timeout", extra={"waited_s": wait_s})
        return False

    @staticmethod
    async def _no_rows(db, metrics: list[str], start, end) -> str:
        """An empty window is NOT the same as an empty pipe, and conflating the
        two is how a briefing ends up announcing "no health data synced" over a
        database full of samples: an automated run has no human to correct a
        wrong date range or a metric the phone doesn't collect. So look at what
        actually exists and hand the caller the coverage it needs to re-query.
        Only a genuinely empty store gets the set-up-the-link message."""
        which = ", ".join(metrics) if metrics else "any metric"
        head = (
            f"No health data stored for {which} between {start.isoformat()} and "
            f"{end.isoformat()}. "
        )
        try:
            st = await health_service.status(db)
        except Exception as exc:  # noqa: BLE001 — never turn a miss into a crash
            logger.warning("health_status_probe_failed", extra={"error": str(exc)})
            return head + "Do not estimate or invent figures; say the data isn't there."

        if not st["samples"]:
            return head + (
                "Nothing has EVER synced — the Health Connect link has not been set "
                "up yet; the owner enables it in Settings ▸ Health in the Android "
                "app. Do not estimate or invent figures; say the data isn't there."
            )

        available = ", ".join(f"{m} ({n})" for m, n in sorted(st["per_metric"].items()))
        msg = head + (
            "The pipe IS live, so do not tell the owner health sync is missing — "
            "this window is simply empty. Stored data covers "
            f"{st['first_day']} → {st['last_day']} (last ingest {st['last_ingest']}), "
            f"metrics: {available}. Re-call this tool with a range inside that span "
            f"(today is {health_service.owner_today().isoformat()}) and a metric "
            "from that list before concluding anything."
        )
        if "sleep_session" in metrics:
            msg += (
                " Note: a night's sleep is filed under the calendar day it STARTED, "
                "so last night's sleep sits on yesterday's date in a morning query."
            )
        return msg


def _freshness_view(report: dict) -> dict:
    """The freshness report as it rides in a tool answer: only metrics that
    actually have data, plus an explicit `stale` list so the caller does not
    have to compare ages against budgets itself. Metrics that have never synced
    are dropped here rather than listed as stale — the owner does not track
    body fat, and saying so on every reply trains the reader to skim the block."""
    have = {m: f for m, f in report.items() if f.get("newest")}
    return {
        "note": (
            "Age of the newest reading per metric, in hours. Anything listed "
            "under `stale` is outside the window in which it can be described "
            "as current — cite it as of its date, or not at all."
        ),
        "metrics": have,
        "stale": [m for m, f in have.items() if not f.get("fresh")],
    }


def _headline(metric: str, agg: dict) -> float | None:
    """The one number that represents a day for this metric — the daily total
    for cumulative/duration metrics, the daily average for point readings. Used
    only for the trend comparison."""
    kind = health_service.metric_kind(metric)
    if kind in (health_service.CUMULATIVE, health_service.DURATION):
        return agg.get("total")
    return agg.get("avg")
