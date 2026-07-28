"""
The one place that knows what time it is.

Igor runs on three clocks that do not agree — the host is on CEST, the
containers are on UTC, and the owner lives in Europe/Istanbul — so "now" is an
ambiguous word and every module that answered it its own way answered it
differently. The result was a fleet of three-hour bugs that all looked like
something else: lecture-end detection firing at the wrong hour, health samples
landing on yesterday, an agent reading a message stamped 11:00 when the owner
sent it at 14:00, and n8n crons hand-compensated to 05:00 so they would land at
08:00.

**The policy, and it has exactly two rules:**

1. **Store and compare in UTC.** Anything written to a column, compared against
   another instant, or used to measure elapsed time uses `utc_now()`.
2. **Decide and display in the owner's timezone.** Anything that means a
   *wall-clock* time — "08:30", "did a lecture just end", "which day does this
   health sample belong to", "good morning" — uses `owner_now()` /
   `owner_today()`.

If you cannot tell which one a call site needs, ask what the value would mean if
the owner flew to Tokyo. An instant is unchanged; a wall clock moves. Instants
are UTC, wall clocks are owner-local.

`owner_now()` deliberately returns a NAIVE datetime. The wall-clock columns it
is compared against are naive, and mixing naive with aware raises TypeError on
comparison — a crash is the good outcome there; the bad one is a silent
three-hour skew, which is what we are fixing.
"""

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings

logger = logging.getLogger(__name__)

_warned = False


def owner_tz() -> ZoneInfo:
    """The owner's timezone, from OWNER_TIMEZONE. Falls back to UTC — loudly,
    once — because a typo here silently shifts every wall-clock decision in the
    system and is otherwise invisible."""
    global _warned
    try:
        return ZoneInfo(settings.owner_timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        if not _warned:
            logger.error(
                "owner_timezone_invalid",
                extra={"value": settings.owner_timezone, "falling_back_to": "UTC"},
            )
            _warned = True
        return ZoneInfo("UTC")


def utc_now() -> datetime:
    """Aware UTC instant. For storage, comparison and durations."""
    return datetime.now(timezone.utc)


def owner_now() -> datetime:
    """The owner's wall clock, naive. For scheduling decisions and display."""
    return datetime.now(owner_tz()).replace(tzinfo=None)


def owner_today() -> date:
    """The owner's calendar date — which is not the UTC date after 21:00 local."""
    return datetime.now(owner_tz()).date()


def to_owner(moment: datetime) -> datetime:
    """Convert an instant to the owner's wall clock, naive.

    A naive input is assumed to be UTC, because that is what every naive
    datetime in this codebase's storage layer is (`datetime.utcnow()` column
    defaults). That assumption is the whole reason this helper exists rather
    than each caller guessing.
    """
    aware = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
    return aware.astimezone(owner_tz()).replace(tzinfo=None)
