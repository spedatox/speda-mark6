"""Interpret a proposal from a scoped mail watch before committing a delivery."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.automation import AttentionItem, Automation
from app.services.coordination_budget import digest, insert_for


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
    kind = candidate.get("disposition")
    if kind not in {"notify", "ask", "digest"} or not isinstance(notice, str) or not notice.strip():
        return "", "invalid_disposition"
    trigger = run.policy["trigger_payload"]
    data = trigger.get("event_data", {})
    observed = {m.get("id") for m in data.get("messages", []) if isinstance(m, dict)}
    sources = candidate.get("source_ids", [])
    if (not isinstance(sources, list) or len(sources) > 16
            or any(not isinstance(s, str) or not s or s not in observed for s in sources)):
        return "", "invalid_sources"
    if not sources and (kind != "notify" or observed):
        return "", "missing_sources"
    if trigger.get("attention_shadow") is True:
        return "", {"ask": "would_ask", "digest": "would_digest", "notify": "would_notify"}[kind]
    notice = notice.strip()[:700]
    if sources:
        watch = await db.scalar(select(Automation).where(Automation.public_key == trigger["automation_key"],
                                                          Automation.user_id == run.user_id))
        key = digest([watch.agent_id, watch.connection_binding, sorted(set(sources))])
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
        inserted = await db.execute(insert_for(db, AttentionItem).values(
            user_id=run.user_id, key=key, watch_id=watch.id, revision=watch.content_version,
            agent_id=watch.agent_id, state=kind, text=notice, expires_at=stamp + timedelta(days=2),
            created_at=stamp).on_conflict_do_nothing())
        if not inserted.rowcount:
            return "", "duplicate"
    return ("" if kind == "digest" else notice), kind


async def active_items(db, owner, agent, state, cutoff, *, lock=False):
    stmt = (select(AttentionItem).join(Automation, Automation.id == AttentionItem.watch_id)
            .where(AttentionItem.user_id == owner, AttentionItem.agent_id == agent,
                   AttentionItem.state == state, AttentionItem.created_at <= cutoff,
                   AttentionItem.expires_at > datetime.utcnow(), Automation.active.is_(True),
                   Automation.desired_active.is_(True), Automation.deleted_at.is_(None),
                   Automation.content_version == AttentionItem.revision)
            .order_by(AttentionItem.created_at.desc()).limit(4))
    if lock:
        stmt = stmt.with_for_update(skip_locked=True)
    # A saved definition can expire or be snoozed without changing an item.
    rows = list(await db.scalars(stmt))
    eligible = []
    now = datetime.now(timezone.utc)
    for item in rows:
        watch = await db.get(Automation, item.watch_id)
        expiry = watch.spec.get("expires_at")
        snooze = watch.spec.get("attention_snoozed_until")
        if (expiry and datetime.fromisoformat(expiry) <= now) or (snooze and datetime.fromisoformat(snooze) > now):
            continue
        eligible.append(item)
    return eligible
