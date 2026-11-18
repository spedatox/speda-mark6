"""Interpret a proposal from a scoped mail watch before committing a delivery."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.automation import Automation


def is_attention_watch(payload):
    return (payload.get("type") == "mail_watch" and payload.get("attention_only") is True
            and payload.get("mail_calendar_create") is False and bool(payload.get("automation_key")))


async def delivery_block(db, owner, trigger, admitted, *, now=None):
    now = now or datetime.now(timezone.utc)
    watch = await db.scalar(select(Automation).where(Automation.user_id == owner,
        Automation.public_key == trigger["automation_key"]).with_for_update())
    if (not watch or watch.deleted_at or not watch.desired_active or not watch.active
            or watch.content_version != trigger.get("automation_revision") or not watch.spec.get("attention_only")):
        return "watch_changed"
    expiry = watch.spec.get("expires_at")
    if expiry and datetime.fromisoformat(expiry) <= now:
        return "expired"
    snooze = watch.spec.get("attention_snoozed_until")
    if snooze and datetime.fromisoformat(snooze) > now:
        return "snoozed"
    if admitted.tzinfo is None:
        admitted = admitted.replace(tzinfo=timezone.utc)
    if now - admitted > timedelta(minutes=10):
        return "expired"
    hour = now.astimezone(ZoneInfo(watch.timezone)).hour
    lower, upper = watch.spec.get("attention_quiet", [22, 9])
    quiet = lower <= hour < upper if lower <= upper else hour >= lower or hour < upper
    return "quiet_hours" if quiet else None


async def disposition(db, run, proposal, *, now=None):
    block = await delivery_block(db, run.user_id, run.policy["trigger_payload"], run.created_at, now=now)
    if block:
        return "", block
    try:
        candidate = json.loads(proposal)
    except ValueError:
        return "", "invalid_disposition"
    if not isinstance(candidate, dict):
        return "", "invalid_disposition"
    if candidate.get("disposition") == "silent":
        return "", "silent"
    notice = candidate.get("notice")
    if candidate.get("disposition") != "notify" or not isinstance(notice, str) or not notice.strip():
        return "", "invalid_disposition"
    if run.policy["trigger_payload"].get("attention_shadow") is True:
        return "", "would_notify"
    return notice.strip()[:700], "notify"
