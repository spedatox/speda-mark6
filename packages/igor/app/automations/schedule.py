"""
The structured schedule — when an automation fires, stored as MEANING rather
than as a cron string.

This module exists because of one bug class. The old spec stored `cron` and
nothing else, so `0 9 * * 1,3,5` was the only record of "Monday, Wednesday and
Friday at nine". Nothing could render that back to the owner in either language,
nothing could re-open it in an editor with the right boxes ticked, and a spec
that lost the cron lost the intent with it. A cron expression is a COMPILATION
ARTIFACT here: `to_cron()` is the only thing that produces one, `describe()` is
the only thing that reads a schedule for display, and the schedule dict is the
source of truth both work from.

Display is returned STRUCTURALLY, never as a sentence. Heartbreaker speaks two
languages (lib/i18n/{tr,en}.ts) and a backend that returns "Günde bir" has
already decided which one the owner reads. The UI formats; this module states.

Day numbering is ISO: 1=Monday … 7=Sunday — the numbering the briefings that
were folded into this module already used (`scripts/briefings_seed.json`).
Cron's own numbering (0=Sunday) is an encoding detail handled in `to_cron()`.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# ISO weekday numbers, the vocabulary the whole module speaks.
MONDAY, SUNDAY = 1, 7

FREQUENCIES = ("once", "daily", "weekly", "monthly")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# A monthly automation on the 29th, 30th or 31st silently skips the months that
# are too short — February eats three of them every year. We do not refuse it
# (the owner may genuinely mean "the 31st, when there is one"), but the fact
# travels with the schedule so the UI can say so instead of the owner finding
# out in February.
SHORT_MONTH_DOM = 29


class ScheduleError(ValueError):
    """A schedule that cannot fire. The message is shown to the owner verbatim,
    so it names the field and the fix, never just 'invalid'."""


def _parse_time(at: str) -> tuple[int, int]:
    m = _TIME_RE.match(str(at or "").strip())
    if not m:
        raise ScheduleError(
            f"Time must be HH:MM in 24-hour form (e.g. '09:00'), got {at!r}."
        )
    return int(m.group(1)), int(m.group(2))


def _parse_days(days) -> list[int]:
    if not isinstance(days, (list, tuple)) or not days:
        raise ScheduleError(
            "A weekly schedule needs at least one weekday in 'days' "
            "(1=Monday … 7=Sunday)."
        )
    out: list[int] = []
    for d in days:
        try:
            n = int(d)
        except (TypeError, ValueError):
            raise ScheduleError(f"Weekday must be a number 1-7, got {d!r}.") from None
        if not MONDAY <= n <= SUNDAY:
            raise ScheduleError(f"Weekday must be 1 (Monday) to 7 (Sunday), got {n}.")
        if n not in out:
            out.append(n)
    return sorted(out)


def normalize(schedule: dict, *, today: date | None = None) -> dict:
    """Validate a schedule and return it in canonical form.

    Raises ScheduleError with an owner-readable message. `today` is injectable
    only so the past-date guard is testable — production passes nothing and the
    guard reads the real clock through `app.core.clock` (Rule 14).
    """
    if not isinstance(schedule, dict):
        raise ScheduleError("Schedule must be an object.")

    freq = str(schedule.get("frequency") or "").strip().lower()
    if freq not in FREQUENCIES:
        raise ScheduleError(
            f"Frequency must be one of {', '.join(FREQUENCIES)}, got {freq!r}."
        )

    hour, minute = _parse_time(schedule.get("at"))
    out: dict = {"frequency": freq, "at": f"{hour:02d}:{minute:02d}"}

    if freq == "weekly":
        out["days"] = _parse_days(schedule.get("days"))

    elif freq == "monthly":
        try:
            dom = int(schedule.get("dom"))
        except (TypeError, ValueError):
            raise ScheduleError(
                "A monthly schedule needs 'dom' — the day of the month, 1-31."
            ) from None
        if not 1 <= dom <= 31:
            raise ScheduleError(f"Day of month must be 1-31, got {dom}.")
        out["dom"] = dom

    elif freq == "once":
        raw = str(schedule.get("date") or "").strip()
        try:
            when = date.fromisoformat(raw)
        except ValueError:
            raise ScheduleError(
                f"A one-off needs 'date' as YYYY-MM-DD, got {raw!r}."
            ) from None
        # A date already gone produces a workflow that is live, green, and can
        # never fire — the failure mode this whole module is meant to make
        # impossible to reach by accident.
        if today is None:
            from app.core.clock import owner_today

            today = owner_today()
        if when < today:
            raise ScheduleError(
                f"{when.isoformat()} is in the past — a one-off scheduled then "
                "would never fire. Pick today or a future date."
            )
        out["date"] = when.isoformat()

    return out


def to_cron(schedule: dict) -> str:
    """Compile a normalized schedule into a 5-field cron expression.

    The ONLY producer of cron in this system. The expression is interpreted in
    the workflow's pinned timezone (composer sets `settings.owner_timezone` on
    every workflow), never in whatever the n8n container happens to be set to.
    """
    freq = schedule["frequency"]
    hour, minute = _parse_time(schedule["at"])

    if freq == "daily":
        return f"{minute} {hour} * * *"

    if freq == "weekly":
        # ISO Sunday is 7; cron's canonical Sunday is 0. Both are accepted by
        # most parsers, but emitting 0 avoids depending on that tolerance.
        dow = ",".join(str(d % 7) for d in schedule["days"])
        return f"{minute} {hour} * * {dow}"

    if freq == "monthly":
        return f"{minute} {hour} {schedule['dom']} * *"

    if freq == "once":
        when = date.fromisoformat(schedule["date"])
        # Pinned to day AND month, so it cannot fire again next month. It could
        # still come round next YEAR (cron has no year field) — which is what
        # `expires_at` below exists to prevent.
        return f"{minute} {hour} {when.day} {when.month} *"

    raise ScheduleError(f"Cannot compile frequency {freq!r}.")


def expiry_for(schedule: dict) -> str | None:
    """The ISO expiry a schedule implies, or None when it runs forever.

    Only a one-off implies one, and it is what actually makes it a one-off: the
    cron alone would come back on the same date next year, on a workflow the
    owner stopped thinking about eleven months earlier. The gate node refuses to
    fire past this, and `manager.list_automations` deactivates it on sight.
    """
    if schedule.get("frequency") != "once":
        return None
    when = date.fromisoformat(schedule["date"])
    hour, minute = _parse_time(schedule["at"])
    fires_at = datetime(when.year, when.month, when.day, hour, minute)
    # An hour's grace past the firing time: enough that a retry or a busy
    # trigger registry does not lose the run, far short of a second firing.
    return (fires_at + timedelta(hours=1)).isoformat()


def describe(schedule: dict) -> dict:
    """The display payload — structure, not prose. See the module docstring.

    `cron` rides along for the drift check and for anyone debugging what n8n was
    actually given; it is not what the UI renders.
    """
    freq = schedule.get("frequency")
    from app.config import settings

    out = {
        "frequency": freq,
        "at": schedule.get("at"),
        "timezone": settings.owner_timezone,
    }
    if freq == "weekly":
        out["days"] = schedule.get("days")
    elif freq == "monthly":
        out["dom"] = schedule.get("dom")
        out["skips_short_months"] = int(schedule.get("dom", 1)) >= SHORT_MONTH_DOM
    elif freq == "once":
        out["date"] = schedule.get("date")
    try:
        out["cron"] = to_cron(schedule)
    except ScheduleError:
        out["cron"] = None
    return out


def summarize(schedule: dict) -> str:
    """A one-line English summary for LOGS and for the agent-facing tool — never
    for the owner's screen, which renders `describe()` in its own language."""
    freq = schedule.get("frequency")
    at = schedule.get("at")
    if freq == "daily":
        return f"every day at {at}"
    if freq == "weekly":
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        days = ", ".join(names[d - 1] for d in schedule.get("days", []))
        return f"every {days} at {at}"
    if freq == "monthly":
        return f"on day {schedule.get('dom')} of each month at {at}"
    if freq == "once":
        return f"once on {schedule.get('date')} at {at}"
    return "on a schedule"
