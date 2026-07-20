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

import json
import logging

from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.services import health as health_service
from app.skills.base import Skill

logger = logging.getLogger(__name__)

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


class HealthDataSkill(Skill):
    name = "health_data"
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
        "individual samples instead. If nothing has synced yet it says so — tell "
        "the owner to set the link up in Settings ▸ Health on the Android app "
        "rather than guessing at numbers."
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
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        metrics = [m for m in (args.get("metrics") or []) if isinstance(m, str)]
        range_spec = str(args.get("range") or "7d")
        granularity = str(args.get("granularity") or "daily").lower()

        start, end = health_service.parse_range(range_spec)
        span_days = (end - start).days + 1

        async with AsyncSessionLocal() as db:
            if granularity == "raw":
                rows = await health_service.raw_rows(db, metrics, start, end)
                if not rows:
                    return self._empty(metrics, start, end)
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
                return json.dumps(payload, ensure_ascii=False)

            rows = await health_service.daily_rows(db, metrics, start, end)
            if not rows:
                return self._empty(metrics, start, end)

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
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _empty(metrics: list[str], start, end) -> str:
        which = ", ".join(metrics) if metrics else "any metric"
        return (
            f"No health data stored for {which} between {start.isoformat()} and "
            f"{end.isoformat()}. Either the range predates the first sync, or the "
            "Health Connect link has not been set up yet — the owner enables it in "
            "Settings ▸ Health in the Android app. Do not estimate or invent "
            "figures; say the data isn't there."
        )


def _headline(metric: str, agg: dict) -> float | None:
    """The one number that represents a day for this metric — the daily total
    for cumulative/duration metrics, the daily average for point readings. Used
    only for the trend comparison."""
    kind = health_service.metric_kind(metric)
    if kind in (health_service.CUMULATIVE, health_service.DURATION):
        return agg.get("total")
    return agg.get("avg")
